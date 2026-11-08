"""The agent: a loop around the model, with budgets, tool errors, and one repair attempt.

    messages = [task]
    for step in range(MAX_STEPS):
        reply = llm.generate(messages, tools)          # the model decides the next step
        if reply asks for tools: run them, append results, continue
        else: parse + verify the JSON answer; if invalid, ask once for a fix
    out of budget -> a recorded failure, never an exception that kills an eval

The model only *asks* for tool calls. This code decides what's allowed (which tools exist, how many
steps, how many tokens) and runs them.

Prompt v2 makes the loop budget-aware: after each tool round the model is told how many turns and
input tokens it has left, and when the next turn would leave no room for an answer, that turn is
made the final one with tools switched off (tool_choice "none"), so the agent answers with what it
has instead of failing. v1 behaviour is kept for reproducing earlier results.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from triagebench.agent.llm import LLMClient, LLMError, Usage
from triagebench.agent.schema import TriageResult, output_json_schema
from triagebench.agent.verify import Checked, parse_and_verify
from triagebench.config import settings
from triagebench.data import Case
from triagebench.tools.impl import ToolError, ToolRunner, tool_specs


def prompt_version() -> str:
    return settings().prompt_version


def load_prompt(name: str, version: str | None = None, **fmt: Any) -> str:
    p = settings().prompts_dir
    v = version or prompt_version()
    text = (p / f"{name}_{v}.md").read_text(encoding="utf-8")
    labels = (p / f"labels_{v}.md").read_text(encoding="utf-8")
    text = text.replace("{LABELS}", labels)
    for k, v in fmt.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def prompt_hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


class Outcome(BaseModel):
    """Everything one system produced for one case. Saved verbatim in results/*.json."""

    case_id: str
    system: str
    result: TriageResult | None = None
    errors: list[str] = []
    steps: int = 0
    tool_calls: list[str] = []
    repaired: bool = False
    evidence_total: int = 0
    evidence_valid: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    replayed_calls: int = 0
    live_calls: int = 0


@dataclass
class Tracker:
    model: str
    usage: Usage = field(default_factory=Usage)
    replayed: int = 0
    live: int = 0
    steps: int = 0

    def add(self, u: Usage, cached: bool) -> None:
        self.usage = self.usage + u
        self.steps += 1
        if cached:
            self.replayed += 1
        else:
            self.live += 1

    def fill(self, out: Outcome) -> Outcome:
        u = self.usage
        out.input_tokens, out.output_tokens = u.input_tokens, u.output_tokens
        out.cache_read_tokens, out.cache_write_tokens = u.cache_read_tokens, u.cache_write_tokens
        out.cost_usd = round(u.cost(self.model), 6)
        out.steps, out.replayed_calls, out.live_calls = self.steps, self.replayed, self.live
        return out


def _apply_check(out: Outcome, checked: Checked) -> None:
    out.result = checked.result
    out.evidence_total, out.evidence_valid = checked.evidence_total, checked.evidence_valid


REPAIR_MSG = (
    "Your answer failed validation:\n- {errors}\n"
    "Fix these problems and reply with the corrected JSON object only. Quote evidence exactly as "
    "it appears in the tool output, with the line number shown next to it."
)
FINAL_MSG = (
    "Budget reached: no more tools. Give your final JSON answer now from the evidence you already "
    "have; lower your confidence if you are unsure."
)
NO_TOOLS = {"type": "none"}


def _add_text(message: dict[str, Any], text: str) -> None:
    """Append a text block to a user message (tool results are a list; a repair note is a str)."""
    if isinstance(message["content"], list):
        message["content"].append({"type": "text", "text": text})
    else:
        message["content"] = f"{message['content']}\n\n{text}"


def run_agent(
    case: Case,
    llm: LLMClient,
    box: ToolRunner,
    with_retrieval: bool = False,
    system_name: str = "b2",
    max_steps: int | None = None,
    max_input_tokens: int | None = None,
) -> Outcome:
    s = settings()
    max_steps = max_steps or s.max_steps
    max_input_tokens = max_input_tokens or s.max_input_tokens
    version = prompt_version()
    budget_aware = version != "v1"
    retrieval_step = (
        (s.prompts_dir / f"retrieval_{version}.md").read_text(encoding="utf-8")
        if with_retrieval
        else ""
    )
    system = load_prompt("agent", RETRIEVAL_STEP=retrieval_step, MAX_STEPS=max_steps)
    tools = tool_specs(with_retrieval)
    task = (
        f"Triage this failed GitHub Actions job.\njob_id: {case.job_id}\n"
        f"repo: {case.repo}\nrun: {case.html_url}"
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    out = Outcome(case_id=case.id, system=system_name)
    tr = Tracker(llm.model)
    t0 = time.perf_counter()
    box.calls = []
    repaired = False
    est_next = 0  # v2: estimated input tokens of the next request, for budget planning

    def lines_for(job_id: int) -> list[str]:
        return box.store.lines(job_id)

    try:
        for step in range(max_steps):
            final = False
            if budget_aware:
                # Keep room for an answer: if this turn plus one more could overrun the budget,
                # or this is the last allowed turn, this turn must be the answer (tools off).
                over = tr.usage.total_input + 2 * est_next > max_input_tokens
                if step > 0 and (step == max_steps - 1 or over):
                    final = True
                    _add_text(messages[-1], FINAL_MSG)
            elif tr.usage.total_input > max_input_tokens:
                out.errors.append(f"input-token budget exceeded ({tr.usage.total_input})")
                break
            reply = llm.generate(
                system,
                messages,
                tools,
                json_schema=output_json_schema(),
                tool_choice=NO_TOOLS if final else None,
            )
            tr.add(reply.usage, reply.cached)
            messages.append({"role": "assistant", "content": reply.content})
            calls = reply.tool_calls
            if calls:
                results: list[dict[str, Any]] = []
                for call in calls:
                    try:
                        content, is_error = box.call(call.name, call.input), False
                    except ToolError as e:
                        content, is_error = f"Error: {e}", True
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": content,
                            "is_error": is_error,
                        }
                    )
                if budget_aware:
                    # The next request = this one + the model's reply + the new tool results.
                    new_chars = sum(len(str(r["content"])) for r in results)
                    est_next = reply.usage.total_input + reply.usage.output_tokens + new_chars // 4
                    left = max(0, max_input_tokens - tr.usage.total_input)
                    note = (
                        f"[budget: {max_steps - step - 1} model turns and about "
                        f"{left:,} input tokens left]"
                    )
                    results.append({"type": "text", "text": note})
                elif step == max_steps - 2:
                    results.append(
                        {
                            "type": "text",
                            "text": "You have one turn left: give your final JSON answer now.",
                        }
                    )
                messages.append({"role": "user", "content": results})
                continue
            if reply.stop_reason == "max_tokens":
                out.errors.append("answer cut off at max_tokens")
                break
            checked = parse_and_verify(reply.text, lines_for)
            _apply_check(out, checked)
            if checked.ok or repaired:
                out.errors = list(checked.errors)
                break
            repaired = True
            messages.append(
                {"role": "user", "content": REPAIR_MSG.format(errors="\n- ".join(checked.errors))}
            )
        else:
            if out.result is None:
                out.errors.append(f"step budget exhausted ({max_steps} model turns)")
    except LLMError as e:
        out.errors.append(f"model error: {e}")
    out.repaired = repaired
    out.tool_calls = list(box.calls)
    out.latency_s = round(time.perf_counter() - t0, 3)
    return tr.fill(out)
