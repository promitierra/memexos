"""Cost 0.00 and "no price configured" are different facts, and the console says which."""

import time

from memexos import events
from memexos.console.data import Data, unpriced_backends, usage_rows


class FakeCfg:
    """Only what the helper reads: the backends section and its entries."""

    def __init__(self, backends):
        self.backends = backends

    def section(self, name):
        return self.backends if name == "backends" else {}


def test_a_backend_with_a_model_and_no_price_is_reported_as_unpriced():
    cfg = FakeCfg({"amr": {"model": "deepseek-v4.1-flash-eco", "api_key_env": "OPEN_DESIGN_API_KEY"}})
    assert unpriced_backends(cfg) == ["amr"]


def test_a_priced_backend_is_not_listed():
    cfg = FakeCfg({"amr": {"model": "m", "price_in": 0.1, "price_out": 0.4}})
    assert unpriced_backends(cfg) == []


def test_one_price_is_enough_to_consider_it_priced():
    """Partial pricing still yields a number; the page must not hide it."""
    cfg = FakeCfg({"deepseek": {"model": "deepseek-flash", "price_in": 0.2}})
    assert unpriced_backends(cfg) == []


def test_command_line_backends_are_never_listed():
    """They report their own cost, so there is nothing to configure."""
    cfg = FakeCfg({"claude-code": {"model": "x"}, "codex": {"model": "y"}})
    assert unpriced_backends(cfg) == []


def test_a_backend_without_a_model_is_ignored():
    cfg = FakeCfg({"openai": {"model": "", "price_in": 1.0}, "anthropic": {"model": ""}})
    assert unpriced_backends(cfg) == []


def test_the_list_is_sorted_and_complete():
    cfg = FakeCfg({
        "amr": {"model": "a"}, "deepseek": {"model": "d"}, "amr-flash": {"model": "f"},
        "codex": {"model": "c"},
    })
    assert unpriced_backends(cfg) == ["amr", "amr-flash", "deepseek"]


# ---------------------------------------------------------------- tokens per model


def call(backend, model, inp, cached, out, cost=None, secs=1.0):
    data = {"backend": backend, "model": model, "seconds": secs,
            "tokens": {"input_tokens": inp, "cached_tokens": cached, "output_tokens": out}}
    if cost is not None:
        data["cost_usd"] = cost
    return {"type": "model.call", "ts": "2026-11-11T20:00:00", "data": data}


def test_usage_rows_adds_calls_and_tokens_per_model():
    rows = usage_rows([
        call("amr", "eco", 100, 40, 10),
        call("amr", "eco", 50, 0, 5),
        call("deepseek", "flash", 30, 0, 7),
    ])
    by = {r["model"]: r for r in rows}
    assert by["amr:eco"]["calls"] == 2
    assert by["amr:eco"]["input"] == 150
    assert by["amr:eco"]["cached"] == 40
    assert by["amr:eco"]["output"] == 15
    assert by["deepseek:flash"]["input"] == 30


def test_a_model_without_cost_is_marked_unpriced_but_still_counted():
    rows = usage_rows([call("amr", "eco", 10, 0, 1), call("amr", "eco", 10, 0, 1, cost=0.002)])
    row = rows[0]
    assert row["calls"] == 2
    assert row["priced"] is True                    # at least one call carried a price
    assert row["cost"] == 0.002
    assert row["priced_calls"] == 1


def test_rows_come_sorted_by_input_tokens():
    rows = usage_rows([call("a", "small", 5, 0, 1), call("b", "big", 900, 0, 1)])
    assert [r["model"] for r in rows] == ["b:big", "a:small"]


def test_tokens_survive_a_missing_usage_dict():
    rows = usage_rows([{"type": "model.call", "ts": "x", "data": {"backend": "a", "model": "b"}}])
    assert rows[0]["input"] == 0 and rows[0]["calls"] == 1


def test_a_call_recorded_without_cost_is_priced_retroactively():
    """History matters: the tokens and the timestamp are in the event, so an old
    call can be priced once someone configures a price."""

    class Cfg:
        def section(self, name):
            return {"deepseek": {"price_in": 0.3, "price_out": 1.2, "cache_discount": 0.02,
                                 "off_peak_factor": 0.5, "peak_hours_utc": "1-4,6-10"}} if name == "backends" else {}

    ev = call("deepseek", "deepseek-flash", 1_000_000, 0, 1_000_000)
    ev["ts"] = "2026-11-11T12:00:00+0000"          # off-peak Wednesday
    rows = usage_rows([ev], Cfg())
    assert rows[0]["priced"] is True
    assert rows[0]["cost"] == 0.75                  # (0.15 + 0.6) off-peak


def test_two_providers_are_priced_with_their_own_table():
    """The same tokens cost differently on two providers: the row must use its
    own backend's prices, not one global price."""

    class Cfg:
        def section(self, name):
            if name != "backends":
                return {}
            common = {"cache_discount": 0.02, "off_peak_factor": 0.5, "peak_hours_utc": "1-4,6-10"}
            return {
                "direct": {"price_in": 0.30, "price_out": 1.20, "price_source": "official", **common},
                "gateway": {"price_in": 1.32, "price_out": 3.96, "price_source": "list", **common},
            }

    tokens = (1_000_000, 0, 1_000_000)
    rows = usage_rows([call("direct", "m", *tokens), call("gateway", "m", *tokens)], Cfg())
    by = {r["model"]: r for r in rows}
    assert by["direct:m"]["cost"] == 0.75           # (0.15 + 0.60) off-peak
    assert by["gateway:m"]["cost"] == 2.64          # (0.66 + 1.98) off-peak
    assert by["direct:m"]["price_source"] == "official"
    assert by["gateway:m"]["price_source"] == "list"


# ---------------------------------------------------- the day and the cents


def test_the_day_belongs_to_the_reader_not_the_container():
    """02:00 UTC is still 21:00 of the previous day in Bogotá."""
    d = object.__new__(Data)                      # day_of reads no state
    iso = "2026-11-12T02:00:00+0000"
    assert d.day_of(iso, 0) == "2026-11-12"
    assert d.day_of(iso, -300) == "2026-11-11"


def test_a_sub_cent_model_call_does_not_render_as_free():
    """$0.0007 rounded to two decimals reads as "this call cost nothing"."""
    line = events.render({"type": "model.call", "job": "ingest",
                          "data": {"role": "judge", "backend": "deepseek", "model": "deepseek-flash",
                                   "seconds": 6.2, "cost_usd": 0.0007}})
    assert line.endswith("$0.0007")      # four decimals, not a truncated "$0.00"


def test_a_dollar_cost_keeps_two_decimals():
    line = events.render({"type": "model.call", "job": "ingest",
                          "data": {"role": "worker", "backend": "amr", "model": "m",
                                   "seconds": 1.0, "cost_usd": 1.5033}})
    assert "$1.50" in line


def test_the_status_line_prices_calls_that_recorded_no_cost():
    """Summing only the recorded cost reported every un-priced call as free."""
    from memexos import status

    cfg = FakeCfg({"amr": {"price_in": 0.30, "price_out": 1.20, "price_cache_read": 0.006, "plan_multiplier": 0.5}})
    today = [
        {"type": "model.call", "ts": "2026-11-11T21:00:00+0000",
         "data": {"backend": "amr", "tokens": {"input_tokens": 31_397 + 9_472, "cached_tokens": 9_472,
                                               "output_tokens": 13_424}}},
        {"type": "publish", "data": {}},
    ]
    assert status._cost_today(cfg, today) == 0.012792


def test_a_day_without_calls_costs_a_real_zero():
    from memexos import status

    assert status._cost_today(FakeCfg({}), []) == 0.0


def test_a_day_whose_calls_have_no_price_is_unknown_not_free():
    """The one fact that must never be rounded to $0.00."""
    from memexos import status

    today = [{"type": "model.call", "ts": "2026-11-11T21:00:00+0000",
              "data": {"backend": "amr", "tokens": {"input_tokens": 10}}}]
    assert status._cost_today(FakeCfg({}), today) is None


def test_the_snapshot_day_is_the_readers_day():
    """01:00 UTC on the 12th is 20:00 of the 11th in Bogotá, and the counter must agree."""
    from memexos import events as ev

    assert ev.today(0) == time.strftime("%Y-%m-%d", time.gmtime())
    assert ev.day_of("2026-11-12T01:00:00+0000", -300) == "2026-11-11"
    assert ev.day_of("2026-11-12T01:00:00+0000", 0) == "2026-11-12"


def test_the_day_cut_does_not_depend_on_the_process_timezone():
    """timegm, not mktime: a container in UTC and a laptop in Bogotá must agree."""
    assert events.today(0) == time.strftime("%Y-%m-%d", time.gmtime())
