"""One narrow interface: `client.structured(system, prompt, schema, name) -> dict`.

The Anthropic implementation asks for a single strict tool call whose input_schema is the JSON schema we want. Every call is cached on disk by a hash of (model, system, prompt,
schema): re-running the pipeline or the demo costs nothing and returns identical results.
Swap providers by implementing `_call`.
"""
from __future__ import annotations
import hashlib, json, os, time
from pathlib import Path
from typing import Callable, Optional


class LLMError(RuntimeError):
    pass


class SpendCapReached(LLMError):
    pass


class SpendLedger:
    """Cumulative real spend across ALL runs and processes (CLI, background jobs, retries after a crash).
    A hard cap stops new API calls once reached, so a loop or a restarted job cannot quietly drain credits.
      EVIDENCE_MAX_SPEND_USD   cap (default 10)
      EVIDENCE_SPEND_LEDGER    file (default ~/.evidence_spend.json)"""
    def __init__(self):
        self.path = Path(os.environ.get("EVIDENCE_SPEND_LEDGER", Path.home() / ".evidence_spend.json"))
        self.cap = float(os.environ.get("EVIDENCE_MAX_SPEND_USD", "10"))

    def total(self) -> float:
        try:
            return float(json.loads(self.path.read_text()).get("total_usd", 0.0))
        except (OSError, ValueError):
            return 0.0

    def add(self, usd: float):
        if usd <= 0:
            return
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                d = json.loads(f.read() or "{}")
            except ValueError:
                d = {}
            d["total_usd"] = round(d.get("total_usd", 0.0) + usd, 6)
            d["calls"] = d.get("calls", 0) + 1
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.seek(0); f.truncate(); f.write(json.dumps(d))

    def check(self):
        t = self.total()
        if t >= self.cap:
            raise SpendCapReached(f"Spend cap reached: ${t:.2f} of ${self.cap:.2f} (EVIDENCE_MAX_SPEND_USD). "
                                  "No further API calls will be made. Raise the cap deliberately to continue.")


class LLMClient:
    def __init__(self, cache_dir: Optional[Path] = None, model: str = ""):
        self.model = model
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self.ledger: Optional[SpendLedger] = None      # real clients set this
        self._last_cost = 0.0

    def structured(self, system: str, prompt: str, schema: dict, name: str = "record") -> dict:
        key = hashlib.sha256(json.dumps([self.model, system, prompt, schema], sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            try:
                out = json.loads(path.read_text())
                self.cache_hits += 1
                return out
            except ValueError:
                path.unlink()                         # a crash mid-write left a broken file: redo just this call
        if self.ledger:
            self.ledger.check()
        self._last_cost = 0.0
        try:
            out = self._call(system, prompt, schema, name)
        finally:
            if self.ledger:
                self.ledger.add(self._last_cost)      # charged even if the call failed after tokens were spent
        self.calls += 1
        if path:                                      # write immediately and atomically: an abrupt stop loses
            tmp = path.with_suffix(".tmp")            # at most the calls that were in flight
            tmp.write_text(json.dumps(out, ensure_ascii=False))
            tmp.replace(path)
        return out

    def _call(self, system, prompt, schema, name) -> dict:
        raise NotImplementedError


# USD per million tokens. Sonnet 5.5 list prices as of Oct 2026 (platform.claude.com pricing); edit as they change.
PRICES = {"claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.50}}
BATCH_DISCOUNT = 0.5


def _strict(schema: dict) -> dict:
    """Strict tool use wants closed objects; drop keywords some validators reject."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items() if k not in ("minItems", "maxItems")}
        if out.get("type") == "object" or (isinstance(out.get("type"), list) and "object" in out["type"]):
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


class AnthropicClient(LLMClient):
    """Structured output via a single strict tool with tool_choice=auto (Sonnet 5.5 rejects forced tool_choice
    and sampling parameters). `effort` trades thinking depth for cost: extraction runs with thinking only between
    tool calls and low effort; the judge can use more."""
    def __init__(self, model: Optional[str] = None, cache_dir: Optional[Path] = None, max_tokens: int = 8000,
                 effort: str = "low", up_front_thinking: bool = False):
        import anthropic
        super().__init__(cache_dir, model or os.environ.get("EVIDENCE_MODEL", "claude-sonnet-5-5"))
        self.client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY
        self.max_tokens, self.effort, self.up_front = max_tokens, effort, up_front_thinking
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.ledger = SpendLedger()

    def cost_usd(self) -> float:
        p = PRICES.get(self.model, PRICES["claude-sonnet-5-5"])
        u = self.usage
        return (u["input"] * p["in"] + u["output"] * p["out"] + u["cache_read"] * p["cache_read"]
                + u["cache_write"] * p["cache_write"]) / 1e6

    def _call(self, system, prompt, schema, name) -> dict:
        tool = {"name": name, "description": "Record the result. Always call this tool exactly once.",
                "input_schema": _strict(schema), "strict": True}
        extra = {"output_config": {"effort": self.effort},
                 "thinking": {"type": "adaptive" if self.up_front else "between_tools"}}
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(3):
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    # cache breakpoint on the system block caches tools + system (the identical prefix of every call)
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    tools=[tool], tool_choice={"type": "auto"},
                    messages=messages, extra_body=extra)
                u = resp.usage
                before = self.cost_usd()
                self.usage["input"] += u.input_tokens or 0
                self.usage["output"] += u.output_tokens or 0
                self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
                self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
                self._last_cost += self.cost_usd() - before
                for block in resp.content:
                    if block.type == "tool_use" and block.name == name:
                        return block.input
                if resp.stop_reason == "max_tokens":
                    # retrying the same input would pay again for the same truncation: fail this window instead
                    raise LLMError("output limit reached; split the input into smaller windows")
                # the model answered in text instead of calling the tool: ask once more, explicitly
                messages = messages + [{"role": "assistant", "content": resp.content},
                                       {"role": "user", "content": f"Call the {name} tool with your result now."}]
            except LLMError:
                raise
            except Exception as e:                    # rate limits / transient network errors
                status = getattr(e, "status_code", None)
                if status in (400, 401, 403, 404) or attempt == 2:
                    raise LLMError(str(e)) from e
                time.sleep(2 ** attempt * 2)
        raise LLMError("model did not return the structured result")


class ScriptedClient(LLMClient):
    """For tests and offline demos: a function decides the response. It never pretends to be a model in
    production; the CLI refuses to run real extraction without a real client."""
    def __init__(self, responder: Callable[[str, str, dict, str], dict], cache_dir: Optional[Path] = None):
        super().__init__(cache_dir, "scripted")
        self.responder = responder

    def _call(self, system, prompt, schema, name) -> dict:
        return self.responder(system, prompt, schema, name)


def default_client(cache_dir: Path, model: Optional[str] = None, effort: str = "low") -> LLMClient:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMError("Set ANTHROPIC_API_KEY (or plug another provider into reason/llm.py).")
    return AnthropicClient(model=model, cache_dir=cache_dir, effort=effort)
