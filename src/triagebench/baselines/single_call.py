"""B1: a workflow, not an agent. Metadata + the reduced log excerpt -> one model call -> verify.

It gets exactly the excerpt the agent's first get_log_excerpt call returns, so B1 vs B2 measures
what the tools (and the model choosing them) add.
"""

from __future__ import annotations

import time
from typing import Any

from triagebench.agent.llm import LLMClient, LLMError
from triagebench.agent.loop import REPAIR_MSG, Outcome, Tracker, _apply_check, load_prompt
from triagebench.agent.schema import output_json_schema
from triagebench.agent.verify import parse_and_verify
from triagebench.config import settings
from triagebench.data import Case
from triagebench.tools.impl import Toolbox


def run_single_call(case: Case, llm: LLMClient, box: Toolbox, strategy: str = "hybrid") -> Outcome:
    system = load_prompt("single_call")
    overview = box.get_run_overview(str(case.job_id))
    excerpt = box.get_log_excerpt(case.job_id, strategy, settings().excerpt_tokens)
    user = f"# Run\n{overview}\n\n# Log excerpt\n{excerpt}"
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    out = Outcome(case_id=case.id, system=f"b1-{strategy}" if strategy != "hybrid" else "b1")
    tr = Tracker(llm.model)
    t0 = time.perf_counter()
    try:
        for attempt in range(2):  # the answer, plus one repair
            reply = llm.generate(system, messages, [], json_schema=output_json_schema())
            tr.add(reply.usage, reply.cached)
            checked = parse_and_verify(reply.text, box.store.lines)
            _apply_check(out, checked)
            out.errors = list(checked.errors)
            if checked.ok or attempt == 1:
                break
            out.repaired = True
            messages += [
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": REPAIR_MSG.format(errors="\n- ".join(checked.errors))},
            ]
    except LLMError as e:
        out.errors.append(f"model error: {e}")
    out.latency_s = round(time.perf_counter() - t0, 3)
    return tr.fill(out)
