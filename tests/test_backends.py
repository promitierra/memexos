import json

import pytest

from memexos import events, backends, config, judge
from memexos.backends.base import Backend, Policy, Result
from memexos.backends.tools import Sandbox


class Fake(Backend):
    """Scripted backend: each call pops the next outcome."""
    script: dict = {}

    def available(self):
        return (self.name not in Fake.unavailable, "not installed")

    def structured(self, prompt, schema, **kw):
        outcome = Fake.script[self.name].pop(0)
        Fake.calls.append(self.name)
        return Result(ok=outcome == "ok", backend=self.name, result={"x": 1} if outcome == "ok" else None,
                      limited=outcome == "limited", transient=outcome == "transient", tail=outcome)

    agent = structured


def make(name):
    return type(name, (Fake,), {"name": name})


@pytest.fixture
def fake(home, monkeypatch):
    monkeypatch.setattr(backends, "REGISTRY", {"a": make("a"), "b": make("b")})
    monkeypatch.setattr(backends.time, "sleep", lambda s: None)
    Fake.calls, Fake.unavailable = [], set()
    cfg = config.load(home / "v")
    cfg.data["models"]["worker"] = ["a", "b"]
    return cfg


def call(cfg, tmp_path):
    return backends.structured(cfg, "worker", "p", {}, cwd=tmp_path, workdir=tmp_path)


def test_quota_switches_backend_and_remembers(fake, tmp_path):
    Fake.script = {"a": ["limited", "ok"], "b": ["ok", "ok"]}
    r = call(fake, tmp_path)
    assert r.ok and r.backend == "b" and Fake.calls == ["a", "b"]
    r = call(fake, tmp_path)                       # a is skipped while its quota mark is fresh
    assert r.backend == "b" and Fake.calls == ["a", "b", "b"]
    ev = list(events.read(fake.paths.state))
    assert [e["data"]["backend"] for e in ev if e["type"] == "model.call"] == ["a", "b", "b"]


def test_transient_is_retried_and_failure_does_not_hop(fake, tmp_path):
    Fake.script = {"a": ["transient", "ok"], "b": []}
    assert call(fake, tmp_path).ok and Fake.calls == ["a", "a"]
    Fake.script = {"a": ["bad"], "b": ["ok"]}
    r = call(fake, tmp_path)
    assert not r.ok and r.backend == "a"             # a real failure is reported, not papered over


def test_unavailable_backend_is_skipped(fake, tmp_path):
    Fake.unavailable = {"a"}
    Fake.script = {"a": [], "b": ["ok"]}
    r = call(fake, tmp_path)
    assert r.ok and r.backend == "b" and r.tried[0]["skipped"]


def test_sandbox_enforces_policy(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "a.md").write_text("source\n")
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("x")
    sb = Sandbox(tmp_path, Policy(write=[tmp_path / "wiki"], commands=[["echo", "ok"]], deny_read=["*.env"]))
    assert "source" in sb.call("read_file", {"path": "raw/a.md", "offset": 1, "limit": 10})[0]
    assert sb.call("read_file", {"path": str(secret), "offset": 1, "limit": 10})[1]          # outside cwd
    assert sb.call("write_file", {"path": "raw/b.md", "content": "no"})[1]                     # raw is not writable
    assert not sb.call("write_file", {"path": "wiki/p.md", "content": "yes"})[1]
    assert sb.call("run_command", {"argv": ["rm", "-rf", "x"]})[1]
    assert "ok" in sb.call("run_command", {"argv": ["echo", "ok", "fine"]})[0]
    assert {d["tool"] for d in sb.denied} == {"read_file", "write_file", "run_command"}


def test_llm_judge_batches(fake, tmp_path, monkeypatch):
    def fake_structured(cfg, role, prompt, schema, **kw):
        n = prompt.count("### Item")
        return Result(ok=True, backend="a", result={"items": [{"id": i, "answer": i % 2 == 0, "confidence": 0.9} for i in range(n)]})
    monkeypatch.setattr(backends, "structured", fake_structured)
    ps = judge.get(fake, "llm").yes_no_many([{"q": i} for i in range(30)], "Is it even?")
    assert len(ps) == 30 and ps[0] == pytest.approx(0.9) and ps[1] == pytest.approx(0.1)
    with pytest.raises(judge.JudgeUnavailable):
        judge.get(fake, "none").yes_no({}, "x")


def test_jev_calls_are_recorded_with_cost(home, monkeypatch):
    from memexos import credentials, net
    cfg = config.load(home / "v")
    monkeypatch.setattr(credentials, "get", lambda name: "k")
    monkeypatch.setattr(credentials, "available", lambda name: True)
    monkeypatch.setattr(judge.JevJudge, "down", None)
    sent = []

    def fake_request(url, **kw):
        sent.append(json.loads(kw["data"]))
        q = sent[-1]["questions"]["q"]
        answer = {"noul": 0.8} if q["type"] == "noul" else {"choice": "a", "probabilities": {"a": 0.7}}
        return net.Response(url, 200, {}, json.dumps({"answers": {"q": answer}, "usage": {"input_tokens": 1000}}).encode())
    monkeypatch.setattr(net, "request", fake_request)
    j = judge.get(cfg, "jev")
    assert j.yes_no_many([{"s": i} for i in range(5)], "ok?") == [0.8] * 5
    assert j.choice({"s": 1}, {"a": "A", "b": "B"}, "which?")["choice"] == "a"
    calls = [e["data"] for e in events.read(cfg.paths.state, types=("model.call",))]
    assert [c["tokens"] for c in calls] == [{"input_tokens": 5000, "calls": 5}, {"input_tokens": 1000, "calls": 1}]
    assert calls[0]["backend"] == "jev" and calls[0]["role"] == "judge"
    assert calls[0]["cost_usd"] == pytest.approx(5000 * 0.042 / 1e6)


def test_exhausted_transient_failure_hops_to_the_next_backend(fake, tmp_path):
    """A 5xx or an HTML error page is not a verdict on the work: after its retries, the next backend gets the prompt."""
    Fake.script = {"a": ["transient", "transient", "transient"], "b": ["ok"]}
    r = call(fake, tmp_path)
    assert r.ok and r.backend == "b" and Fake.calls == ["a", "a", "a", "b"]
