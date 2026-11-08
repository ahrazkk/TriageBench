import pytest
from conftest import answer_json
from hypothesis import given
from hypothesis import strategies as st

from triagebench.agent.schema import Evidence
from triagebench.agent.verify import check_evidence, parse_and_verify, quote_ok
from triagebench.evals.metrics import macro_f1
from triagebench.evals.stats import bootstrap_ci, cohen_kappa, mcnemar_exact, wilson
from triagebench.retrieval.search import or_tsquery, rrf

LOG = ["first line", "E   AssertionError: expected 3, got 4", "last line"]


def lines_for(job_id: int) -> list[str]:
    if job_id != 1:
        raise KeyError(job_id)
    return LOG


def test_correct_quote_passes() -> None:
    assert check_evidence(Evidence(job_id=1, line=2, text=LOG[1]), lines_for) is None


def test_long_substring_passes_short_one_fails() -> None:
    assert quote_ok("AssertionError: expected 3", LOG[1])
    assert not quote_ok("expected", LOG[1])  # too short to be a meaningful quote


def test_changed_character_fails() -> None:
    msg = check_evidence(Evidence(job_id=1, line=2, text=LOG[1].replace("4", "5")), lines_for)
    assert msg is not None and "doesn't contain" in msg


def test_wrong_line_number_fails_and_hints_the_right_one() -> None:
    msg = check_evidence(Evidence(job_id=1, line=1, text=LOG[1]), lines_for)
    assert msg is not None and "line 2" in msg


def test_out_of_range_and_unknown_job() -> None:
    assert "outside" in str(check_evidence(Evidence(job_id=1, line=9, text="x"), lines_for))
    assert "isn't a job" in str(check_evidence(Evidence(job_id=2, line=1, text="x"), lines_for))


@given(st.lists(st.text(min_size=1, max_size=60), min_size=1, max_size=30), st.data())
def test_any_exact_line_passes_any_edit_fails(lines: list[str], data: st.DataObject) -> None:
    i = data.draw(st.integers(0, len(lines) - 1))
    line = lines[i]
    if not line.strip():
        return
    assert quote_ok(line, line)
    edited = line.strip() + "§"  # an edit that can't appear in the original
    assert not quote_ok(edited, line)


def test_parse_handles_fences_schema_errors_and_bad_json() -> None:
    ok = parse_and_verify("```json\n" + answer_json(1, 2, LOG[1]) + "\n```", lines_for)
    assert ok.ok and ok.evidence_valid == 1
    bad_schema = parse_and_verify(answer_json(1, 2, LOG[1], failure_type="vibes"), lines_for)
    assert not bad_schema.ok and "schema" in bad_schema.errors[0]
    assert "valid JSON" in parse_and_verify("{not json", lines_for).errors[0]


# ---- stats: known values from PLAN section 5.11 ------------------------------------------------
def test_wilson_known_values() -> None:
    lo, hi = wilson(80, 100)
    assert (round(lo, 3), round(hi, 3)) == (0.711, 0.867)
    lo, hi = wilson(70, 100)
    assert (round(lo, 3), round(hi, 3)) == (0.604, 0.781)


def test_mcnemar_known_value() -> None:
    assert round(mcnemar_exact(2, 9), 3) == 0.065
    assert mcnemar_exact(0, 0) == 1.0


def test_kappa() -> None:
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    # 50% agreement when chance agreement is also 50% -> kappa 0
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)


def test_bootstrap_brackets_the_mean() -> None:
    xs = [0, 1] * 50
    lo, hi = bootstrap_ci(xs, lambda s: sum(s) / len(s), n_boot=2000)
    assert lo < 0.5 < hi


def test_macro_f1_weights_classes_equally() -> None:
    gold = ["a"] * 9 + ["b"]
    pred = ["a"] * 10  # 90% accurate, but class b is never found
    assert macro_f1(gold, pred) == pytest.approx((2 * 0.9 / 1.9 + 0) / 2)


def test_rrf_known_values() -> None:
    fused = dict(rrf([["x", "y"], ["z", "w", "x"]]))
    assert round(fused["x"], 4) == round(1 / 61 + 1 / 63, 4) == 0.0323
    assert round(fused["y"], 4) == 0.0161


def test_or_tsquery() -> None:
    assert or_tsquery("TypeError in test_parse TypeError") == "typeerror | test_parse"
    assert or_tsquery("a b") == ""
