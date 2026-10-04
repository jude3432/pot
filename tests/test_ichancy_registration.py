import unittest
from unittest.mock import Mock

from ichancy_api.client import IChancyClient


class RegistrationResponseTests(unittest.TestCase):
    def test_player_id_extractor_supports_nested_and_legacy_keys(self):
        self.assertEqual(IChancyClient._extract_player_id({"player": {"playerID": 123}}), "123")
        self.assertEqual(IChancyClient._extract_player_id({"data": [{"playerId": "456"}]}), "456")

    def test_official_business_error_is_returned_without_legacy_duplicate_request(self):
        client = IChancyClient.__new__(IChancyClient)
        client._official_request = Mock(return_value={
            "status": False,
            "result": "ex",
            "notification": [{"content": "بيانات التسجيل غير صالحة"}],
        })
        client._normalize_agent_id = IChancyClient._normalize_agent_id
        client._official_player_id = Mock()
        client.session = Mock()
        client._official_api_base_url = "https://agents.example.test"

        result = client._register_account("new_user_123", "safe-password", "new_user_123@gmail.com")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "بيانات التسجيل غير صالحة")
        client.session.post.assert_not_called()

    def test_official_success_still_returns_player_details(self):
        client = IChancyClient.__new__(IChancyClient)
        client._official_request = Mock(return_value={
            "status": True,
            "result": {"playerId": "98765"},
        })
        client._official_player_id = Mock(return_value="98765")
        client._normalize_agent_id = IChancyClient._normalize_agent_id
        client._official_api_base_url = "https://agents.example.test"

        result = client._register_account("new_user_123", "safe-password", "new_user_123@gmail.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["player_id"], "98765")

    def test_access_denial_uses_legacy_endpoint_once(self):
        client = IChancyClient.__new__(IChancyClient)
        client._official_request = Mock(return_value={
            "status": False,
            "result": "ex",
            "notification": [{"content": "You have not access to add player"}],
        })
        client._normalize_agent_id = IChancyClient._normalize_agent_id
        client._official_player_id = Mock()
        client._get_player_id = Mock(return_value="98765")
        client._official_api_base_url = "https://agents.example.test"

        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.text = '{"result": {"playerId": "98765"}}'
        response.json.return_value = {"result": {"playerId": "98765"}}
        client.session = Mock()
        client.session.post.return_value = response

        result = client._register_account("new_user_123", "safe-password", "new_user_123@gmail.com")

        self.assertTrue(result["success"])
        self.assertEqual(result["player_id"], "98765")
        self.assertEqual(client.session.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
