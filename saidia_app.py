import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import time

import streamlit as st

from agent_engine import (
    DocumentAgentError,
    NonIncidentDocumentError,
    prepare_incident_case,
    run_document_agent,
)
from case_handoff import (
    CaseHandoffError,
    customer_case_handoff_enabled,
    send_case_to_make,
    send_customer_case_to_make,
)
from customer_intake import (
    COMPLAINT_TYPE_LABELS,
    CONFIGURED_CARRIER,
    IMAGE_EVIDENCE_TYPES,
    SUPPORTED_COUNTRIES,
    SUPPORTED_EVIDENCE_TYPES,
    build_customer_complaint,
    validate_customer_submission,
)
from rag_pipeline import process_document
from s3_upload import (
    S3UploadError,
    create_private_evidence_download_url,
    upload_evidence_to_s3,
    upload_to_s3,
)
from vector_store import chunk_text, embed_chunks
from database import (
    create_customer_case,
    document_has_embeddings,
    get_customer_case_for_handoff,
    record_processing_event,
    save_document_chunks,
    save_customer_case_analysis,
    search_document_chunks,
    update_customer_case_status,
    update_customer_evidence,
    upsert_document,
)


# Fix asyncio initialization for environments that do not provide a loop.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


DOCUMENT_STATE_KEYS = [
    "processing_requested",
    "uploaded_file_name",
    "uploaded_file_data",
    "processed_doc_hash",
    "processed_file_name",
    "s3_object_key",
    "document_id",
    "extracted_text",
    "document_metadata",
    "chunks",
    "chat_messages",
    "incident_case",
    "case_handoff_receipt",
    "incident_workflow_error",
    "non_incident_message",
    "processing_timings",
]

def run_customer_stage(
    case_reference, stage, function, *args, evidence_id=None, function_kwargs=None
):
    """Run one case stage and persist latency without logging customer content."""
    started_at = time.perf_counter()
    try:
        result = function(*args, **(function_kwargs or {}))
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        try:
            record_processing_event(
                case_reference=case_reference,
                evidence_id=evidence_id,
                stage=stage,
                duration_ms=duration_ms,
                status="failed",
                error_category=type(exc).__name__,
            )
        except Exception:
            pass
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    record_processing_event(
        case_reference=case_reference,
        evidence_id=evidence_id,
        stage=stage,
        duration_ms=duration_ms,
        status="completed",
    )
    return result


def process_customer_evidence(complaint):
    """Store images for people and route text documents to the RAG index."""
    for item in complaint["evidence"]:
        file_data = item.pop("data")
        extension = Path(item["file_name"]).suffix.lower().lstrip(".")
        item["evidence_kind"] = "image" if extension in IMAGE_EVIDENCE_TYPES else "document"
        item["s3_object_key"] = run_customer_stage(
            complaint["case_reference"],
            "s3_upload",
            upload_evidence_to_s3,
            file_data,
            item["file_name"],
            complaint["case_reference"],
            evidence_id=item["evidence_id"],
        )
        item["upload_status"] = "stored_private"

        if item["evidence_kind"] == "image":
            item["processing_status"] = "ready_for_human_review"
            update_customer_evidence(item)
            continue

        result = run_customer_stage(
            complaint["case_reference"],
            "document_extraction",
            process_uploaded_bytes,
            file_data,
            item["file_name"],
            evidence_id=item["evidence_id"],
        )
        extracted_text = result["text"]
        metadata = result["metadata"]
        chunks = run_customer_stage(
            complaint["case_reference"],
            "document_chunking",
            chunk_text,
            extracted_text,
            evidence_id=item["evidence_id"],
        )
        if not chunks:
            raise RuntimeError("The evidence document produced no searchable text.")
        document_hash = hashlib.sha256(file_data).hexdigest()
        document_id = run_customer_stage(
            complaint["case_reference"],
            "document_persistence",
            upsert_document,
            evidence_id=item["evidence_id"],
            function_kwargs={
                "document_hash": document_hash,
                "original_file_name": item["file_name"],
                "s3_object_key": item["s3_object_key"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "document_kind": metadata.get("document_kind"),
                "extraction_method": metadata.get("extraction_method"),
                "used_vision": metadata.get("used_vision", False),
                "page_count": metadata.get("page_count"),
                "extracted_word_count": metadata.get("extracted_word_count"),
                "extracted_character_count": metadata.get(
                    "extracted_character_count"
                ),
                "extracted_text": extracted_text,
                "processing_status": "processed",
            },
        )
        embeddings = run_customer_stage(
            complaint["case_reference"],
            "embedding",
            embed_chunks,
            chunks,
            evidence_id=item["evidence_id"],
        )
        run_customer_stage(
            complaint["case_reference"],
            "vector_persistence",
            save_document_chunks,
            evidence_id=item["evidence_id"],
            function_kwargs={
                "document_id": document_id,
                "chunks": chunks,
                "embeddings": embeddings,
                "embedding_model": "sentence-transformers/all-mpnet-base-v2",
            },
        )
        item.update({
            "document_id": document_id,
            "chunk_count": len(chunks),
            "extraction_metadata": metadata,
            "processing_status": "indexed",
        })
        update_customer_evidence(item)

    complaint["downstream_processing_status"] = "evidence_processed"
    update_customer_case_status(
        complaint["case_reference"], "evidence_processed"
    )
    return complaint


def retrieve_customer_evidence_context(complaint):
    """Retrieve focused document passages for grounded case preparation."""
    searchable_items = [
        item for item in complaint["evidence"] if item.get("document_id")
    ]
    if not searchable_items:
        return []
    queries = [
        "shipment tracking number carrier country delivery date",
        "invoice purchase price declared value proof of value",
        "description of parcel damage loss delay or non-delivery",
    ]
    query_embeddings = embed_chunks(queries)
    excerpts = []
    for item in searchable_items:
        document_id = item["document_id"]
        seen_chunks = set()
        for query, query_embedding in zip(queries, query_embeddings):
            rows = search_document_chunks(
                document_id=document_id,
                query_embedding=query_embedding,
                limit=2,
            )
            for chunk_index, chunk_text_value, similarity in rows:
                if chunk_index in seen_chunks:
                    continue
                seen_chunks.add(chunk_index)
                excerpts.append(
                    f"[Evidence: {item['file_name']}; chunk {chunk_index}; "
                    f"similarity {float(similarity):.3f}]\n{chunk_text_value}"
                )
    return excerpts


def prepare_customer_case_analysis(complaint):
    """Prepare a grounded case using structured intake and retrieved evidence."""
    evidence_labels = []
    for item in complaint["evidence"]:
        if item.get("evidence_kind") == "image":
            evidence_labels.append(
                f"Customer-uploaded photograph: {item['file_name']} "
                "(stored for human review; not automatically interpreted)"
            )
        else:
            evidence_labels.append(f"Searchable document: {item['file_name']}")

    retrieved_excerpts = retrieve_customer_evidence_context(complaint)
    source_text = "\n".join([
        "Customer logistics complaint",
        f"Incident ID: {complaint['case_reference']}",
        f"Tracking number: {complaint['tracking_number']}",
        f"Carrier: {complaint['carrier']}",
        f"Country: {complaint['country']}",
        f"Delivery date: {complaint['delivery_date'] or 'Not applicable or unknown'}",
        f"Declared value: {complaint['declared_value'] or 'Not supplied'}",
        f"Incident type: {complaint['complaint_type']}",
        f"Reported date: {complaint['reported_at'][:10]}",
        f"Claimant role: {complaint['claimant_role']}",
        f"Customer statement: {complaint['additional_information'] or 'Not supplied'}",
        "Evidence available:",
        *(f"- {label}" for label in evidence_labels),
        "Retrieved evidence passages:",
        *(retrieved_excerpts or ["- No searchable document passages were available."]),
    ])
    analysis = prepare_incident_case(
        source_text,
        source_file=f"{complaint['case_reference']}-customer-submission",
        source_document_hash=hashlib.sha256(
            complaint["case_reference"].encode("utf-8")
        ).hexdigest(),
    )
    return analysis.to_dict()


def clear_document_session():
    """Forget the processed document and its conversation."""
    for key in DOCUMENT_STATE_KEYS:
        st.session_state.pop(key, None)


def render_agent_trace(tool_trace):
    """Show which read-only tools the document agent selected."""
    if not tool_trace:
        return

    with st.expander("🧰 Agent activity", expanded=False):
        for step_number, step in enumerate(tool_trace, start=1):
            st.markdown(
                f"**{step_number}. `{step['tool']}`** — {step['summary']}"
            )


def process_uploaded_bytes(file_data, file_name):
    """Process the original upload bytes without downloading the S3 copy."""
    with tempfile.TemporaryDirectory(prefix="saidia-processing-") as temp_dir:
        local_path = Path(temp_dir) / Path(file_name).name
        local_path.write_bytes(file_data)
        return process_document(str(local_path))


def timed_call(function, *args):
    """Return a function result together with its elapsed wall-clock seconds."""
    started_at = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - started_at


def render_incident_case(incident_case):
    """Lead with the workflow result and keep detailed analysis available."""

    handoff_receipt = st.session_state.get("case_handoff_receipt")
    if handoff_receipt and handoff_receipt.get("case_id") == incident_case.case_id:
        st.success("The incident was routed to the operational workflow.")
        jira_result = handoff_receipt.get("jira_result", {})
        if jira_result:
            st.markdown("**Jira ticket**")
            st.table([
                {"Field": label, "Value": jira_result.get(field, "Not returned")}
                for field, label in (
                    ("issue_key", "Issue key"),
                    ("title", "Title"),
                    ("routing", "Routing"),
                    ("status", "Status"),
                )
            ])
            st.markdown(
                "**Recommended action:** "
                + jira_result.get(
                    "recommended_action",
                    incident_case.recommended_next_action,
                )
            )
            if jira_result.get("jira_url"):
                st.link_button("Open Jira ticket", jira_result["jira_url"])
        else:
            st.info(
                "The case was handed off successfully, but Make did not return "
                "ticket details."
            )
        st.caption(
            f"Event ID: `{handoff_receipt['event_id']}` · "
            f"HTTP {handoff_receipt['http_status']}"
        )
    else:
        st.warning(
            "The document was analyzed, but the Make handoff did not complete."
        )
        if st.session_state.get("incident_workflow_error"):
            st.caption(st.session_state.incident_workflow_error)
        if st.button(
            "Retry Make Handoff",
            key=f"retry_handoff_{incident_case.case_id}",
            use_container_width=True,
        ):
            with st.spinner("Retrying the operational handoff..."):
                try:
                    st.session_state.case_handoff_receipt = send_case_to_make(
                        incident_case
                    )
                    st.session_state.incident_workflow_error = None
                    st.rerun()
                except CaseHandoffError as exc:
                    st.session_state.incident_workflow_error = str(exc)
                    st.error(f"The case was not handed off: {exc}")

    with st.expander("View case analysis", expanded=False):
        fact_rows = [
            {"Field": "Case ID", "Value": incident_case.case_id},
            {"Field": "Incident ID", "Value": incident_case.incident_id or "Unresolved"},
            {"Field": "Tracking number", "Value": incident_case.tracking_number or "Unresolved"},
            {"Field": "Carrier", "Value": incident_case.carrier or "Unresolved"},
            {"Field": "Country", "Value": incident_case.country or "Unresolved"},
            {"Field": "Incident type", "Value": incident_case.incident_type or "Unresolved"},
            {"Field": "Delivery date", "Value": incident_case.delivery_date or "Unresolved"},
            {"Field": "Reported date", "Value": incident_case.reported_date or "Unresolved"},
            {"Field": "Declared value", "Value": incident_case.declared_value or "Unresolved"},
        ]
        st.table(fact_rows)
        st.markdown(f"**Factual summary:** {incident_case.factual_summary}")

        if incident_case.policy_match_status == "matched":
            st.markdown(
                f"**Matched evaluation policy:** {incident_case.policy_title} "
                f"(`{incident_case.policy_id}`)"
            )
            if incident_case.policy_is_fictional:
                st.caption(
                    "This is a fictional project evaluation policy, not verified "
                    "real-world carrier terms."
                )
        else:
            st.warning(
                "No single matching evaluation policy was found. The case must not "
                "be treated as policy-compliant."
            )

        timing_value = (
            "Yes"
            if incident_case.reported_on_time is True
            else "No"
            if incident_case.reported_on_time is False
            else "Not determined"
        )
        st.markdown(
            f"**Claim deadline:** {incident_case.claim_deadline or 'Not determined'}  \n"
            f"**Reported on time:** {timing_value}"
        )

        st.markdown("**Evidence explicitly listed as supplied:**")
        if incident_case.evidence_supplied:
            for item in incident_case.evidence_supplied:
                st.markdown(f"- {item}")
        else:
            st.markdown("- None identified")

        st.markdown("**Missing required policy evidence:**")
        if incident_case.missing_required_evidence:
            for item in incident_case.missing_required_evidence:
                st.markdown(f"- {item}")
        else:
            st.markdown("- None identified from the matched evaluation policy")

        if incident_case.unresolved_fields:
            st.markdown(
                "**Unresolved document fields:** "
                + ", ".join(incident_case.unresolved_fields)
            )
        st.markdown(
            f"**Recommended next action:** {incident_case.recommended_next_action}"
        )
        case_json = json.dumps(incident_case.to_dict(), indent=2)
        st.download_button(
            "Download structured case JSON",
            data=case_json,
            file_name=f"{incident_case.case_id}.json",
            mime="application/json",
            key=f"download_{incident_case.case_id}",
        )


st.set_page_config(page_title="Saidia Claims Assistant", layout="wide")
st.title("Saidia")
st.markdown(
    "Report a delivery problem and provide the information needed for your "
    "case to be reviewed."
)

st.subheader("Report a Delivery Problem")
st.caption(
    f"Fictional evaluation environment for {CONFIGURED_CARRIER}. "
    "Policies are project examples, not real carrier terms."
)

with st.form("customer_complaint_form", clear_on_submit=False):
    claimant_role = st.radio(
        "Are you the sender or recipient?",
        options=["Recipient", "Sender"],
        horizontal=True,
    )
    tracking_number = st.text_input(
        "Tracking number",
        placeholder="Enter your parcel tracking number",
    )
    country = st.selectbox(
        "Destination country",
        options=SUPPORTED_COUNTRIES,
        index=None,
        placeholder="Select a country",
    )
    incident_type = st.selectbox(
        "What happened?",
        options=list(COMPLAINT_TYPE_LABELS),
        index=None,
        placeholder="Select a problem",
        format_func=lambda value: COMPLAINT_TYPE_LABELS[value],
    )
    customer_email = st.text_input(
        "Email",
        placeholder="Enter the email address we should use for this case",
    )
    delivery_date = st.date_input(
        "Delivery or expected delivery date",
        value=None,
        help="Use the expected delivery date when the parcel was never delivered.",
    )
    declared_value = st.text_input(
        "Declared or purchase value (optional)",
        placeholder="For example, EUR 899.00",
    )
    evidence_files = st.file_uploader(
        "Supporting evidence",
        type=SUPPORTED_EVIDENCE_TYPES,
        accept_multiple_files=True,
        help=(
            "Upload up to 10 files (50 MB combined). Images: 10 MB each. "
            "Documents: 20 MB each. Damage and missing-item complaints require "
            "at least one JPG or PNG photo."
        ),
        key="customer_evidence_files",
    )
    additional_information = st.text_area(
        "Additional information",
        placeholder=(
            "Describe what happened and include any details that may help us "
            "review the delivery problem"
        ),
        height=160,
    )
    complaint_submitted = st.form_submit_button(
        "Submit complaint", use_container_width=True
    )

if complaint_submitted:
    validation_errors = validate_customer_submission(
        tracking_number,
        country,
        delivery_date,
        incident_type,
        customer_email,
        evidence_files,
    )
    if validation_errors:
        for validation_error in validation_errors:
            st.error(validation_error)
    else:
        complaint = build_customer_complaint(
            claimant_role,
            tracking_number,
            country,
            delivery_date,
            declared_value,
            incident_type,
            customer_email,
            additional_information,
            evidence_files,
        )
        try:
            with st.spinner("Securely storing and preparing your evidence..."):
                submission_processing_started = time.perf_counter()
                case_creation_started = time.perf_counter()
                create_customer_case(complaint)
                record_processing_event(
                    case_reference=complaint["case_reference"],
                    stage="case_creation",
                    duration_ms=round(
                        (time.perf_counter() - case_creation_started) * 1000, 2
                    ),
                    status="completed",
                )
                complaint["downstream_processing_status"] = "processing_evidence"
                update_customer_case_status(
                    complaint["case_reference"], "processing_evidence"
                )
                st.session_state.customer_complaint = process_customer_evidence(
                    complaint
                )
                try:
                    analysis = run_customer_stage(
                        complaint["case_reference"],
                        "grounded_case_analysis",
                        prepare_customer_case_analysis,
                        complaint,
                    )
                    complaint["case_analysis"] = analysis
                    complaint["analysis_status"] = "completed"
                    save_customer_case_analysis(
                        complaint["case_reference"], analysis, "completed"
                    )
                except (DocumentAgentError, NonIncidentDocumentError):
                    complaint["analysis_status"] = "needs_human_preparation"
                    save_customer_case_analysis(
                        complaint["case_reference"],
                        None,
                        "needs_human_preparation",
                    )
                if customer_case_handoff_enabled():
                    try:
                        persisted_case = get_customer_case_for_handoff(
                            complaint["case_reference"]
                        )
                        st.session_state.customer_case_handoff_receipt = run_customer_stage(
                            complaint["case_reference"],
                            "make_handoff",
                            send_customer_case_to_make,
                            persisted_case,
                            function_kwargs={
                                "download_url_factory": create_private_evidence_download_url,
                            },
                        )
                        complaint["downstream_processing_status"] = "handoff_accepted"
                        update_customer_case_status(
                            complaint["case_reference"], "handoff_accepted"
                        )
                    except Exception:
                        # Submission and evidence storage remain successful even
                        # when the operational handoff needs an internal retry.
                        complaint["downstream_processing_status"] = "handoff_failed"
                        update_customer_case_status(
                            complaint["case_reference"],
                            "handoff_failed",
                            "The Make handoff did not complete.",
                        )
                else:
                    complaint["downstream_processing_status"] = "ready_for_handoff"
                    update_customer_case_status(
                        complaint["case_reference"], "ready_for_handoff"
                    )
                record_processing_event(
                    case_reference=complaint["case_reference"],
                    stage="submission_to_ready",
                    duration_ms=round(
                        (time.perf_counter() - submission_processing_started) * 1000,
                        2,
                    ),
                    status="completed",
                )
        except Exception as exc:
            # Do not retain uploaded file bodies in Streamlit memory after the
            # persistence attempt, including when a later routing step fails.
            for evidence_item in complaint["evidence"]:
                evidence_item.pop("data", None)
            complaint["downstream_processing_status"] = "evidence_processing_failed"
            complaint["processing_error"] = str(exc)
            if complaint.get("customer_case_id"):
                try:
                    update_customer_case_status(
                        complaint["case_reference"],
                        "evidence_processing_failed",
                        "Evidence processing did not complete.",
                    )
                except Exception:
                    pass
            st.session_state.customer_complaint = complaint
            st.error(
                "We could not finish preparing your evidence. Your case reference "
                "has been retained; please retry later."
            )
            st.markdown(f"Case reference: **`{complaint['case_reference']}`**")

if (
    st.session_state.get("customer_complaint", {}).get(
        "downstream_processing_status"
    )
    in {
        "evidence_processed",
        "ready_for_handoff",
        "handoff_accepted",
        "handoff_failed",
    }
):
    submitted_complaint = st.session_state.customer_complaint
    st.success("Your complaint has been submitted successfully.")
    st.markdown(
        f"Your case reference is **`{submitted_complaint['case_reference']}`**. "
        "Keep it for future correspondence."
    )
    st.caption(
        "Your original evidence is stored securely and has been prepared for "
        "human review."
    )


# Keep the existing backend workflow intact but out of the customer experience
# until customer submissions are connected to downstream processing.
if not st.session_state.get("show_internal_operations", False):
    st.stop()

with st.expander("Existing document processing", expanded=False):
    st.markdown("**📤 Upload Document**")
    uploaded_file = st.file_uploader(
        "Choose a PDF, DOCX, TXT, JPG, JPEG, or PNG file",
        type=["pdf", "txt", "docx", "jpg", "jpeg", "png"],
        key="internal_document_upload",
    )

    st.caption(
        "Images and scanned PDFs are automatically processed with OpenAI "
        "Vision when native text is unavailable."
    )

    if uploaded_file and st.button("🚀 Process Document", key="process_btn"):
        file_data = uploaded_file.getvalue()
        document_hash = hashlib.sha256(file_data).hexdigest()

        if (
            document_hash == st.session_state.get("processed_doc_hash")
        ):
            st.info("This document is already processed for the current session.")
        else:
            clear_document_session()
            st.session_state.uploaded_file_name = Path(uploaded_file.name).name
            st.session_state.uploaded_file_data = file_data
            st.session_state.processing_requested = True
            st.rerun()

    st.button(
        "❌ Clear Document",
        on_click=clear_document_session,
        key="clear_btn",
    )


# This block runs only for a newly selected document. Once complete, later
# Streamlit reruns reuse the objects stored in session_state.
if st.session_state.get("processing_requested"):
    file_name = st.session_state.uploaded_file_name
    file_data = st.session_state.uploaded_file_data
    document_hash = hashlib.sha256(file_data).hexdigest()

    st.info(f"📁 Processing `{file_name}` for the first time in this session.")
    processing_started_at = time.perf_counter()
    processing_timings = {}

    try:
        with st.status("Preparing document...", expanded=True) as status:
            status.write(
                "Uploading to S3 while extracting from the original file..."
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                upload_future = executor.submit(
                    timed_call,
                    upload_to_s3,
                    file_data,
                    file_name,
                )
                extraction_future = executor.submit(
                    timed_call,
                    process_uploaded_bytes,
                    file_data,
                    file_name,
                )
                processing_result, extraction_seconds = extraction_future.result()
                s3_object_key, upload_seconds = upload_future.result()

            processing_timings["Text extraction"] = extraction_seconds
            processing_timings["S3 upload"] = upload_seconds
            extracted_text = processing_result["text"]
            document_metadata = processing_result["metadata"]
            status.write("Saving document metadata to PostgreSQL...")

            database_started_at = time.perf_counter()
            document_id = upsert_document(
                document_hash=document_hash,
                original_file_name=file_name,
                s3_object_key=s3_object_key,
                content_type=getattr(
                    st.session_state.get("uploaded_file"),
                    "type",
                    None,
                ),
                size_bytes=len(file_data),
                document_kind=document_metadata.get("extension"),
                extraction_method=document_metadata.get("extraction_method"),
                used_vision=document_metadata.get("used_vision", False),
                page_count=document_metadata.get("page_count"),
                extracted_word_count=document_metadata.get("extracted_word_count"),
                extracted_character_count=document_metadata.get(
                    "extracted_character_count"
                ),
                extracted_text=extracted_text,
                processing_status="processed",
            )
            processing_timings["PostgreSQL persistence"] = (
                time.perf_counter() - database_started_at
            )
            if not extracted_text.strip():
                raise RuntimeError("No text could be extracted from the document.")

            status.write("Preparing searchable document chunks...")
            chunking_started_at = time.perf_counter()
            chunks = chunk_text(extracted_text)
            if not chunks:
                raise RuntimeError("The extracted document produced no text chunks.")
            processing_timings["Text chunking"] = (
                time.perf_counter() - chunking_started_at
            )

            st.session_state.processed_doc_hash = document_hash
            st.session_state.processed_file_name = file_name
            st.session_state.s3_object_key = s3_object_key
            st.session_state.document_id = document_id
            st.session_state.extracted_text = extracted_text
            st.session_state.document_metadata = document_metadata
            st.session_state.chunks = chunks
            # Build embeddings lazily on the first chat question so the Jira
            # result is not delayed by semantic-search preparation.
            st.session_state.chat_messages = []

            status.write("Analyzing the incident and preparing its case record...")
            analysis_started_at = time.perf_counter()
            try:
                incident_case = prepare_incident_case(
                    extracted_text,
                    source_file=file_name,
                    source_document_hash=document_hash,
                )
                st.session_state.incident_case = incident_case
                processing_timings["Incident analysis"] = (
                    time.perf_counter() - analysis_started_at
                )

                status.write("Routing the processed case to Make...")
                handoff_started_at = time.perf_counter()
                st.session_state.case_handoff_receipt = send_case_to_make(
                    incident_case
                )
                processing_timings["Make/Jira handoff"] = (
                    time.perf_counter() - handoff_started_at
                )
                st.session_state.incident_workflow_error = None
                st.session_state.non_incident_message = None
            except NonIncidentDocumentError as exc:
                st.session_state.non_incident_message = str(exc)
                st.session_state.incident_workflow_error = None
            except (DocumentAgentError, CaseHandoffError) as exc:
                st.session_state.incident_workflow_error = str(exc)

            processing_timings["Total"] = (
                time.perf_counter() - processing_started_at
            )
            st.session_state.processing_timings = processing_timings
            st.session_state.processing_requested = False
            status.update(label="Document processing complete", state="complete")

        st.rerun()
    except S3UploadError as exc:
        st.session_state.processing_requested = False
        st.error(f"❌ Upload to S3 failed: {exc}")
        st.stop()
    except Exception as exc:
        st.session_state.processing_requested = False
        st.error(f"❌ Document processing failed: {exc}")
        st.stop()


if st.session_state.get("processed_doc_hash"):
    file_name = st.session_state.processed_file_name
    extracted_text = st.session_state.extracted_text
    document_metadata = st.session_state.document_metadata
    chunks = st.session_state.chunks
    chat_messages = st.session_state.setdefault("chat_messages", [])

    st.success(f"✅ `{file_name}` is processed and cached for this session.")
    col1, col2, col3 = st.columns(3)
    col1.metric("Extracted characters", f"{len(extracted_text):,}")
    col2.metric("Searchable chunks", len(chunks))
    col3.metric(
        "Extraction method",
        document_metadata["extraction_method"].replace("_", " ").title(),
    )

    if document_metadata.get("used_vision"):
        st.caption(
            "OpenAI Vision was selected automatically because this document "
            "did not contain enough usable native text."
        )

    processing_timings = st.session_state.get("processing_timings", {})
    if processing_timings:
        with st.expander("⏱️ Processing performance", expanded=False):
            st.table([
                {"Stage": stage, "Seconds": f"{seconds:.2f}"}
                for stage, seconds in processing_timings.items()
            ])

    with st.expander("🧠 Preview extracted text", expanded=False):
        st.text_area(
            "Document text",
            extracted_text,
            height=300,
            disabled=True,
            key=f"preview_{st.session_state.processed_doc_hash}",
        )

    st.subheader("🎫 Incident workflow result")
    st.caption(
        "Saidia analyzes the processed document automatically and routes the "
        "operational case to Jira through Make."
    )
    incident_case = st.session_state.get("incident_case")
    if incident_case is None:
        non_incident_message = st.session_state.get("non_incident_message")
        if non_incident_message:
            st.info("No supported logistics incident was detected.")
            st.caption(non_incident_message)
            st.caption(
                "The document is still available for preview and chat. No case "
                "was sent to Make, Google Sheets, or Jira."
            )
        else:
            st.warning(
                "Document extraction completed, but incident analysis did not finish."
            )
            if st.session_state.get("incident_workflow_error"):
                st.caption(st.session_state.incident_workflow_error)
        if not non_incident_message and st.button(
            "Retry Incident Workflow",
            key="prepare_case_btn",
        ):
            with st.spinner("Retrying incident analysis and handoff..."):
                try:
                    incident_case = prepare_incident_case(
                        extracted_text,
                        source_file=file_name,
                        source_document_hash=st.session_state.processed_doc_hash,
                    )
                    st.session_state.incident_case = incident_case
                    st.session_state.case_handoff_receipt = send_case_to_make(
                        incident_case
                    )
                    st.session_state.incident_workflow_error = None
                    st.session_state.non_incident_message = None
                    st.rerun()
                except NonIncidentDocumentError as exc:
                    st.session_state.non_incident_message = str(exc)
                    st.session_state.incident_workflow_error = None
                    st.rerun()
                except (DocumentAgentError, CaseHandoffError) as exc:
                    st.session_state.incident_workflow_error = str(exc)
                    st.error(f"The incident workflow could not complete: {exc}")
    else:
        render_incident_case(incident_case)

    st.subheader("💬 Chat with this document")
    if not chat_messages:
        st.caption(
            "The document will not be uploaded or embedded again while this session remains active."
        )

    for message in chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            render_agent_trace(message.get("tool_trace"))

    question = st.chat_input("Ask a question about the document")
    if question:
        prior_history = list(chat_messages)
        chat_messages.append({"role": "user", "content": question})

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("The document agent is deciding what to inspect..."):
                try:
                    if not document_has_embeddings(st.session_state.document_id):
                        index_started_at = time.perf_counter()

                        embeddings = embed_chunks(chunks)

                        save_document_chunks(
                            document_id=st.session_state.document_id,
                            chunks=chunks,
                            embeddings=embeddings,
                            embedding_model="sentence-transformers/all-mpnet-base-v2",
                        )

                        st.session_state.setdefault("processing_timings", {})[
                            "Semantic index (first chat)"
                        ] = time.perf_counter() - index_started_at
                    agent_result = run_document_agent(
                        question,
                        file_name=file_name,
                        document_metadata=document_metadata,
                        document_id=st.session_state.document_id,
                        chunks=chunks,
                        chat_history=prior_history,
                    )
                    answer = agent_result["answer"]
                    tool_trace = agent_result["tool_trace"]
                except DocumentAgentError as exc:
                    answer = f"I could not answer that question: {exc}"
                    tool_trace = []
                except Exception:
                    answer = (
                        "I could not prepare semantic search for this document. "
                        "Please try the question again."
                    )
                    tool_trace = []

            st.markdown(answer)
            render_agent_trace(tool_trace)
            chat_messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "tool_trace": tool_trace,
                }
            )
else:
    st.info("Upload a document and select **Process Document** to begin.")
