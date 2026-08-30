import json
import unittest
from unittest.mock import patch

from case_handoff import (
    CaseHandoffError,
    build_customer_case_handoff_event,
    build_customer_case_update_event,
    build_handoff_event,
    send_customer_case_to_make,
    send_customer_case_update_to_make,
    send_case_to_make,
)
from incident_case import IncidentCase


def make_case():
    return IncidentCase(
        case_id="case-f717c1c08bbc2d65",
        schema_version="2.0",
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
    )


class CaseHandoffTests(unittest.TestCase):
    def test_customer_update_targets_existing_jira_issue(self):
        case_update = {
            "case_reference": "CASE-20260826-ABC123",
            "update_reference": "UPDATE-ABC123",
            "new_additional_information": "Added the purchase receipt.",
            "evidence": [{
                "evidence_id": 9,
                "file_name": "receipt.pdf",
                "content_type": "application/pdf",
                "size_bytes": 4321,
                "evidence_kind": "document",
                "processing_status": "indexed",
                "document_id": 12,
                "s3_object_key": "customer-cases/CASE/evidence/receipt.pdf",
            }],
        }
        event = build_customer_case_update_event(
            case_update,
            jira_result={"issue_key": "OPS-42"},
            download_url_factory=lambda _key: "https://signed.example/receipt",
            sent_at="2026-08-28T10:00:00Z",
        )
        self.assertEqual(event["event_type"], "saidia.customer_case.updated")
        self.assertEqual(event["event_id"], "customer-update-UPDATE-ABC123")
        self.assertEqual(event["jira"]["issue_key"], "OPS-42")
        self.assertEqual(
            event["update"]["evidence"][0]["attachment_download_url"],
            "https://signed.example/receipt",
        )
        self.assertEqual(
            event["update"]["evidence"][0]["attachment_url_expires_in_seconds"],
            3600,
        )

    def test_customer_update_reuses_update_idempotency_key(self):
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs)
            return 200, '{"issue_key":"OPS-42","status":"To Do"}'

        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_customer_case_update_to_make(
                {
                    "case_reference": "CASE-1",
                    "update_reference": "UPDATE-1",
                    "additional_information": "New detail",
                    "evidence": [],
                },
                jira_result={"issue_key": "OPS-42"},
                download_url_factory=lambda _key: "https://signed.example/file",
                post_request=fake_post,
            )
        self.assertEqual(
            captured["headers"]["Idempotency-Key"],
            "customer-update-UPDATE-1",
        )
        self.assertEqual(receipt["jira_result"]["issue_key"], "OPS-42")

    def test_customer_handoff_attaches_images_without_ai_interpretation(self):
        customer_case = {
            "case_reference": "CASE-20260826-ABC123",
            "reported_at": "2026-08-26T10:00:00+00:00",
            "status": "submitted",
            "claimant_role": "recipient",
            "tracking_number": "TRACK-123",
            "complaint_type": "parcel_damage",
            "customer_email": "customer@example.com",
            "additional_information": "Screen appears broken.",
            "downstream_processing_status": "evidence_processed",
            "evidence": [{
                "id": 7,
                "original_file_name": "damage.jpg",
                "content_type": "image/jpeg",
                "size_bytes": 1234,
                "evidence_kind": "image",
                "processing_status": "ready_for_human_review",
                "document_id": None,
                "s3_object_key": "customer-cases/CASE-20260826-ABC123/evidence/damage.jpg",
            }],
        }
        event = build_customer_case_handoff_event(
            customer_case,
            download_url_factory=lambda _key: "https://signed.example/evidence",
            sent_at="2026-08-26T11:00:00Z",
        )

        self.assertEqual(event["event_version"], "1.0")
        self.assertEqual(event["case"]["final_decision_owner"], "human_reviewer")
        self.assertNotIn("customer_email", event["case"])
        self.assertNotIn("ai_observations", event)
        self.assertNotIn("s3_object_key", event["evidence"][0])
        self.assertEqual(
            event["evidence"][0]["attachment_download_url"],
            "https://signed.example/evidence",
        )
        self.assertEqual(
            event["evidence"][0]["attachment_url_expires_in_seconds"],
            3600,
        )

    def test_customer_handoff_uses_stable_idempotency_key(self):
        customer_case = {
            "case_reference": "CASE-20260826-ABC123",
            "reported_at": "2026-08-26T10:00:00+00:00",
            "status": "submitted",
            "claimant_role": "recipient",
            "tracking_number": "TRACK-123",
            "complaint_type": "late_delivery",
            "customer_email": "customer@example.com",
            "additional_information": "Late.",
            "downstream_processing_status": "evidence_processed",
            "evidence": [],
        }
        captured = {}

        def fake_post(_url, **kwargs):
            captured.update(kwargs)
            return 200, "{}"

        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_customer_case_to_make(
                customer_case,
                download_url_factory=lambda _key: "https://signed.example/evidence",
                post_request=fake_post,
            )

        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(
            captured["headers"]["Idempotency-Key"],
            "customer-handoff-CASE-20260826-ABC123",
        )

    def test_processed_event_contains_versioned_case_payload(self):
        event = build_handoff_event(
            make_case(),
            sent_at="2026-08-15T12:00:00Z",
        )

        self.assertEqual(event["event_type"], "saidia.case.processed")
        self.assertEqual(event["event_version"], "2.0")
        self.assertEqual(
            event["event_id"],
            "handoff-case-f717c1c08bbc2d65",
        )
        self.assertNotIn("approval_status", event["case"])

    def test_processed_case_posts_json_and_returns_receipt(self):
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
            captured_request["event"]["case"]["evidence_supplied"],
            ["Commercial invoice"],
        )
        self.assertEqual(
            captured_request["headers"]["Idempotency-Key"],
            "handoff-case-f717c1c08bbc2d65",
        )
        self.assertEqual(captured_request["timeout"], 15)

    def test_json_response_returns_recruiter_safe_jira_result(self):
        response = json.dumps({
            "jira_result": {
                "issue_key": "OPS-42",
                "title": "Review damaged parcel INC-002",
                "routing": "Claims Operations",
                "status": "To Do",
                "recommended_action": "Request packaging photograph",
                "jira_url": "https://example.atlassian.net/browse/OPS-42",
                "internal_only": "not exposed",
            }
        })
        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_case_to_make(
                make_case(),
                post_request=lambda *_args, **_kwargs: (200, response),
            )

        self.assertEqual(receipt["jira_result"]["issue_key"], "OPS-42")
        self.assertNotIn("internal_only", receipt["jira_result"])

    def test_incomplete_jira_browse_url_is_completed_with_issue_key(self):
        response = json.dumps({
            "jira_result": {
                "issue_key": "KAN-15",
                "jira_url": "https://saidia-logistics.atlassian.net/browse/",
            }
        })
        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_case_to_make(
                make_case(),
                post_request=lambda *_args, **_kwargs: (200, response),
            )

        self.assertEqual(
            receipt["jira_result"]["jira_url"],
            "https://saidia-logistics.atlassian.net/browse/KAN-15",
        )

    def test_jira_url_is_hidden_without_issue_key(self):
        response = json.dumps({
            "jira_result": {
                "jira_url": "https://saidia-logistics.atlassian.net/browse/",
            }
        })
        with patch(
            "case_handoff._read_make_webhook_url",
            return_value="https://hook.eu2.make.com/example",
        ):
            receipt = send_case_to_make(
                make_case(),
                post_request=lambda *_args, **_kwargs: (200, response),
            )

        self.assertNotIn("jira_result", receipt)

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
