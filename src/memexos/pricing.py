"""What a call costs, from tokens and prices — one owner for the arithmetic.

Prices are per million tokens, as providers publish them. Three things make
this more than a multiplication:

- Cached input is much cheaper than a cache miss (2 % for DeepSeek), so the two
  are priced separately.
- Some providers charge less outside peak hours (DeepSeek: half price), and the
  windows are fixed UTC hours on weekdays.
- A call may have happened before anyone configured a price. Cost is therefore
  computable at read time from the tokens and the timestamp, which is what
  makes a historical report possible.

No price configured means `None`, never zero: "unknown" and "free" are
different facts and the console shows them differently.
"""

from __future__ import annotations

import datetime as dt

DEFAULTS = {
    "cache_discount": 0.5,        # fraction of price_in charged for a cache hit
    "off_peak_factor": 1.0,       # multiplier outside the peak windows
    "peak_hours_utc": "",         # "1-4,6-10" (start inclusive, end exclusive)
    "peak_weekdays_only": True,   # weekend is always off-peak
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
    """Cost of one call in USD, or None when the prices do not let us say.

    `prices` is the backend's configuration section: price_in, price_out and the
    optional keys in DEFAULTS. `at` is when the call happened; without it the
    call is priced at the peak rate, which is the conservative choice.
    """
    pin = _number(prices.get("price_in"), 0.0)
    pout = _number(prices.get("price_out"), 0.0)
    if not (pin or pout):
        return None

    tok = tokens or {}
    hit = int(tok.get("cached_tokens") or 0)
    miss = max(int(tok.get("input_tokens") or 0) - hit, 0)
    out = int(tok.get("output_tokens") or 0)

    cache_discount = _number(prices.get("cache_discount"), DEFAULTS["cache_discount"])
    factor = 1.0
    if at is not None and is_peak(at, str(prices.get("peak_hours_utc") or ""),
                                 bool(prices.get("peak_weekdays_only", DEFAULTS["peak_weekdays_only"]))):
        factor = 1.0
    elif at is not None:
        factor = _number(prices.get("off_peak_factor"), DEFAULTS["off_peak_factor"])

    total = (miss * pin + hit * pin * cache_discount + out * pout) * factor
    return round(total / 1e6, 6)


def parse_ts(value: str | None) -> dt.datetime | None:
    """Parse an event timestamp ('2026-11-11T20:04:28+0000' or ISO) into UTC."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]          # +0000 -> +00:00
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
