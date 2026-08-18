import json
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from incident_case import IncidentCase


HANDOFF_EVENT_TYPE = "saidia.case.processed"
HANDOFF_EVENT_VERSION = "2.0"
HANDOFF_TIMEOUT_SECONDS = 15


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
    return result
