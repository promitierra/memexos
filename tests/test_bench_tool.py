"""The benchmark report: it must read the event log without models or network."""

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "bench.py"


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=60)


def write_events(state: Path, rows: list[dict]) -> None:
    d = state / "events"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-11.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def published_run() -> list[dict]:
    return [
        {"ts": "2026-11-11T20:20:28", "run": "r1", "job": "ingest", "type": "run.start", "data": {}},
        {"ts": "2026-11-11T20:24:51", "run": "r1", "job": "ingest", "type": "step.end",
         "data": {"name": "write", "seconds": 262.6, "ok": True, "model": "m", "cost_usd": 0.02}},
        {"ts": "2026-11-11T20:41:00", "run": "r1", "job": "ingest", "type": "step.end",
         "data": {"name": "selfcheck", "seconds": 923.0, "ok": True, "cost_usd": 0.03}},
        {"ts": "2026-11-11T20:41:01", "run": "r1", "job": "ingest", "type": "gate1.result",
         "data": {"passed": True, "errors": 0, "warnings": 0}},
        {"ts": "2026-11-11T20:41:02", "run": "r1", "job": "ingest", "type": "selfcheck.counts",
         "data": {"total": 250, "flagged": 3}},
        {"ts": "2026-11-11T20:41:03", "run": "r1", "job": "ingest", "type": "gate2.result",
         "data": {"passed": True}},
        {"ts": "2026-11-11T20:41:04", "run": "r1", "job": "ingest", "type": "publish",
         "data": {"stats": {"added_pages": 10}}},
        {"ts": "2026-11-11T20:24:52", "run": "r1", "job": "ingest", "type": "model.call",
         "data": {"backend": "deepseek", "model": "deepseek-flash", "seconds": 12.0,
                  "tokens": {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 500}}},
        {"ts": "2026-11-11T20:41:05", "run": "r1", "job": "ingest", "type": "run.end",
         "data": {"seconds": 1237, "status": "ok"}},
    ]


def test_report_reads_a_run_with_quality_and_speed(tmp_path):
    write_events(tmp_path, published_run())
    out = run_tool("report", "--state", str(tmp_path), "--json")
    assert out.returncode == 0, out.stderr
    row = json.loads(out.stdout)[0]
    assert row["wall_s"] == 1237
    assert row["calls"] == 2                       # write + selfcheck
    assert round(row["cost_usd"], 2) == 0.05
    assert row["seconds_by_step"]["write"] == 262.6
    assert row["gate1"] is True and row["gate2"] is True
    assert row["claims"] == 250 and row["flagged"] == 3
    assert row["pages"] == 10
    assert row["verdict"] == "publicado"


def test_a_run_without_review_is_not_a_publication(tmp_path):
    rows = [r for r in published_run() if r["type"] not in ("gate2.result", "publish")]
    write_events(tmp_path, rows)
    out = run_tool("report", "--state", str(tmp_path), "--json")
    assert out.returncode == 0, out.stderr
    row = json.loads(out.stdout)[0]
    assert row["gate2"] is None
    assert row["verdict"] == "gate 1 ok, sin revisión"


def test_failed_gate_one_is_reported_as_such(tmp_path):
    rows = published_run()
    for r in rows:
        if r["type"] == "gate1.result":
            r["data"] = {"passed": False, "errors": 1, "warnings": 0}
    write_events(tmp_path, rows)
    out = run_tool("report", "--state", str(tmp_path), "--json")
    row = json.loads(out.stdout)[0]
    assert row["gate1"] is False
    assert row["gate1_errors"] == 1
    assert row["verdict"] == "gate 1 falló"         # the first fatal gate wins over a later pass


def test_empty_state_exits_with_two(tmp_path):
    out = run_tool("report", "--state", str(tmp_path))
    assert out.returncode == 2
    assert "sin corridas" in out.stderr


def test_torn_line_does_not_hide_the_rest(tmp_path):
    d = tmp_path / "events"
    d.mkdir(parents=True)
    good = "\n".join(json.dumps(r) for r in published_run())
    (d / "2026-11.jsonl").write_text(good + "\n{\"ts\": \"2026-11-11T20:4", encoding="utf-8")
    out = run_tool("report", "--state", str(tmp_path), "--json")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)[0]["wall_s"] == 1237
