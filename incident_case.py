import hashlib
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any, Dict, List, Optional


CASE_SCHEMA_VERSION = "1.0"
APPROVAL_STATUSES = {"draft", "approved", "rejected"}
POLICY_MATCH_STATUSES = {"matched", "not_found", "ambiguous"}


class IncidentCaseError(ValueError):
    """Raised when an incident case violates the internal case contract."""


@dataclass(frozen=True)
class IncidentCase:
    """A validated case payload that can later be handed to external systems."""

    case_id: str
    schema_version: str
    source_file: str
    source_document_hash: str
    incident_id: Optional[str]
    tracking_number: Optional[str]
    carrier: Optional[str]
    country: Optional[str]
    incident_type: Optional[str]
    delivery_date: Optional[str]
    reported_date: Optional[str]
    declared_value: Optional[str]
    evidence_supplied: List[str]
    factual_summary: str
    unresolved_fields: List[str]
    policy_match_status: str
    policy_id: Optional[str]
    policy_title: Optional[str]
    policy_is_fictional: bool
    claim_deadline: Optional[str]
    reported_on_time: Optional[bool]
    required_evidence: List[str]
    missing_required_evidence: List[str]
    recommended_next_action: str
    approval_status: str = "draft"
    reviewer_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clean_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        raise IncidentCaseError("Expected a list of strings in the incident facts.")
    cleaned = []
    for item in value:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _normalize(value: Any) -> str:
    return " ".join(
        str(value or "")
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def build_case_id(source_document_hash: str, incident_id: Optional[str]) -> str:
    """Create a stable identifier so the same document is not handed off twice."""
    document_hash = str(source_document_hash or "").strip()
    if not document_hash:
        raise IncidentCaseError("A source document hash is required.")
    identity = f"{document_hash}:{_normalize(incident_id) or 'unknown'}"
    return f"case-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def _evidence_is_supplied(required_item: str, facts: Dict[str, Any]) -> bool:
    normalized_required = _normalize(required_item)
    evidence_text = " | ".join(
        _normalize(item) for item in facts.get("evidence_supplied", [])
    )

    if normalized_required == "tracking number":
        return bool(facts.get("tracking_number"))
    if normalized_required == "delivery date":
        return bool(facts.get("delivery_date"))
    if normalized_required == "damage report date":
        return bool(facts.get("reported_date"))
    if normalized_required == "commercial invoice or other proof of value":
        return any(
            phrase in evidence_text
            for phrase in ("commercial invoice", "proof of value", "purchase invoice")
        )
    if normalized_required == "photograph of the damaged item":
        return any(
            phrase in evidence_text
            for phrase in (
                "photograph of the damaged item",
                "photograph of damaged item",
                "photo of the damaged item",
                "photo of damaged item",
                "photograph of the damaged items",
                "photograph of damaged items",
            )
        )
    if normalized_required == "photograph of the external packaging":
        return any(
            phrase in evidence_text
            for phrase in (
                "photograph of the external packaging",
                "photograph of external packaging",
                "photo of the external packaging",
                "photo of external packaging",
                "external packaging photograph",
                "external packaging photo",
            )
        )
    return normalized_required in evidence_text


def _policy_assessment(
    facts: Dict[str, Any], policy_result: Dict[str, Any]
) -> Dict[str, Any]:
    policies = policy_result.get("policies", [])
    if len(policies) == 1:
        policy_status = "matched"
        policy = policies[0]
    elif len(policies) > 1:
        policy_status = "ambiguous"
        policy = None
    else:
        policy_status = "not_found"
        policy = None

    if not policy:
        return {
            "policy_match_status": policy_status,
            "policy_id": None,
            "policy_title": None,
            "policy_is_fictional": False,
            "claim_deadline": None,
            "reported_on_time": None,
            "required_evidence": [],
            "missing_required_evidence": [],
            "recommended_next_action": (
                "No single matching evaluation policy was found. Route the case "
                "for human review without making a compliance or liability decision."
            ),
        }

    required_evidence = list(policy.get("required_evidence", []))
    missing_evidence = [
        item
        for item in required_evidence
        if not _evidence_is_supplied(item, facts)
    ]

    delivery_date = _parse_iso_date(facts.get("delivery_date"))
    reported_date = _parse_iso_date(facts.get("reported_date"))
    reporting_window = policy.get("reporting_window_days")
    claim_deadline = None
    reported_on_time = None
    if delivery_date and isinstance(reporting_window, int):
        deadline = delivery_date + timedelta(days=reporting_window)
        claim_deadline = deadline.isoformat()
        if reported_date:
            reported_on_time = reported_date <= deadline

    if missing_evidence:
        action = (
            "Request the missing required evidence before external submission, "
            "then require human review. Do not approve, deny, or assign liability "
            "from the incident document alone."
        )
    else:
        action = (
            "The listed policy evidence appears complete. Require human review "
            "before any external submission, approval, denial, or liability decision."
        )

    return {
        "policy_match_status": policy_status,
        "policy_id": _clean_optional(policy.get("policy_id")),
        "policy_title": _clean_optional(policy.get("title")),
        "policy_is_fictional": bool(policy.get("fictional_evaluation_policy")),
        "claim_deadline": claim_deadline,
        "reported_on_time": reported_on_time,
        "required_evidence": required_evidence,
        "missing_required_evidence": missing_evidence,
        "recommended_next_action": action,
    }


def build_incident_case(
    facts: Dict[str, Any],
    *,
    source_file: str,
    source_document_hash: str,
    policy_result: Dict[str, Any],
) -> IncidentCase:
    """Validate extracted facts and enrich them with deterministic policy logic."""
    required_fact_keys = {
        "incident_id",
        "tracking_number",
        "carrier",
        "country",
        "incident_type",
        "delivery_date",
        "reported_date",
        "declared_value",
        "evidence_supplied",
        "factual_summary",
        "unresolved_fields",
    }
    missing_keys = sorted(required_fact_keys.difference(facts))
    if missing_keys:
        raise IncidentCaseError(
            "The extracted incident facts are missing: " + ", ".join(missing_keys)
        )

    cleaned_facts = {
        key: _clean_optional(facts.get(key))
        for key in required_fact_keys
        if key not in {"evidence_supplied", "unresolved_fields"}
    }
    cleaned_facts["evidence_supplied"] = _clean_list(
        facts.get("evidence_supplied")
    )
    cleaned_facts["unresolved_fields"] = _clean_list(
        facts.get("unresolved_fields")
    )
    cleaned_facts["factual_summary"] = cleaned_facts.get("factual_summary") or (
        "No factual incident summary was extracted."
    )

    for date_field in ("delivery_date", "reported_date"):
        value = cleaned_facts.get(date_field)
        if value and not _parse_iso_date(value):
            cleaned_facts[date_field] = None
            if date_field not in cleaned_facts["unresolved_fields"]:
                cleaned_facts["unresolved_fields"].append(date_field)

    assessment = _policy_assessment(cleaned_facts, policy_result)
    return IncidentCase(
        case_id=build_case_id(
            source_document_hash,
            cleaned_facts.get("incident_id"),
        ),
        schema_version=CASE_SCHEMA_VERSION,
        source_file=str(source_file),
        source_document_hash=str(source_document_hash),
        incident_id=cleaned_facts.get("incident_id"),
        tracking_number=cleaned_facts.get("tracking_number"),
        carrier=cleaned_facts.get("carrier"),
        country=cleaned_facts.get("country"),
        incident_type=cleaned_facts.get("incident_type"),
        delivery_date=cleaned_facts.get("delivery_date"),
        reported_date=cleaned_facts.get("reported_date"),
        declared_value=cleaned_facts.get("declared_value"),
        evidence_supplied=cleaned_facts["evidence_supplied"],
        factual_summary=cleaned_facts["factual_summary"],
        unresolved_fields=cleaned_facts["unresolved_fields"],
        **assessment,
    )


def review_incident_case(
    incident_case: IncidentCase,
    approval_status: str,
    reviewer_note: str = "",
) -> IncidentCase:
    """Record a human review decision without changing the extracted facts."""
    if approval_status not in APPROVAL_STATUSES:
        raise IncidentCaseError(
            "Approval status must be draft, approved, or rejected."
        )
    return replace(
        incident_case,
        approval_status=approval_status,
        reviewer_note=str(reviewer_note or "").strip(),
    )
