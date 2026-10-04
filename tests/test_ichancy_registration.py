import unittest
from unittest.mock import Mock

from ichancy_api.client import IChancyClient


class RegistrationResponseTests(unittest.TestCase):
    def test_player_id_extractor_supports_nested_response_keys(self):
        self.assertEqual(IChancyClient._extract_player_id({"player": {"playerID": 123}}), "123")
        self.assertEqual(IChancyClient._extract_player_id({"data": [{"playerId": "456"}]}), "456")

    def test_official_registration_error_does_not_fall_back(self):
        client = IChancyClient.__new__(IChancyClient)
        client._official_request = Mock(return_value={
            "status": False,
            "result": "ex",
            "notification": [{"content": "بيانات التسجيل غير صالحة"}],
        })
        client._normalize_agent_id = IChancyClient._normalize_agent_id
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

    def test_access_denial_is_returned_without_cookie_endpoint_retry(self):
        client = IChancyClient.__new__(IChancyClient)
        client._official_request = Mock(return_value={
            "status": False,
            "result": "ex",
            "notification": [{"content": "You have not access to add player"}],
        })
        client._normalize_agent_id = IChancyClient._normalize_agent_id
        client._official_api_base_url = "https://agents.example.test"
        client.session = Mock()

        result = client._register_account("new_user_123", "safe-password", "new_user_123@gmail.com")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "You have not access to add player")
        client.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
