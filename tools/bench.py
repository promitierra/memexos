#!/usr/bin/env python3
"""Benchmark report: one row per compile run, read from the event log.

The point is to see whether a change is a change for the better. Speed and
quality travel together on purpose: a run is not an improvement for being
faster if the pages it produced got worse. That is why every row carries
gate results and claim counts next to seconds and cost.

No model calls, no network: it only reads <state>/events/<YYYY-MM>.jsonl.

Usage:
  python tools/bench.py report [--state DIR] [--month YYYY-MM] [--json]
  python tools/bench.py report --compare <run-id>   # a row against another

Exit codes: 0 ok · 2 no events found.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def state_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    try:
        from memexos import config

        return config.load().paths.state
    except Exception:  # noqa: BLE001 — the tool must still work with an explicit --state
        return Path.home() / ".local" / "state" / "memexos"


def load_events(state: Path, month: str | None) -> list[dict]:
    directory = state / "events"
    if not directory.is_dir():
        # The state lives per vault (<state>/memex-<hash>); accept either the
        # parent or the vault directory so --state is not a guessing game.
        candidates = [p / "events" for p in sorted(state.glob("memex-*")) if (p / "events").is_dir()]
        if len(candidates) == 1:
            directory = candidates[0]
    files = [directory / f"{month}.jsonl"] if month else sorted(directory.glob("????-??.jsonl"))
    events: list[dict] = []
    for path in files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue           # a torn last line after a crash must not hide the rest
    return events


def summarize(events: list[dict]) -> list[dict]:
    runs: dict[str, dict] = {}
    for ev in events:
        rid = ev.get("run") or ""
        if not rid:
            continue
        row = runs.setdefault(rid, {
            "run": rid, "started": ev.get("ts") or "", "job": ev.get("job") or "",
            "wall_s": None, "status": "", "calls": 0, "cost_usd": 0.0,
            "steps": defaultdict(int), "seconds_by_step": defaultdict(float),
            "gate1": None, "gate1_errors": 0, "gate1_warnings": 0,
            "gate2": None, "claims": None, "flagged": None, "pages": None,
        })
        if ev.get("ts") and (not row["started"] or ev["ts"] < row["started"]):
            row["started"] = ev["ts"]
        kind = ev.get("type") or ""
        data = ev.get("data") or {}
        if kind == "run.end":
            row["wall_s"] = data.get("seconds")
            row["status"] = data.get("status") or ""
        elif kind == "step.end":
            name = str(data.get("name") or "?")
            row["steps"][name] += 1
            row["seconds_by_step"][name] += float(data.get("seconds") or 0)
            row["calls"] += 1
            row["cost_usd"] += float(data.get("cost_usd") or 0)
        elif kind == "gate1.result":
            row["gate1"] = bool(data.get("passed"))
            # compile.py emits counts, not lists; a list is tolerated so the
            # report also reads hand-written fixtures.
            errors = data.get("errors") or 0
            warnings = data.get("warnings") or 0
            row["gate1_errors"] = errors if isinstance(errors, int) else len(errors)
            row["gate1_warnings"] = warnings if isinstance(warnings, int) else len(warnings)
        elif kind == "gate2.result":
            row["gate2"] = data.get("passed")
        elif kind in ("selfcheck.counts", "selfcheck.result"):
            row["claims"] = data.get("total")
            row["flagged"] = data.get("flagged")
        elif kind == "publish":
            stats = data.get("stats") if isinstance(data.get("stats"), dict) else data
            if isinstance(stats, dict) and "added_pages" in stats:
                row["pages"] = stats.get("added_pages")
    return sorted(runs.values(), key=lambda r: (r["started"], r["run"]))


def verdict(row: dict) -> str:
    """First fatal gate wins: a run cannot be "published" if gate 1 rejected it."""
    if row["gate1"] is False:
        return "gate 1 falló"
    if row["gate2"] is True:
        return "publicado"
    if row["gate2"] is False:
        return "revisión falló"
    if row["gate1"] is True:
        return "gate 1 ok, sin revisión"
    return "incompleto"


def fmt(value, width: int, kind: str = "s") -> str:
    if value is None:
        return "—".rjust(width)
    if kind == "f":
        return f"{value:.2f}".rjust(width)
    return str(value).rjust(width)


def table(rows: list[dict]) -> str:
    head = (f"{'corrida':<20} {'inicio':<17} {'pared':>6} {'llam':>5} {'costo':>7} "
            f"{'writer':>7} {'juez':>6} {'g1':>4} {'g2':>4} {'afirm':>6} {'obs':>4}  estado")
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(
            f"{r['run'][:20]:<20} {r['started'][:16]:<17} {fmt(r['wall_s'], 6)} {fmt(r['calls'], 5)} "
            f"{fmt(r['cost_usd'], 7, 'f')} {fmt(r['seconds_by_step'].get('write'), 7)} "
            f"{fmt(r['seconds_by_step'].get('selfcheck'), 6)} "
            f"{'ok' if r['gate1'] else ('no' if r['gate1'] is False else '—'):>4} "
            f"{'ok' if r['gate2'] else ('no' if r['gate2'] is False else '—'):>4} "
            f"{fmt(r['claims'], 6)} {fmt(r['flagged'], 4)}  {verdict(r)}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bench.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["report"])
    ap.add_argument("--state", help="state directory (default: from the config)")
    ap.add_argument("--month", help="YYYY-MM (default: every month on disk)")
    ap.add_argument("--json", action="store_true", help="machine-readable rows")
    a = ap.parse_args(argv)

    events = load_events(state_dir(a.state), a.month)
    rows = summarize(events)
    if not rows:
        print("sin corridas en el event log", file=sys.stderr)
        return 2

    if a.json:
        for r in rows:
            r["steps"] = dict(r["steps"])
            r["seconds_by_step"] = dict(r["seconds_by_step"])
            r["verdict"] = verdict(r)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
