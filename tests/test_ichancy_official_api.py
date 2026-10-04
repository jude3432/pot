import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests
from ichancy_api.client import IChancyClient


def api_response(status_code, payload):
    response = Mock()
    response.status_code = status_code
    response.headers = {"content-type": "application/json"}
    response.text = json.dumps(payload)
    response.json.return_value = payload
    return response


def client_with_mock_session():
    client = IChancyClient.__new__(IChancyClient)
    client._official_lock = threading.RLock()
    client._official_api_base_url = "https://agents.example.test"
    client._official_access_token = "access-old"
    client._official_refresh_token = "refresh-old"
    client._official_access_token_expires_at = time.time() + 300
    client._official_last_auth_error = None
    client.session = Mock()
    client.session.cookies = Mock()
    return client


class OfficialApiAuthenticationTests(unittest.TestCase):
    def test_http_request_does_not_send_or_retain_preloaded_cookie(self):
        client = client_with_mock_session()
        client.session = requests.Session()
        client.session.cookies.set("legacy_session", "must-not-be-sent")
        captured = {}

        def capture_send(prepared_request, **kwargs):
            captured["request"] = prepared_request
            response = requests.Response()
            response.status_code = 200
            response.headers["content-type"] = "application/json"
            response._content = b'{"status":true,"result":1}'
            response.url = prepared_request.url
            response.request = prepared_request
            return response

        client.session.send = capture_send
        response = client._post_json_without_cookies(
            "https://agents.example.test/global/api/UserApi/test",
            {},
            headers={"Authorization": "Bearer access-old"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cookie", captured["request"].headers)
        self.assertEqual(len(client.session.cookies), 0)

    def test_requests_use_bearer_and_refresh_after_result_ex_without_cookies(self):
        client = client_with_mock_session()
        client.session.post.side_effect = [
            api_response(201, {"status": False, "result": "ex"}),
            api_response(200, {"status": True, "result": 1}),
        ]

        def rotate_tokens():
            client._official_access_token = "access-new"
            client._official_refresh_token = "refresh-new"
            return True

        client._official_refresh = Mock(side_effect=rotate_tokens)

        result = client._official_request(
            "/global/api/UserApi/depositToPlayer",
            {"playerId": "123", "amount": 25},
            "test deposit",
        )

        self.assertEqual(result["result"], 1)
        self.assertEqual(client.session.post.call_count, 2)
        first_headers = client.session.post.call_args_list[0].kwargs["headers"]
        second_headers = client.session.post.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer access-old")
        self.assertEqual(second_headers["Authorization"], "Bearer access-new")
        client._official_refresh.assert_called_once_with()
        self.assertEqual(client.session.cookies.clear.call_count, 4)

    def test_refresh_saves_rotated_token_pair(self):
        client = client_with_mock_session()
        client.session.post.return_value = api_response(200, {
            "status": True,
            "result": {"accessToken": "access-rotated", "refreshToken": "refresh-rotated"},
        })
        client._save_official_tokens = Mock()

        self.assertTrue(client._official_refresh())

        call = client.session.post.call_args
        self.assertEqual(call.kwargs["json"], {"refreshToken": "refresh-old"})
        self.assertEqual(client._save_official_tokens.call_args.args[:2], ("access-rotated", "refresh-rotated"))
        self.assertGreater(client._save_official_tokens.call_args.args[2], time.time())
        self.assertEqual(client.session.cookies.clear.call_count, 2)

    def test_agent_balance_uses_official_wallet_route(self):
        client = client_with_mock_session()
        client._official_request = Mock(return_value={
            "status": True,
            "result": [
                {"currencyCode": "USD", "balance": 9},
                {"currencyCode": "NSP", "balance": 12345},
            ],
        })

        self.assertEqual(client._get_admin_balance(), 12345)
        client._official_request.assert_called_once_with(
            "/global/api/UserApi/getAgentAllWallets",
            {},
            "official getAgentAllWallets",
        )

    def test_player_search_uses_documented_exact_username_filter(self):
        client = client_with_mock_session()
        client._official_request = Mock(return_value={
            "status": True,
            "result": {"records": [{"username": "example", "playerId": "987"}]},
        })

        self.assertEqual(client._official_player_id("example"), "987")
        client._official_request.assert_called_once_with(
            "/global/api/UserApi/getPlayersForCurrentAgent",
            {
                "start": 0,
                "limit": 20,
                "filter": {
                    "userName": {
                        "action": "=",
                        "value": "example",
                        "valueLabel": "example",
                    }
                },
            },
            "official player search",
        )


    def test_independent_official_requests_are_not_serialized_by_network_wait(self):
        client = client_with_mock_session()
        rendezvous = threading.Barrier(2)

        def wait_for_other_request(*args, **kwargs):
            rendezvous.wait(timeout=2)
            return api_response(200, {"status": True, "result": 1})

        client._post_json_without_cookies = Mock(side_effect=wait_for_other_request)
        with ThreadPoolExecutor(max_workers=2) as executor:
            requests = [
                executor.submit(client._official_request, "/test", {"n": n}, f"test {n}")
                for n in range(2)
            ]
            results = [request.result(timeout=3) for request in requests]

        self.assertEqual([result["result"] for result in results], [1, 1])

    def test_worker_threads_get_separate_http_sessions(self):
        client = client_with_mock_session()
        client.session = requests.Session()
        client._session_local = threading.local()
        rendezvous = threading.Barrier(2)

        def session_for_worker(_):
            session = client._request_session()
            rendezvous.wait(timeout=2)
            return session

        with ThreadPoolExecutor(max_workers=2) as executor:
            sessions = list(executor.map(session_for_worker, range(2)))

        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(sessions[0].headers["User-Agent"], sessions[1].headers["User-Agent"])

    def test_transfer_verification_checks_immediately_then_uses_short_retry(self):
        client = IChancyClient.__new__(IChancyClient)
        client.get_player_balance = AsyncMock(side_effect=[100, 125])

        async def run_check():
            with patch("ichancy_api.client.settings.ICHANCY_TRANSFER_VERIFY_ATTEMPTS", 2), \
                    patch("ichancy_api.client.settings.ICHANCY_TRANSFER_VERIFY_DELAY_SECONDS", 0.25), \
                    patch("ichancy_api.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
                verified, balance = await client._verify_transfer_balance("123", 100, 25, "deposit")
                self.assertTrue(verified)
                self.assertEqual(balance, 125)
                sleep.assert_awaited_once_with(0.25)

        import asyncio
        asyncio.run(run_check())

    def test_transfer_verification_does_not_poll_without_baseline(self):
        client = IChancyClient.__new__(IChancyClient)
        client.get_player_balance = AsyncMock()

        async def run_check():
            verified, balance = await client._verify_transfer_balance("123", None, 25, "deposit")
            self.assertFalse(verified)
            self.assertIsNone(balance)
            client.get_player_balance.assert_not_awaited()

        import asyncio
        asyncio.run(run_check())


if __name__ == "__main__":
    unittest.main()