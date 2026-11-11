"""Cost 0.00 and "no price configured" are different facts, and the console says which."""

from memexos.console.data import unpriced_backends


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
