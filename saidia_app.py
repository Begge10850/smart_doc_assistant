import asyncio
import hashlib
import json
import os
from pathlib import Path

st.write("DATABASE_URL secret exists:", "DATABASE_URL" in st.secrets)

import streamlit as st

from agent_engine import (
    DocumentAgentError,
    prepare_incident_case,
    run_document_agent,
)
from case_handoff import CaseHandoffError, send_case_to_make
from incident_case import review_incident_case
from rag_pipeline import (
    download_file_from_s3,
    process_document,
)
from s3_upload import S3UploadError, upload_to_s3
from vector_store import build_faiss_index, chunk_text, embed_chunks
from database import upsert_document


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
    "extracted_text",
    "document_metadata",
    "chunks",
    "vector_index",
    "chat_messages",
    "incident_case",
    "case_handoff_receipt",
]


def clear_document_session():
    """Forget the processed document, vector index, and its conversation."""
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


def render_incident_case_review(incident_case):
    """Render a human-readable review of the structured incident payload."""
    status = incident_case.approval_status
    status_message = {
        "draft": "Draft awaiting human review",
        "approved": "Approved locally for a later handoff",
        "rejected": "Rejected during human review",
    }[status]
    st.info(f"**Status:** {status_message}")

    fact_rows = [
        {"Field": "Case ID", "Value": incident_case.case_id},
        {"Field": "Incident ID", "Value": incident_case.incident_id or "Unresolved"},
        {
            "Field": "Tracking number",
            "Value": incident_case.tracking_number or "Unresolved",
        },
        {"Field": "Carrier", "Value": incident_case.carrier or "Unresolved"},
        {"Field": "Country", "Value": incident_case.country or "Unresolved"},
        {
            "Field": "Incident type",
            "Value": incident_case.incident_type or "Unresolved",
        },
        {
            "Field": "Delivery date",
            "Value": incident_case.delivery_date or "Unresolved",
        },
        {
            "Field": "Reported date",
            "Value": incident_case.reported_date or "Unresolved",
        },
        {
            "Field": "Declared value",
            "Value": incident_case.declared_value or "Unresolved",
        },
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

    reviewer_note = st.text_area(
        "Reviewer note (optional)",
        value=incident_case.reviewer_note,
        key=f"reviewer_note_{incident_case.case_id}_{status}",
        disabled=status != "draft",
    )

    if status == "draft":
        st.caption(
            "These buttons only record a decision in this browser session. "
            "Nothing is sent to Jira, Make.com, Google Sheets, or another system."
        )
        approve_col, reject_col = st.columns(2)
        if approve_col.button(
            "✅ Approve Case Draft",
            key=f"approve_{incident_case.case_id}",
            use_container_width=True,
        ):
            st.session_state.incident_case = review_incident_case(
                incident_case,
                "approved",
                reviewer_note,
            )
            st.rerun()
        if reject_col.button(
            "❌ Reject Case Draft",
            key=f"reject_{incident_case.case_id}",
            use_container_width=True,
        ):
            st.session_state.incident_case = review_incident_case(
                incident_case,
                "rejected",
                reviewer_note,
            )
            st.rerun()
    elif status == "approved":
        st.markdown("#### External workflow handoff")
        handoff_receipt = st.session_state.get("case_handoff_receipt")
        if (
            handoff_receipt
            and handoff_receipt.get("case_id") == incident_case.case_id
        ):
            st.success(
                "Make accepted this approved case at "
                f"{handoff_receipt['sent_at']}."
            )
            st.caption(
                f"Event ID: `{handoff_receipt['event_id']}` · "
                f"HTTP {handoff_receipt['http_status']}"
            )
        else:
            st.caption(
                "This explicitly sends the approved structured case to the "
                "configured Make.com webhook. It does not approve the customer claim."
            )
            if st.button(
                "Send Approved Case to Make",
                key=f"handoff_{incident_case.case_id}",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Sending the approved case to Make..."):
                    try:
                        st.session_state.case_handoff_receipt = send_case_to_make(
                            incident_case
                        )
                        st.rerun()
                    except CaseHandoffError as exc:
                        st.error(f"The case was not handed off: {exc}")

    case_json = json.dumps(incident_case.to_dict(), indent=2)
    st.download_button(
        "Download structured case JSON",
        data=case_json,
        file_name=f"{incident_case.case_id}.json",
        mime="application/json",
        key=f"download_{incident_case.case_id}_{status}",
    )


st.set_page_config(page_title="Saidia Smart Document Assistant", layout="wide")
st.title("📄 Saidia Smart Document Assistant")
st.markdown(
    "Upload a document once, then continue a grounded conversation about it."
)


with st.sidebar:
    st.header("📤 Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a PDF, DOCX, TXT, JPG, JPEG, or PNG file",
        type=["pdf", "txt", "docx", "jpg", "jpeg", "png"],
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
            and st.session_state.get("vector_index") is not None
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

    try:
        with st.status("Preparing document...", expanded=True) as status:
            status.write("Uploading the original document to S3...")
            s3_object_key = upload_to_s3(file_data, file_name)

            local_path = os.path.join("temp", s3_object_key)
            os.makedirs("temp", exist_ok=True)

            status.write("Downloading the stored document for processing...")
            if not download_file_from_s3(s3_object_key, local_path):
                raise RuntimeError("The document could not be downloaded from S3.")

            status.write("Inspecting the file and selecting an extraction method...")
            processing_result = process_document(local_path)
            extracted_text = processing_result["text"]
            document_metadata = processing_result["metadata"]
            status.write("Saving document metadata to PostgreSQL...")

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
                processing_status="processed",
            )
            if not extracted_text.strip():
                raise RuntimeError("No text could be extracted from the document.")

            status.write("Creating document chunks and embeddings...")
            chunks = chunk_text(extracted_text)
            if not chunks:
                raise RuntimeError("The extracted document produced no text chunks.")

            embeddings = embed_chunks(chunks)
            vector_index = build_faiss_index(embeddings)

            st.session_state.processed_doc_hash = document_hash
            st.session_state.processed_file_name = file_name
            st.session_state.s3_object_key = s3_object_key
            st.session_state.document_id = document_id
            st.session_state.extracted_text = extracted_text
            st.session_state.document_metadata = document_metadata
            st.session_state.chunks = chunks
            st.session_state.vector_index = vector_index
            st.session_state.chat_messages = []
            st.session_state.processing_requested = False

            status.update(label="Document ready for conversation", state="complete")

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
    vector_index = st.session_state.vector_index
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

    with st.expander("🧠 Preview extracted text", expanded=False):
        st.text_area(
            "Document text",
            extracted_text,
            height=300,
            disabled=True,
            key=f"preview_{st.session_state.processed_doc_hash}",
        )

    st.subheader("📋 Incident case review")
    st.caption(
        "Prepare a consistent case record for human review before any later "
        "handoff to Jira, Make.com, Google Sheets, or a database."
    )
    incident_case = st.session_state.get("incident_case")
    if incident_case is None:
        if st.button("Prepare Case for Review", key="prepare_case_btn"):
            with st.spinner("Extracting and validating the incident case..."):
                try:
                    st.session_state.incident_case = prepare_incident_case(
                        extracted_text,
                        source_file=file_name,
                        source_document_hash=st.session_state.processed_doc_hash,
                    )
                    st.rerun()
                except DocumentAgentError as exc:
                    st.error(f"The case draft could not be prepared: {exc}")
    else:
        render_incident_case_review(incident_case)

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
                    agent_result = run_document_agent(
                        question,
                        file_name=file_name,
                        document_metadata=document_metadata,
                        vector_index=vector_index,
                        chunks=chunks,
                        chat_history=prior_history,
                    )
                    answer = agent_result["answer"]
                    tool_trace = agent_result["tool_trace"]
                except DocumentAgentError as exc:
                    answer = f"I could not answer that question: {exc}"
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
