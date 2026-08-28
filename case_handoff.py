import json
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request, urlopen

from incident_case import IncidentCase


HANDOFF_EVENT_TYPE = "saidia.case.processed"
HANDOFF_EVENT_VERSION = "2.0"
HANDOFF_TIMEOUT_SECONDS = 15
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
        response_text = response.read(4096).decode("utf-8", errors="replace")
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
            "attachment_url_expires_in_seconds": 900,
        })

    case_fields = {
        field: customer_case.get(field)
        for field in (
            "case_reference", "reported_at", "status", "claimant_role",
            "tracking_number", "carrier", "country", "delivery_date",
            "declared_value", "complaint_type", "additional_information",
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
    receipt = {
        "case_reference": customer_case["case_reference"],
        "event_id": event["event_id"],
        "sent_at": event["sent_at"],
        "http_status": response_status,
        "status": "accepted",
    }
    jira_result = _parse_jira_result(str(response_text or "").strip())
    if jira_result:
        receipt["jira_result"] = jira_result
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
            "attachment_url_expires_in_seconds": 900,
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
        "title",
        "routing",
        "status",
        "recommended_action",
        "jira_url",
    ):
        value = source.get(field)
        if value is not None and str(value).strip():
            result[field] = str(value).strip()

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
