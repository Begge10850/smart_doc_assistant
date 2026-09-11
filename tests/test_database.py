import unittest
from unittest.mock import MagicMock, patch

import psycopg
from psycopg.pq import DiagnosticField

from database import DuplicateActiveShipmentError, create_customer_case


class DatabaseTests(unittest.TestCase):
    def test_active_shipment_unique_conflict_uses_duplicate_domain_error(self):
        violation = psycopg.errors.UniqueViolation(
            info={
                DiagnosticField.CONSTRAINT_NAME:
                    b"customer_cases_one_active_shipment_uidx"
            }
        )
        cursor = MagicMock()
        cursor.execute.side_effect = violation
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        complaint = {
            "complaint_details": {},
            "evidence_types": [],
            "evidence": [],
        }
        with patch("database.get_database_url", return_value="postgresql://unused"):
            with patch("database.psycopg.connect", return_value=connection):
                with self.assertRaises(DuplicateActiveShipmentError):
                    create_customer_case(complaint)


if __name__ == "__main__":
    unittest.main()
