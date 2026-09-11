from datetime import date
import unittest

from customer_intake import (
    CONFIGURED_CARRIER,
    DUPLICATE_CASE_MESSAGE,
    MAX_IMAGE_SIZE_BYTES,
    build_customer_complaint,
    calculate_delay_days,
    recommend_late_delivery_fee_review,
    validate_customer_submission,
)


class FakeUpload:
    def __init__(self, name, data=b"evidence", content_type="image/jpeg"):
        self.name = name
        self.type = content_type
        self._data = data

    def getvalue(self):
        return self._data


class CustomerIntakeTests(unittest.TestCase):
    def test_duplicate_message_matches_customer_contract(self):
        self.assertEqual(
            DUPLICATE_CASE_MESSAGE,
            "A case has already been opened for this tracking ID. Please wait for "
            "updates while we investigate.",
        )

    def test_damage_accepts_incomplete_evidence_but_requires_delivery_date(self):
        errors = validate_customer_submission(
            "TRACK-1", "Germany", None,
            "parcel_damage", "customer@example.com", []
        )
        self.assertTrue(any("delivery date" in error for error in errors))
        self.assertFalse(any("damaged item" in error for error in errors))
        self.assertFalse(any("external packaging" in error for error in errors))
        self.assertFalse(any("proof of value" in error for error in errors))

    def test_future_actual_delivery_date_is_rejected(self):
        errors = validate_customer_submission(
            "TRACK-1", "Germany", date.today().replace(year=date.today().year + 1),
            "parcel_damage", "customer@example.com", [],
            complaint_details={"declared_value": "50"}, evidence_types=[],
        )
        self.assertTrue(any("cannot be in the future" in error for error in errors))

    def test_declared_value_rejects_non_numeric_text(self):
        errors = validate_customer_submission(
            "TRACK-1", "Germany", date(2026, 8, 27), "parcel_damage",
            "customer@example.com", [],
            complaint_details={"declared_value": "757890i"}, evidence_types=[],
        )
        self.assertTrue(any("numbers only" in error for error in errors))

    def test_incomplete_evidence_is_recorded_for_policy_review(self):
        complaint = build_customer_complaint(
            "Recipient", "TRACK-1", "Germany", date(2026, 8, 27), "50",
            "parcel_damage", "customer@example.com", "", [],
            complaint_details={"declared_value": "50"},
            evidence_types=["damage_photo", "packaging_photo"],
        )
        self.assertEqual(complaint["intake_completeness"], "incomplete")

    def test_complete_damage_submission_passes_complaint_requirements(self):
        errors = validate_customer_submission(
            "TRACK-1", "Germany", date(2026, 8, 27),
            "parcel_damage", "customer@example.com",
            [FakeUpload("damage.jpg"), FakeUpload("packaging.jpg")],
            complaint_details={"declared_value": "50"},
            evidence_types=["damage_photo", "packaging_photo", "proof_of_value"],
        )
        self.assertEqual(errors, [])

    def test_lost_and_delivered_not_received_require_different_facts(self):
        lost_errors = validate_customer_submission(
            "TRACK-1", "Germany", None, "lost_parcel",
            "customer@example.com", [], complaint_details={}, evidence_types=[],
        )
        non_delivery_errors = validate_customer_submission(
            "TRACK-1", "Germany", None, "non_delivery",
            "customer@example.com", [], complaint_details={}, evidence_types=[],
        )
        self.assertTrue(any("latest tracking status" in item for item in lost_errors))
        self.assertFalse(any("recipient confirmation" in item for item in lost_errors))
        self.assertTrue(any("recipient confirmation" in item for item in non_delivery_errors))
        self.assertFalse(any("carrier tracking shows delivered" in item for item in non_delivery_errors))

    def test_late_delivery_calculation_and_human_review_guidance(self):
        self.assertEqual(
            calculate_delay_days(date(2026, 8, 20), date(2026, 8, 22)), 2
        )
        self.assertEqual(
            recommend_late_delivery_fee_review(1, []),
            "review_partial_delivery_fee_reimbursement",
        )
        self.assertEqual(
            recommend_late_delivery_fee_review(2, []),
            "review_full_delivery_fee_reimbursement",
        )
        self.assertEqual(
            recommend_late_delivery_fee_review(2, ["severe_weather"]),
            "human_review_required_due_to_possible_policy_exclusion",
        )

    def test_late_delivery_contract_contains_policy_review_fields(self):
        complaint = build_customer_complaint(
            "Recipient", "TRACK-2", "Germany", date(2026, 8, 22), "",
            "late_delivery", "customer@example.com", "Arrived late", [],
            complaint_details={
                "service_type": "NorthStar Express",
                "promised_duration_days": 3,
                "promised_delivery_date": date(2026, 8, 20),
                "actual_delivery_date": date(2026, 8, 22),
                "policy_exclusions": [],
            },
            evidence_types=["promised_delivery_evidence"],
        )
        details = complaint["complaint_details"]
        self.assertEqual(details["delay_duration_days"], 2)
        self.assertEqual(
            details["reimbursement_recommendation"],
            "requires_internal_cause_and_exclusion_review",
        )
        self.assertEqual(
            details["delay_cause_review_status"],
            "pending_carrier_record_review",
        )
        self.assertTrue(details["reimbursement_requires_human_review"])
        self.assertEqual(complaint["intake_source"], "web_form")
        self.assertEqual(complaint["intake_completeness"], "complete")

    def test_oversized_image_is_rejected(self):
        upload = FakeUpload("damage.jpg", b"x" * (MAX_IMAGE_SIZE_BYTES + 1))
        errors = validate_customer_submission(
            "TRACK-1", "Germany", date(2026, 8, 27),
            "parcel_damage", "customer@example.com", [upload]
        )
        self.assertTrue(any("10 MB" in error for error in errors))

    def test_normalized_contract_uses_canonical_values(self):
        complaint = build_customer_complaint(
            "Recipient", " TRACK-1 ", "Germany",
            date(2026, 8, 26), "899.00", "parcel_damage",
            " Customer@Example.com ", " Screen broken ",
            [FakeUpload("../damage.jpg")],
        )
        self.assertTrue(complaint["case_reference"].startswith("CASE-"))
        self.assertEqual(complaint["customer_email"], "customer@example.com")
        self.assertEqual(complaint["evidence"][0]["file_name"], "damage.jpg")
        self.assertEqual(complaint["delivery_date"], "2026-08-26")
        self.assertEqual(complaint["carrier"], CONFIGURED_CARRIER)

if __name__ == "__main__":
    unittest.main()
