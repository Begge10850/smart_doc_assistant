import os

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
            complaint_type, customer_email, additional_information,
            downstream_processing_status
        ) values (
            %(case_reference)s, %(reported_at)s, %(status)s, %(claimant_role)s,
            %(tracking_number)s, %(complaint_type)s, %(customer_email)s,
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


def update_customer_case_status(case_reference, status, processing_error=None):
    """Update the durable lifecycle state for a customer case."""
    query = """
        update customer_cases
        set downstream_processing_status = %s,
            processing_error = %s,
            updated_at = now()
        where case_reference = %s;
    """
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (status, processing_error, case_reference))
        connection.commit()


def update_customer_evidence(evidence):
    """Persist storage, routing, and AI-observation fields for one evidence item."""
    query = """
        update customer_case_evidence
        set evidence_kind = %(evidence_kind)s,
            s3_object_key = %(s3_object_key)s,
            upload_status = %(upload_status)s,
            processing_status = %(processing_status)s,
            document_id = %(document_id)s,
            vision_observations = %(vision_observations)s,
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
        "vision_observations": psycopg.types.json.Jsonb(
            evidence.get("vision_observations")
        ) if evidence.get("vision_observations") is not None else None,
        "processing_error": evidence.get("processing_error"),
    }
    with psycopg.connect(get_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
        connection.commit()

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
