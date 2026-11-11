"""Cost 0.00 and "no price configured" are different facts, and the console says which."""

from memexos.console.data import unpriced_backends, usage_rows


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
