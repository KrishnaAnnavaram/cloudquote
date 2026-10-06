"""Billing-unit conventions, in one place so every calculation uses the same ones.

* A billing month is **730 hours** (8760 h / 12), which is the convention both AWS and GCP use
  to turn hourly rates into monthly figures. A day is 24 hours, so a month is 30.4167 days.
* Storage sizes are normalised to **GB** with 1 TB = 1024 GB (and 1 TiB = 1024 GiB, treated as
  the same billing unit), matching how the providers' storage price tables are tiered.
* Money is ``decimal.Decimal``; rounding to cents happens only for display.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

HOURS_PER_MONTH = Decimal(730)
HOURS_PER_DAY = Decimal(24)
HOURS_PER_WEEK = Decimal(168)
DAYS_PER_MONTH = HOURS_PER_MONTH / HOURS_PER_DAY
GB_PER_TB = Decimal(1024)
GB_PER_PB = GB_PER_TB * 1024
CENT = Decimal("0.01")

_SIZE_FACTORS = {"mb": Decimal(1) / 1024, "mib": Decimal(1) / 1024, "gb": Decimal(1), "gib": Decimal(1),
                 "tb": GB_PER_TB, "tib": GB_PER_TB, "pb": GB_PER_PB, "pib": GB_PER_PB}


def D(value: float | int | str | Decimal) -> Decimal:
    """Exact Decimal from a number (via ``str`` so 0.1 stays 0.1)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def to_gb(amount: float | Decimal, unit: str) -> Decimal:
    try:
        return D(amount) * _SIZE_FACTORS[unit.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unknown size unit {unit!r}") from exc


def hours_per_month(amount: float | Decimal, per: str) -> Decimal:
    """Convert 'N hours per day/week/month' into hours per billing month (max 730)."""
    amount = D(amount)
    per = per.strip().lower()
    if per in {"day", "daily"}:
        if amount > HOURS_PER_DAY:
            raise ValueError(f"{amount} hours per day is more than 24")
        return amount / HOURS_PER_DAY * HOURS_PER_MONTH
    if per in {"week", "weekly"}:
        if amount > HOURS_PER_WEEK:
            raise ValueError(f"{amount} hours per week is more than 168")
        return amount / HOURS_PER_WEEK * HOURS_PER_MONTH
    if per in {"month", "monthly", "mo"}:
        if amount > HOURS_PER_MONTH:
            raise ValueError(f"{amount} hours per month is more than {HOURS_PER_MONTH}")
        return amount
    raise ValueError(f"unknown period {per!r}")


def months_from(amount: float | Decimal, unit: str) -> Decimal:
    unit = unit.strip().lower().rstrip("s")
    amount = D(amount)
    if unit == "day":
        return amount / DAYS_PER_MONTH
    if unit == "week":
        return amount * 7 / DAYS_PER_MONTH
    if unit == "month":
        return amount
    if unit == "year":
        return amount * 12
    raise ValueError(f"unknown duration unit {unit!r}")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal, currency: str = "USD") -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(money(value)):,.2f}" if currency == "USD" else f"{money(value):,.2f} {currency}"
