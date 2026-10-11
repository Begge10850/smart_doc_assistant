import unittest
from datetime import date
from unittest.mock import patch

from policy_store import search_carrier_policies


DAMAGE_POLICY_ROW = (
    1,
    "northstar-parcel-damage-eu-v1",
    "NorthStar Parcel EU Damage Claim Policy",
    "NorthStar Parcel",
    ["France", "Germany"],
    "parcel_damage",
    date(2026, 1, 1),
    7,
    "calendar days after delivery",
    [],
    [
        "tracking number",
        "delivery date",
        "photograph of the external packaging",
    ],
    ["Require human review."],
    "A fictional damaged-parcel evaluation policy.",
    True,
)


class PolicyStoreTests(unittest.TestCase):
    def test_northstar_france_damage_policy_is_found(self):
        with patch("policy_store.find_carrier_policies", return_value=[DAMAGE_POLICY_ROW]):
            result = search_carrier_policies(
                "NorthStar Parcel",
                "France",
                "parcel_damage",
            )

        self.assertEqual(result["match_count"], 1)
        policy = result["policies"][0]
        self.assertEqual(policy["reporting_window_days"], 7)
        self.assertIn(
            "photograph of the external packaging",
            policy["required_evidence"],
        )
        self.assertTrue(policy["fictional_evaluation_policy"])

    def test_unknown_carrier_returns_no_policy(self):
        with patch("policy_store.find_carrier_policies", return_value=[]):
            result = search_carrier_policies(
                "Unknown Carrier",
                "France",
                "parcel_damage",
            )

        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["policies"], [])

    def test_explicit_northstar_company_alias_matches_policy(self):
        with patch(
            "policy_store.find_carrier_policies",
            return_value=[DAMAGE_POLICY_ROW],
        ) as lookup:
            result = search_carrier_policies(
                "Northstar Parcel Co.",
                "France",
                "parcel_damage",
            )

        self.assertEqual(result["match_count"], 1)
        self.assertEqual(
            result["policies"][0]["carrier"],
            "NorthStar Parcel",
        )
        lookup.assert_called_once_with(
            carrier="NorthStar Parcel",
            country="France",
            incident_type="parcel_damage",
        )

    def test_similar_unlisted_carrier_does_not_match(self):
        with patch("policy_store.find_carrier_policies", return_value=[]) as lookup:
            result = search_carrier_policies(
                "Northstar Parcels",
                "France",
                "parcel_damage",
            )

        self.assertEqual(result["match_count"], 0)
        lookup.assert_called_once_with(
            carrier="Northstar Parcels",
            country="France",
            incident_type="parcel_damage",
        )


if __name__ == "__main__":
    unittest.main()
