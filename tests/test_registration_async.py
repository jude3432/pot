import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_bot.handlers import menu


class RegistrationHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_handler_awaits_async_client_result(self):
        message = SimpleNamespace(
            text="safe-password",
            from_user=SimpleNamespace(id=12345, username="tester", first_name="Tester"),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"ichancy_username": "newplayer"}),
            clear=AsyncMock(),
        )
        expected = {"success": False, "error": "test rejection"}

        with patch.object(
            menu.ichancy_api_client,
            "register_account",
            new_callable=AsyncMock,
            return_value=expected,
        ) as register_account, patch.object(
            menu,
            "get_user_menu_keyboard",
            return_value=None,
        ):
            await menu.process_ichancy_password(message, state)

        register_account.assert_awaited_once_with(
            "newplayer", "safe-password", "newplayer@gmail.com"
        )
        self.assertGreaterEqual(message.answer.await_count, 2)
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
