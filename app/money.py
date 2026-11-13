"""Money helpers. Prices are stored as integers in the smallest unit (cents/paisa)."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_cents(value: str | float | Decimal) -> int:
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid price, for example 1499.99") from exc
    if amount < 0:
        raise ValueError("Price cannot be negative")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def format_money(cents: int | None, currency: str = "PKR") -> str:
    cents = cents or 0
    return f"{currency} {cents / 100:,.2f}"
