import unittest
from unittest.mock import patch

from telegram_bot.middlewares.performance import SlowUpdateMiddleware


class SlowUpdateMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_duration_without_event_payload(self):
        middleware = SlowUpdateMiddleware(threshold_ms=0)
        event = object()

        async def handler(received_event, data):
            self.assertIs(received_event, event)
            self.assertEqual(data, {})
            return "done"

        with patch("telegram_bot.middlewares.performance.logger.warning") as warning:
            result = await middleware(handler, event, {})

        self.assertEqual(result, "done")
        warning.assert_called_once()
        self.assertIn("elapsed_ms", warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()