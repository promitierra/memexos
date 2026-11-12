"""Model backends and the router that picks one for each role.

Roles (config [models]): writer (edits the wiki in staging), reviewer (gate 2), worker (small structured jobs),
vision (reading video frames). Each role lists backends in order of preference. The router uses the first
available one; when a backend reports an exhausted quota it is skipped for `retry_after_minutes` and the next one
takes over, so the pipeline keeps running. Every call is recorded as a `model.call` event (backend, model,
seconds, tokens, cost) for the status view and the console.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from memexos import events
from memexos.backends.anthropic_api import AnthropicAPI
from memexos.backends.base import Backend, Policy, Result
from memexos.backends.claude_code import ClaudeCode
from memexos.backends.codex import Codex
from memexos.backends.openai_compat import OpenAICompatible

REGISTRY: dict[str, type[Backend]] = {"claude-code": ClaudeCode, "codex": Codex, "anthropic": AnthropicAPI, "openai": OpenAICompatible}


def kind_of(cfg, base: str) -> str | None:
    """The backend type behind a name: a built-in name, or [backends.<name>] with type = "<built-in name>"."""
    if base in REGISTRY:
        return base
    t = (cfg.get(f"backends.{base}") or {}).get("type") if isinstance(cfg.get(f"backends.{base}"), dict) else None
    return t if t in REGISTRY else None


def names(cfg) -> list[str]:
    """Every backend the settings know: the built-in ones and any extra named sections with a type."""
    extra = [k for k, v in (cfg.get("backends") or {}).items() if isinstance(v, dict) and v.get("type") in REGISTRY and k not in REGISTRY]
    return list(REGISTRY) + extra
__all__ = ["Policy", "Result", "agent", "structured", "get", "chain", "status"]


def get(cfg, name: str) -> Backend:
    """A backend by name. "name:model" picks a model for this role only, e.g. "claude-code:sonnet"."""
    base, _, model = name.partition(":")
    kind = kind_of(cfg, base)
    if not kind:
        raise KeyError(f"unknown backend {base!r} (known: {', '.join(names(cfg))})")
    section = dict(cfg.section(f"backends.{base}"))
    if model:
        section["model"] = model
    b = REGISTRY[kind](cfg, section)
    b.name = name if model else base
    return b


def chain(cfg, role: str) -> list[str]:
    """The backends a role tries, in order. "@name" is a fixed backend (evaluations compare named agents)."""
    if role.startswith("@"):
        return [role[1:]]
    v = cfg.get(f"models.{role}") or cfg.get("models.worker") or ["claude-code"]
    return [v] if isinstance(v, str) else list(v)


def _mark_path(cfg, name: str) -> Path:
    return cfg.paths.state / "quota" / f"{name.partition(':')[0]}.json"      # quotas belong to the account, not the model


def exhausted(cfg, name: str) -> dict | None:
    try:
        m = json.loads(_mark_path(cfg, name).read_text())
    except (OSError, ValueError):
        return None
    minutes = float(cfg.get(f"backends.{name.partition(':')[0]}.retry_after_minutes") or 60)
    return m if time.time() - m.get("at", 0) < minutes * 60 else None


def _mark(cfg, name: str, evidence: str) -> None:
    p = _mark_path(cfg, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.time(), "at_str": time.strftime("%F %T"), "evidence": evidence[:300]}))


def _clear(cfg, name: str) -> None:
    _mark_path(cfg, name).unlink(missing_ok=True)


def status(cfg) -> dict:
    """{backend: {"available": bool, "why": str, "exhausted": mark-or-None}} for doctor and the console."""
    out = {}
    for name in names(cfg):
        ok, why = get(cfg, name).available()
        out[name] = {"available": ok, "why": why, "exhausted": exhausted(cfg, name)}
    return out


def _record(cfg, role: str, kind: str, r: Result, job: str) -> None:
    events.emit(cfg.paths.state, "model.call", job=job or None, role=role, kind=kind, backend=r.backend, model=r.model,
                ok=r.ok, seconds=r.elapsed_s, cost_usd=r.cost_usd, limited=r.limited, transient=r.transient,
                tokens={k: v for k, v in (r.usage or {}).items() if isinstance(v, (int, float))},
                denials=len(r.denials))


def _route(cfg, role: str, kind: str, call, job: str, retries: int, backoff: float) -> Result:
    names = chain(cfg, role)
    tried: list[dict] = []
    last: Result | None = None
    for i, name in enumerate(names):
        later = names[i + 1:]
        if later and exhausted(cfg, name):
            tried.append({"backend": name, "skipped": "quota exhausted recently"})
            continue
        b = get(cfg, name)
        ok, why = b.available()
        if not ok:
            tried.append({"backend": name, "skipped": why})
            continue
        for attempt in range(retries + 1):
            r = call(b)
            _record(cfg, role, kind, r, job)
            tried.append(r.summary())
            if not r.transient or attempt == retries:
                break
            time.sleep(backoff * (attempt + 1))
        last = r
        if r.ok:
            _clear(cfg, name)
            r.tried = tried
            return r
        if r.limited:
            _mark(cfg, name, r.tail)
            continue
        if r.transient and later:
            # A 5xx or an HTML error page from the gateway is not a verdict on the
            # work: the next backend in the chain gets the same prompt. Giving up
            # here threw away a whole batch that had already been written and paid for.
            tried.append({"backend": name, "skipped": f"transient failure, next backend: {r.tail[:160]}"})
            continue
        break        # a real failure (bad output, refusal): do not hop backends, report it
    if last is None:
        last = Result(ok=False, backend=",".join(names), tail="no backend available: " +
                      "; ".join(f"{t['backend']}: {t.get('skipped', '')}" for t in tried))
    last.tried = tried
    return last


def structured(cfg, role: str, prompt: str, schema: dict, *, cwd, workdir, effort: str | None = None,
               timeout: float = 3600, images=(), extra: str = "", job: str = "") -> Result:
    """JSON answer matching `schema` from the role's backends (read-only access to files under cwd)."""
    return _route(cfg, role, "structured",
                  lambda b: b.structured(prompt, schema, cwd=Path(cwd), workdir=Path(workdir),
                                         effort=effort or b.section.get("effort") or "high", timeout=timeout,
                                         images=list(images), extra=extra),
                  job, retries=2, backoff=60)


def agent(cfg, role: str, prompt: str, *, cwd, workdir, policy: Policy, effort: str | None = None,
          timeout: float = 3600, schema: dict | None = None, images=(), job: str = "") -> Result:
    """Run a task with tools under `policy`. Transient failures are returned (callers resume with context)."""
    return _route(cfg, role, "agent",
                  lambda b: b.agent(prompt, cwd=Path(cwd), workdir=Path(workdir), policy=policy,
                                    effort=effort or b.section.get("effort") or "high", timeout=timeout,
                                    schema=schema, images=list(images)),
                  job, retries=0, backoff=0)
