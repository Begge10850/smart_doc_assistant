import json
from pathlib import Path
import unittest


class PolicyFixtureTests(unittest.TestCase):
    def test_northstar_has_one_policy_for_every_supported_complaint(self):
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "policies"
            / "carrier_policies.json"
        )
        policies = json.loads(fixture_path.read_text(encoding="utf-8"))
        by_type = {policy["incident_type"]: policy for policy in policies}
        self.assertEqual(
            set(by_type),
            {
                "parcel_damage",
                "lost_parcel",
                "late_delivery",
                "partial_loss",
                "non_delivery",
            },
        )
        for policy in policies:
            self.assertEqual(policy["carrier"], "NorthStar Parcel")
            self.assertEqual(set(policy["countries"]), {"Germany", "France"})
            self.assertTrue(policy["fictional_evaluation_policy"])
            self.assertTrue(policy["required_evidence"])


if __name__ == "__main__":
    unittest.main()
