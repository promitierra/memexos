"""Cost from tokens and prices: cache, plan multipliers, peak hours.

The fixtures are real rows from the provider's billing detail, so the arithmetic
is checked against a bill and not against my own assumption.
"""

import datetime as dt

from memexos import pricing

# Published list prices, USD per 1M tokens (OpenDesign dashboard, 2026-11-11).
FLASH = {"price_in": 0.30, "price_out": 1.20, "price_cache_read": 0.006, "price_cache_write": 0.30,
         "billing_multiplier": 1, "plan_multiplier": 0.5}
FLASH_PAYG = {**FLASH, "plan_multiplier": 1, "off_peak_factor": 0.5, "peak_hours_utc": "1-4,6-10"}
ECO = {**FLASH, "plan_multiplier": 0.27}
PRO = {"price_in": 1.32, "price_out": 3.96, "price_cache_read": 0.044, "price_cache_write": 1.32,
       "billing_multiplier": 1, "plan_multiplier": 1}

PEAK = dt.datetime(2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc)      # Wednesday 02:00 UTC
OFF = dt.datetime(2026, 11, 11, 21, 0, tzinfo=dt.timezone.utc)      # Wednesday 21:00 UTC
WEEKEND = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.timezone.utc)   # Saturday 02:00 UTC


def test_flash_call_matches_the_billed_amount():
    """Billed list price $0.0256, actual plan credits $0.0128."""
    tokens = {"input_tokens": 31_397 + 9_472, "cached_tokens": 9_472, "output_tokens": 13_424}
    assert pricing.cost_usd(tokens, FLASH, at=OFF) == 0.012792


def test_pro_call_matches_the_billed_amount():
    """Billed list price $0.0189 with plan multiplier 1."""
    tokens = {"input_tokens": 300 + 27_648, "cached_tokens": 27_648, "output_tokens": 4_361}
    assert pricing.cost_usd(tokens, PRO, at=OFF) == 0.018882


def test_economy_call_applies_its_own_multiplier():
    tokens = {"input_tokens": 4_205, "cached_tokens": 0, "output_tokens": 1_467}
    listed = pricing.cost_usd(tokens, {**ECO, "plan_multiplier": 1}, at=OFF)
    eco = pricing.cost_usd(tokens, ECO, at=OFF)
    assert listed == 0.003022
    assert eco == round(listed * 0.27, 6)


def test_cache_read_is_an_absolute_price_not_a_ratio():
    """Pro reads cache at 3.3 % of its input price, not the 2 % Flash uses."""
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH, at=OFF) == 0.003          # 0.006 * 0.5 plan
    assert pricing.cost_usd(tokens, PRO, at=OFF) == 0.044


def test_without_prices_the_cost_is_unknown_not_zero():
    assert pricing.cost_usd({"input_tokens": 1000, "output_tokens": 10}, {}) is None


def test_a_pay_as_you_go_backend_pays_half_off_peak():
    tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH_PAYG, at=PEAK) == 1.5
    assert pricing.cost_usd(tokens, FLASH_PAYG, at=OFF) == 0.75
    assert pricing.cost_usd(tokens, FLASH_PAYG, at=WEEKEND) == 0.75


def test_without_a_timestamp_the_peak_rate_is_used():
    """Conservative on purpose: never under-report a bill."""
    assert pricing.cost_usd({"input_tokens": 1_000_000}, FLASH_PAYG) == 0.3


def test_cache_writes_are_charged_only_when_priced():
    tokens = {"input_tokens": 0, "cache_write_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, FLASH_PAYG, at=OFF) == 0.15     # $0.30 * 0.5
    assert pricing.cost_usd(tokens, {"price_in": 0.3, "price_out": 1.2}, at=OFF) == 0.3   # no peak window configured


def test_legacy_cache_discount_still_works():
    """Older configs set a ratio; it must still price instead of returning None."""
    legacy = {"price_in": 0.30, "price_out": 1.20, "cache_discount": 0.02}
    tokens = {"input_tokens": 1_000_000, "cached_tokens": 1_000_000}
    assert pricing.cost_usd(tokens, legacy) == 0.006


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


# ------------------------------------------------ the event and the printed form


def test_a_recorded_cost_wins_over_pricing_it_again():
    """The backend measured it; re-pricing would second-guess the invoice."""
    ev = {"ts": "2026-11-11T21:00:00+0000",
          "data": {"backend": "amr", "cost_usd": 0.5, "tokens": {"input_tokens": 1_000_000}}}
    assert pricing.event_cost(ev, FLASH) == 0.5


def test_an_unpriced_event_is_priced_from_its_tokens_and_timestamp():
    """A call made before the price existed is not a free call."""
    ev = {"ts": "2026-11-11T21:00:00+0000",
          "data": {"backend": "amr", "tokens": {"input_tokens": 31_397 + 9_472, "cached_tokens": 9_472,
                                                "output_tokens": 13_424}}}
    assert pricing.event_cost(ev, FLASH) == 0.012792


def test_an_event_whose_backend_has_no_price_is_unknown_not_zero():
    ev = {"ts": "2026-11-11T21:00:00+0000", "data": {"backend": "x", "tokens": {"input_tokens": 10}}}
    assert pricing.event_cost(ev, {}) is None


def test_money_keeps_four_decimals_below_a_cent():
    """Two decimals would print every model call as "$0.00", which reads as free."""
    assert pricing.money(0.0007) == "$0.0007"
    assert pricing.money(0.0014) == "$0.0014"


def test_money_keeps_two_decimals_from_a_cent_up():
    assert pricing.money(1.5033) == "$1.50"
    assert pricing.money(0.01) == "$0.01"


def test_money_prints_a_real_zero_but_an_unknown_as_a_dash():
    assert pricing.money(0) == "$0.00"
    assert pricing.money(None) == "-"
