"""Structured event log: the single source of truth for "what is the system doing".

Every MemexOS command that does work writes its own events here, through emit() and the run() / step() / job()
context managers below; nothing else writes them and nothing parses human-readable logs. One JSON line per event, in
one file per month, <state>/events/<YYYY-MM>.jsonl:

    {"ts": "2026-11-01T03:17:05+0800", "run": "ingest-20261101-031700-4f2a", "job": "ingest",
     "type": "gate2.result", "data": {"passed": false, "problems": 3}}

- run.start / run.end      a command, with its pid (a run whose process died shows as interrupted) and its outcome
- step.start / step.end    one stage of the work (write, gate2, admit, …) with its duration and result
- everything else          what happened: gate results, publish, hold, captures, model calls with tokens and cost, …

`memex status`, the console and the evaluations read these events (memexos.runs rebuilds timelines from them).
Months older than state.keep_events_months are deleted. A human-readable line per event also goes to
<state>/memex.log (rendered from the language pack, rotated by size) for people who like `tail -f`.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

from memexos import i18n

_run: dict[str, str] = {}
KEEP_MONTHS = 12                 # set from state.keep_events_months by the command line
LOG_MAX_BYTES = 10 << 20
LOG_KEEP = 3


def configure(cfg) -> None:
    global KEEP_MONTHS
    KEEP_MONTHS = int(cfg.get("state.keep_events_months") or 12)


def month_file(state_dir: Path, t: float | None = None) -> Path:
    return state_dir / "events" / (time.strftime("%Y-%m", time.localtime(t)) + ".jsonl")


def files(state_dir: Path) -> list[Path]:
    """Event files oldest first; a legacy single events.jsonl counts as the oldest."""
    d = state_dir / "events"
    out = sorted(d.glob("????-??.jsonl")) if d.is_dir() else []
    legacy = state_dir / "events.jsonl"
    return ([legacy] if legacy.exists() else []) + out


def _prune(state_dir: Path) -> None:
    if KEEP_MONTHS <= 0:
        return
    now = time.localtime()
    y, m = now.tm_year, now.tm_mon - KEEP_MONTHS
    while m <= 0:
        y, m = y - 1, m + 12
    oldest = f"{y:04d}-{m:02d}"
    for f in files(state_dir):
        if f.parent.name == "events" and f.stem < oldest:
            f.unlink(missing_ok=True)


def _log_line(state_dir: Path, line: str) -> None:
    p = state_dir / "memex.log"
    try:
        if p.stat().st_size > LOG_MAX_BYTES:
            for i in range(LOG_KEEP - 1, 0, -1):
                older = state_dir / f"memex.log.{i}"
                if older.exists():
                    older.replace(state_dir / f"memex.log.{i + 1}")
            p.replace(state_dir / "memex.log.1")
    except OSError:
        pass
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def proc_start(pid: int) -> str:
    """A marker that changes when the OS hands the same pid to a different process.

    `os.kill(pid, 0)` answers "some process has this pid", not "it is the process
    that started the run". In a container the console is always pid 7, so after a
    restart the orphaned run of the previous console looks alive forever and the
    UI keeps saying it is working. The marker is the process start instant:
    /proc/<pid>/stat on Linux, `ps -o lstart=` elsewhere. Empty when the platform
    offers neither, and then only the pid is checked — the old, weaker behaviour.
    """
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as f:
            data = f.read().decode("utf-8", "replace")
        # fields after the parenthesised command name start at field 3; starttime is field 22
        return data.rsplit(")", 1)[-1].split()[19]
    except (OSError, ValueError, IndexError, TypeError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def new_run(job: str) -> str:
    """Start a run. A run started by another run's process (through MEMEX_RUN) records that run as its parent."""
    parent = os.environ.get("MEMEX_RUN", "")
    rid = f"{job}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    _run.update(id=rid, job=job, parent=parent if parent != rid else "")
    os.environ["MEMEX_RUN"] = rid
    return rid


@contextlib.contextmanager
def run(state_dir: Path, job: str) -> Iterator[str]:
    """A whole command: run.start (with the pid and its start marker, so readers can tell a live run from a
    crashed one even when the OS reuses the pid) … run.end."""
    saved, saved_env = dict(_run), os.environ.get("MEMEX_RUN")
    rid = new_run(job)
    t0 = time.time()
    emit(state_dir, "run.start", pid=os.getpid(), start=proc_start(os.getpid()))
    status = "error"
    try:
        yield rid
        status = "ok"
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except SystemExit as e:
        status = "ok" if not e.code else "error"
        raise
    finally:
        emit(state_dir, "run.end", seconds=round(time.time() - t0), status=status)
        # back to the enclosing run (or none), so a later run in the same process is not recorded as this one's child
        _run.clear()
        _run.update(saved)
        if saved_env is None:
            os.environ.pop("MEMEX_RUN", None)
        else:
            os.environ["MEMEX_RUN"] = saved_env


@contextlib.contextmanager
def job(name: str) -> Iterator[None]:
    """Attribute the events inside to another job name (a nightly run doing intake, then fixes, …)."""
    prev = _run.get("job", "")
    _run["job"] = name
    try:
        yield
    finally:
        _run["job"] = prev


@contextlib.contextmanager
def step(state_dir: Path, name: str, **data: Any) -> Iterator[dict]:
    """One stage of the work (write, gate2, admit, …): step.start … step.end with its duration.
    The body may fill the yielded dict (result, cost, model…); it is recorded on step.end."""
    info: dict[str, Any] = {}
    t0 = time.time()
    emit(state_dir, "step.start", name=name, **data)
    try:
        yield info
    except BaseException as e:
        info.setdefault("result", "error")
        info.setdefault("why", repr(e)[:200])
        raise
    finally:
        emit(state_dir, "step.end", name=name, seconds=round(time.time() - t0, 1), **info)


def current_run() -> str:
    return _run.get("id") or os.environ.get("MEMEX_RUN", "")


def emit(state_dir: Path, type: str, job: str | None = None, **data: Any) -> dict:
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": current_run(), "job": job or _run.get("job", ""),
          "type": type, "data": data}
    if _run.get("parent"):
        ev["parent"] = _run["parent"]
    target = month_file(state_dir)
    new_month = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    if new_month:
        _prune(state_dir)
    _log_line(state_dir, render(ev))
    return ev


def _money(value: float) -> str:
    """The one money formatter lives in pricing; rendering only borrows it."""
    from memexos import pricing

    return pricing.money(value)


def render(ev: dict, lang: str | None = None, job: bool = True) -> str:
    """One readable line from the language pack. Lists also give {key_n} (their length); a boolean `passed`
    picks the variant event.<type>_pass / _fail; steps get {label} from step.<name>."""
    raw = ev.get("data") or {}
    data: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            data[k], data[f"{k}_n"] = ", ".join(map(str, v)), len(v)
        elif isinstance(v, float):
            data[k] = round(v, 2)
        else:
            data[k] = v
    if ev["type"].startswith("step."):
        data["label"] = i18n.t(f"step.{raw.get('name', '')}", lang) if i18n.has(f"step.{raw.get('name', '')}") else raw.get("name", "")
    if isinstance(raw.get("cost_usd"), (int, float)):
        data["cost"] = _money(raw["cost_usd"])
    else:
        data["cost"] = "-"
    keys = [f"event.{ev['type']}"]
    if isinstance(raw.get("passed"), bool):
        keys.insert(0, f"event.{ev['type']}_{'pass' if raw['passed'] else 'fail'}")
    head = f"{ev.get('job') or '-'}: " if job else ""
    for key in keys:
        if i18n.has(key):
            return head + i18n.t(key, lang, **data)
    extra = " ".join(f"{k}={v}" for k, v in data.items() if k != "cost" or v != "-")
    return f"{head}{ev['type']} {extra}".rstrip()


def read(state_dir: Path, since: float | None = None, types: tuple[str, ...] = (), run: str | None = None) -> Iterator[dict]:
    first = time.strftime("%Y-%m", time.localtime(since)) if since is not None else ""
    for p in files(state_dir):
        if p.parent.name == "events" and p.stem < first:
            continue                              # a whole month before `since`
        with open(p, "rb") as f:
            if since is not None:
                f.seek(_offset_since(f, since))
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if types and not ev.get("type", "").startswith(types):
                    continue
                if run and ev.get("run") != run:
                    continue
                if since is not None and _epoch(ev.get("ts", "")) < since:
                    continue
                yield ev


def _offset_since(f, since: float) -> int:
    """Events are appended in time order, so the first one at or after `since` can be found by bisecting byte offsets."""
    f.seek(0, os.SEEK_END)
    lo, hi = 0, f.tell()
    while hi - lo > 4096:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()                     # skip the partial line
        line = f.readline()
        try:
            t = _epoch(json.loads(line).get("ts", "")) if line else float("inf")
        except ValueError:
            t = 0.0
        if t < since:
            lo = mid
        else:
            hi = mid
    if lo:
        f.seek(lo)
        f.readline()
        return f.tell()
    return 0


def today(offset_minutes: int = 0) -> str:
    """The reader's calendar day as YYYY-MM-DD.

    `offset_minutes` is east of UTC: Bogota is -300, so its day ends at 05:00
    UTC. A day cut in the process's own timezone disagrees with the timestamps
    the console renders, and the counter resets hours before the day is over.
    """
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + offset_minutes * 60))


def day_of(ts: str, offset_minutes: int = 0) -> str:
    """The calendar day an event belongs to, in the reader's timezone."""
    from memexos import pricing

    parsed = pricing.parse_ts(ts)
    if parsed is None:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(parsed.timestamp() + offset_minutes * 60))


def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0
