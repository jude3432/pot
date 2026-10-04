import unittest
from unittest.mock import patch

import database.repository as repo


class OxaPayWebhookFixTests(unittest.TestCase):
    def test_only_paid_state_is_processed(self):
        with patch.object(repo.DatabaseManager, "get_connection") as get_connection:
            result = repo.complete_oxapay_payment_atomic(
                {"track_id": "track-1", "status": " Paying "}
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["ignored"])
        get_connection.assert_not_called()

    def test_paid_state_is_trimmed_and_case_insensitive(self):
        class Cursor:
            def execute(self, *args):
                self.last_query = args[0]

            def fetchone(self):
                return None

            def close(self):
                pass

        class Connection:
            def cursor(self):
                return Cursor()

            def rollback(self):
                pass

        with patch.object(repo.DatabaseManager, "get_connection", return_value=Connection()):
            result = repo.complete_oxapay_payment_atomic(
                {
                    "track_id": "track-1",
                    "status": "  PAID ",
                    "currency": "USDT",
                    "amount": "10.00000000",
                }
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "transaction_not_found")


if __name__ == "__main__":
    unittest.main()
