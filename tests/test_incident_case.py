import unittest

from incident_case import (
    IncidentCaseError,
    build_case_id,
    build_incident_case,
    review_incident_case,
)


FACTS = {
    "incident_id": "INC-002",
    "tracking_number": "NSP-FR-20260809-7821",
    "carrier": "NorthStar Parcel",
    "country": "France",
    "incident_type": "parcel_damage",
    "delivery_date": "2026-08-09",
    "reported_date": "2026-08-14",
    "declared_value": "EUR 89.90",
    "evidence_supplied": [
        "Commercial invoice",
        "Photograph of the damaged item",
    ],
    "factual_summary": "Two glass jars were reported broken on arrival.",
    "unresolved_fields": [],
}

POLICY_RESULT = {
    "match_count": 1,
    "policies": [
        {
            "policy_id": "northstar-parcel-damage-eu-v1",
            "title": "NorthStar Parcel EU Damage Claim Policy",
            "reporting_window_days": 7,
            "required_evidence": [
                "tracking number",
                "delivery date",
                "damage report date",
                "commercial invoice or other proof of value",
                "photograph of the damaged item",
                "photograph of the external packaging",
            ],
            "fictional_evaluation_policy": True,
        }
    ],
}


class IncidentCaseTests(unittest.TestCase):
    def test_case_id_is_stable_for_same_document_and_incident(self):
        first = build_case_id("abc123", "INC-002")
        second = build_case_id("abc123", "inc-002")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("case-"))

    def test_policy_deadline_and_missing_evidence_are_deterministic(self):
        incident_case = build_incident_case(
            FACTS,
            source_file="INC-002.pdf",
            source_document_hash="abc123",
            policy_result=POLICY_RESULT,
        )

        self.assertEqual(incident_case.claim_deadline, "2026-08-16")
        self.assertTrue(incident_case.reported_on_time)
        self.assertEqual(
            incident_case.missing_required_evidence,
            ["photograph of the external packaging"],
        )
        self.assertEqual(incident_case.approval_status, "draft")

    def test_human_review_changes_status_without_changing_facts(self):
        incident_case = build_incident_case(
            FACTS,
            source_file="INC-002.pdf",
            source_document_hash="abc123",
            policy_result=POLICY_RESULT,
        )
        approved = review_incident_case(
            incident_case,
            "approved",
            "Ready for handoff after the missing photo arrives.",
        )

        self.assertEqual(approved.approval_status, "approved")
        self.assertEqual(approved.tracking_number, incident_case.tracking_number)
        self.assertEqual(incident_case.approval_status, "draft")

    def test_declared_value_alone_is_not_proof_of_value(self):
        facts_without_invoice = dict(FACTS)
        facts_without_invoice["evidence_supplied"] = [
            "Photograph of damaged items"
        ]
        incident_case = build_incident_case(
            facts_without_invoice,
            source_file="INC-002.pdf",
            source_document_hash="abc123",
            policy_result=POLICY_RESULT,
        )

        self.assertIn(
            "commercial invoice or other proof of value",
            incident_case.missing_required_evidence,
        )

    def test_invalid_approval_status_is_rejected(self):
        incident_case = build_incident_case(
            FACTS,
            source_file="INC-002.pdf",
            source_document_hash="abc123",
            policy_result=POLICY_RESULT,
        )
        with self.assertRaises(IncidentCaseError):
            review_incident_case(incident_case, "sent")


if __name__ == "__main__":
    unittest.main()
