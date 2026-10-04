import asyncio
import unittest
from datetime import datetime, timezone

from integrations import syriatel_cash as api


class SyriatelCashApiTests(unittest.TestCase):
    def test_transaction_number_match_requires_exact_amount(self):
        tx = {"transaction_no": "123456", "amount": "25000", "date": "2026-09-21 08:00:00"}
        result = api.find_matching_transaction([tx], 25000, "123-456")
        self.assertTrue(result["ok"])
        self.assertEqual(result["external_ref"], "123456")
        self.assertFalse(api.find_matching_transaction([tx], 25001, "123456")["ok"])

    def test_phone_match_uses_api_from_field(self):
        tx = {"transaction_no": "123456", "amount": "25000", "from": "0933123456"}
        self.assertTrue(api.find_matching_transaction([tx], 25000, "0933 123456")["ok"])
        self.assertFalse(api.find_matching_transaction([tx], 25000, "0933123457")["ok"])

    def test_invalid_transaction_number_is_rejected_before_api_call(self):
        original = api._request
        calls = []

        async def fake_request(params):
            calls.append(params)
            return {"ok": True, "data": {}}

        api._request = fake_request
        try:
            result = asyncio.run(api.find_transaction("abc", query="0933123456"))
        finally:
            api._request = original
        self.assertEqual(result["reason"], "invalid_transaction_number")
        self.assertEqual(calls, [])

    def test_find_tx_response_validates_amount_and_time(self):
        original = api._request
        old_token = getattr(api.settings, "SYRIATEL_API_TOKEN", None)
        old_query = getattr(api.settings, "SYRIATEL_API_QUERY", None)
        api.settings.SYRIATEL_API_TOKEN = "test-token"
        api.settings.SYRIATEL_API_QUERY = "0933123456"

        async def fake_request(params):
            return {
                "ok": True,
                "data": {
                    "success": True,
                    "data": {
                        "found": True,
                        "transaction": {
                            "transaction_no": "123456",
                            "amount": "25000",
                            "date": "2026-09-21 08:00:00",
                        },
                    },
                },
            }

        api._request = fake_request
        try:
            ok = asyncio.run(api.verify_incoming_deposit(
                25000, "123456", datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
            ))
            bad = asyncio.run(api.verify_incoming_deposit(
                25001, "123456", datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
            ))
        finally:
            api._request = original
            api.settings.SYRIATEL_API_TOKEN = old_token
            api.settings.SYRIATEL_API_QUERY = old_query
        self.assertTrue(ok["ok"])
        self.assertEqual(bad["reason"], "amount_mismatch")

    def test_shamcash_match_requires_currency_and_amount(self):
        tx = {
            "tran_id": "987654321",
            "currency": "SYP",
            "amount": 25000,
            "datetime": "2026-09-21 08:00:00",
        }
        ok = api.find_matching_shamcash_transaction([tx], 25000, "SYP", user_reference="987654321")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["external_ref"], "987654321")
        self.assertEqual(
            api.find_matching_shamcash_transaction([tx], 25, "USD", user_reference="987654321")["reason"],
            "not_found",
        )

    def test_shamcash_find_tx_uses_account_address(self):
        original = api._request

        async def fake_request(params):
            self.assertEqual(params["resource"], "shamcash")
            self.assertEqual(params["action"], "find_tx")
            self.assertEqual(params["account_address"], "251a-test")
            return {
                "ok": True,
                "data": {"success": True, "data": {"found": False, "tran_id": "987654321"}},
            }

        api._request = fake_request
        try:
            result = asyncio.run(api.find_shamcash_transaction("987654321", "251a-test"))
        finally:
            api._request = original
        self.assertEqual(result["reason"], "not_found")


if __name__ == "__main__":
    unittest.main()
