"""What the console shows, built from the vault, its git history, the event log and the ledgers.

Every builder returns plain JSON-able data with stable ids (page types, relations, stages, verdicts); the browser
turns ids into words with the language pack, so nothing here depends on the display language.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from memexos import backends, events, i18n, rawref, runs
from memexos.util import split_frontmatter

TYPES = {"topics": "topic", "concepts": "concept", "entities": "entity", "sources": "source"}
LAYER = {"topic": 0, "concept": 1, "entity": 1, "source": 2, "raw": 3}
RING = ["intake", "classify", "compile", "gate1", "selfcheck", "gate2", "publish", "retrieve", "feedback"]
RAW_KIND = re.compile(r"^\d{4}-\d{2}-\d{2}-([a-z]+)-")
INDEX_LINE = re.compile(r"^\s*-\s*\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]\s*[—–:-]+\s*(.+)$", re.M)
LINK = re.compile(r"\[\[([^\]]+)\]\]")
EVENT_KIND = {
    "alert": {"ingest.failed", "stage.merge_failed", "hold", "gate2.unavailable", "intake.channel_failed", "capture.failed",
              "classify.failed", "visual.failed", "nightly.step_failed", "inbox.record_blocked"},
    "warn": {"writer.retry", "writer.denials", "intake.quota", "ingest.waiting", "stage.discard", "capture.redacted"},
    "ok": {"publish", "intake.done", "capture.done", "capture.video", "visual.done", "correct.done", "catalog.sync", "index.update",
           "nightly.done"},
}
FEED_SKIP = {"model.call", "step.end", "run.start", "run.end", "search", "read", "stage.open", "index.qmd", "catalog.check"}


def event_kind(ev: dict) -> str:
    t, d = ev.get("type", ""), ev.get("data") or {}
    if isinstance(d.get("passed"), bool):
        return "ok" if d["passed"] else "warn"
    if t == "gate2.result":
        return "alert"
    return next((k for k, types in EVENT_KIND.items() if t in types), "info")


def unpriced_backends(cfg) -> list[str]:
    """Backends with a model but no price configured: their cost is unknown, not zero.

    The command-line backends report their own cost, so they are never listed:
    there is nothing to configure for them.
    """
    out = []
    for name, sec in (cfg.section("backends") or {}).items():
        if not isinstance(sec, dict) or not sec.get("model"):
            continue
        if name in ("claude-code", "codex"):
            continue
        if not (float(sec.get("price_in") or 0) or float(sec.get("price_out") or 0)):
            out.append(name)
    return sorted(out)


def usage_rows(calls) -> list[dict]:
    """Aggregate `model.call` events per backend:model: calls, tokens and cost.

    Tokens are measured, not guessed, so this table is useful even when no
    price is configured: it answers "which model consumes what" without
    pretending to know what it costs.
    """
    rows: dict[str, dict] = {}
    for e in calls:
        d = e.get("data") or {}
        key = f"{d.get('backend') or '?'}:{d.get('model') or '?'}"
        row = rows.setdefault(key, {"model": key, "calls": 0, "input": 0, "cached": 0, "output": 0,
                                    "cost": 0.0, "priced_calls": 0, "seconds": 0.0})
        row["calls"] += 1
        tok = d.get("tokens") or {}
        row["input"] += int(tok.get("input_tokens") or 0)
        row["cached"] += int(tok.get("cached_tokens") or 0)
        row["output"] += int(tok.get("output_tokens") or 0)
        row["seconds"] += float(d.get("seconds") or 0)
        if isinstance(d.get("cost_usd"), (int, float)):
            row["cost"] += d["cost_usd"]
            row["priced_calls"] += 1
    for row in rows.values():
        row["cost"] = round(row["cost"], 4)
        row["seconds"] = round(row["seconds"], 1)
        row["priced"] = row["priced_calls"] > 0
    return sorted(rows.values(), key=lambda r: (-r["input"], r["model"]))


class Data:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vault: Path = cfg.vault
        self.state: Path = cfg.paths.state
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._usage_refresh = 0.0

    # ------------------------------------------------------------ plumbing
    def cached(self, key: str, version, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == version:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (version, val)
        return val

    def git(self, *args: str, timeout: float = 60) -> str:
        return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(self.vault), *args], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL).stdout

    def head(self) -> str:
        """Changes when anything is committed; also covers uncommitted edits through the index file's mtime."""
        try:
            idx = (self.vault / ".git" / "index").stat().st_mtime
        except OSError:
            idx = 0
        return self.git("rev-parse", "HEAD").strip() + f":{idx}"

    def mtime(self, *paths: Path) -> tuple:
        return tuple((p.stat().st_mtime, p.stat().st_size) if p.exists() else None for p in paths)

    def safe_rel(self, path: str) -> str | None:
        """Only the vault's wiki/, raw/, inbox/ and the top-level markdown files can be opened."""
        p = os.path.normpath((path or "").lstrip("/"))
        if p.startswith("..") or not (p.startswith(("wiki/", "raw/", "inbox/")) or p in ("index.md", "log.md", "AGENTS.md")):
            return None
        return p if (self.vault / p).is_file() else None

    # ------------------------------------------------------------ the knowledge base
    def taxonomy(self):
        try:
            from memexos.catalog.taxonomy import Taxonomy
        except ImportError:
            return None
        return self.cached("tx", self.mtime(self.cfg.paths.ledger / "taxonomy.md", self.cfg.paths.ledger / "taxonomy-nodes.jsonl"),
                           lambda: Taxonomy(self.cfg))

    def catalog(self) -> dict[str, dict]:
        def build():
            try:
                from memexos.catalog.classify import load
            except ImportError:
                return {}
            return load(self.cfg)
        return self.cached("catalog", self.mtime(self.cfg.paths.ledger / "catalog.jsonl"), build)

    def domain_label(self, d: str | None) -> str:
        tx = self.taxonomy()
        return tx.label(d) if tx and d else (d or "")

    def domain_top(self, d: str | None) -> str:
        from memexos.catalog.taxonomy import Taxonomy
        return Taxonomy.top(d) if d else ""

    def domain_group(self, d: str | None) -> str:
        """Second-level grouping: official domains as they are; drafts as "Top/name*" so they are counted apart."""
        tx = self.taxonomy()
        if not tx or not d:
            return d or ""
        kind, x = tx.resolve(d)
        if kind == "official":
            return x
        if kind == "draft":
            return f"{x['parent']}/{x['name']}*"
        return (d or "").removeprefix("draft:")

    def dim(self, dim: str, value):
        from memexos.catalog import dimensions
        return dimensions.label(dim, value, i18n.language()) if value else value

    def pages(self) -> list[dict]:
        return self.cached("pages", self.head(), self._build_pages)

    def _build_pages(self) -> list[dict]:
        summaries: dict[str, str] = {}
        try:
            for m in INDEX_LINE.finditer((self.vault / "index.md").read_text(encoding="utf-8")):
                summaries.setdefault(m.group(1).strip(), m.group(2).strip()[:200])
        except OSError:
            pass
        names = rawref.index(self.vault)
        pages, links = [], collections.defaultdict(set)
        for f in sorted((self.vault / "wiki").rglob("*.md")):
            rel = f.relative_to(self.vault).as_posix()
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = split_frontmatter(text)
            parts = rel.split("/")
            kind = TYPES.get(parts[1], "other") if len(parts) > 2 else "other"
            outs = {x.split("|")[0].split("#")[0].strip() for x in LINK.findall(body)}
            for o in outs:
                links[o].add(f.stem)
            first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith(("#", ">", "|", "-", "!"))), "")
            dom = fm.get("domain")
            pages.append({"path": rel, "name": f.stem, "type": kind, "domain": dom if isinstance(dom, list) else ([dom] if dom else []),
                          "created": str(fm.get("created") or ""), "updated": str(fm.get("updated") or ""),
                          "raws": sorted(r for r in rawref.find(text, names, self.vault) if not r.startswith("raw/assets/")),
                          "summary": summaries.get(f.stem) or first[:160], "chars": len(body), "out": sorted(outs)})
        for p in pages:
            p["in"] = sorted(links.get(p["name"], ()))
        return pages

    def raw_files(self) -> list[str]:
        root = self.vault / "raw"
        return sorted(p.relative_to(self.vault).as_posix() for p in root.glob("*") if p.is_file() and p.suffix in (".md", ".html"))

    def raw_kinds(self) -> list:
        c = collections.Counter()
        for r in self.raw_files():
            m = RAW_KIND.match(Path(r).name)
            c[m.group(1) if m else "other"] += 1
        return c.most_common()

    def growth(self) -> list[dict]:
        def build():
            out = collections.defaultdict(lambda: {"raw": 0, "wiki": 0})
            day = None
            for line in self.git("log", "--diff-filter=A", "--name-only", "--format=@%ad", "--date=short", "--", "raw", "wiki",
                                 timeout=120).splitlines():
                if line.startswith("@"):
                    day = line[1:]
                elif line.startswith("raw/") and not line.startswith(("raw/assets/", "raw/feedback/")) and line.endswith((".md", ".html")):
                    out[day]["raw"] += 1
                elif line.startswith("wiki/") and line.endswith(".md"):
                    out[day]["wiki"] += 1
            return sorted(({"day": d, **v} for d, v in out.items() if d), key=lambda x: x["day"])
        return self.cached("growth", self.head(), build)

    def overview(self, q: dict) -> dict:
        ps = self.pages()
        cat = self.catalog()
        doms = [r.get("domain") for r in cat.values() if r.get("domain")]
        recent = []
        for line in self.git("log", "-40", "--first-parent", "--format=%h\t%ad\t%s", "--date=format:%m-%d %H:%M").splitlines():
            h, d, s = (line.split("\t") + ["", ""])[:3]
            recent.append({"sha": h, "when": d, "subject": s})
        topics = [p for p in ps if p["type"] == "topic"]
        return {"raw": len(self.raw_files()), "raw_kinds": self.raw_kinds(), "wiki": len(ps),
                "wiki_types": collections.Counter(p["type"] for p in ps).most_common(),
                "domains1": collections.Counter(self.domain_top(d) for d in doms).most_common(),
                "domains2": collections.Counter(self.domain_group(d) for d in doms).most_common(),
                "evidence": collections.Counter(self.dim("evidence", r.get("evidence")) for r in cat.values() if r.get("evidence")).most_common(),
                "content_types": collections.Counter(self.dim("content_type", r.get("content_type")) for r in cat.values()
                                                     if r.get("content_type")).most_common(),
                "growth": self.growth(), "recent": recent,
                "topics": [{"path": p["path"], "name": p["name"], "summary": p["summary"], "in": len(p["in"]), "out": len(p["out"])} for p in topics],
                "most_linked": sorted(({"path": p["path"], "name": p["name"], "type": p["type"], "in": len(p["in"])} for p in ps),
                                      key=lambda x: -x["in"])[:15]}

    def page_domains(self, p: dict) -> set[str]:
        cat = self.catalog()
        return {d for d in list(p["domain"]) + [cat.get(r, {}).get("domain", "") for r in p["raws"]] if d}

    def page_list(self, q: dict) -> dict:
        typ, dom, text = q.get("type", ""), q.get("domain", ""), q.get("q", "").strip().lower()
        used = self.usage_counts()
        out = []
        for p in self.pages():
            if typ and p["type"] != typ:
                continue
            if dom and not any(self.domain_top(d) == dom or self.domain_group(d) == dom or d == dom for d in self.page_domains(p)):
                continue
            if text and text not in p["name"].lower() and text not in p["summary"].lower():
                continue
            out.append({k: p[k] for k in ("path", "name", "type", "summary", "created", "updated")} |
                       {"domain": [self.domain_label(d) for d in p["domain"]], "raws": len(p["raws"]), "in": len(p["in"]),
                        "used": used.get(p["path"], 0)})
        sort = q.get("sort", "in")
        key = {"in": lambda x: -x["in"], "used": lambda x: -x["used"], "new": lambda x: x["created"] or "", "name": lambda x: x["name"]}.get(sort)
        out.sort(key=key or (lambda x: -x["in"]), reverse=(sort == "new"))
        return {"total": len(out), "pages": out[:500]}

    def usage_events(self) -> list[dict]:
        p = self.cfg.paths.state / "usage" / "events.jsonl"

        def build():
            from memexos.util import read_jsonl
            return [e for e in read_jsonl(p) if e.get("kind") == "real"]
        return self.cached("usage_ev", self.mtime(p), build)

    def usage_counts(self) -> collections.Counter:
        from memexos.usage import norm_page

        def build():
            c = collections.Counter()
            for e in self.usage_events():
                for call in e.get("calls", []):
                    if call.get("op") in ("get", "read") and (call.get("opened") or call.get("page")):
                        c[norm_page(self.cfg, call.get("opened") or call.get("page"))] += 1
            return c
        return self.cached("usage_cnt", self.mtime(self.cfg.paths.state / "usage" / "events.jsonl"), build)

    def gate2_dirs(self) -> list[Path]:
        root = self.state / "gate2"
        return sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []

    def gate2_issues(self) -> dict[str, list[dict]]:
        """Problems reviews found, by page."""
        from memexos.pipeline import report
        from memexos.pipeline.stage import ledger

        def build():
            fixed = ledger(self.vault, "fixed-reviews.txt")
            idx = collections.defaultdict(list)
            for d in self.gate2_dirs():
                for pr in report.problems(d):
                    for pg in re.findall(r"(wiki/[^；;，,\n]+?\.md|index\.md)", pr.get("page", "")):
                        idx[pg].append({"review": d.name, "kind": pr["kind"], "label": i18n.t(f"report.kind.{pr['kind']}"),
                                        "claim": (pr.get("wiki") or pr.get("source") or "")[:300], "reason": (pr.get("why") or "")[:300],
                                        "fixed": d.name in fixed})
            return idx
        ver = (self.head(), max((d.stat().st_mtime for d in self.gate2_dirs()), default=0))
        return self.cached("g2idx", ver, build)

    def page(self, q: dict) -> dict:
        rel = self.safe_rel(q.get("path", ""))
        if not rel:
            return {"error": "not_found"}
        text = (self.vault / rel).read_text(encoding="utf-8", errors="replace")
        fm, body = split_frontmatter(text)
        p = next((x for x in self.pages() if x["path"] == rel), {})
        cat = self.catalog()
        hist = []
        for line in self.git("log", "--follow", "-30", "--format=%h\t%ad\t%s", "--date=format:%Y-%m-%d %H:%M", "--", rel).splitlines():
            h, d, s = (line.split("\t") + ["", ""])[:3]
            hist.append({"sha": h, "when": d, "subject": s[:200]})
        from memexos.usage import norm_page
        used = []
        for e in self.usage_events():
            if any(norm_page(self.cfg, c.get("opened") or c.get("page")) == rel for c in e.get("calls", []) if c.get("op") in ("get", "read")):
                used.append({"when": (e.get("session_last") or "")[:16], "agent": e.get("agent"), "project": Path(e.get("cwd") or "").name,
                             "question": (e.get("question") or "")[:200]})
        raws = p.get("raws") or sorted(r for r in rawref.find(text, None, self.vault) if not r.startswith("raw/assets/"))
        sources = [{"path": r, "exists": (self.vault / r).exists(), "domain": self.domain_label(cat.get(r, {}).get("domain")) or None,
                    "evidence": self.dim("evidence", cat.get(r, {}).get("evidence"))} for r in raws]
        c = cat.get(rel) if rel.startswith("raw/") else None
        catalog = None
        if c:
            catalog = {"domain": self.domain_label(c.get("domain")), "content_type": self.dim("content_type", c.get("content_type")),
                       "level": self.dim("level", c.get("level")), "evidence": self.dim("evidence", c.get("evidence")),
                       "durability": self.dim("durability", c.get("durability")), "summary": c.get("summary", ""),
                       "scope_note": c.get("scope_note", "")}
        return {"path": rel, "frontmatter": fm, "body": body, "type": p.get("type") or ("raw" if rel.startswith("raw/") else ""),
                "in": p.get("in", []), "out": p.get("out", []), "sources": sources,
                "cited_by": [x["path"] for x in self.pages() if rel.startswith("raw/") and rel in x["raws"]], "history": hist,
                "issues": self.gate2_issues().get(rel, []), "used": sorted(used, key=lambda x: x["when"], reverse=True)[:50], "catalog": catalog}

    def resolve(self, q: dict) -> dict:
        name = q.get("name", "").strip()
        return {"path": next((p["path"] for p in self.pages() if p["name"] == name), None)}

    def search(self, q: dict) -> dict:
        from memexos.search import cli as scli
        from memexos.search import engine
        text = q.get("q", "").strip()
        if not text:
            return {"hits": []}
        if self._cache.get("search_head") != self.head():
            scli._compiled.clear()                   # the raw → source page map is rebuilt after new commits
            scli._raw_names.clear()
            self._cache["search_head"] = self.head()
        t0 = time.time()
        hits, kws, degraded = engine.search(self.cfg, text, None, 12, q.get("mode", "auto"), None)
        scli.note_search(self.cfg, text, q.get("mode", "auto"), len(hits), time.time() - t0, job="console")
        out = []
        for h in hits:
            label, cls, page = scli.describe(self.cfg, h)
            out.append({"path": h["path"], "title": h.get("title"), "snippet": h.get("snippet", ""), "kind": label, "classification": cls,
                        "compiled_page": page})
        return {"hits": out, "keywords": kws, "degraded": degraded, "seconds": round(time.time() - t0, 2)}

    def graph(self, q: dict) -> dict:
        return self.cached("graph", (self.head(), self.mtime(self.cfg.paths.ledger / "catalog.jsonl")), self._build_graph)

    def _build_graph(self) -> dict:
        ps = self.pages()
        cat = self.catalog()
        by_name = {p["name"]: p for p in ps}
        by_lower = {p["name"].lower(): p for p in ps}
        missing: collections.Counter = collections.Counter()
        nodes, idx = [], {}
        for p in ps:
            idx[p["path"]] = len(nodes)
            nodes.append({"id": p["path"], "name": p["name"], "type": p["type"], "layer": LAYER.get(p["type"], 1),
                          "domain": self.domain_top(p["domain"][0]) if p["domain"] else "", "inferred": not p["domain"],
                          "summary": re.sub(r"\*\*|`|\[\[|\]\]", "", p["summary"])[:160], "in": len(p["in"]), "out": len(p["out"])})
        for r in sorted({r for p in ps for r in p["raws"]}):
            c = cat.get(r, {})
            idx[r] = len(nodes)
            title = re.sub(r"^\d{4}-\d{2}-\d{2}-[a-z]+-[a-z0-9]+-", "", Path(r).stem)
            nodes.append({"id": r, "name": title[:60], "type": "raw", "layer": 3, "domain": self.domain_top(c.get("domain")),
                          "inferred": False, "summary": (c.get("summary") or "")[:160], "evidence": self.dim("evidence", c.get("evidence")),
                          "in": 0, "out": 0, "exists": (self.vault / r).exists()})
        edges = []
        for p in ps:
            i = idx[p["path"]]
            for o in p["out"]:
                t = by_name.get(o) or by_lower.get(o.lower())
                if t and t["path"] != p["path"]:
                    edges.append([i, idx[t["path"]], relation(p["type"], t["type"])])
                elif not t and o and not o.lower().endswith((".png", ".jpg", ".jpeg", ".pdf")) and \
                        not o.startswith(("raw/", "index", "log", "AGENTS")):
                    missing[o] += 1
            for r in p["raws"]:
                if r in idx:
                    edges.append([i, idx[r], "cites"])
                    nodes[idx[r]]["in"] += 1
        # pages without a classification take the most common domain among the pages and sources they connect to
        nb = collections.defaultdict(list)
        for a, b, _ in edges:
            nb[a].append(b)
            nb[b].append(a)
        for rnd in range(2):
            for i, n in enumerate(nodes):
                if n["inferred"] and n["type"] != "raw":
                    c = collections.Counter(nodes[j]["domain"] for j in nb[i] if nodes[j]["domain"] and (not nodes[j]["inferred"] or rnd))
                    n["domain"] = c.most_common(1)[0][0] if c else n["domain"]
        doms = collections.Counter(n["domain"] for n in nodes if n["domain"])
        return {"nodes": nodes, "edges": edges, "domains": doms.most_common(),
                "missing": [{"name": n, "links": c} for n, c in missing.most_common(50)],
                "totals": {"wiki": len(ps), "raw": len(self.raw_files()), "cited_raw": sum(1 for n in nodes if n["type"] == "raw"),
                           "links": len(edges), "dangling": len(missing),
                           "domains": len({self.domain_group(r.get("domain")) for r in cat.values() if r.get("domain")})}}

    # ------------------------------------------------------------ the pipeline
    def recent_events(self, hours: float = 24, limit: int = 40) -> list[dict]:
        out = []
        for ev in events.read(self.state, since=time.time() - hours * 3600):
            if ev.get("type") in FEED_SKIP:
                continue
            t = events._epoch(ev.get("ts", ""))
            out.append({"t": t, "type": ev["type"], "job": ev.get("job", ""), "kind": event_kind(ev),
                        "text": events.render(ev, job=False)[:240]})
        return out[-limit:]

    def backend_state(self) -> list[dict]:
        def build():
            st = backends.status(self.cfg)
            return [{"name": k, "available": v["available"], "why": v["why"],
                     "exhausted": bool(v["exhausted"]), "until": (v["exhausted"] or {}).get("until_str") or (v["exhausted"] or {}).get("at_str")}
                    for k, v in st.items()]
        return self.cached("backends", int(time.time() // 60), build)

    def live(self, q: dict) -> dict:
        from memexos import status
        snap = status.snapshot(self.cfg)
        units = runs.timeline(self.state, time.time() - 6 * 3600)
        act = runs.active_stages(self.state, units)
        searching = [e for e in events.read(self.state, since=time.time() - 90, types=("search",)) if e.get("job") != "console"]
        if searching:
            act.setdefault("retrieve", []).append("search")
        if any(u["job"] == "reindex" and u["status"] == "running" for u in units):
            act.setdefault("publish", []).append("reindex")
        week = time.time() - 7 * 86400
        g2 = collections.Counter()
        for e in events.read(self.state, since=week, types=("gate2.result",)):
            d = e.get("data") or {}
            if d.get("mode") == "ingest" and isinstance(d.get("passed"), bool):
                g2["pass" if d["passed"] else "fail"] += 1
        ev = sorted(self.usage_events(), key=lambda e: e.get("session_last") or "", reverse=True)
        day_ago = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 86400))
        from memexos.usage import norm_page
        used_pages = list(dict.fromkeys(norm_page(self.cfg, c.get("opened") or c.get("page")) for e in ev[:30] for c in e.get("calls", [])
                                        if c.get("op") in ("get", "read") and (c.get("opened") or c.get("page"))))
        changed = [l for l in self.git("log", "--since=6.hours", "--name-only", "--format=", "--", "wiki").splitlines() if l.endswith(".md")]
        verdicts = self._verdicts()
        clear = [v for v in verdicts.values() if v != "unclear"]
        return {
            "time": time.strftime("%H:%M:%S"), "date": time.strftime("%Y-%m-%d"), "state": snap["state"], "state_key": snap["state_key"],
            "paused": snap["paused"], "alerts": snap["alerts"], "warn": snap["warn"], "doing": snap["doing"], "done": snap["done"],
            "logins": self._logins(),
            "running": [{k: u[k] for k in ("id", "job", "t0", "active")} for u in units if u["status"] == "running"],
            "stages": [{"id": s, "active": s in act, "jobs": act.get(s, [])} for s in RING],
            "metrics": {"wiki": len(self.pages()), "raw": len(self.raw_files()), "pending": len(snap["pending"]),
                        "cost_today": snap["cost_today"], "published_today": snap["published_today"],
                        "gate2_pass": g2["pass"], "gate2_fail": g2["fail"],
                        "usage_24h": sum(1 for e in ev if (e.get("session_last") or "") >= day_ago), "usage_30d": len(ev),
                        "helped": round(sum(v in ("helped", "honest_gap") for v in clear) / len(clear), 3) if clear else None},
            "backends": self.backend_state(),
            "log": self.recent_events(24, 40),
            "queries": [{"q": (e.get("question") or "")[:160], "project": Path(e.get("cwd") or "").name, "agent": e.get("agent"),
                         "when": (e.get("session_last") or "")[5:16].replace("T", " ")} for e in ev[:8]],
            "pulse": {"changed": changed[:60], "used": used_pages[:40]},
            "inbox": snap.get("inbox") or {},
            "schedule": {"intake_at": self.cfg.get("schedule.intake_at"), "lint_at": self.cfg.get("schedule.lint_at")},
        }

    def _logins(self) -> list[dict]:
        """Collector sites waiting for the person to log in (the console offers to open the login window)."""
        from memexos.capture import cookies
        try:
            return [{"id": x, "name": cookies.SITES[x].name} for x in cookies.needs_login(self.cfg)]
        except Exception:  # noqa: BLE001 — never let this break the live view
            return []

    def _verdicts(self) -> dict[str, str]:
        p = self.cfg.paths.state / "usage" / "judgments.jsonl"

        def build():
            from memexos.util import read_jsonl
            return {r["key"]: r["verdict"] for r in read_jsonl(p) if "key" in r}
        return self.cached("verdicts", self.mtime(p), build)

    def run_list(self, q: dict) -> dict:
        hours = float(q.get("hours", "72"))
        units = runs.timeline(self.state, time.time() - hours * 3600)
        return {"now": time.time(), "runs": units[::-1]}

    def run_detail(self, q: dict) -> dict:
        rid = q.get("id", "")
        units = runs.timeline(self.state, time.time() - 30 * 86400)
        u = next((x for x in units if x["id"] == rid), None)
        if not u:
            return {"error": "not_found"}
        t1 = (u["t1"] or time.time()) + 1
        log = []
        for ev in events.read(self.state, since=u["t0"] - 1, run=u["run"]):
            t = events._epoch(ev.get("ts", ""))
            if t > t1:
                break
            if ev.get("type") in ("run.start", "run.end"):
                continue
            log.append({"t": t, "kind": event_kind(ev), "text": events.render(ev, job=False)[:1200]})
        from memexos.pipeline import report
        reps = []
        for g in u["gate2"]:
            d = self.state / "gate2" / (g.get("dir") or "")
            try:
                rep = json.loads((d / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rep = {}
            m = rep.get("metrics") or {}
            reps.append({"dir": d.name, "mode": g.get("mode"), "passed": g.get("passed"), "errors": rep.get("errors", []),
                         "warnings": rep.get("warnings", [])[:60], "n_warnings": len(rep.get("warnings", [])),
                         "verdicts": m.get("verdicts"), "claims": m.get("claims"), "model": g.get("model") or m.get("model"),
                         "backend": g.get("backend"), "cost": g.get("cost_usd"), "seconds": g.get("seconds"),
                         "problems": [{**p, "label": i18n.t(f"report.kind.{p['kind']}"), "severity": report.SEVERITY.get(p["kind"], "medium")}
                                      for p in report.problems(d)][:60]})
        return {"run": u, "log": log[-400:], "gate2": reps}

    def pipeline(self, q: dict) -> dict:
        from memexos.pipeline.stage import pending_raw
        active = set(re.findall(r"^branch refs/heads/(\S+)$", self.git("worktree", "list", "--porcelain"), re.M))
        holds = {e["data"].get("branch"): e["data"] for e in events.read(self.state, types=("hold",))}
        held = []
        for b in self.git("branch", "--list", "staging/*", "--format=%(refname:short)\t%(committerdate:format:%m-%d %H:%M)\t%(subject)").splitlines():
            name, d, s = (b.split("\t") + ["", ""])[:3]
            if name not in active:          # the batch being compiled right now is also a staging branch
                h = holds.get(name) or {}
                held.append({"branch": name, "when": d, "subject": s[:200], "reason": h.get("reason", ""), "raws": h.get("raws", [])})
        return {"pending": pending_raw(self.vault), "held": held}

    # ------------------------------------------------------------ inbox, usage, quality
    def inbox(self, q: dict) -> dict:
        from memexos import inbox as I
        files = sorted((self.vault / "inbox").glob("*.md")) + sorted((self.vault / "inbox" / "candidates").glob("*/README.md")) + \
            [I.sdir(self.cfg) / "state.jsonl", self.cfg.paths.ledger / "taxonomy-nodes.jsonl"]
        items = self.cached("inbox", self.mtime(*files), lambda: I.merged(self.cfg))
        out = []
        for it in items:
            big = it["kind"] == "decide" or it["status"] in ("decided", "queued")
            body = it["body"] if big or len(it["body"]) < 1200 else it["body"][:1200] + "\n…"
            out.append({k: it.get(k) for k in ("id", "src", "file", "date", "title", "kind", "status", "decision", "result", "research",
                                               "url", "key", "impact", "dup_of", "branch")} | {"body": body, "triage": it.get("triage")})
        live = runs.running(self.state)
        steps = {s for u in live if u["job"] == "inbox" for s in u["active"]}
        return {"items": out, "running": {"apply": any(u["job"] == "inbox" for u in live), "triage": "triage" in steps,
                                          "research": "research" in steps}}

    def usage(self, q: dict) -> dict:
        """Reading new session logs takes seconds: done when asked (Re-read), otherwise in the background every 10 minutes
        while pages show what is already extracted."""
        from memexos import usage as U
        days = int(q.get("days", "30"))
        if q.get("refresh") == "1":
            self._usage_refresh = time.time()
            return U.summary(self.cfg, days, include_eval=False, refresh=True)
        if time.time() - self._usage_refresh > 600:
            self._usage_refresh = time.time()
            threading.Thread(target=self._extract_usage, daemon=True).start()
        return U.summary(self.cfg, days, include_eval=False, refresh=False)

    def _extract_usage(self) -> None:
        from memexos import usage as U
        try:
            U.extract(self.cfg)
        except Exception:  # noqa: BLE001 — a failed background read is retried on the next interval
            self._usage_refresh = 0.0

    def quality(self, q: dict) -> dict:
        from memexos.pipeline import report
        since = time.time() - 60 * 86400
        days = collections.defaultdict(lambda: {"pass": 0, "fail": 0, "unfinished": 0})
        rechecks = {"pass": 0, "fail": 0, "unfinished": 0}
        models = collections.Counter()
        fail_dirs = []
        for e in events.read(self.state, since=since, types=("gate2.result",)):
            d = e.get("data") or {}
            res = "pass" if d.get("passed") is True else "fail" if d.get("passed") is False else "unfinished"
            (days[e["ts"][:10]] if d.get("mode") == "ingest" else rechecks)[res] += 1
            models[d.get("model") or d.get("backend") or "?"] += 1
            if d.get("mode") == "ingest" and res == "fail" and events._epoch(e["ts"]) > time.time() - 30 * 86400:
                fail_dirs.append(d.get("dir"))
        roles = list((self.cfg.get("models") or {}).keys())
        cost = collections.defaultdict(lambda: collections.defaultdict(float))
        calls = list(events.read(self.state, since=since, types=("model.call",)))
        for e in calls:
            d = e.get("data") or {}
            if isinstance(d.get("cost_usd"), (int, float)):
                cost[e["ts"][:10]][d.get("role") or "other"] += d["cost_usd"]
        reasons = collections.defaultdict(lambda: [0, 0])
        for name in fail_dirs:
            seen = set()
            for p in report.problems(self.state / "gate2" / (name or "-")):
                reasons[p["kind"]][1] += 1
                if p["kind"] not in seen:
                    reasons[p["kind"]][0] += 1
                    seen.add(p["kind"])
        evals = []
        for f in sorted(glob.glob(str(self.state / "evals" / "retrieval" / "*" / "summary.json")))[-6:]:
            try:
                evals.append(json.loads(Path(f).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        used_roles = sorted({r for d in cost.values() for r in d}, key=lambda r: roles.index(r) if r in roles else 99)
        return {"gate2_days": [{"day": k, **v} for k, v in sorted(days.items())], "gate2_rechecks": rechecks,
                "gate2_models": models.most_common(),
                "cost_days": [{"day": k, **{r: round(v.get(r, 0), 4) for r in used_roles}} for k, v in sorted(cost.items())],
                "roles": used_roles, "backends": self.backend_state(),
                "fail_reasons": sorted(([k, i18n.t(f"report.kind.{k}"), *v] for k, v in reasons.items()), key=lambda x: -x[2]),
                "fail_reports_30d": len(fail_dirs), "retrieval_evals": evals,
                # cost 0.00 and "no price configured" are different facts; the page must say which
                "unpriced_backends": unpriced_backends(self.cfg),
                "cost_priced": not unpriced_backends(self.cfg),
                "model_usage": usage_rows(calls)}

    # ------------------------------------------------------------ the owner's profile (opt-in)
    def profile(self, q: dict) -> dict:
        if not self.cfg.get("console.profile_page"):
            return {"enabled": False, "items": []}
        from memexos.intake.sessions import claude_dir
        root = claude_dir(self.cfg)
        out = []
        for f in sorted(root.glob("*/memory/*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            fm, body = split_frontmatter(text)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            typ = fm.get("type") or meta.get("type") or ""
            out.append({"project": decode_project(f.parent.parent.name), "file": f.name, "name": fm.get("name") or f.stem,
                        "description": fm.get("description", ""), "type": typ or ("index" if f.name == "MEMORY.md" else ""),
                        "body": body, "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))})
        return {"enabled": True, "items": out}


def relation(a: str, b: str) -> str:
    """[[links]] carry no type; it is inferred from the layers at both ends (the legend says so)."""
    if b == "raw":
        return "cites"
    if "topic" in (a, b):
        return "contains"
    if b == "source":
        return "basis"
    if "entity" in (a, b):
        return "mentions"
    return "related"


def decode_project(name: str) -> str:
    """Claude Code names project folders after the working directory with every "/" (and ".") turned into "-".
    Walk the real file system to undo that where possible; show it relative to home."""
    home = str(Path.home())
    parts = [p for p in name.split("-") if p != ""]
    cur, i = Path("/"), 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            for sep in ("-", ".", "_", " "):
                cand = cur / sep.join(parts[i:j])
                if cand.is_dir():
                    cur, i = cand, j
                    break
            else:
                continue
            break
        else:
            cur = cur / "-".join(parts[i:])
            break
    s = str(cur)
    return "~" + s[len(home):] if s.startswith(home) else s
