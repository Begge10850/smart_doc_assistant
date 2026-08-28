import os
from uuid import uuid4

import psycopg
import streamlit as st
from dotenv import load_dotenv
from pgvector import Vector

from pgvector.psycopg import register_vector


load_dotenv()


def get_database_url():
    """Return the configured PostgreSQL connection URL."""

    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return database_url

    try:
        database_url = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError):
        database_url = None

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    return database_url


def test_database_connection():
    """Open a short PostgreSQL connection and verify the database responds."""
    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            return cursor.fetchone()[0]


def create_customer_case(complaint):
    """Persist a submitted case and its initial evidence inventory."""
    case_query = """
        insert into customer_cases (
            case_reference, reported_at, status, claimant_role, tracking_number,
            carrier, country, delivery_date, declared_value,
            complaint_type, customer_email, additional_information,
            downstream_processing_status
        ) values (
            %(case_reference)s, %(reported_at)s, %(status)s, %(claimant_role)s,
            %(tracking_number)s, %(carrier)s, %(country)s, %(delivery_date)s,
            %(declared_value)s, %(complaint_type)s, %(customer_email)s,
            %(additional_information)s, %(downstream_processing_status)s
        )
        returning id;
    """
    evidence_query = """
        insert into customer_case_evidence (
            customer_case_id, original_file_name, content_type, size_bytes
        ) values (%s, %s, %s, %s)
        returning id;
    """

    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(case_query, complaint)
            customer_case_id = cursor.fetchone()[0]
            for evidence in complaint["evidence"]:
                cursor.execute(
                    evidence_query,
                    (
                        customer_case_id,
                        evidence["file_name"],
                        evidence.get("content_type"),
                        evidence["size_bytes"],
                    ),
                )
                evidence["evidence_id"] = cursor.fetchone()[0]
        connection.commit()

    complaint["customer_case_id"] = customer_case_id
    return customer_case_id


def find_active_customer_case(tracking_number, complaint_type):
    """Find an existing active case for the same shipment problem."""
    query = """
        select case_reference, status, downstream_processing_status, reported_at
        from customer_cases
        where upper(tracking_number) = upper(%s)
          and complaint_type = %s
          and status not in ('closed', 'cancelled')
        order by reported_at desc
        limit 1;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (tracking_number.strip(), complaint_type))
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "case_reference": row[0],
                "status": row[1],
                "downstream_processing_status": row[2],
                "reported_at": row[3].isoformat(),
            }


def record_duplicate_submission_attempt(case_reference):
    """Record a repeat report for employee visibility without a new case."""
    query = """
        with matched_case as (
            update customer_cases
            set duplicate_submission_count = duplicate_submission_count + 1,
                last_duplicate_submission_at = now(),
                updated_at = now()
            where case_reference = %(case_reference)s
            returning id
        )
        insert into customer_case_updates (
            update_reference, customer_case_id, update_type,
            additional_information, processing_status
        )
        select %(update_reference)s, id, 'duplicate_submission_attempt', '', 'recorded'
        from matched_case;
    """
    update_reference = f"DUPLICATE-{uuid4().hex[:12].upper()}"
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, {
                "case_reference": case_reference,
                "update_reference": update_reference,
            })
        connection.commit()


def create_customer_case_update(case_update):
    """Verify an existing case and persist one update with evidence inventory."""
    case_query = """
        select id, case_reference, tracking_number, carrier, country,
               delivery_date, declared_value, complaint_type,
               additional_information, reported_at, status, claimant_role
        from customer_cases
        where upper(case_reference) = upper(%s)
          and upper(tracking_number) = upper(%s)
          and status not in ('closed', 'cancelled')
        limit 1;
    """
    update_query = """
        insert into customer_case_updates (
            update_reference, customer_case_id, update_type,
            additional_information, processing_status
        ) values (%s, %s, 'additional_information', %s, 'pending')
        returning id;
    """
    evidence_query = """
        insert into customer_case_evidence (
            customer_case_id, customer_case_update_id,
            original_file_name, content_type, size_bytes
        ) values (%s, %s, %s, %s, %s)
        returning id;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                case_query,
                (case_update["case_reference"], case_update["tracking_number"]),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            customer_case_id = row[0]
            cursor.execute(
                update_query,
                (
                    case_update["update_reference"],
                    customer_case_id,
                    case_update["additional_information"],
                ),
            )
            case_update_id = cursor.fetchone()[0]
            for evidence in case_update["evidence"]:
                cursor.execute(
                    evidence_query,
                    (
                        customer_case_id,
                        case_update_id,
                        evidence["file_name"],
                        evidence.get("content_type"),
                        evidence["size_bytes"],
                    ),
                )
                evidence["evidence_id"] = cursor.fetchone()[0]
        connection.commit()

    case_update.update({
        "customer_case_id": customer_case_id,
        "customer_case_update_id": case_update_id,
        "case_reference": row[1],
        "tracking_number": row[2],
        "carrier": row[3],
        "country": row[4],
        "delivery_date": row[5].isoformat() if row[5] else None,
        "declared_value": row[6],
        "complaint_type": row[7],
        "original_additional_information": row[8],
        "reported_at": row[9].isoformat(),
        "status": row[10],
        "claimant_role": row[11],
    })
    return case_update_id


def update_customer_case_update_status(
    update_reference, status, processing_error=None
):
    """Persist preparation status for one customer-supplied case update."""
    query = """
        update customer_case_updates
        set processing_status = %s,
            processing_error = %s,
            updated_at = now()
        where update_reference = %s;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (status, processing_error, update_reference))
        connection.commit()


def update_customer_case_status(case_reference, status, processing_error=None):
    """Update the durable lifecycle state for a customer case."""
    query = """
        update customer_cases
        set downstream_processing_status = %s,
            processing_error = %s,
            ready_for_review_at = case
                when %s in ('ready_for_handoff', 'handoff_accepted')
                then coalesce(ready_for_review_at, now())
                else ready_for_review_at
            end,
            handoff_accepted_at = case
                when %s = 'handoff_accepted' then coalesce(handoff_accepted_at, now())
                else handoff_accepted_at
            end,
            updated_at = now()
        where case_reference = %s;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (status, processing_error, status, status, case_reference),
            )
        connection.commit()


def record_processing_event(
    *, case_reference, stage, duration_ms, status,
    evidence_id=None, error_category=None
):
    """Persist one privacy-safe technical performance measurement."""
    query = """
        insert into case_processing_events (
            customer_case_id, evidence_id, stage, duration_ms, status,
            error_category
        )
        select id, %s, %s, %s, %s, %s
        from customer_cases
        where case_reference = %s;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    evidence_id, stage, duration_ms, status,
                    error_category, case_reference,
                ),
            )
        connection.commit()


def save_customer_case_analysis(case_reference, analysis, status):
    """Store the grounded preparation result separately from source evidence."""
    query = """
        update customer_cases
        set analysis_status = %s,
            case_analysis = %s,
            updated_at = now()
        where case_reference = %s;
    """
    analysis_json = (
        psycopg.types.json.Jsonb(analysis) if analysis is not None else None
    )
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (status, analysis_json, case_reference))
        connection.commit()


def update_customer_evidence(evidence):
    """Persist storage and routing fields for one evidence item."""
    query = """
        update customer_case_evidence
        set evidence_kind = %(evidence_kind)s,
            s3_object_key = %(s3_object_key)s,
            upload_status = %(upload_status)s,
            processing_status = %(processing_status)s,
            document_id = %(document_id)s,
            processing_error = %(processing_error)s,
            updated_at = now()
        where id = %(evidence_id)s;
    """
    params = {
        "evidence_id": evidence["evidence_id"],
        "evidence_kind": evidence.get("evidence_kind"),
        "s3_object_key": evidence.get("s3_object_key"),
        "upload_status": evidence.get("upload_status", "pending"),
        "processing_status": evidence.get("processing_status", "pending"),
        "document_id": evidence.get("document_id"),
        "processing_error": evidence.get("processing_error"),
    }
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
        connection.commit()


def get_customer_case_for_handoff(case_reference):
    """Load one persisted case and its successfully prepared evidence."""
    case_query = """
        select case_reference, reported_at, status, claimant_role,
               tracking_number, carrier, country, delivery_date, declared_value,
               complaint_type, customer_email,
               additional_information, downstream_processing_status,
               analysis_status, case_analysis
        from customer_cases
        where case_reference = %s;
    """
    evidence_query = """
        select id, original_file_name, content_type, size_bytes, evidence_kind,
               s3_object_key, upload_status, processing_status, document_id
        from customer_case_evidence
        where customer_case_id = (
            select id from customer_cases where case_reference = %s
        )
        order by id;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(case_query, (case_reference,))
            case_row = cursor.fetchone()
            if case_row is None:
                raise RuntimeError("The customer case could not be found.")
            case_columns = [description.name for description in cursor.description]
            customer_case = dict(zip(case_columns, case_row))

            cursor.execute(evidence_query, (case_reference,))
            evidence_columns = [description.name for description in cursor.description]
            customer_case["evidence"] = [
                dict(zip(evidence_columns, row)) for row in cursor.fetchall()
            ]

    customer_case["reported_at"] = customer_case["reported_at"].isoformat()
    if customer_case.get("delivery_date"):
        customer_case["delivery_date"] = customer_case["delivery_date"].isoformat()
    return customer_case


def save_customer_workflow_result(
    *, case_reference, event_id, event_type, event_version,
    handoff_status, jira_result=None, error_message=None
):
    """Persist a customer case's Make/Jira handoff result idempotently."""
    jira_result = jira_result or {}
    query = """
        insert into workflow_results (
            customer_case_id, event_id, event_type, event_version,
            handoff_status, jira_issue_key, jira_title, jira_routing,
            jira_status, jira_url, error_message
        )
        select
            id, %(event_id)s, %(event_type)s, %(event_version)s,
            %(handoff_status)s, %(jira_issue_key)s, %(jira_title)s,
            %(jira_routing)s, %(jira_status)s, %(jira_url)s,
            %(error_message)s
        from customer_cases
        where case_reference = %(case_reference)s
        on conflict (event_id) do update set
            handoff_status = excluded.handoff_status,
            jira_issue_key = excluded.jira_issue_key,
            jira_title = excluded.jira_title,
            jira_routing = excluded.jira_routing,
            jira_status = excluded.jira_status,
            jira_url = excluded.jira_url,
            error_message = excluded.error_message,
            updated_at = now();
    """
    params = {
        "case_reference": case_reference,
        "event_id": event_id,
        "event_type": event_type,
        "event_version": event_version,
        "handoff_status": handoff_status,
        "jira_issue_key": jira_result.get("issue_key"),
        "jira_title": jira_result.get("title"),
        "jira_routing": jira_result.get("routing"),
        "jira_status": jira_result.get("status"),
        "jira_url": jira_result.get("jira_url"),
        "error_message": error_message,
    }
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
        connection.commit()


def get_latest_customer_jira_result(case_reference):
    """Return the latest accepted Jira target for an existing customer case."""
    query = """
        select wr.jira_issue_key, wr.jira_title, wr.jira_routing,
               wr.jira_status, wr.jira_url
        from workflow_results wr
        join customer_cases cc on cc.id = wr.customer_case_id
        where cc.case_reference = %s
          and wr.handoff_status = 'accepted'
          and wr.jira_issue_key is not null
        order by wr.updated_at desc
        limit 1;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (case_reference,))
            row = cursor.fetchone()
    if row is None:
        return None
    return {
        "issue_key": row[0],
        "title": row[1],
        "routing": row[2],
        "status": row[3],
        "jira_url": row[4],
    }

def upsert_document(
    *,
    document_hash,
    original_file_name,
    s3_object_key,
    content_type=None,
    size_bytes=None,
    document_kind=None,
    extraction_method=None,
    used_vision=False,
    page_count=None,
    extracted_word_count=None,
    extracted_character_count=None,
    extracted_text=None,
    processing_status="uploaded",
    processing_error=None,
):
    
    """Insert a document record, or update it if the document hash already exists."""

    database_url = get_database_url()

    query = """
        insert into documents (
            document_hash,
            original_file_name,
            s3_object_key,
            content_type,
            size_bytes,
            document_kind,
            extraction_method,
            used_vision,
            page_count,
            extracted_word_count,
            extracted_character_count,
            extracted_text,
            processing_status,
            processing_error,
            processed_at
        )
        values (
            %(document_hash)s,
            %(original_file_name)s,
            %(s3_object_key)s,
            %(content_type)s,
            %(size_bytes)s,
            %(document_kind)s,
            %(extraction_method)s,
            %(used_vision)s,
            %(page_count)s,
            %(extracted_word_count)s,
            %(extracted_character_count)s,
            %(extracted_text)s,
            %(processing_status)s,
            %(processing_error)s,
            case
                when %(processing_status)s = 'processed'
                then now()
                else null
            end
        )
        on conflict (document_hash)
        do update set
            original_file_name = excluded.original_file_name,
            s3_object_key = excluded.s3_object_key,
            content_type = excluded.content_type,
            size_bytes = excluded.size_bytes,
            document_kind = excluded.document_kind,
            extraction_method = excluded.extraction_method,
            used_vision = excluded.used_vision,
            page_count = excluded.page_count,
            extracted_word_count = excluded.extracted_word_count,
            extracted_character_count = excluded.extracted_character_count,
            extracted_text = excluded.extracted_text,
            processing_status = excluded.processing_status,
            processing_error = excluded.processing_error,
            processed_at = excluded.processed_at
        returning id;
    """

    params = {
        "document_hash": document_hash,
        "original_file_name": original_file_name,
        "s3_object_key": s3_object_key,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "document_kind": document_kind,
        "extraction_method": extraction_method,
        "used_vision": used_vision,
        "page_count": page_count,
        "extracted_word_count": extracted_word_count,
        "extracted_character_count": extracted_character_count,
        "extracted_text": extracted_text,
        "processing_status": processing_status,
        "processing_error": processing_error,
    }

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            document_id = cursor.fetchone()[0]
            connection.commit()

    return document_id


def save_document_chunks(
    *,
    document_id,
    chunks,
    embeddings,
    embedding_model,
):
    """Persist document chunks and their embeddings in PostgreSQL."""

    if len(chunks) != len(embeddings):
        raise ValueError(
            "The number of chunks must match the number of embeddings."
        )

    database_url = get_database_url()

    query = """
        insert into document_chunks (
            document_id,
            chunk_index,
            chunk_text,
            character_count,
            embedding,
            embedding_model
        )
        values (
            %(document_id)s,
            %(chunk_index)s,
            %(chunk_text)s,
            %(character_count)s,
            %(embedding)s,
            %(embedding_model)s
        )
        on conflict (document_id, chunk_index)
        do update set
            chunk_text = excluded.chunk_text,
            character_count = excluded.character_count,
            embedding = excluded.embedding,
            embedding_model = excluded.embedding_model;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            for chunk_index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings)
            ):
                cursor.execute(
                    query,
                    {
                        "document_id": document_id,
                        "chunk_index": chunk_index,
                        "chunk_text": chunk,
                        "character_count": len(chunk),
                        "embedding": Vector(embedding),
                        "embedding_model": embedding_model,
                    },
                )

        connection.commit()


def document_has_embeddings(document_id):
    """Return whether PostgreSQL has an embedded chunk for this document."""

    database_url = get_database_url()

    query = """
        select exists (
            select 1
            from document_chunks
            where document_id = %s
              and embedding is not null
        );
    """

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (document_id,))
            return cursor.fetchone()[0]


def search_document_chunks(
    *,
    document_id,
    query_embedding,
    limit=4,
):
    """Return the document chunks most semantically similar to a query."""

    database_url = get_database_url()

    query = """
        select
            chunk_index,
            chunk_text,
            1 - (embedding <=> %(query_embedding)s) as similarity
        from document_chunks
        where document_id = %(document_id)s
          and embedding is not null
        order by embedding <=> %(query_embedding)s
        limit %(limit)s;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "document_id": document_id,
                    "query_embedding": Vector(query_embedding),
                    "limit": limit,
                },
            )

            rows = cursor.fetchall()

    return rows

def save_policy_chunks(
    *,
    policy_id,
    chunks,
    embeddings,
    embedding_model,
):
    """Persist policy chunks and their embeddings in PostgreSQL."""

    if len(chunks) != len(embeddings):
        raise ValueError(
            "The number of chunks must match the number of embeddings."
        )

    database_url = get_database_url()

    query = """
        insert into policy_chunks (
            policy_id,
            chunk_index,
            chunk_text,
            character_count,
            embedding,
            embedding_model
        )
        values (
            %(policy_id)s,
            %(chunk_index)s,
            %(chunk_text)s,
            %(character_count)s,
            %(embedding)s,
            %(embedding_model)s
        )
        on conflict (policy_id, chunk_index)
        do update set
            chunk_text = excluded.chunk_text,
            character_count = excluded.character_count,
            embedding = excluded.embedding,
            embedding_model = excluded.embedding_model;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            for chunk_index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings)
            ):
                cursor.execute(
                    query,
                    {
                        "policy_id": policy_id,
                        "chunk_index": chunk_index,
                        "chunk_text": chunk,
                        "character_count": len(chunk),
                        "embedding": Vector(embedding),
                        "embedding_model": embedding_model,
                    },
                )

        connection.commit()


def policy_has_embeddings(policy_id):
    """Return whether PostgreSQL has an embedded chunk for this policy."""

    database_url = get_database_url()

    query = """
        select exists (
            select 1
            from policy_chunks
            where policy_id = %s
              and embedding is not null
        );
    """

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (policy_id,))
            return cursor.fetchone()[0]


def search_policy_chunks(
    *,
    policy_id,
    query_embedding,
    limit=4,
):
    """Return the policy chunks most semantically similar to a query."""

    database_url = get_database_url()

    query = """
        select
            chunk_index,
            chunk_text,
            1 - (embedding <=> %(query_embedding)s) as similarity
        from policy_chunks
        where policy_id = %(policy_id)s
          and embedding is not null
        order by embedding <=> %(query_embedding)s
        limit %(limit)s;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "policy_id": policy_id,
                    "query_embedding": Vector(query_embedding),
                    "limit": limit,
                },
            )

            rows = cursor.fetchall()

    return rows

def find_carrier_policies(
    *,
    carrier,
    country,
    incident_type,
):
    """Return structured carrier policies matching explicit incident facts."""

    database_url = get_database_url()

    query = """
        select
            cp.id,
            cp.policy_id,
            cp.title,
            c.name as carrier,
            cp.countries,
            cp.incident_type,
            cp.effective_date,
            cp.deadline_days,
            cp.deadline_basis,
            cp.additional_timing_rules,
            cp.required_evidence,
            cp.handling_guidance,
            cp.policy_text,
            cp.fictional_evaluation_policy
        from carrier_policies cp
        join carriers c
            on c.id = cp.carrier_id
        where lower(c.name) = lower(%(carrier)s)
          and cp.countries ? %(country)s
          and lower(cp.incident_type) = lower(%(incident_type)s)
          and c.active = true
        order by cp.id;
    """

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "carrier": carrier,
                    "country": country,
                    "incident_type": incident_type,
                },
            )

            rows = cursor.fetchall()

    return rows
