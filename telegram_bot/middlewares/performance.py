import logging
import time

from aiogram import BaseMiddleware


logger = logging.getLogger(__name__)


class SlowUpdateMiddleware(BaseMiddleware):
    """Log slow Telegram updates without recording message contents or IDs."""

    def __init__(self, threshold_ms=750):
        self.threshold_ms = float(threshold_ms)

    async def __call__(self, handler, event, data):
        started_at = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if elapsed_ms >= self.threshold_ms:
                logger.warning(
                    "Slow Telegram update: type=%s elapsed_ms=%.1f",
                    type(event).__name__,
                    elapsed_ms,
                )