"""The self-check spends money to avoid a review round; when the judge is slow it has to stop, not wait."""

import time

from memexos import config, judge
from memexos.pipeline import selfcheck


def test_judge_does_not_ask_when_the_budget_is_gone(home, monkeypatch):
    j = judge.LLMJudge(config.load(home / "v"))
    asked = []
    monkeypatch.setattr(j, "_ask", lambda *a, **k: asked.append(1) or None)
    assert j.yes_no_many([{"claim": "c"}], "instruction", deadline=time.monotonic() - 1) == [None]
    assert asked == []


def test_selfcheck_reports_the_budget_instead_of_waiting(home, tmp_path, monkeypatch):
    cfg = config.load(home / "v")
    cfg.data["pipeline"]["selfcheck_timeout"] = 0.0001          # the budget is gone before the first batch

    class Glacial(judge.LLMJudge):
        def available(self):
            return True, ""

        def supported(self, items, deadline=None):
            assert deadline is not None
            time.sleep(0.01)
            return [None] * len(items)

    monkeypatch.setattr(judge, "get", lambda c, name=None: Glacial(c))
    monkeypatch.setattr(selfcheck, "diff_units", lambda stage, base: [
        {"page": "wiki/x.md", "text": "Una oración concreta con datos", "line": "+Una oración concreta con datos"}])
    code, text, summary = selfcheck.run(cfg, tmp_path, "base", [])
    assert code == 2 and text == "" and "budget" in summary
