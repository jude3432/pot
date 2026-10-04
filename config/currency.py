"""Canonical currency units used by the bot.

All bot balances and SYP amounts stored in the database are NEW SYP.
Ichancy still exposes the legacy denomination, so game transfers use the
explicit conversion helpers below. Never do ad-hoc *100 or /100 elsewhere.
"""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

BOT_CURRENCY = "SYP_NEW"
ICHANCY_CURRENCY = "SYP_OLD"
ICHANCY_RATE = Decimal("100")  # 1 new SYP = 100 old SYP


def old_to_new(amount, *, rounding=ROUND_DOWN) -> Decimal:
    value = Decimal(str(amount))
    return (value / ICHANCY_RATE).quantize(Decimal("0.01"), rounding=rounding)


def new_to_old(amount) -> int:
    value = Decimal(str(amount))
    if value < 0:
        raise ValueError("amount cannot be negative")
    return int((value * ICHANCY_RATE).to_integral_value(rounding=ROUND_HALF_UP))


def require_new_integer(amount) -> int:
    value = Decimal(str(amount))
    if value != value.to_integral_value() or value <= 0:
        raise ValueError("amount must be a positive whole new-SYP amount")
    return int(value)


def require_old_multiple_of_rate(amount) -> int:
    value = Decimal(str(amount))
    if value != value.to_integral_value() or value <= 0:
        raise ValueError("amount must be a positive whole old-SYP amount")
    if value % ICHANCY_RATE:
        raise ValueError("old-SYP amount must be divisible by 100")
    return int(value)


def format_new(amount) -> str:
    value = Decimal(str(amount))
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_old(amount) -> str:
    return f"{int(Decimal(str(amount))):,}"
