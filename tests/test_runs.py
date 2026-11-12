"""The run timeline is rebuilt from events alone."""

import json
import os
import time

from memexos import events, runs


def write(state, evs):
    state.mkdir(parents=True, exist_ok=True)
    t0 = time.time() - 3600
    with open(state / "events.jsonl", "w") as f:
        for i, (run, job, typ, data) in enumerate(evs):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t0 + i * 10))
            f.write(json.dumps({"ts": ts, "run": run, "job": job, "type": typ, "data": data}) + "\n")


def test_batches_steps_and_jobs(tmp_path):
    st = tmp_path / "state"
    me = os.getpid()
    write(st, [
        ("n1", "nightly", "run.start", {"pid": me}),
        ("n1", "intake", "step.start", {"name": "collect"}),
        ("n1", "intake", "step.end", {"name": "collect", "seconds": 10}),
        ("n1", "intake", "model.call", {"cost_usd": 0.5}),
        ("n1", "ingest", "ingest.start", {"files": ["raw/a.md"], "remaining": 1}),
        ("n1", "ingest", "stage.open", {"branch": "staging/1"}),
        ("n1", "ingest", "step.start", {"name": "write"}),
        ("n1", "ingest", "search", {"query": "q"}),
        ("n1", "ingest", "model.call", {"cost_usd": 1.25}),
        ("n1", "ingest", "step.end", {"name": "write", "seconds": 20}),
        ("n1", "ingest", "gate1.result", {"passed": True, "errors": 0, "warnings": 0, "round": 0}),
        ("n1", "ingest", "step.start", {"name": "gate2", "mode": "ingest"}),
        ("n1", "ingest", "step.end", {"name": "gate2", "seconds": 5, "result": "failed"}),
        ("n1", "ingest", "gate2.result", {"passed": False, "code": 1, "mode": "ingest"}),
        ("n1", "ingest", "hold", {"branch": "staging/1", "reason": "still failing", "raws": ["raw/a.md"]}),
        ("n1", "nightly", "run.end", {"seconds": 140, "status": "ok"}),
    ])
    units = runs.timeline(st, 0)
    intake, batch = units
    assert intake["job"] == "intake" and intake["lane"] == "collect" and intake["status"] == "done" and intake["cost"] == 0.5
    assert batch["kind"] == "batch" and batch["status"] == "held" and batch["note"] == "still failing"
    assert batch["files"] == ["raw/a.md"] and batch["branch"] == "staging/1" and batch["cost"] == 1.25
    assert [s["step"] for s in batch["segs"]] == ["write", "gate2"]
    assert batch["segs"][1]["data"]["result"] == "failed"
    assert [m["type"] for m in batch["marks"]] == ["gate1.result", "hold"]
    assert batch["marks"][0]["ok"] is True


def test_dead_process_is_interrupted_live_one_is_running(tmp_path):
    st = tmp_path / "state"
    write(st, [
        ("dead", "ingest", "run.start", {"pid": 999999}),
        ("dead", "ingest", "stage.open", {"branch": "staging/2"}),
        ("dead", "ingest", "step.start", {"name": "write"}),
        ("live", "intake", "run.start", {"pid": os.getpid()}),
        ("live", "intake", "step.start", {"name": "admit", "channel": "rss"}),
    ])
    by = {u["run"]: u for u in runs.timeline(st, 0)}
    assert by["dead"]["status"] == "interrupted"
    assert by["live"]["status"] == "running" and by["live"]["active"] == ["admit"]
    assert runs.active_stages(st) == {"intake": ["intake"]}


def test_step_context_records_result_and_errors(tmp_path):
    st = tmp_path / "state"
    events.new_run("test")
    with events.step(st, "gate2", mode="fix") as info:
        info["result"] = "passed"
    try:
        with events.step(st, "write"):
            raise ValueError("boom")
    except ValueError:
        pass
    evs = list(events.read(st))
    ends = [e["data"] for e in evs if e["type"] == "step.end"]
    assert ends[0]["result"] == "passed" and ends[1]["result"] == "error" and "boom" in ends[1]["why"]


def test_render_picks_pass_and_fail_variants():
    ev = {"type": "gate1.result", "job": "ingest", "data": {"passed": False, "errors": 2, "warnings": 0, "round": 1}}
    assert "2 problem" in events.render(ev)
    ev["data"]["passed"] = True
    assert "passed" in events.render(ev, job=False) and not events.render(ev, job=False).startswith("ingest")
    ev = {"type": "ingest.start", "job": "ingest", "data": {"files": ["a", "b"], "remaining": 5}}
    assert "2 raw" in events.render(ev)


def test_monthly_files_retention_and_legacy(tmp_path, monkeypatch):
    st = tmp_path / "state"
    st.mkdir()
    (st / "events.jsonl").write_text(json.dumps({"ts": "2020-01-01T00:00:00+0000", "run": "old", "job": "x", "type": "legacy", "data": {}}) + "\n")
    (st / "events").mkdir()
    (st / "events" / "2001-01.jsonl").write_text("")                       # far beyond retention
    monkeypatch.setattr(events, "KEEP_MONTHS", 12)
    events.new_run("t")
    events.emit(st, "hello", n=1)
    assert events.month_file(st).exists() and not (st / "events" / "2001-01.jsonl").exists()
    assert [e["type"] for e in events.read(st)] == ["legacy", "hello"]
    assert [e["type"] for e in events.read(st, since=time.time() - 60)] == ["hello"]


def test_every_emitted_event_type_has_words_in_both_languages():
    import re
    from pathlib import Path
    from memexos import i18n
    src = Path(events.__file__).parent
    types = set()
    for f in src.rglob("*.py"):
        types |= set(re.findall(r'emit\([^"\n]*"([a-z0-9_.]+)"', f.read_text(encoding="utf-8")))
    missing = []
    for t in sorted(types):
        for lang in ("en", "zh"):
            keys = [f"event.{t}", f"event.{t}_pass"]
            if not any(k in i18n._pack(lang) for k in keys):
                missing.append(f"{lang}:{t}")
    assert not missing, missing


def test_messages_may_use_key_and_name_placeholders():
    from memexos import i18n
    assert "“Life/Tools”" in i18n.t("catalog.exists", key="Life/Tools")
    assert i18n.t("inbox.cand_title", key="x:1")


def test_unit_ids_do_not_depend_on_the_window(home, monkeypatch):
    import time
    import types
    from memexos import config, events, runs
    cfg = config.load(home / "v")
    st = cfg.paths.state
    old = time.time() - 3 * 86400                       # one run three days ago, outside the narrow window
    fake = types.SimpleNamespace(**{k: getattr(time, k) for k in ("localtime", "mktime", "strptime", "gmtime")})
    fake.time = lambda: old
    fake.strftime = lambda fmt, tup=None: time.strftime(fmt, tup or time.localtime(old))
    monkeypatch.setattr(events, "time", fake)
    with events.run(st, "ingest"):
        events.emit(st, "ingest.start", files=["raw/old.md"], remaining=1)
        events.emit(st, "publish", branch="staging/old", message="m")
    monkeypatch.setattr(events, "time", time)
    for _ in range(3):
        with events.run(st, "ingest"):
            events.emit(st, "ingest.start", files=["raw/a.md"], remaining=1)
            with events.step(st, "write"):
                pass
            events.emit(st, "publish", branch="staging/x", message="m")
    wide = runs.timeline(st, time.time() - 30 * 86400)
    narrow = runs.timeline(st, time.time() - 3600)
    assert {u["id"] for u in narrow} <= {u["id"] for u in wide} and len(narrow) == 3


def test_command_pattern_is_fast_on_many_backslashes():
    import time
    from memexos import usage
    s = 'cmd: "' + "\\\\" * 5000 + "x"            # never closed: a backtracking pattern would take forever
    t0 = time.time()
    assert usage.JS_CMD.search(s) is None
    assert time.time() - t0 < 1.0
    m = usage.JS_CMD.search('cmd: "memex search \\"q\\""')
    assert m and m.group(2) == 'memex search \\"q\\"'


def test_reused_pid_is_not_the_same_process(tmp_path):
    """A container that restarts gives the console the same pid again: the start marker, not the pid, decides."""
    st = tmp_path / "state"
    mine = events.proc_start(os.getpid())
    write(st, [
        ("old", "ingest", "run.start", {"pid": os.getpid(), "start": "1"}),
        ("old", "ingest", "stage.open", {"branch": "staging/9"}),
        ("old", "ingest", "step.start", {"name": "selfcheck"}),
        ("new", "intake", "run.start", {"pid": os.getpid(), "start": mine}),
        ("new", "intake", "step.start", {"name": "admit"}),
    ])
    by = {u["run"]: u for u in runs.timeline(st, 0)}
    assert by["old"]["status"] == "interrupted"          # the pid lives, but it is another process
    assert by["new"]["status"] == "running"
    assert runs.active_stages(st) == {"intake": ["intake"]}


def test_run_start_records_the_process_marker(tmp_path):
    st = tmp_path / "state"
    with events.run(st, "ingest"):
        pass
    start = next(e for e in events.read(st) if e["type"] == "run.start")
    assert start["data"]["pid"] == os.getpid()
    assert start["data"]["start"] == events.proc_start(os.getpid())
    assert events.proc_start(999999) == ""               # a dead pid has no marker to compare
