"""What a call costs, from tokens and prices — one owner for the arithmetic.

Prices are per million tokens, as the provider publishes them. What makes this
more than a multiplication, learned from a real billing breakdown:

- Cached input has its own price, in dollars. It is *not* a fixed fraction of the
  input price: DeepSeek lists $0.006 for Flash (2 % of $0.30) and $0.044 for Pro
  (3.3 % of $1.32). A ratio would be wrong for one of them.
- A reseller bills the list price and then applies its own multipliers: a
  billing multiplier (× 1) and a plan contribution multiplier (× 0.5 for Flash,
  × 0.27 for Eco, × 1 for Pro). The plan multiplier is what the dashboard calls
  "actual plan credits used".
- Some providers charge half outside peak hours, on fixed UTC windows.

A call may also have happened before anyone configured a price, so cost is
computable at read time from the tokens and the timestamp.

No price configured means `None`, never zero: "unknown" and "free" are
different facts.
"""

from __future__ import annotations

import datetime as dt

DEFAULTS = {
    "price_cache_write": None,     # None = the provider does not charge writes
    "cache_discount": None,        # legacy fallback: cache read = price_in * this
    "off_peak_factor": 1.0,
    "peak_hours_utc": "",          # "1-4,6-10" (start inclusive, end exclusive)
    "peak_weekdays_only": True,
    "billing_multiplier": 1.0,
    "plan_multiplier": 1.0,
}


def _number(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def is_peak(ts: dt.datetime, hours: str, weekdays_only: bool = True) -> bool:
    """Whether `ts` (aware or naive UTC) falls inside the configured peak windows."""
    if not hours.strip():
        return False
    when = ts.astimezone(dt.timezone.utc) if ts.tzinfo else ts
    if weekdays_only and when.weekday() >= 5:
        return False
    for chunk in hours.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        start, _, end = chunk.partition("-")
        try:
            lo, hi = int(start), int(end)
        except ValueError:
            continue
        if lo <= when.hour < hi:
            return True
    return False


def cost_usd(tokens: dict, prices: dict, at: dt.datetime | None = None) -> float | None:
    """Cost of one call, or None when the prices do not let us say.

    `prices` is the backend's configuration section. `at` is when the call
    happened; without it the call is priced at the peak rate, the conservative
    choice.
    """
    pin = _number(prices.get("price_in"), 0.0)
    pout = _number(prices.get("price_out"), 0.0)
    if not (pin or pout):
        return None

    tok = tokens or {}
    hit = int(tok.get("cached_tokens") or 0)
    miss = max(int(tok.get("input_tokens") or 0) - hit, 0)
    write = int(tok.get("cache_write_tokens") or 0)
    out = int(tok.get("output_tokens") or 0)

    # cache read: its own price when configured, else the legacy ratio
    price_read = prices.get("price_cache_read")
    if price_read is None:
        discount = prices.get("cache_discount")
        price_read = pin * (_number(discount, 0.5) if discount is not None else 0.5)
    price_write = prices.get("price_cache_write")
    price_write = pin if price_write is None else _number(price_write, 0.0)

    factor = 1.0
    if at is not None:
        windows = str(prices.get("peak_hours_utc") or "")
        if not is_peak(at, windows, bool(prices.get("peak_weekdays_only", True))):
            factor = _number(prices.get("off_peak_factor"), DEFAULTS["off_peak_factor"])

    subtotal = miss * pin + hit * _number(price_read, 0.0) + write * price_write + out * pout
    total = (subtotal / 1e6) * factor
    total *= _number(prices.get("billing_multiplier"), 1.0)
    total *= _number(prices.get("plan_multiplier"), 1.0)
    return round(total, 6)


def parse_ts(value: str | None) -> dt.datetime | None:
    """Parse an event timestamp ('2026-11-11T20:04:28+0000' or ISO) into UTC."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
