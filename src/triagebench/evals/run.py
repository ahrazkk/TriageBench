"""Run a system over a split and save everything needed to reproduce the numbers.

Every result file records: system, model, effort, prompt hash, tool-description hash, dataset hash,
label status (how many labels are human-verified), git commit, date, budgets, every outcome, and
the metrics computed from them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from triagebench.agent.llm import AnthropicLLM, CassetteLLM, LLMClient
from triagebench.agent.loop import PROMPT_VERSION, Outcome, load_prompt, prompt_hash, run_agent
from triagebench.baselines.regex import run_regex
from triagebench.baselines.single_call import run_single_call
from triagebench.config import REPO_ROOT, settings
from triagebench.data import Case, CaseStore, load_cases, load_labels
from triagebench.evals.metrics import score
from triagebench.tools.impl import TOOL_SPECS, Toolbox

SystemFn = Callable[[Case], Outcome]
SYSTEMS = {
    "b0": "regex rules",
    "b1": "single LLM call",
    "b1-tail": "single call, tail excerpt",
    "b2": "agent with tools",
    "b3": "agent + retrieval",
}


def make_llm(replay_only: bool = False) -> LLMClient:
    s = settings()
    inner = None if replay_only else AnthropicLLM()
    return CassetteLLM(inner, s.cassette_dir, replay_only=replay_only)


def make_system(
    name: str,
    box: Toolbox,
    llm: LLMClient | None = None,
    replay_only: bool = False,
    tools: str = "direct",
) -> SystemFn:
    if name == "b0":
        return lambda c: run_regex(c, box)
    llm = llm or make_llm(replay_only)
    if name == "b1":
        return lambda c: run_single_call(c, llm, box)
    if name == "b1-tail":
        return lambda c: run_single_call(c, llm, box, strategy="tail")
    if name in ("b2", "b3"):
        with_r = name == "b3"

        if tools == "mcp":
            # Every tool call goes over MCP to `triagebench mcp` (a stdio subprocess).
            from triagebench.tools.mcp_client import McpToolbox

            mcp_box = McpToolbox(box.store)
            return lambda c: run_agent(
                c,
                llm,
                mcp_box,
                with_retrieval=with_r,
                system_name=name,
            )

        def run(c: Case) -> Outcome:
            # One Toolbox per case: tool-call bookkeeping isn't shared between threads.
            local = Toolbox(box.store, retriever=box.retriever, allow_live=False)
            return run_agent(c, llm, local, with_retrieval=with_r, system_name=name)

        return run
    raise ValueError(f"unknown system {name!r}; choose from {', '.join(SYSTEMS)}")


def git_commit() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "prompts"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "uncommitted"


def dataset_hash(cases: list[Case], labels: dict[str, Any]) -> str:
    blob = json.dumps(
        [[c.id, labels[c.id].model_dump(mode="json") if c.id in labels else None] for c in cases],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def run_eval(
    system: str,
    split: str,
    limit: int = 0,
    concurrency: int = 4,
    tag: str = "",
    replay_only: bool = False,
    tools: str = "direct",
    echo: Callable[[str], None] = print,
) -> Path:
    s = settings()
    labels = load_labels()
    all_cases = load_cases()
    cases = [c for c in all_cases if c.split == split and c.id in labels]
    cases.sort(key=lambda c: c.id)
    if limit:
        cases = cases[:limit]
    if not cases:
        raise SystemExit(f"no labelled cases in split {split!r}")
    retriever = None
    if system == "b3":
        from triagebench.retrieval.search import Retriever

        retriever = Retriever()
    box = Toolbox(CaseStore(all_cases), retriever=retriever, allow_live=False)
    fn = make_system(system, box, replay_only=replay_only, tools=tools)
    if tools == "mcp":
        concurrency = 1  # one MCP session, used sequentially
    echo(f"{system} ({SYSTEMS.get(system)}) on {len(cases)} {split} cases ...")

    def safe(c: Case) -> Outcome:
        try:
            return fn(c)
        except Exception as e:  # noqa: BLE001 - one bad case must not kill the run
            return Outcome(case_id=c.id, system=system, errors=[f"crash: {type(e).__name__}: {e}"])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        outcomes = list(pool.map(safe, cases))
    metrics = score(outcomes, labels)
    agent_prompt = load_prompt("agent", RETRIEVAL_STEP="", MAX_STEPS=s.max_steps)
    used = [labels[c.id] for c in cases]
    now = datetime.now(UTC)
    tag = tag or ("mcp" if tools == "mcp" else "")
    run_id = f"{now:%Y%m%d-%H%M%S}-{system}-{split}" + (f"-{tag}" if tag else "")
    record = {
        "run_id": run_id,
        "system": system,
        "system_desc": SYSTEMS.get(system),
        "split": split,
        "n_cases": len(cases),
        "model": None if system == "b0" else s.model,
        "effort": None if system == "b0" else s.effort,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash(agent_prompt, load_prompt("single_call")),
        "tools_hash": prompt_hash(json.dumps(TOOL_SPECS, sort_keys=True)),
        "dataset_hash": dataset_hash(cases, labels),
        "labels": {
            "verified": sum(lab.verified for lab in used),
            "ai_drafted_unverified": sum(lab.ai_drafted and not lab.verified for lab in used),
        },
        "budgets": {
            "max_steps": s.max_steps,
            "max_input_tokens": s.max_input_tokens,
            "excerpt_tokens": s.excerpt_tokens,
        },
        "tools_transport": tools,
        "commit": git_commit(),
        "date": now.isoformat(timespec="seconds"),
        "metrics": metrics,
        "outcomes": [o.model_dump(mode="json") for o in outcomes],
    }
    s.results_dir.mkdir(parents=True, exist_ok=True)
    path = s.results_dir / f"{run_id}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    a, b = metrics["axis_A"], metrics["axis_B"]
    echo(
        f"  axis A acc {a['accuracy']:.1%}"
        f" [{a['accuracy_ci95'][0]:.1%}, {a['accuracy_ci95'][1]:.1%}]"
        f" F1 {a['macro_f1']:.3f} | axis B acc {b['accuracy']:.1%}"
        f" [{b['accuracy_ci95'][0]:.1%}, {b['accuracy_ci95'][1]:.1%}] F1 {b['macro_f1']:.3f}"
        f" | evidence {metrics['evidence_validity']} | ${metrics['cost_usd_mean']:.4f}/triage"
        f" | errors {metrics['errors']}"
    )
    return path
