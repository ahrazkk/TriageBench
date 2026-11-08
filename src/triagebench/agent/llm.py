"""The model behind one small interface, plus record/replay "cassettes".

`LLMClient.generate()` is the only thing the agent, the baselines and the judge call. Two
implementations:
- AnthropicLLM: the Claude API (Messages API + tool use + structured outputs)
- FakeLLM:      scripted replies, so agent tests run with no network and no money

`CassetteLLM` wraps any client: each request is hashed (model, prompt, tools, history); a hit is
replayed from disk, a miss calls the real model and records it. Re-running an eval is free and CI
can replay without an API key.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from triagebench.config import secrets, settings

# Anthropic cache pricing multipliers relative to the base input price.
CACHE_WRITE_X = 1.25
CACHE_READ_X = 0.10


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, o: Usage) -> Usage:
        return Usage(
            self.input_tokens + o.input_tokens,
            self.output_tokens + o.output_tokens,
            self.cache_read_tokens + o.cache_read_tokens,
            self.cache_write_tokens + o.cache_write_tokens,
        )

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def cost(self, model: str) -> float:
        from triagebench.config import PRICES_USD_PER_MTOK

        pin, pout = PRICES_USD_PER_MTOK.get(model, (0.0, 0.0))
        dollars = (
            self.input_tokens * pin
            + self.cache_write_tokens * pin * CACHE_WRITE_X
            + self.cache_read_tokens * pin * CACHE_READ_X
            + self.output_tokens * pout
        )
        return dollars / 1_000_000


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMReply:
    content: list[dict[str, Any]]  # raw assistant blocks, appended to history unchanged
    stop_reason: str
    usage: Usage
    model: str
    cached: bool = False  # replayed from a cassette

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(b["id"], b["name"], dict(b.get("input") or {}))
            for b in self.content
            if b.get("type") == "tool_use"
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "stop_reason": self.stop_reason,
            "usage": self.usage.__dict__,
            "model": self.model,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any], cached: bool = False) -> LLMReply:
        return cls(d["content"], d["stop_reason"], Usage(**d["usage"]), d["model"], cached)


class LLMClient(Protocol):
    model: str

    def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMReply: ...


class LLMError(Exception):
    pass


# ---- Claude ------------------------------------------------------------------------------------
class AnthropicLLM:
    def __init__(self, model: str | None = None, effort: str | None = None) -> None:
        import anthropic

        key = secrets().anthropic_api_key
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set (put it in .env)")
        self.model = model or settings().model
        self.effort = effort or settings().effort
        self._client = anthropic.Anthropic(api_key=key, max_retries=6, timeout=300)

    def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMReply:
        output_config: dict[str, Any] = {"effort": self.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or settings().max_output_tokens,
            "system": system,
            "messages": messages,
            "output_config": output_config,
            # Auto prompt caching: an agent re-sends its growing history every step, so steps
            # 2..N read the shared prefix at 10% of the input price.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            # {"type": "none"} = the model must answer in text; used for the agent's final turn
            kwargs["tool_choice"] = tool_choice
        resp = self._client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise LLMError("the model declined this request (stop_reason=refusal)")
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        content = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        return LLMReply(content, str(resp.stop_reason), usage, resp.model)


# ---- scripted fake (tests) ---------------------------------------------------------------------
class FakeLLM:
    """Returns scripted replies in order. Each script item is either a str (final text) or a list
    of (tool_name, input) pairs (tool calls)."""

    def __init__(self, script: list[str | list[tuple[str, dict[str, Any]]]], model: str = "fake"):
        self.script = list(script)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMReply:
        self.calls.append(
            {
                "system": system,
                "messages": json.loads(json.dumps(messages)),
                "tool_choice": tool_choice,
            }
        )
        if not self.script:
            raise LLMError("FakeLLM script exhausted")
        item = self.script.pop(0)
        usage = Usage(input_tokens=1000, output_tokens=100)
        if isinstance(item, str):
            return LLMReply([{"type": "text", "text": item}], "end_turn", usage, self.model)
        blocks = [
            {"type": "tool_use", "id": f"toolu_{len(self.calls)}_{i}", "name": n, "input": a}
            for i, (n, a) in enumerate(item)
        ]
        return LLMReply(blocks, "tool_use", usage, self.model)


# ---- record / replay ---------------------------------------------------------------------------
def request_key(model: str, effort: str, payload: dict[str, Any]) -> str:
    blob = json.dumps({"model": model, "effort": effort, **payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class CassetteMiss(LLMError):
    pass


class CassetteLLM:
    """Replay recorded replies by request hash; record on a miss unless replay_only."""

    _lock = threading.Lock()

    def __init__(
        self,
        inner: LLMClient | None,
        cassette_dir: Path,
        replay_only: bool = False,
        repeat: int = 0,
    ):
        self.inner = inner
        # Hosted models aren't deterministic, so measuring run-to-run variation needs fresh calls:
        # repeat k > 0 is part of the request key, giving each repeat its own recordings.
        self.repeat = repeat
        self.model = inner.model if inner else settings().model
        self.effort = getattr(inner, "effort", settings().effort)
        self.dir = cassette_dir
        self.replay_only = replay_only
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMReply:
        payload = {
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }
        if tool_choice is not None:
            # Only part of the key when set, so recordings made before it existed still replay.
            payload["tool_choice"] = tool_choice
        if self.repeat:
            payload["repeat"] = self.repeat
        key = request_key(self.model, self.effort, payload)
        path = self._path(key)
        if path.exists():
            self.hits += 1
            return LLMReply.from_json(json.loads(path.read_text(encoding="utf-8")), cached=True)
        self.misses += 1
        if self.replay_only or self.inner is None:
            raise CassetteMiss(f"no recording for request {key[:12]} (live eval needed)")
        reply = self.inner.generate(system, messages, tools, max_tokens, json_schema, tool_choice)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(reply.to_json()), encoding="utf-8")
        return reply
