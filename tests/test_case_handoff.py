import unittest
from unittest.mock import patch

from case_handoff import (
    CaseHandoffError,
    build_handoff_event,
    send_case_to_make,
)
from incident_case import IncidentCase


def make_case(status="approved"):
    return IncidentCase(
        case_id="case-f717c1c08bbc2d65",
        schema_version="1.0",
        source_file="INC-002_incomplete_damage.pdf",
        source_document_hash="abc123",
        incident_id="INC-002",
        tracking_number="NSP-FR-20260809-7821",
        carrier="NorthStar Parcel",
        country="France",
        incident_type="parcel_damage",
        delivery_date="2026-08-09",
        reported_date="2026-08-14",
        declared_value="EUR 89.90",
        evidence_supplied=["Commercial invoice"],
        factual_summary="Two glass jars were broken on arrival.",
        unresolved_fields=[],
        policy_match_status="matched",
        policy_id="northstar-parcel-damage-eu-v1",
        policy_title="NorthStar Parcel EU Damage Claim Policy",
        policy_is_fictional=True,
        claim_deadline="2026-08-16",
        reported_on_time=True,
        required_evidence=["photograph of the external packaging"],
        missing_required_evidence=["photograph of the external packaging"],
        recommended_next_action="Request the missing photograph.",
        approval_status=status,
        reviewer_note="Reviewed for internal handoff.",
    )


class CaseHandoffTests(unittest.TestCase):
    def test_draft_case_cannot_build_external_event(self):
        with self.assertRaises(CaseHandoffError):
            build_handoff_event(make_case("draft"))

    def test_approved_event_contains_versioned_case_payload(self):
        event = build_handoff_event(
            make_case(),
            sent_at="2026-08-15T12:00:00Z",
        )

        self.assertEqual(event["event_type"], "saidia.case.approved")
        self.assertEqual(event["event_version"], "1.0")
        self.assertEqual(
            event["event_id"],
            "handoff-case-f717c1c08bbc2d65",
        )
        self.assertEqual(event["case"]["approval_status"], "approved")

    def test_approved_case_posts_json_and_returns_receipt(self):
        captured_request = {}

        def fake_post(url, **kwargs):
            captured_request["url"] = url
            captured_request.update(kwargs)
            return 200, "Accepted"

        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_case_to_make(make_case(), post_request=fake_post)

        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["http_status"], 200)
        self.assertEqual(
            captured_request["event"]["case"]["incident_id"],
            "INC-002",
        )
        self.assertEqual(
            captured_request["headers"]["Idempotency-Key"],
            "handoff-case-f717c1c08bbc2d65",
        )
        self.assertEqual(captured_request["timeout"], 15)

    def test_make_rejection_becomes_safe_handoff_error(self):
        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            with self.assertRaisesRegex(CaseHandoffError, "HTTP status 429"):
                send_case_to_make(
                    make_case(),
                    post_request=lambda *_args, **_kwargs: (
                        429,
                        "Too many requests",
                    ),
                )

    def test_timeout_warns_user_to_check_history_before_retrying(self):
        def timed_out(*_args, **_kwargs):
            raise TimeoutError("test timeout")

        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            with self.assertRaisesRegex(CaseHandoffError, "scenario history"):
                send_case_to_make(make_case(), post_request=timed_out)


if __name__ == "__main__":
    unittest.main()
