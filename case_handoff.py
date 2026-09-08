import json
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request, urlopen

from evidence_config import EVIDENCE_DOWNLOAD_EXPIRY_SECONDS
from incident_case import IncidentCase


HANDOFF_EVENT_TYPE = "saidia.case.processed"
HANDOFF_EVENT_VERSION = "2.0"
HANDOFF_TIMEOUT_SECONDS = 60
MAX_WEBHOOK_RESPONSE_BYTES = 64 * 1024
CUSTOMER_HANDOFF_EVENT_TYPE = "saidia.customer_case.ready_for_human_review"
CUSTOMER_HANDOFF_EVENT_VERSION = "1.0"
CUSTOMER_UPDATE_EVENT_TYPE = "saidia.customer_case.updated"
CUSTOMER_UPDATE_EVENT_VERSION = "1.0"


class CaseHandoffError(RuntimeError):
    """A safe handoff error that may be displayed in the Streamlit interface."""


def _post_json(url, *, event, headers, timeout):
    """POST a JSON event using only Python's standard library."""
    request = Request(
        url,
        data=json.dumps(event).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        status_code = getattr(response, "status", response.getcode())
        response_body = response.read(MAX_WEBHOOK_RESPONSE_BYTES + 1)
        if len(response_body) > MAX_WEBHOOK_RESPONSE_BYTES:
            raise CaseHandoffError(
                "Make returned more than 64 KB. Reduce the Webhook Response "
                "body to the recruiter-facing case fields."
            )
        response_text = response_body.decode("utf-8", errors="replace")
    return status_code, response_text


def _read_make_webhook_url() -> str:
    """Read the Make webhook from Streamlit secrets or the local environment."""
    webhook_url = os.getenv("MAKE_WEBHOOK_URL")
    try:
        import streamlit as st

        make_secrets = st.secrets.get("make", {})
        webhook_url = make_secrets.get("WEBHOOK_URL", webhook_url)
    except Exception:
        # Local development may rely on MAKE_WEBHOOK_URL instead.
        pass

    webhook_url = str(webhook_url or "").strip()
    if not webhook_url:
        raise CaseHandoffError(
            "The Make webhook is not configured. Add WEBHOOK_URL to the make "
            "section of Streamlit Secrets or set MAKE_WEBHOOK_URL locally."
        )

    parsed_url = urlparse(webhook_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise CaseHandoffError("The Make webhook must be a valid HTTPS URL.")
    return webhook_url


def customer_case_handoff_enabled() -> bool:
    """Require an explicit switch before sending the new customer event shape."""
    configured = os.getenv("ENABLE_CUSTOMER_CASE_HANDOFF", "false")
    try:
        import streamlit as st

        make_secrets = st.secrets.get("make", {})
        configured = make_secrets.get("ENABLE_CUSTOMER_CASE_HANDOFF", configured)
    except Exception:
        pass
    return str(configured).strip().lower() in {"1", "true", "yes", "on"}


def build_handoff_event(
    incident_case: IncidentCase,
    *,
    sent_at: str = None,
) -> Dict[str, Any]:
    """Build the versioned event envelope sent to workflow integrations."""
    event_time = sent_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )
    return {
        "event_type": HANDOFF_EVENT_TYPE,
        "event_version": HANDOFF_EVENT_VERSION,
        "event_id": f"handoff-{incident_case.case_id}",
        "sent_at": event_time,
        "case": incident_case.to_dict(),
    }


def send_case_to_make(
    incident_case: IncidentCase,
    *,
    post_request=_post_json,
) -> Dict[str, Any]:
    """Send one processed case event to Make and return a safe receipt."""
    event = build_handoff_event(incident_case)
    webhook_url = _read_make_webhook_url()

    try:
        response_status, response_text = post_request(
            webhook_url,
            event=event,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event["event_id"],
            },
            timeout=HANDOFF_TIMEOUT_SECONDS,
        )
    except (TimeoutError, socket.timeout) as exc:
        raise CaseHandoffError(
            "Make did not respond before the handoff timeout. Check the Make "
            "scenario history before trying again."
        ) from exc
    except HTTPError as exc:
        raise CaseHandoffError(
            f"Make rejected the handoff with HTTP status {exc.code}. "
            "Check the scenario and webhook queue before trying again."
        ) from exc
    except URLError as exc:
        raise CaseHandoffError(
            "The processed case could not be sent to Make. Check the webhook "
            "configuration and network connection."
        ) from exc
    except Exception as exc:
        raise CaseHandoffError(
            "The processed case handoff failed unexpectedly. Check the application logs."
        ) from exc

    if not 200 <= response_status < 300:
        raise CaseHandoffError(
            f"Make rejected the handoff with HTTP status {response_status}. "
            "Check the scenario and webhook queue before trying again."
        )

    response_text = str(response_text or "").strip()
    jira_result = _parse_jira_result(response_text)
    receipt = {
        "case_id": incident_case.case_id,
        "event_id": event["event_id"],
        "sent_at": event["sent_at"],
        "http_status": response_status,
        "make_response": response_text[:500],
        "status": "accepted",
    }
    if jira_result:
        receipt["jira_result"] = jira_result
    return receipt


def build_customer_case_handoff_event(customer_case, *, download_url_factory, sent_at=None):
    """Build a human-review event from a persisted customer case."""
    if customer_case.get("downstream_processing_status") not in {
        "evidence_processed", "ready_for_handoff"
    }:
        raise CaseHandoffError("The customer case evidence is not ready for handoff.")

    event_time = sent_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    evidence_items = []
    for evidence in customer_case.get("evidence", []):
        evidence_items.append({
            "evidence_id": evidence["id"],
            "file_name": evidence["original_file_name"],
            "content_type": evidence.get("content_type"),
            "size_bytes": evidence["size_bytes"],
            "evidence_kind": evidence.get("evidence_kind"),
            "processing_status": evidence.get("processing_status"),
            "document_id": evidence.get("document_id"),
            "attachment_download_url": download_url_factory(evidence["s3_object_key"]),
            "attachment_url_expires_in_seconds": EVIDENCE_DOWNLOAD_EXPIRY_SECONDS,
        })

    case_fields = {
        field: customer_case.get(field)
        for field in (
            "case_reference", "reported_at", "status", "claimant_role",
            "tracking_number", "carrier", "country", "delivery_date",
            "declared_value", "complaint_type", "customer_email",
            "additional_information",
            "complaint_details", "evidence_types", "intake_source",
            "intake_completeness",
        )
    }
    case_fields["final_decision_owner"] = "human_reviewer"
    case_fields["analysis_status"] = customer_case.get("analysis_status")
    case_fields["grounded_case_analysis"] = customer_case.get("case_analysis")
    return {
        "event_type": CUSTOMER_HANDOFF_EVENT_TYPE,
        "event_version": CUSTOMER_HANDOFF_EVENT_VERSION,
        "event_id": f"customer-handoff-{customer_case['case_reference']}",
        "sent_at": event_time,
        "case": case_fields,
        "evidence": evidence_items,
    }


def send_customer_case_to_make(customer_case, *, download_url_factory, post_request=_post_json):
    """Send one persisted customer case to Make for human Jira review."""
    event = build_customer_case_handoff_event(
        customer_case, download_url_factory=download_url_factory
    )
    webhook_url = _read_make_webhook_url()
    try:
        response_status, response_text = post_request(
            webhook_url,
            event=event,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event["event_id"],
            },
            timeout=HANDOFF_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise CaseHandoffError(
            "The customer case could not be handed to Make. Check the scenario history before retrying."
        ) from exc
    if not 200 <= response_status < 300:
        raise CaseHandoffError(
            f"Make rejected the customer case with HTTP status {response_status}."
        )
    response_text = str(response_text or "").strip()

    receipt = {
        "case_reference": customer_case["case_reference"],
        "event_id": event["event_id"],
        "sent_at": event["sent_at"],
        "http_status": response_status,
        "status": "accepted",
    }

    receipt.update(_parse_customer_make_response(response_text))

    return receipt


def build_customer_case_update_event(
    case_update, *, jira_result, download_url_factory, sent_at=None
):
    """Build an idempotent event that updates an existing Jira case."""
    if not jira_result or not jira_result.get("issue_key"):
        raise CaseHandoffError(
            "The existing case does not yet have a Jira issue to update."
        )
    event_time = sent_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    evidence_items = []
    for evidence in case_update.get("evidence", []):
        evidence_items.append({
            "evidence_id": evidence["evidence_id"],
            "file_name": evidence["file_name"],
            "content_type": evidence.get("content_type"),
            "size_bytes": evidence["size_bytes"],
            "evidence_kind": evidence.get("evidence_kind"),
            "processing_status": evidence.get("processing_status"),
            "document_id": evidence.get("document_id"),
            "attachment_download_url": download_url_factory(
                evidence["s3_object_key"]
            ),
            "attachment_url_expires_in_seconds": EVIDENCE_DOWNLOAD_EXPIRY_SECONDS,
        })
    return {
        "event_type": CUSTOMER_UPDATE_EVENT_TYPE,
        "event_version": CUSTOMER_UPDATE_EVENT_VERSION,
        "event_id": f"customer-update-{case_update['update_reference']}",
        "sent_at": event_time,
        "case_reference": case_update["case_reference"],
        "update": {
            "update_reference": case_update["update_reference"],
            "additional_information": case_update.get(
                "new_additional_information",
                case_update.get("additional_information", ""),
            ),
            "evidence": evidence_items,
        },
        "jira": {
            "issue_key": jira_result["issue_key"],
            "jira_url": jira_result.get("jira_url"),
        },
    }


def send_customer_case_update_to_make(
    case_update, *, jira_result, download_url_factory, post_request=_post_json
):
    """Send new information to the existing Jira case and return a receipt."""
    event = build_customer_case_update_event(
        case_update,
        jira_result=jira_result,
        download_url_factory=download_url_factory,
    )
    try:
        response_status, response_text = post_request(
            _read_make_webhook_url(),
            event=event,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event["event_id"],
            },
            timeout=HANDOFF_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise CaseHandoffError(
            "The case update could not be handed to Make. Check the scenario "
            "history before retrying."
        ) from exc
    if not 200 <= response_status < 300:
        raise CaseHandoffError(
            f"Make rejected the case update with HTTP status {response_status}."
        )
    receipt = {
        "case_reference": case_update["case_reference"],
        "update_reference": case_update["update_reference"],
        "event_id": event["event_id"],
        "sent_at": event["sent_at"],
        "http_status": response_status,
        "status": "accepted",
    }
    returned_jira = _parse_jira_result(str(response_text or "").strip())
    receipt["jira_result"] = returned_jira or jira_result
    return receipt


def _parse_jira_result(response_text: str) -> Dict[str, Any]:
    """Normalize an optional recruiter-safe Jira result returned by Make."""
    if not response_text:
        return {}
    try:
        response_data = json.loads(response_text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(response_data, dict):
        return {}

    source = response_data.get("jira_result", response_data)
    if not isinstance(source, dict):
        return {}

    result = {}
    for field in (
        "issue_key",
        "issue_id",
        "title",
        "routing",
        "status",
        "recommended_action",
        "jira_url",
    ):
        value = source.get(field)
        if value is not None and str(value).strip():
            result[field] = str(value).strip()

    jira_url = result.get("jira_url")
    if jira_url:
        parsed_url = urlparse(jira_url)
        if parsed_url.scheme == "https" and parsed_url.netloc:
            path_parts = [part for part in parsed_url.path.split("/") if part]
            if not result.get("issue_key") and "browse" in path_parts:
                browse_index = path_parts.index("browse")
                if browse_index + 1 < len(path_parts):
                    result["issue_key"] = path_parts[browse_index + 1]
        else:
            result.pop("jira_url", None)

    issue_key = result.get("issue_key")
    jira_url = result.get("jira_url")
    if not issue_key:
        result.pop("jira_url", None)
    elif jira_url:
        parsed_url = urlparse(jira_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            result.pop("jira_url", None)
        elif parsed_url.path.rstrip("/").endswith("/browse"):
            result["jira_url"] = urlunparse(
                parsed_url._replace(
                    path=f"{parsed_url.path.rstrip('/')}/{quote(issue_key)}"
                )
            )
    return result


def _parse_customer_make_response(response_text: str) -> Dict[str, Any]:
    """Return only recruiter-safe sections from the customer Make response."""
    if not response_text:
        return {}
    try:
        response_data = json.loads(response_text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(response_data, dict):
        return {}

    parsed = {}
    jira_result = _parse_jira_result(response_text)
    if jira_result:
        parsed["jira_result"] = jira_result

    allowed_sections = {
        "case_details": {
            "case_reference", "tracking_number", "claimant_role", "carrier",
            "country", "delivery_date", "declared_value", "complaint_type",
            "reported_at", "customer_email", "additional_information",
            "complaint_details", "evidence_types",
        },
        "saidia_analysis": {
            "factual_summary", "policy_match_status", "policy_explanation", "policy_id",
            "policy_title", "claim_deadline", "reported_on_time",
            "required_evidence", "missing_required_evidence",
            "recommended_next_action", "analysis_status", "policy_effective_date",
            "reporting_window_days", "deadline_basis", "handling_guidance",
        },
        "human_review": {"final_decision_owner", "message"},
    }
    for section_name, allowed_fields in allowed_sections.items():
        source = response_data.get(section_name)
        if isinstance(source, dict):
            section = {
                field: source[field]
                for field in allowed_fields
                if field in source
                and source[field] is not None
                and not (isinstance(source[field], str) and not source[field].strip())
            }
            if section:
                parsed[section_name] = section
    return parsed
