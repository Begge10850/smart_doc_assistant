from datetime import date
import unittest

from customer_intake import (
    CONFIGURED_CARRIER,
    MAX_IMAGE_SIZE_BYTES,
    build_customer_complaint,
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
    def test_damage_requires_photo_and_delivery_date(self):
        errors = validate_customer_submission(
            "TRACK-1", "Germany", None,
            "parcel_damage", "customer@example.com", []
        )
        self.assertTrue(any("delivery date" in error for error in errors))
        self.assertTrue(any("JPG or PNG" in error for error in errors))

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
            date(2026, 8, 26), " EUR 899.00 ", "parcel_damage",
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
