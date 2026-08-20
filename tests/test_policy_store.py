import unittest

from policy_store import search_carrier_policies


class PolicyStoreTests(unittest.TestCase):
    def test_northstar_france_damage_policy_is_found(self):
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
        result = search_carrier_policies(
            "Unknown Carrier",
            "France",
            "parcel_damage",
        )

        self.assertEqual(result["match_count"], 0)
        self.assertEqual(result["policies"], [])

    def test_explicit_northstar_company_alias_matches_policy(self):
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

    def test_similar_unlisted_carrier_does_not_match(self):
        result = search_carrier_policies(
            "Northstar Parcels",
            "France",
            "parcel_damage",
        )

        self.assertEqual(result["match_count"], 0)


if __name__ == "__main__":
    unittest.main()
