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
from case_handoff import CaseHandoffError, send_case_to_make
from rag_pipeline import process_document
from s3_upload import S3UploadError, upload_to_s3
from vector_store import chunk_text, embed_chunks
from database import (
    document_has_embeddings,
    save_document_chunks,
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

claimant_role = st.radio(
    "Are you the sender or recipient?",
    options=["Recipient", "Sender"],
    horizontal=True,
)

tracking_number = st.text_input(
    "Tracking number",
    placeholder="Enter your parcel tracking number",
)

complaint_type_labels = {
    "parcel_damage": "Package arrived damaged",
    "lost_parcel": "Package is lost",
    "late_delivery": "Package arrived late",
    "partial_loss": "Some items are missing",
    "non_delivery": "Package shows delivered but was not received",
}
incident_type = st.selectbox(
    "What happened?",
    options=list(complaint_type_labels),
    index=None,
    placeholder="Select a problem",
    format_func=lambda value: complaint_type_labels[value],
)

customer_email = st.text_input(
    "Email",
    placeholder="Enter the email address we should use for this case",
)

evidence_photo = st.file_uploader(
    "Photo of the parcel or goods",
    type=["jpg", "jpeg", "png"],
    help="A JPG or PNG photo is required before the complaint can be submitted.",
    key="customer_evidence_photo",
)

additional_information = st.text_area(
    "Additional information",
    placeholder=(
        "Describe what happened and include any details that may help us "
        "review the delivery problem"
    ),
    height=160,
)

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
