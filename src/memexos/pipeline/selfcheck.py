"""Self-check before review: the judge compares each newly written sentence with the sources.

Most first-time review failures are sentences the sources do not say (usually the writer's background
knowledge left unmarked), and every failure costs a full review round. So before gate 2, each new sentence is
paired with the closest source passages (BM25 over this batch's raw files, the raw files the page already
cites, and pages the sentence links to) and the judge estimates whether the passage supports it. Sentences
below the threshold go back to the writer to verify. Gate 2 itself is unchanged.

run() returns (code, text, summary): 0 nothing to check, 1 sentences to verify (text is the list), 2 the check
could not run (no judge, too few answers): the pipeline then goes straight to review.
"""

from __future__ import annotations

import collections
import math
import re
import subprocess
import time
from pathlib import Path

from memexos import events, i18n, judge, rawref
from memexos.search import text as T

META_KEYS = re.compile(r"^(tags|created|updated|sources|aliases|domain|content_type|level|evidence|usage|durability|type|use_note)\s*:")
MARKED = re.compile(r"⚠️|编译推论|待核实|compiler's inference|unverified", re.I)
SENT_SPLIT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+|\n")


def sentences(line: str) -> list[str]:
    line = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line).strip()
    if line.startswith("|"):
        return [line] if len(re.sub(r"[|\s-]", "", line)) >= 6 else []
    return [x.strip() for x in SENT_SPLIT.split(line) if x and len(re.sub(r"[\W_]", "", x)) >= 6]


def diff_units(stage: Path, base: str) -> list[dict]:
    """New sentences: added lines split into sentences, minus sentences that already existed in removed lines."""
    subprocess.run(["git", "-C", str(stage), "add", "-A", "--", "wiki", "index.md"], capture_output=True, stdin=subprocess.DEVNULL)
    out = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(stage), "diff", "--cached", "-U0", base, "--", "wiki", "index.md"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    units, page, old, new = [], None, set(), []

    def flush():
        for ln in new:
            for s in sentences(ln):
                if s in old or MARKED.search(s):
                    continue
                units.append({"page": page, "text": s, "line": ln})

    for ln in out.splitlines():
        if ln.startswith("diff --git"):
            if page:
                flush()
            page, old, new = re.sub(r"^diff --git a/(.*) b/.*$", r"\1", ln), set(), []
        elif ln.startswith(("---", "+++", "@@")):
            continue
        elif ln.startswith("-"):
            old.update(sentences(ln[1:]))
        elif ln.startswith("+"):
            body = ln[1:]
            if META_KEYS.match(body) or body.lstrip().startswith("#") or re.match(r"^\s*-\s*`?raw/", body):
                continue
            new.append(body)
    if page:
        flush()
    return units


class Evidence:
    """BM25 over overlapping chunks of a set of files."""

    def __init__(self, stage: Path):
        self.stage = stage
        self.cache: dict[str, list[str]] = {}

    def chunks(self, rel: str, size: int = 500, step: int = 350) -> list[str]:
        if rel not in self.cache:
            try:
                txt = (self.stage / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                txt = ""
            self.cache[rel] = [txt[i:i + size] for i in range(0, max(len(txt) - 150, 1), step)] or [txt]
        return self.cache[rel]

    def best(self, claim: str, files: list[str], k: int = 3) -> list[str]:
        docs = [ch for f in files for ch in self.chunks(f) if ch.strip()]
        if not docs:
            return []
        q = set(T.tokens(claim))
        toks = [T.tokens(ch) for ch in docs]
        n = len(docs)
        df = collections.Counter(t for ts in toks for t in set(ts))
        avg = sum(len(t) for t in toks) / n or 1
        scores = []
        for ts in toks:
            tf = collections.Counter(ts)
            scores.append(sum(math.log(1 + (n - df[t] + .5) / (df[t] + .5)) * tf[t] * 2.2 / (tf[t] + 1.2 * (.25 + .75 * len(ts) / avg))
                              for t in q if t in tf))
        return [docs[i] for i in sorted(range(n), key=lambda i: -scores[i])[:k]]


def run(cfg, stage: Path, base: str, raws: list[str], threshold: float | None = None, limit: int = 60,
        timeout: float | None = None) -> tuple[int, str, str]:
    """`timeout` caps the whole check in seconds. Without a cap the judge batches run one after another and a
    slow backend turns a step that was going to be skipped anyway into half an hour of paid waiting."""
    j = judge.get(cfg)
    ok, why = j.available() if j.name != "none" else (False, "no judge configured")
    if not ok:
        return 2, "", i18n.t("selfcheck.skipped", why=why)
    threshold = float(threshold if threshold is not None else cfg.get("pipeline.selfcheck_threshold") or 0.6)
    units = diff_units(stage, base)
    if not units:
        return 0, "", i18n.t("selfcheck.nothing")
    names = rawref.index(stage)
    pages = {p.stem: p.relative_to(stage).as_posix() for p in (stage / "wiki").rglob("*.md")}
    ev = Evidence(stage)
    items, evid = [], []
    for u in units:
        try:
            cited = [r for r in rawref.find((stage / u["page"]).read_text(encoding="utf-8"), names, stage) if not r.startswith("raw/assets/")][:8]
        except OSError:
            cited = []
        linked = [pages[n] for n in dict.fromkeys(m.split("|")[0].strip() for m in re.findall(r"\[\[([^\]]+)\]\]", u["line"])) if n in pages]
        files = [f for f in dict.fromkeys(raws + cited + linked) if (stage / f).exists()]
        best = ev.best(u["text"], files)
        evid.append(best)
        # one question per sentence, with its closest passages together: supported if any of them supports it
        items.append({"page": Path(u["page"]).stem, "claim": u["text"], "source": "\n…\n".join(best) or "(no source passage found)"})
    budget = float(timeout if timeout is not None else cfg.get("pipeline.selfcheck_timeout") or 900)
    deadline = time.monotonic() + budget if budget > 0 else None
    started = time.monotonic()
    try:
        scores = j.supported(items, deadline=deadline)
    except judge.JudgeUnavailable as e:
        return 2, "", i18n.t("selfcheck.skipped", why=str(e))
    answered = sum(s is not None for s in scores)
    if deadline is not None and answered < len(items) and time.monotonic() >= deadline:
        return 2, "", i18n.t("selfcheck.budget", got=answered, total=len(items), seconds=round(time.monotonic() - started))
    if answered < len(items) * 0.5:
        return 2, "", i18n.t("selfcheck.too_few", got=answered, total=len(items))
    flagged = [(p, u, best[0] if best else "") for p, u, best in zip(scores, units, evid) if p is not None and p < threshold]
    summary = i18n.t("selfcheck.summary", total=len(units), flagged=len(flagged))
    # the counts travel as numbers too: the benchmark report cannot parse a translated sentence
    events.emit(cfg.paths.state, "selfcheck.counts", total=len(units), flagged=len(flagged))
    if not flagged:
        return 0, "", summary
    flagged.sort(key=lambda x: x[0])
    lines = [i18n.t("selfcheck.item", page=u["page"], text=u["text"][:200], source=re.sub(r"\s+", " ", ev)[:160]) for _, u, ev in flagged[:limit]]
    if len(flagged) > limit:
        lines.append(i18n.t("selfcheck.more", n=len(flagged) - limit))
    return 1, "\n".join(lines), summary
