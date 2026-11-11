"""Cost from tokens and prices: cache, peak hours and the "no price" case."""

import datetime as dt

from memexos import pricing

FLASH = {
    "price_in": 0.30,            # USD per 1M, cache miss, peak
    "price_out": 1.20,           # USD per 1M, peak
    "cache_discount": 0.02,      # DeepSeek: a cache hit costs 2 %
    "off_peak_factor": 0.5,
    "peak_hours_utc": "1-4,6-10",
    "peak_weekdays_only": True,
}

PEAK = dt.datetime(2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc)        # Wednesday 02:00 UTC
OFF = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)        # Wednesday 12:00 UTC
WEEKEND = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.timezone.utc)     # Saturday 02:00 UTC


def test_without_prices_the_cost_is_unknown_not_zero():
    assert pricing.cost_usd({"input_tokens": 1000, "output_tokens": 10}, {}) is None


def test_peak_rate_is_the_published_one():
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 0, "output_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH, at=PEAK) == 1.5            # 0.30 + 1.20


def test_off_peak_is_half_price():
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 0, "output_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH, at=OFF) == 0.75


def test_weekend_is_off_peak():
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 0, "output_tokens": 0}
    assert pricing.cost_usd(tokens, FLASH, at=WEEKEND) == 0.15


def test_cached_input_is_priced_far_below_a_cache_miss():
    miss = pricing.cost_usd({"input_tokens": 1_000_000, "cached_tokens": 0}, FLASH, at=OFF)
    hit = pricing.cost_usd({"input_tokens": 1_000_000, "cached_tokens": 1_000_000}, FLASH, at=OFF)
    assert hit == 0.003                                               # 2 % of 0.15
    assert hit < miss / 10


def test_a_mixed_call_splits_cached_and_uncached_tokens():
    tokens = {"input_tokens": 2_000_000, "cached_tokens": 1_500_000, "output_tokens": 0}
    # 500k miss * 0.15 + 1.5M hit * 0.003, off-peak
    assert pricing.cost_usd(tokens, FLASH, at=OFF) == round(0.075 + 0.0045, 6)


def test_without_a_timestamp_the_peak_rate_is_used():
    """Conservative on purpose: never under-report a bill."""
    tokens = {"input_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH) == 0.3


def test_parse_ts_handles_the_event_format_and_zulu():
    assert pricing.parse_ts("2026-11-11T20:04:28+0000").hour == 20
    assert pricing.parse_ts("2026-11-11T20:04:28Z").hour == 20
    assert pricing.parse_ts("") is None
    assert pricing.parse_ts("not a date") is None


def test_is_peak_boundaries():
    hours = "1-4,6-10"
    assert pricing.is_peak(dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.timezone.utc), hours) is True
    assert pricing.is_peak(dt.datetime(2026, 11, 11, 3, 59, tzinfo=dt.timezone.utc), hours) is True
    assert pricing.is_peak(dt.datetime(2026, 11, 11, 4, 0, tzinfo=dt.timezone.utc), hours) is False
    assert pricing.is_peak(dt.datetime(2026, 11, 11, 5, 0, tzinfo=dt.timezone.utc), hours) is False
