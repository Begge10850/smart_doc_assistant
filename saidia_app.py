import asyncio
import copy
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
    CUSTOMER_HANDOFF_EVENT_TYPE,
    CUSTOMER_HANDOFF_EVENT_VERSION,
    CUSTOMER_UPDATE_EVENT_TYPE,
    CUSTOMER_UPDATE_EVENT_VERSION,
    customer_case_handoff_enabled,
    send_case_to_make,
    send_customer_case_update_to_make,
    send_customer_case_to_make,
)
from customer_intake import (
    COMPLAINT_TYPE_LABELS,
    COMPLAINT_REQUIREMENTS,
    CONFIGURED_CARRIER,
    EVIDENCE_TYPE_LABELS,
    POLICY_EVIDENCE_BY_TYPE,
    IMAGE_EVIDENCE_TYPES,
    SUPPORTED_COUNTRIES,
    SUPPORTED_EVIDENCE_TYPES,
    build_customer_case_update,
    build_customer_complaint,
    validate_case_update,
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
    create_customer_case_update,
    create_customer_case,
    document_has_embeddings,
    find_active_customer_case,
    get_customer_case_for_handoff,
    get_latest_customer_jira_result,
    record_duplicate_submission_attempt,
    record_processing_event,
    save_document_chunks,
    save_customer_case_analysis,
    save_customer_workflow_result,
    search_document_chunks,
    update_customer_case_status,
    update_customer_case_update_status,
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

CUSTOMER_FORM_WIDGET_KEYS = [
    "customer_claimant_role",
    "customer_tracking_number",
    "customer_country",
    "customer_incident_type",
    "customer_email",
    "customer_delivery_date",
    "customer_expected_delivery_date",
    "customer_carrier_recorded_delivery_date",
    "customer_service_type",
    "customer_promised_duration_days",
    "customer_promised_delivery_date",
    "customer_actual_delivery_date",
    "customer_tracking_status",
    "customer_package_contents_description",
    "customer_missing_items_description",
    "customer_recipient_statement",
    "customer_policy_exclusions",
    "customer_evidence_types",
    "customer_declared_value",
    "customer_evidence_files",
    "customer_additional_information",
    "customer_update_case_reference",
    "customer_update_tracking_number",
    "customer_update_evidence_files",
    "customer_update_additional_information",
]


def reset_customer_form_state():
    """Clear every customer widget, including the evidence uploader."""
    for key in CUSTOMER_FORM_WIDGET_KEYS:
        st.session_state.pop(key, None)


@st.cache_resource
def get_customer_processing_executor():
    """Keep customer follow-up work off the response-rendering thread."""
    return ThreadPoolExecutor(max_workers=2)


def start_customer_report():
    """Open a fresh complaint form."""
    reset_customer_form_state()
    st.session_state.pop("customer_complaint", None)
    st.session_state.pop("customer_case_handoff_receipt", None)
    st.session_state.customer_intake_view = "form"


def start_customer_update():
    """Open a fresh existing-case update form."""
    reset_customer_form_state()
    st.session_state.pop("customer_case_update", None)
    st.session_state.customer_intake_view = "update_form"

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
        "Complaint-specific facts: "
        + json.dumps(complaint.get("complaint_details", {}), sort_keys=True),
        "Evidence supplied: "
        + "; ".join(
            POLICY_EVIDENCE_BY_TYPE.get(item, item)
            for item in complaint.get("evidence_types", [])
        ),
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


def complete_customer_case_processing(complaint):
    """Prepare persisted evidence, analysis, and optional operational handoff."""
    processing_started = time.perf_counter()
    complaint = process_customer_evidence(complaint)

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
        handoff_event_id = f"customer-handoff-{complaint['case_reference']}"
        try:
            persisted_case = get_customer_case_for_handoff(
                complaint["case_reference"]
            )
            handoff_receipt = run_customer_stage(
                complaint["case_reference"],
                "make_handoff",
                send_customer_case_to_make,
                persisted_case,
                function_kwargs={
                    "download_url_factory": create_private_evidence_download_url,
                },
            )
        except Exception:
            # The report remains safely received when an internal handoff fails.
            try:
                save_customer_workflow_result(
                    case_reference=complaint["case_reference"],
                    event_id=handoff_event_id,
                    event_type=CUSTOMER_HANDOFF_EVENT_TYPE,
                    event_version=CUSTOMER_HANDOFF_EVENT_VERSION,
                    handoff_status="failed",
                    error_message="The Make handoff did not complete.",
                )
            except Exception:
                pass
            complaint["downstream_processing_status"] = "handoff_failed"
            update_customer_case_status(
                complaint["case_reference"],
                "handoff_failed",
                "The Make handoff did not complete.",
            )
        else:
            complaint["downstream_processing_status"] = "handoff_accepted"
            update_customer_case_status(
                complaint["case_reference"], "handoff_accepted"
            )
            try:
                save_customer_workflow_result(
                    case_reference=complaint["case_reference"],
                    event_id=handoff_receipt["event_id"],
                    event_type=CUSTOMER_HANDOFF_EVENT_TYPE,
                    event_version=CUSTOMER_HANDOFF_EVENT_VERSION,
                    handoff_status=handoff_receipt["status"],
                    jira_result=handoff_receipt.get("jira_result"),
                )
            except Exception:
                # A successful external handoff remains successful even if its
                # local receipt needs an operational persistence retry.
                pass
    else:
        complaint["downstream_processing_status"] = "ready_for_handoff"
        update_customer_case_status(
            complaint["case_reference"], "ready_for_handoff"
        )

    record_processing_event(
        case_reference=complaint["case_reference"],
        stage="submission_to_ready",
        duration_ms=round(
            (time.perf_counter() - processing_started) * 1000,
            2,
        ),
        status="completed",
    )
    return complaint


def process_customer_case_in_background(complaint):
    """Finish evidence, analysis, and handoff after the case is acknowledged."""
    try:
        return complete_customer_case_processing(complaint)
    except Exception as exc:
        for evidence_item in complaint["evidence"]:
            evidence_item.pop("data", None)
        complaint["downstream_processing_status"] = "evidence_processing_failed"
        complaint["processing_error"] = str(exc)
        try:
            update_customer_case_status(
                complaint["case_reference"],
                "evidence_processing_failed",
                "Evidence processing did not complete.",
            )
        except Exception:
            pass
        return complaint


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

if st.session_state.pop("reset_customer_form_on_rerun", False):
    reset_customer_form_state()

customer_intake_view = st.session_state.setdefault(
    "customer_intake_view", "landing"
)

if customer_intake_view == "landing":
    st.subheader("How can we help?")
    st.markdown(
        "Report a new delivery problem or add information and evidence to a "
        "case you already reported."
    )
    report_column, update_column = st.columns(2)
    with report_column:
        st.button(
            "Report a new problem",
            type="primary",
            use_container_width=True,
            on_click=start_customer_report,
        )
    with update_column:
        st.button(
            "Update an existing case",
            use_container_width=True,
            on_click=start_customer_update,
        )

elif customer_intake_view == "form":
    st.subheader("Report a Delivery Problem")
    st.caption(
        f"Fictional evaluation environment for {CONFIGURED_CARRIER}. "
        "Policies are project examples, not real carrier terms."
    )

    incident_type = st.selectbox(
        "What happened?",
        options=list(COMPLAINT_TYPE_LABELS),
        index=None,
        placeholder="Select a problem",
        format_func=lambda value: COMPLAINT_TYPE_LABELS[value],
        key="customer_incident_type",
        help=(
            "Package is lost means tracking has no valid delivery event. "
            "Delivered but not received means carrier tracking explicitly shows delivered."
        ),
    )
    if incident_type:
        requirements = COMPLAINT_REQUIREMENTS[incident_type]
        st.info(
            "Required evidence: "
            + "; ".join(
                EVIDENCE_TYPE_LABELS[item]
                for item in requirements["required_evidence"]
            )
        )

    complaint_details = {}
    evidence_types = []
    delivery_date = None
    with st.form("customer_complaint_form", clear_on_submit=False):
        claimant_role = st.radio(
            "Are you the sender or recipient?",
            options=["Recipient", "Sender"],
            horizontal=True,
            key="customer_claimant_role",
        )
        tracking_number = st.text_input(
            "Tracking number",
            placeholder="Enter your parcel tracking number",
            key="customer_tracking_number",
        )
        country = st.selectbox(
            "Destination country",
            options=SUPPORTED_COUNTRIES,
            index=None,
            placeholder="Select a country",
            key="customer_country",
        )
        customer_email = st.text_input(
            "Contact email",
            placeholder="Enter an email address the case team can use to contact you",
            key="customer_email",
        )
        declared_value = ""
        if incident_type in {"parcel_damage", "partial_loss"}:
            delivery_date = st.date_input(
                "Actual delivery date *", value=None, key="customer_delivery_date"
            )
            complaint_details["delivery_date"] = delivery_date
        elif incident_type == "lost_parcel":
            expected_date = st.date_input(
                "Expected delivery date *", value=None,
                key="customer_expected_delivery_date",
            )
            delivery_date = expected_date
            complaint_details["expected_delivery_date"] = expected_date
            complaint_details["tracking_status"] = st.text_input(
                "Latest carrier tracking status *",
                placeholder="For example, departed regional hub on 12 August",
                key="customer_tracking_status",
            )
            complaint_details["package_contents_description"] = st.text_area(
                "Parcel contents *", key="customer_package_contents_description"
            )
        elif incident_type == "late_delivery":
            complaint_details["service_type"] = st.text_input(
                "Delivery service type *",
                placeholder="For example, NorthStar Express",
                key="customer_service_type",
            )
            complaint_details["promised_duration_days"] = st.number_input(
                "Promised transit duration in days (if known)",
                min_value=1, value=None, step=1,
                key="customer_promised_duration_days",
            )
            complaint_details["promised_delivery_date"] = st.date_input(
                "Promised delivery date *", value=None,
                key="customer_promised_delivery_date",
            )
            actual_date = st.date_input(
                "Actual delivery date *", value=None,
                key="customer_actual_delivery_date",
            )
            complaint_details["actual_delivery_date"] = actual_date
            delivery_date = actual_date
            complaint_details["policy_exclusions"] = st.multiselect(
                "Known delay circumstances (optional; reviewed against policy)",
                options=[
                    "severe_weather", "customs_delay",
                    "customer_requested_delivery_change", "incomplete_address",
                ],
                format_func=lambda value: value.replace("_", " ").title(),
                key="customer_policy_exclusions",
            )
            st.caption(
                "Any delivery-fee reimbursement is only a policy-guided "
                "recommendation and always requires human review."
            )
        if incident_type == "partial_loss":
            complaint_details["missing_items_description"] = st.text_area(
                "Missing items *", key="customer_missing_items_description"
            )
        elif incident_type == "non_delivery":
            recorded_date = st.date_input(
                "Carrier-recorded delivery date *", value=None,
                key="customer_carrier_recorded_delivery_date",
            )
            delivery_date = recorded_date
            complaint_details["carrier_recorded_delivery_date"] = recorded_date
            complaint_details["recipient_statement"] = st.checkbox(
                "I confirm that the carrier shows this parcel as delivered, "
                "but the recipient did not receive it. *",
                key="customer_recipient_statement",
            )

        if incident_type in {"parcel_damage", "lost_parcel", "partial_loss"}:
            declared_value = st.text_input(
                "Declared or purchase value *",
                placeholder="For example, EUR 899.00",
                key="customer_declared_value",
            )
            complaint_details["declared_value"] = declared_value

        if incident_type:
            evidence_types = st.multiselect(
                "Confirm the required evidence included in the files below *",
                options=list(COMPLAINT_REQUIREMENTS[incident_type]["required_evidence"]),
                format_func=lambda value: EVIDENCE_TYPE_LABELS[value],
                key="customer_evidence_types",
                help="Select every evidence type present, then upload the matching files.",
            )
        evidence_files = st.file_uploader(
            "Supporting evidence",
            type=SUPPORTED_EVIDENCE_TYPES,
            accept_multiple_files=True,
            help=(
                "Upload up to 10 files (50 MB combined). Images: 10 MB each. "
                "Documents: 20 MB each. The evidence list above changes with the complaint."
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
            key="customer_additional_information",
        )
        complaint_submitted = st.form_submit_button(
            "Submit complaint", use_container_width=True
        )

elif customer_intake_view == "update_form":
    st.subheader("Update an Existing Case")
    st.markdown(
        "Enter the case reference shown after your original report together "
        "with the same parcel tracking number."
    )
    with st.form("customer_case_update_form", clear_on_submit=False):
        update_case_reference = st.text_input(
            "Case reference",
            placeholder="For example, CASE-20260828-ABC123",
            key="customer_update_case_reference",
        )
        update_tracking_number = st.text_input(
            "Tracking number",
            placeholder="Enter the tracking number from the original case",
            key="customer_update_tracking_number",
        )
        update_information = st.text_area(
            "Additional information",
            placeholder="Explain what you want the reviewer to add to your case",
            height=160,
            key="customer_update_additional_information",
        )
        update_evidence_files = st.file_uploader(
            "Additional evidence",
            type=SUPPORTED_EVIDENCE_TYPES,
            accept_multiple_files=True,
            help=(
                "Upload up to 10 new files (50 MB combined). Images: 10 MB "
                "each. Documents: 20 MB each."
            ),
            key="customer_update_evidence_files",
        )
        case_update_submitted = st.form_submit_button(
            "Add to existing case", use_container_width=True
        )

elif customer_intake_view == "processing":
    processing_complaint = st.session_state.get("customer_complaint")
    if processing_complaint:
        st.success("Report received")
        st.markdown(
            "Your case reference is "
            f"**`{processing_complaint['case_reference']}`**. Keep this reference "
            "for future correspondence."
        )
        st.info(
            "Preparing your evidence and case details for review. This page "
            "will update automatically when preparation is complete."
        )
    else:
        st.session_state.customer_intake_view = "landing"
        st.rerun()

elif customer_intake_view == "duplicate":
    st.warning("A case is already open")
    st.markdown(
        "We have already received a report for this tracking number and "
        "delivery problem. No duplicate case was created."
    )
    st.info(
        "Use the case reference shown after your original submission to add "
        "information or supporting evidence. The repeated report has been "
        "recorded for the reviewing team."
    )
    st.button(
        "Update the existing case",
        type="primary",
        use_container_width=True,
        on_click=start_customer_update,
    )

elif customer_intake_view == "update_processing":
    processing_update = st.session_state.get("customer_case_update")
    if processing_update:
        st.success("Additional information received")
        st.markdown(
            "Your update is being added to case "
            f"**`{processing_update['case_reference']}`**."
        )
        st.info(
            "Preparing the new information and evidence for the case reviewer. "
            "This page will update automatically."
        )
    else:
        st.session_state.customer_intake_view = "landing"
        st.rerun()

elif customer_intake_view == "update_success":
    submitted_update = st.session_state.get("customer_case_update")
    if submitted_update:
        st.success("Additional information received")
        st.markdown(
            "Your information has been received for case "
            f"**`{submitted_update['case_reference']}`** and will be considered "
            "during review."
        )
        if submitted_update.get("processing_status") == "processing_failed":
            st.warning(
                "The update is recorded, but its evidence is taking longer than "
                "expected to prepare. Do not submit the same update again."
            )
        st.button(
            "Return to case options",
            use_container_width=True,
            on_click=lambda: st.session_state.update(customer_intake_view="landing"),
        )
    else:
        st.session_state.customer_intake_view = "landing"
        st.rerun()

else:
    submitted_complaint = st.session_state.get("customer_complaint")
    if submitted_complaint:
        processing_future = st.session_state.get("customer_processing_future")
        if processing_future is not None and processing_future.done():
            submitted_complaint = processing_future.result()
            st.session_state.customer_complaint = submitted_complaint
            st.session_state.pop("customer_processing_future", None)
        st.success("Case reported successfully")
        st.markdown(
            "We have received your delivery problem. Your case reference is "
            f"**`{submitted_complaint['case_reference']}`**. Keep this reference "
            "for future correspondence."
        )
        if (
            submitted_complaint.get("downstream_processing_status")
            == "evidence_processing_failed"
        ):
            st.warning(
                "Your report is recorded, but evidence preparation is taking "
                "longer than expected. Keep your case reference; the case can "
                "still be followed up safely."
            )
        elif processing_future is not None and not processing_future.done():
            st.caption(
                "Your report is safely recorded. Evidence preparation and the "
                "internal human-review handoff are continuing in the background."
            )
        else:
            st.caption(
                "Your original evidence is stored securely and is available for "
                "human review."
            )
        st.button(
            "Return to case options",
            type="primary",
            use_container_width=True,
            on_click=lambda: st.session_state.update(customer_intake_view="landing"),
        )
    else:
        st.session_state.customer_intake_view = "landing"
        st.rerun()

if customer_intake_view == "form" and complaint_submitted:
    validation_errors = validate_customer_submission(
        tracking_number,
        country,
        delivery_date,
        incident_type,
        customer_email,
        evidence_files,
        complaint_details=complaint_details,
        evidence_types=evidence_types,
    )
    if validation_errors:
        for validation_error in validation_errors:
            st.error(validation_error)
    else:
        existing_case = find_active_customer_case(
            tracking_number, incident_type
        )
        if existing_case:
            record_duplicate_submission_attempt(
                existing_case["case_reference"]
            )
            st.session_state.reset_customer_form_on_rerun = True
            st.session_state.customer_intake_view = "duplicate"
            st.rerun()

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
            complaint_details=complaint_details,
            evidence_types=evidence_types,
        )
        try:
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
        except Exception as exc:
            for evidence_item in complaint["evidence"]:
                evidence_item.pop("data", None)
            st.error(
                "We could not receive your report. Please check your connection "
                "and try again."
            )
        else:
            st.session_state.customer_complaint = complaint
            st.session_state.customer_processing_future = (
                get_customer_processing_executor().submit(
                    process_customer_case_in_background,
                    copy.deepcopy(complaint),
                )
            )
            st.session_state.reset_customer_form_on_rerun = True
            st.session_state.customer_intake_view = "success"
            st.rerun()

if customer_intake_view == "update_form" and case_update_submitted:
    validation_errors = validate_case_update(
        update_case_reference,
        update_tracking_number,
        update_information,
        update_evidence_files,
    )
    if validation_errors:
        for validation_error in validation_errors:
            st.error(validation_error)
    else:
        case_update = build_customer_case_update(
            update_case_reference,
            update_tracking_number,
            update_information,
            update_evidence_files,
        )
        update_lookup_failed = False
        try:
            case_update_id = create_customer_case_update(case_update)
        except Exception:
            case_update_id = None
            update_lookup_failed = True
            st.error(
                "We could not verify the case right now. Please try again later."
            )
        if case_update_id is None and not update_lookup_failed:
            st.error(
                "The case reference and tracking number did not match an active "
                "case. Check both values and try again."
            )
        elif case_update_id is not None:
            st.session_state.customer_case_update = case_update
            st.session_state.reset_customer_form_on_rerun = True
            st.session_state.customer_intake_view = "update_processing"
            st.rerun()

if customer_intake_view == "processing":
    complaint = st.session_state.get("customer_complaint")
    if complaint:
        try:
            complaint = complete_customer_case_processing(complaint)
        except Exception as exc:
            for evidence_item in complaint["evidence"]:
                evidence_item.pop("data", None)
            complaint["downstream_processing_status"] = "evidence_processing_failed"
            complaint["processing_error"] = str(exc)
            try:
                update_customer_case_status(
                    complaint["case_reference"],
                    "evidence_processing_failed",
                    "Evidence processing did not complete.",
                )
            except Exception:
                pass
        st.session_state.customer_complaint = complaint
        st.session_state.customer_intake_view = "success"
        st.rerun()

if customer_intake_view == "update_processing":
    case_update = st.session_state.get("customer_case_update")
    if case_update:
        try:
            case_update = process_customer_evidence(case_update)
            case_update["new_additional_information"] = case_update.get(
                "additional_information", ""
            )
            case_update["additional_information"] = "\n".join(filter(None, [
                case_update.get("original_additional_information"),
                case_update.get("additional_information"),
            ]))
            analysis = run_customer_stage(
                case_update["case_reference"],
                "case_update_analysis",
                prepare_customer_case_analysis,
                case_update,
            )
            case_update["case_analysis"] = analysis
            save_customer_case_analysis(
                case_update["case_reference"], analysis, "completed"
            )
            update_customer_case_update_status(
                case_update["update_reference"], "processed"
            )
            if customer_case_handoff_enabled():
                jira_result = get_latest_customer_jira_result(
                    case_update["case_reference"]
                )
                if jira_result:
                    try:
                        update_receipt = send_customer_case_update_to_make(
                            case_update,
                            jira_result=jira_result,
                            download_url_factory=create_private_evidence_download_url,
                        )
                        save_customer_workflow_result(
                            case_reference=case_update["case_reference"],
                            event_id=update_receipt["event_id"],
                            event_type=CUSTOMER_UPDATE_EVENT_TYPE,
                            event_version=CUSTOMER_UPDATE_EVENT_VERSION,
                            handoff_status=update_receipt["status"],
                            jira_result=update_receipt.get("jira_result"),
                        )
                    except Exception:
                        # Evidence is already safe. Jira delivery can be retried
                        # internally without asking the customer to upload again.
                        update_customer_case_update_status(
                            case_update["update_reference"],
                            "processed_handoff_pending",
                            "The Jira update handoff did not complete.",
                        )
        except Exception:
            for evidence_item in case_update["evidence"]:
                evidence_item.pop("data", None)
            update_customer_case_update_status(
                case_update["update_reference"],
                "processing_failed",
                "The case update could not be fully prepared.",
            )
            case_update["processing_status"] = "processing_failed"
        st.session_state.customer_case_update = case_update
        st.session_state.customer_intake_view = "update_success"
        st.rerun()

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
