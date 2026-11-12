"""Runs as timelines, rebuilt from the event log.

A *unit* is one row of work in the status view and the console:

- a staging batch: it opens with ingest.start or stage.open and closes with publish, hold or stage.discard;
- for jobs that do not stage (intake, visual, classify, usage, …) the job itself.

Inside a unit, *segments* are step.start / step.end pairs (write, gate2, admit, …) and *marks* are point events
(gate 1 results, retries, failures, publish). Model costs (model.call) are added to the unit that was open in the
same run when the call ended.

Nothing here parses human-readable logs, so the wording of messages can change freely.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from memexos import events

COLLECT_JOBS = {"intake", "visual", "add-url", "add-video", "classify"}
WRITE_JOBS = {"ingest", "fix", "lint", "correct", "inbox"}

# point events worth showing on a timeline: type -> ok (True / False / None = neutral)
MARKS = {"gate1.result": None, "writer.retry": False, "writer.denials": False, "gate2.unavailable": False,
         "ingest.failed": False, "intake.quota": False, "intake.channel_failed": False, "capture.failed": False,
         "classify.failed": False, "visual.failed": False, "nightly.step_failed": False, "stage.merge_failed": False,
         "selfcheck.result": None, "capture.done": True, "capture.video": True, "publish": True, "hold": False}
CLOSE = {"publish": "published", "hold": "held", "stage.discard": "discarded"}

# which ring stage of the console a running step lights up
STEP_STAGE = {"collect": "intake", "admit": "intake", "capture": "intake", "visual": "intake", "classify": "classify",
              "write": "compile", "rework": "compile", "inbox": "compile", "selfcheck": "selfcheck", "gate2": "gate2",
              "triage": "feedback", "research": "intake", "investigate": "intake", "usage": "feedback", "verify": "feedback",
              "index": "publish", "embed": "publish"}


def lane(job: str) -> str:
    return "collect" if job in COLLECT_JOBS else "write" if job in WRITE_JOBS else "other"


def alive(pid, start: str | None = None) -> bool:
    """Whether the process that started this run is still the one holding that pid.

    `start` is the marker recorded in run.start (see events.proc_start). Without
    it — events written before the marker existed — only the pid is checked, which
    is what made a container restart look like a run that never ends.
    """
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        pass
    if start:
        now = events.proc_start(int(pid))
        if now and now != start:
            return False
    return True


class _Builder:
    def __init__(self) -> None:
        self.units: list[dict] = []
        self.ids: set[str] = set()
        self.runs: dict[str, dict] = {}          # run id -> {job, t0, pid, unit, ended}

    def unit(self, rs: dict, rid: str, job: str, t: float, kind: str = "job") -> dict:
        # the id must not depend on how much of the log was read (the list and the detail view read different windows)
        uid = f"{rid}#{int(t)}"
        n = 2
        while uid in self.ids:
            uid, n = f"{rid}#{int(t)}-{n}", n + 1
        self.ids.add(uid)
        u = {"id": uid, "run": rid, "job": job, "lane": lane(job), "kind": kind, "t0": t, "t1": None,
             "status": "running", "files": [], "branch": "", "segs": [], "marks": [], "cost": 0.0, "note": "", "summary": "",
             "gate2": []}
        self.units.append(u)
        rs["unit"] = u
        return u

    def close(self, rs: dict, t: float, status: str, note: str = "") -> None:
        u = rs.get("unit")
        if not u:
            return
        for s in u["segs"]:
            if s["t1"] is None:
                s["t1"], s["open_at_close"] = t, True
        u["t1"], u["status"] = t, status
        if note and not u["note"]:
            u["note"] = note[:400]
        rs["unit"] = None

    def current(self, rs: dict, rid: str, ev: dict, t: float) -> dict:
        """The unit an event belongs to; a job-level unit is created on demand (and replaced when the job name changes)."""
        u = rs.get("unit")
        job = ev.get("job") or rs["job"]
        if u and u["kind"] == "job" and u["job"] != job:
            self.close(rs, t, "done")
            u = None
        return u or self.unit(rs, rid, job, t)

    def feed(self, ev: dict) -> None:
        rid, typ, d = ev.get("run") or "-", ev.get("type", ""), ev.get("data") or {}
        if typ in ("search", "read"):        # agents reading; not part of any unit of work
            return
        t = events._epoch(ev.get("ts", ""))
        rs = self.runs.get(rid)
        if rs is None:
            rs = self.runs[rid] = {"job": ev.get("job", ""), "t0": t, "pid": None, "start": None, "unit": None,
                                   "ended": False, "last": t}
        rs["last"] = t
        if typ == "run.start":
            rs.update(job=ev.get("job", ""), t0=t, pid=d.get("pid"), start=d.get("start"))
            return
        if typ == "run.end":
            u = rs.get("unit")
            if u:
                bad = d.get("status") in ("error", "interrupted")
                self.close(rs, t, ("interrupted" if u["kind"] == "batch" else "failed" if bad else "done"))
            rs["ended"] = True
            return
        if typ in ("model.call",):
            u = rs.get("unit")
            if u and isinstance(d.get("cost_usd"), (int, float)):
                u["cost"] += d["cost_usd"]
            return
        if typ == "ingest.start" or (typ == "stage.open" and not (rs.get("unit") and rs["unit"]["kind"] == "batch" and not rs["unit"]["branch"])):
            if rs.get("unit"):
                self.close(rs, t, "interrupted" if rs["unit"]["kind"] == "batch" else "done")
            self.unit(rs, rid, ev.get("job") or rs["job"], t, "batch")
        u = self.current(rs, rid, ev, t)
        if typ == "ingest.start":
            u["files"] = list(d.get("files") or [])
            u["remaining"] = d.get("remaining")
        elif typ == "stage.open":
            u["branch"] = d.get("branch", "")
        elif typ in ("fix.start",):
            u["note"] = ""
            u["problems"] = d.get("problems")
        elif typ == "correct.start":
            u["files"] = list(d.get("files") or [])
        elif typ == "writer.done":
            u["summary"] = d.get("summary", "")
        elif typ == "intake.run":
            u["items"] = d.get("items", 0)
        elif typ == "intake.done":
            u["result"] = {k: d.get(k, 0) for k in ("admit", "reject", "uncertain", "blocked", "missing")}
        if typ == "step.start":
            u["segs"].append({"step": d.get("name", ""), "t0": t, "t1": None, "data": {k: v for k, v in d.items() if k != "name"}})
        elif typ == "step.end":
            seg = next((s for s in reversed(u["segs"]) if s["step"] == d.get("name") and s["t1"] is None), None)
            if seg is None:              # the start was in an earlier unit or before the window: draw it from its duration
                seg = {"step": d.get("name", ""), "t0": t - float(d.get("seconds") or 0), "t1": None, "data": {}}
                u["segs"].append(seg)
            seg["t1"] = t
            seg["data"].update({k: v for k, v in d.items() if k not in ("name",)})
        elif typ == "gate2.result":
            u["gate2"].append({k: d.get(k) for k in ("code", "passed", "mode", "problems", "backend", "model", "seconds", "cost_usd", "dir")})
        if typ in MARKS:
            ok = MARKS[typ]
            if ok is None and isinstance(d.get("passed"), bool):
                ok = d["passed"]
            if typ == "selfcheck.result":
                ok = None if d.get("code") == 2 else d.get("code") == 0
            u["marks"].append({"t": t, "type": typ, "ok": ok, "text": events.render(ev, job=False)[:300]})
        if typ in CLOSE:
            self.close(rs, t, CLOSE[typ], d.get("reason", "") if typ != "publish" else "")
            if typ == "publish":
                u["subject"] = d.get("message", "")

    def finish(self, now: float) -> list[dict]:
        for rs in self.runs.values():
            u = rs.get("unit")
            if u and u["t1"] is None and not rs["ended"]:
                # no run.end: the process died (pid gone), or the events came from outside a run (no pid) and stopped
                if rs.get("pid") and not alive(rs["pid"], rs.get("start")):
                    self.close(rs, rs["last"], "interrupted")
                elif not rs.get("pid") and now - rs["last"] > 600:
                    self.close(rs, rs["last"], "interrupted" if u["kind"] == "batch" else "done")
        for u in self.units:
            u["cost"] = round(u["cost"], 4)
            u["active"] = [s["step"] for s in u["segs"] if s["t1"] is None] if u["status"] == "running" else []
        return self.units


def timeline(state_dir: Path, since: float, until: float | None = None) -> list[dict]:
    """Units that were running at any time after `since` (oldest first). Reads a margin before `since`
    so a unit that started earlier is complete."""
    b = _Builder()
    for ev in events.read(state_dir, since=since - 6 * 3600):
        if until is not None and events._epoch(ev.get("ts", "")) > until:
            break
        b.feed(ev)
    units = b.finish(time.time())
    return [u for u in units if (u["t1"] or time.time()) >= since and (u["segs"] or u["marks"] or u["files"] or u["status"] == "running")]


def running(state_dir: Path) -> list[dict]:
    return [u for u in timeline(state_dir, time.time() - 6 * 3600) if u["status"] == "running"]


def active_stages(state_dir: Path, units: list[dict] | None = None) -> dict[str, list[str]]:
    """Ring stage -> labels of what is running there now."""
    units = running(state_dir) if units is None else [u for u in units if u["status"] == "running"]
    out: dict[str, list[str]] = {}
    for u in units:
        for s in u["active"]:
            st = STEP_STAGE.get(s)
            if st:
                out.setdefault(st, []).append(u["job"])
    return out
