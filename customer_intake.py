from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from uuid import uuid4


SUPPORTED_EVIDENCE_TYPES = ["pdf", "txt", "docx", "jpg", "jpeg", "png"]
IMAGE_EVIDENCE_TYPES = {"jpg", "jpeg", "png"}
CONFIGURED_CARRIER = "NorthStar Parcel"
DUPLICATE_CASE_MESSAGE = (
    "A case has already been opened for this tracking ID. Please wait for updates "
    "while we investigate."
)
SUPPORTED_COUNTRIES = ["Germany", "France"]
MAX_EVIDENCE_FILES = 10
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 50 * 1024 * 1024
COMPLAINT_TYPE_LABELS = {
    "parcel_damage": "Package arrived damaged",
    "lost_parcel": "Package is lost",
    "late_delivery": "Package arrived late",
    "partial_loss": "Some items are missing",
    "non_delivery": "Package shows delivered but was not received",
}
EVIDENCE_TYPE_LABELS = {
    "damage_photo": "Photo of the damaged item",
    "packaging_photo": "Photo of the external packaging",
    "proof_of_value": "Invoice, receipt, order confirmation, or other proof of value",
    "promised_delivery_evidence": "Evidence of the promised delivery date",
    "delivery_status_evidence": "Evidence that carrier tracking shows delivered",
    "packing_list": "Packing list or other record of the parcel contents",
}
POLICY_EVIDENCE_BY_TYPE = {
    "damage_photo": "photograph of the damaged item",
    "packaging_photo": "photograph of the external packaging",
    "proof_of_value": "commercial invoice or other proof of value",
    "promised_delivery_evidence": "evidence of the promised delivery date",
    "delivery_status_evidence": "proof of carrier delivery status",
    "packing_list": "packing list or description of missing contents",
}
COMPLAINT_REQUIREMENTS = {
    "parcel_damage": {
        "required_fields": ("delivery_date", "declared_value"),
        "required_evidence": ("damage_photo", "packaging_photo", "proof_of_value"),
    },
    "lost_parcel": {
        "required_fields": (
            "expected_delivery_date", "declared_value", "package_contents_description",
            "tracking_status",
        ),
        "required_evidence": ("proof_of_value",),
    },
    "late_delivery": {
        "required_fields": (
            "service_type", "promised_delivery_date", "actual_delivery_date",
        ),
        "required_evidence": ("promised_delivery_evidence",),
    },
    "partial_loss": {
        "required_fields": (
            "delivery_date", "declared_value", "missing_items_description",
        ),
        "required_evidence": ("packaging_photo", "proof_of_value", "packing_list"),
    },
    "non_delivery": {
        "required_fields": ("carrier_recorded_delivery_date", "recipient_statement"),
        "required_evidence": ("delivery_status_evidence",),
    },
}
FIELD_LABELS = {
    "delivery_date": "delivery date",
    "expected_delivery_date": "expected delivery date",
    "declared_value": "declared or purchase value",
    "package_contents_description": "description of the parcel contents",
    "tracking_status": "latest tracking status",
    "service_type": "delivery service type",
    "promised_delivery_date": "promised delivery date",
    "actual_delivery_date": "actual delivery date",
    "missing_items_description": "description of the missing items",
    "carrier_recorded_delivery_date": "carrier-recorded delivery date",
    "recipient_statement": "recipient confirmation that the parcel was not received",
}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_customer_submission(
    tracking_number, country, delivery_date, complaint_type,
    customer_email, evidence_files, *, complaint_details=None,
    evidence_types=None
):
    """Return customer-facing validation errors without external side effects."""
    errors = []
    if not tracking_number.strip():
        errors.append("Enter your tracking number.")
    if country not in SUPPORTED_COUNTRIES:
        errors.append("Enter the destination country.")
    if complaint_type not in COMPLAINT_TYPE_LABELS:
        errors.append("Select what happened to your delivery.")
    if not EMAIL_PATTERN.fullmatch(customer_email.strip().lower()):
        errors.append("Enter a valid contact email address.")
    errors.extend(validate_evidence_files(evidence_files))
    details = dict(complaint_details or {})
    if delivery_date is not None:
        details.setdefault("delivery_date", delivery_date)
    requirements = COMPLAINT_REQUIREMENTS.get(complaint_type, {})
    for field in requirements.get("required_fields", ()):
        value = details.get(field)
        if value is None or (isinstance(value, str) and not value.strip()) or value is False:
            errors.append(f"Enter {FIELD_LABELS[field]}.")
    declared_value = details.get("declared_value")
    if declared_value not in (None, ""):
        try:
            if Decimal(str(declared_value).strip()) <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errors.append("Enter the declared or purchase value using numbers only.")

    selected_evidence = set(evidence_types or [])
    if selected_evidence and not list(evidence_files or []):
        errors.append("Upload the evidence files you identified.")
    image_evidence = {"damage_photo", "packaging_photo"}.intersection(selected_evidence)
    if image_evidence:
        image_count = sum(
            Path(item.name).suffix.lower().lstrip(".") in IMAGE_EVIDENCE_TYPES
            for item in (evidence_files or [])
        )
        if image_count < len(image_evidence):
            errors.append(
                "Upload a separate JPG or PNG file for each required photo type."
            )

    promised = _as_date(details.get("promised_delivery_date"))
    actual = _as_date(details.get("actual_delivery_date"))
    actual_dates = {
        "delivery date": _as_date(details.get("delivery_date")),
        "actual delivery date": actual,
        "carrier-recorded delivery date": _as_date(
            details.get("carrier_recorded_delivery_date")
        ),
    }
    for label, actual_date in actual_dates.items():
        if actual_date and actual_date > date.today():
            errors.append(f"The {label} cannot be in the future.")
    if complaint_type == "late_delivery" and promised and actual and actual <= promised:
        errors.append("Actual delivery must be after the promised delivery date for a late-delivery complaint.")
    return errors


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def calculate_delay_days(promised_delivery_date, actual_delivery_date):
    """Return calendar days late when both dates are valid and delivery was late."""
    promised = _as_date(promised_delivery_date)
    actual = _as_date(actual_delivery_date)
    if not promised or not actual:
        return None
    return max((actual - promised).days, 0)


def recommend_late_delivery_fee_review(delay_days, policy_exclusions):
    """Return non-binding fictional-policy guidance for a human reviewer."""
    if delay_days is None or delay_days <= 0:
        return None
    if policy_exclusions:
        return "human_review_required_due_to_possible_policy_exclusion"
    if delay_days == 1:
        return "review_partial_delivery_fee_reimbursement"
    return "review_full_delivery_fee_reimbursement"


def validate_evidence_files(evidence_files):
    """Validate evidence limits for new cases."""
    errors = []
    evidence_files = list(evidence_files or [])
    if len(evidence_files) > MAX_EVIDENCE_FILES:
        errors.append(f"Upload no more than {MAX_EVIDENCE_FILES} evidence files.")

    total_size = 0
    for evidence_file in evidence_files:
        size_bytes = len(evidence_file.getvalue())
        total_size += size_bytes
        extension = Path(evidence_file.name).suffix.lower().lstrip(".")
        size_limit = (
            MAX_IMAGE_SIZE_BYTES
            if extension in IMAGE_EVIDENCE_TYPES
            else MAX_DOCUMENT_SIZE_BYTES
        )
        if size_bytes > size_limit:
            limit_mb = size_limit // (1024 * 1024)
            errors.append(
                f"{Path(evidence_file.name).name} exceeds the {limit_mb} MB "
                "limit for its file type."
            )
    if total_size > MAX_TOTAL_EVIDENCE_BYTES:
        errors.append("Combined evidence must not exceed 50 MB.")

    return errors


def normalize_evidence_files(evidence_files):
    """Return the normalized in-memory evidence contract."""
    evidence = []
    for evidence_file in evidence_files or []:
        file_data = evidence_file.getvalue()
        evidence.append({
            "file_name": Path(evidence_file.name).name,
            "content_type": evidence_file.type,
            "size_bytes": len(file_data),
            "data": file_data,
        })
    return evidence


def build_customer_complaint(
    claimant_role, tracking_number, country, delivery_date,
    declared_value, complaint_type, customer_email, additional_information,
    evidence_files, *, complaint_details=None, evidence_types=None
):
    """Create the normalized complaint contract used by persistence and workflow."""
    reported_at = datetime.now(timezone.utc)
    details = dict(complaint_details or {})
    normalized_details = {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value
        for key, value in details.items()
    }
    promised_date = normalized_details.get("promised_delivery_date")
    actual_date = normalized_details.get("actual_delivery_date")
    normalized_details["delay_duration_days"] = calculate_delay_days(
        promised_date, actual_date
    )
    normalized_details.setdefault("policy_exclusions", [])
    normalized_details["delay_cause_review_status"] = (
        "pending_carrier_record_review" if complaint_type == "late_delivery" else None
    )
    normalized_details["reimbursement_recommendation"] = (
        "requires_internal_cause_and_exclusion_review"
        if complaint_type == "late_delivery" else None
    )
    normalized_details["reimbursement_requires_human_review"] = True
    complaint = {
        "case_reference": f"CASE-{reported_at:%Y%m%d}-{uuid4().hex[:10].upper()}",
        "reported_at": reported_at.isoformat(),
        "status": "submitted",
        "claimant_role": claimant_role.strip().lower(),
        "tracking_number": tracking_number.strip(),
        "carrier": CONFIGURED_CARRIER,
        "country": country.strip(),
        "delivery_date": delivery_date.isoformat() if delivery_date else None,
        "declared_value": (
            format(Decimal(str(declared_value)), "f")
            if declared_value not in (None, "") else ""
        ),
        "complaint_type": complaint_type,
        "customer_email": customer_email.strip().lower(),
        "additional_information": additional_information.strip(),
        "evidence": normalize_evidence_files(evidence_files),
        "evidence_types": sorted(set(evidence_types or [])),
        "complaint_details": normalized_details,
        "intake_source": "web_form",
        "intake_completeness": (
            "complete"
            if set(COMPLAINT_REQUIREMENTS.get(complaint_type, {}).get(
                "required_evidence", ()
            )).issubset(set(evidence_types or []))
            else "incomplete"
        ),
        "downstream_processing_status": "not_connected",
    }
    return complaint
