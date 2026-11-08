"""The agent loop with a scripted FakeLLM: deterministic, no network, no API key."""

from __future__ import annotations

from conftest import PYTEST_BODY, answer_json

from triagebench.agent.llm import FakeLLM
from triagebench.agent.loop import run_agent
from triagebench.baselines.regex import run_regex
from triagebench.baselines.single_call import run_single_call
from triagebench.tools.impl import Toolbox

JOB = 101
ERR_LINE = 204 + 5  # body starts after 200 setup lines + 3 header lines; the "E   ..." line
ERR_TEXT = PYTEST_BODY[5]


def test_normal_run(box: Toolbox) -> None:
    llm = FakeLLM(
        [
            [("get_run_overview", {"target": str(JOB)})],
            [("get_log_excerpt", {"job_id": JOB})],
            answer_json(JOB, ERR_LINE, ERR_TEXT),
        ]
    )
    out = run_agent(box.store.get(JOB), llm, box)
    assert out.result is not None and out.errors == []
    assert out.result.failure_type.value == "test_assertion"
    assert out.tool_calls == ["get_run_overview", "get_log_excerpt"]
    assert out.steps == 3 and out.evidence_valid == 1 and not out.repaired
    # the tool output really reached the model, wrapped as untrusted
    last_user = llm.calls[-1]["messages"][-1]["content"]
    assert "<untrusted_log" in last_user[0]["content"]


def test_unknown_tool_and_tool_error_are_reported_back(box: Toolbox) -> None:
    llm = FakeLLM(
        [
            [("delete_repo", {})],
            [("search_log", {"job_id": JOB, "pattern": "("})],  # invalid regex
            answer_json(JOB, ERR_LINE, ERR_TEXT),
        ]
    )
    out = run_agent(box.store.get(JOB), llm, box)
    assert out.result is not None
    results = [m for m in llm.calls[1]["messages"] if m["role"] == "user"][-1]["content"]
    assert results[0]["is_error"] and "unknown tool" in results[0]["content"]
    results = [m for m in llm.calls[2]["messages"] if m["role"] == "user"][-1]["content"]
    assert results[0]["is_error"] and "valid Python regex" in results[0]["content"]


def test_budget_exceeded(box: Toolbox) -> None:
    llm = FakeLLM([[("get_run_history", {"job_id": JOB})]] * 3)
    out = run_agent(box.store.get(JOB), llm, box, max_steps=3)
    assert out.result is None
    assert any("budget" in e for e in out.errors)
    # the model was warned before its last turn
    warned = llm.calls[-1]["messages"][-1]["content"][-1]
    assert warned["type"] == "text" and "one turn left" in warned["text"]


def test_input_token_budget(box: Toolbox) -> None:
    llm = FakeLLM([[("get_run_history", {"job_id": JOB})]] * 5)
    out = run_agent(box.store.get(JOB), llm, box, max_steps=5, max_input_tokens=1500)
    assert any("input-token budget" in e for e in out.errors)


def test_invalid_answer_then_successful_repair(box: Toolbox) -> None:
    llm = FakeLLM(
        [
            "not json at all",
            answer_json(JOB, ERR_LINE, ERR_TEXT),
        ]
    )
    out = run_agent(box.store.get(JOB), llm, box)
    assert out.repaired and out.result is not None and out.errors == []
    assert "failed validation" in llm.calls[1]["messages"][-1]["content"]


def test_hallucinated_evidence_is_caught_after_one_repair(box: Toolbox) -> None:
    fake = "E   AssertionError: something the log never said"
    llm = FakeLLM([answer_json(JOB, ERR_LINE, fake), answer_json(JOB, ERR_LINE, fake)])
    out = run_agent(box.store.get(JOB), llm, box)
    assert out.repaired and out.result is not None
    assert out.evidence_valid == 0 and out.errors


def test_single_call_baseline(box: Toolbox) -> None:
    llm = FakeLLM([answer_json(JOB, ERR_LINE, ERR_TEXT)])
    out = run_single_call(box.store.get(JOB), llm, box)
    assert out.result is not None and out.steps == 1 and out.evidence_valid == 1
    assert "<untrusted_log" in llm.calls[0]["messages"][0]["content"]


def test_regex_baseline(box: Toolbox) -> None:
    assert run_regex(box.store.get(101), box).result.failure_type.value == "test_assertion"  # type: ignore[union-attr]
    dep = run_regex(box.store.get(102), box).result
    assert dep is not None and dep.failure_type.value == "dependency_install"
    assert dep.cause_class.value == "environment"
