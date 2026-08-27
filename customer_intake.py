from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4


SUPPORTED_EVIDENCE_TYPES = ["pdf", "txt", "docx", "jpg", "jpeg", "png"]
IMAGE_EVIDENCE_TYPES = {"jpg", "jpeg", "png"}
CONFIGURED_CARRIER = "NorthStar Parcel"
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
EVIDENCE_REQUIREMENTS = {
    "parcel_damage": "At least one JPG or PNG photo of the damaged parcel or goods",
    "partial_loss": "At least one JPG or PNG photo of the parcel, contents, or packaging",
}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_customer_submission(
    tracking_number, country, delivery_date, complaint_type,
    customer_email, evidence_files
):
    """Return customer-facing validation errors without external side effects."""
    errors = []
    if not tracking_number.strip():
        errors.append("Enter your tracking number.")
    if country not in SUPPORTED_COUNTRIES:
        errors.append("Enter the destination country.")
    if delivery_date is None:
        errors.append("Enter the delivery or expected delivery date.")
    if complaint_type not in COMPLAINT_TYPE_LABELS:
        errors.append("Select what happened to your delivery.")
    if not EMAIL_PATTERN.fullmatch(customer_email.strip().lower()):
        errors.append("Enter a valid email address.")

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

    if complaint_type in EVIDENCE_REQUIREMENTS:
        has_image = any(
            Path(evidence_file.name).suffix.lower().lstrip(".")
            in IMAGE_EVIDENCE_TYPES
            for evidence_file in evidence_files
        )
        if not has_image:
            errors.append(EVIDENCE_REQUIREMENTS[complaint_type] + ".")
    return errors


def build_customer_complaint(
    claimant_role, tracking_number, country, delivery_date,
    declared_value, complaint_type, customer_email, additional_information,
    evidence_files
):
    """Create the normalized complaint contract used by persistence and workflow."""
    reported_at = datetime.now(timezone.utc)
    evidence = []
    for evidence_file in evidence_files or []:
        file_data = evidence_file.getvalue()
        evidence.append({
            "file_name": Path(evidence_file.name).name,
            "content_type": evidence_file.type,
            "size_bytes": len(file_data),
            "data": file_data,
        })
    return {
        "case_reference": f"CASE-{reported_at:%Y%m%d}-{uuid4().hex[:10].upper()}",
        "reported_at": reported_at.isoformat(),
        "status": "submitted",
        "claimant_role": claimant_role.strip().lower(),
        "tracking_number": tracking_number.strip(),
        "carrier": CONFIGURED_CARRIER,
        "country": country.strip(),
        "delivery_date": delivery_date.isoformat() if delivery_date else None,
        "declared_value": declared_value.strip(),
        "complaint_type": complaint_type,
        "customer_email": customer_email.strip().lower(),
        "additional_information": additional_information.strip(),
        "evidence": evidence,
        "downstream_processing_status": "not_connected",
    }
