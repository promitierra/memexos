"""Backend: any OpenAI-compatible Chat Completions API, hosted or local.

One backend type for many providers: OpenAI, Google Gemini (its OpenAI-compatible endpoint), DeepSeek, Qwen (DashScope
compatible mode), Kimi, OpenRouter, and local servers such as Ollama, LM Studio or vLLM. Define as many as you like:

    [backends.openai]                 # the built-in one
    base_url = "https://api.openai.com/v1"
    model = "…"

    [backends.local]                  # another one, any name
    type = "openai"
    base_url = "http://localhost:11434/v1"
    model = "qwen3:32b"
    api_key_env = ""                  # local servers usually need no key

then name it in a role: [models] writer = ["local"]. MemexOS runs the agent loop itself with the sandboxed file tools of
memexos.backends.tools (the Policy is enforced by this process), plus a fetch_url tool when the policy allows the web.
No extra package is needed: requests go through memexos.net, with the same proxy settings as everything else.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path

from memexos import credentials, i18n, net
from memexos.backends.base import QUOTA_RE, READ_ONLY_NOTE, Backend, Policy, Result, parse_json_text, schema_errors
from memexos.backends.tools import Sandbox

MAX_TURNS = 200
FETCH_TOOL = {"type": "function", "function": {
    "name": "fetch_url", "description": "Fetch a public web page and return its main text (no login, no private addresses).",
    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}}}


class OpenAICompatible(Backend):
    name = "openai"

    def model(self) -> str:
        return self.section.get("model") or ""

    def _key_env(self) -> str:
        v = self.section.get("api_key_env")
        return "OPENAI_API_KEY" if v is None else v

    def available(self) -> tuple[bool, str]:
        if not self.section.get("base_url"):
            return False, i18n.t("backend.no_setting", name=self.name, field="base_url")
        if not self.model():
            return False, i18n.t("backend.no_setting", name=self.name, field="model")
        if self._key_env() and not credentials.get(self._key_env()):
            return False, i18n.t("backend.no_key", env=self._key_env())
        return True, ""

    # -- HTTP --
    def _post(self, body: dict, timeout: float) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._key_env():
            headers["Authorization"] = "Bearer " + (credentials.get(self._key_env()) or "")
        for k, v in (self.section.get("headers") or {}).items():
            headers[str(k)] = str(v)
        proxy = (self.section.get("proxy") or "").strip()
        r = net.request(self.section["base_url"].rstrip("/") + "/chat/completions", method="POST",
                        data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, timeout=timeout,
                        proxy="" if proxy == "direct" else (proxy or None),
                        proxy_cacert=self.cfg.get("fetch.proxy_cacert") or None)
        return json.loads(r.data)

    def _cost(self, usage: dict) -> float | None:
        """Priced by memexos.pricing: one owner for tokens, cache and peak hours."""
        import datetime as _dt
        from memexos import pricing
        return pricing.cost_usd(usage, self.section, at=_dt.datetime.now(_dt.timezone.utc))

    def _body(self, messages: list, tools: list, effort: str, schema: dict | None, final_json: bool = False) -> dict:
        body: dict = {"model": self.model(), "messages": messages}
        if tools:
            body["tools"] = tools
        if self.section.get("max_tokens"):
            body["max_tokens"] = int(self.section["max_tokens"])
        if self.section.get("temperature") is not None and self.section.get("temperature") != "":
            body["temperature"] = float(self.section["temperature"])
        if self.section.get("reasoning_effort"):         # only for providers that accept it
            want = self.section["reasoning_effort"]
            body["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(effort, effort) if want == "auto" else want
        mode = self.section.get("json_mode") or "schema"
        if schema is not None and final_json and mode == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
        elif schema is not None and final_json and mode == "object":
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _tool_defs(sandbox: Sandbox) -> list[dict]:
        return [{"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["input_schema"]}}
                for d in sandbox.definitions()]

    def _loop(self, content, sandbox: Sandbox, web: bool, effort: str, schema: dict | None, workdir: Path, timeout: float) -> Result:
        res = Result(ok=False, backend=self.name, model=self.model())
        usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "turns": 0}
        t0 = time.time()
        if schema is not None and (self.section.get("json_mode") or "schema") == "prompt":
            content = content + [{"type": "text", "text": "\n\nReply with only one JSON object matching this JSON Schema:\n"
                                                         + json.dumps(schema, ensure_ascii=False)}]
        messages: list = [{"role": "user", "content": content}]
        tools = self._tool_defs(sandbox) + ([FETCH_TOOL] if web else [])
        transcript = []
        try:
            while usage["turns"] < MAX_TURNS and time.time() - t0 < timeout:
                remaining = max(30, timeout - (time.time() - t0))
                # no output format while the model works with tools: constrained output stops many servers from calling tools
                out = self._post(self._body(messages, tools, effort, schema), min(remaining, 900))
                usage["turns"] += 1
                u = out.get("usage") or {}
                usage["input_tokens"] += u.get("prompt_tokens") or 0
                usage["output_tokens"] += u.get("completion_tokens") or 0
                usage["cached_tokens"] += ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                choice = (out.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                calls = msg.get("tool_calls") or []
                if calls:
                    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                    for call in calls:
                        fn = call.get("function") or {}
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except ValueError:
                            args, result, err = {}, "INVALID_JSON: the arguments were not valid JSON; send them again", True
                        else:
                            result, err = self._call(sandbox, fn.get("name", ""), args)
                        transcript.append({"tool": fn.get("name"), "input": args, "error": err})
                        messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
                    continue
                if msg.get("refusal"):
                    res.tail = f"refused: {msg['refusal'][:200]}"
                    break
                text = msg.get("content") or ""
                res.text = text
                if schema is not None:
                    value = parse_json_text(text)
                    problems = schema_errors(schema, value) if value is not None else ["no JSON"]
                    if problems:                        # one more turn, no tools, output constrained to the schema
                        messages.append({"role": "assistant", "content": text})
                        messages.append({"role": "user", "content": "Give your final answer now as one JSON object matching this JSON "
                                         "Schema, with no other text:\n" + json.dumps(schema, ensure_ascii=False)
                                         + "\nProblems with the previous answer: " + "; ".join(problems)})
                        out = self._post(self._body(messages, [], effort, schema, final_json=True), min(max(30, timeout - (time.time() - t0)), 900))
                        usage["turns"] += 1
                        u = out.get("usage") or {}
                        usage["input_tokens"] += u.get("prompt_tokens") or 0
                        usage["output_tokens"] += u.get("completion_tokens") or 0
                        text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                        res.text = text
                        value = parse_json_text(text)
                        problems = schema_errors(schema, value) if value is not None else ["no JSON"]
                    res.result = value if not problems else None
                    res.ok = not problems
                    if problems:
                        res.tail = f"the answer does not fit the schema: {'; '.join(problems)[:300]}: {text[-200:]}"
                else:
                    res.result, res.ok = text, bool(text) or choice.get("finish_reason") == "stop"
                break
            else:
                res.tail = "stopped: too many turns or out of time"
        except net.NetError as e:
            body = e.body.decode("utf-8", "ignore")[:400]
            res.tail = f"HTTP {e.status}: {body}" if e.status else f"connection error: {e}"
            res.limited = e.status in (402, 429) and bool(QUOTA_RE.search(body) or "insufficient_quota" in body)
            res.transient = (e.status is None or e.status == 429 or e.status >= 500) and not res.limited
        except ValueError as e:                                   # not JSON at all
            res.tail, res.transient = f"unexpected response: {e}", True
        res.usage = usage
        res.cost_usd = self._cost(usage)
        res.elapsed_s = round(time.time() - t0, 1)
        res.denials = sandbox.denied
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "api-transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, default=str)[:2_000_000], encoding="utf-8")
        if res.ok and res.result is not None:
            (workdir / "out.json").write_text(json.dumps(res.result, ensure_ascii=False, indent=1), encoding="utf-8")
        return res

    def _call(self, sandbox: Sandbox, name: str, args: dict) -> tuple[str, bool]:
        if name == "fetch_url":
            from memexos.capture import fetch
            try:
                text, how = fetch.web(self.cfg, str(args.get("url", "")))
                return fetch.emit(f"{args.get('url')} ({how})", text, 30_000), False
            except Exception as e:  # noqa: BLE001 — the model sees why and can try another source
                return f"fetch failed: {e}", True
        return sandbox.call(name, args)

    def _content(self, text: str, images) -> list:
        parts = []
        if images and self.section.get("vision", True) is not False:
            for im in images:
                mt = mimetypes.guess_type(str(im))[0] or "image/jpeg"
                data = base64.standard_b64encode(Path(im).read_bytes()).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}})
        parts.append({"type": "text", "text": text})
        return parts

    def structured(self, prompt, schema, *, cwd, workdir, effort="high", timeout=3600, images=(), extra=""):
        text = prompt + READ_ONLY_NOTE.get(i18n.language(), READ_ONLY_NOTE["en"]) + (("\n" + extra) if extra else "")
        sandbox = Sandbox(Path(cwd), Policy(), read_only=True)
        return self._loop(self._content(text, images), sandbox, False, effort, schema, Path(workdir), timeout)

    def agent(self, prompt, *, cwd, workdir, policy: Policy, effort="high", timeout=3600, schema=None, images=()):
        sandbox = Sandbox(Path(cwd), policy)
        return self._loop(self._content(prompt, images), sandbox, policy.web, effort, schema, Path(workdir), timeout)
