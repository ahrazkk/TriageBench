from hypothesis import given, settings
from hypothesis import strategies as st

from triagebench.logs.clean import clean_line, clean_log
from triagebench.logs.redact import redact
from triagebench.logs.reduce import est_tokens, is_error_line, reduce_log


def test_clean_strips_timestamp_and_ansi_but_keeps_line_count() -> None:
    raw = "2026-11-01T12:00:00.1234567Z \x1b[31mFAILED\x1b[0m test_x\n2026-11-01T12:00:01Z ok\n"
    lines = clean_log(raw)
    assert lines == ["FAILED test_x", "ok"]
    assert clean_line("no timestamp here") == "no timestamp here"


def test_redaction_removes_email_and_tokens_and_counts_them() -> None:
    text = (
        "author jane.doe@corp.io pushed\n"
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789 leaked\n"
        "bot 41898282+github-actions[bot]@users.noreply.github.com is fine\n"
        "Authorization: Bearer abcdef0123456789abcdef\n"
    )
    out, counts = redact(text)
    assert "jane.doe@corp.io" not in out
    assert "ghp_" not in out
    assert "users.noreply.github.com" in out
    assert "abcdef0123456789abcdef" not in out
    assert counts == {"email": 1, "github_token": 1, "bearer": 1}


def test_error_line_detection() -> None:
    assert is_error_line("E       AssertionError: boom")
    assert is_error_line("npm ERR! code ERESOLVE")
    assert is_error_line("##[error]Process completed with exit code 2.")
    assert not is_error_line("Collecting numpy")
    assert not is_error_line("gcc -Werror -O2 main.c")


def test_hybrid_keeps_error_region_and_tail() -> None:
    lines = [f"noise {i}" for i in range(5000)]
    lines[1234] = "E   AssertionError: expected 3, got 4"
    ex = reduce_log(lines, "hybrid", 1000)
    kept = dict(ex.lines)
    assert 1235 in kept  # 1-based line number of the error
    assert 5000 in kept  # the tail
    assert ex.total_lines == 5000
    assert "lines omitted" in ex.render()


@settings(max_examples=60, deadline=None)
@given(
    lines=st.lists(
        st.sampled_from(["ok", "FAILED test_a", "error: boom", "", "x" * 900, "Traceback (most"]),
        min_size=1,
        max_size=400,
    ),
    budget=st.integers(min_value=50, max_value=3000),
    strategy=st.sampled_from(["tail", "errors", "hybrid"]),
)
def test_reducer_properties(lines: list[str], budget: int, strategy: str) -> None:
    ex = reduce_log(lines, strategy, budget)
    nums = [n for n, _ in ex.lines]
    assert nums == sorted(set(nums))  # ordered, no duplicates
    assert all(1 <= n <= len(lines) for n in nums)
    assert all(lines[n - 1] == t for n, t in ex.lines)  # never rewrites a line
    # never exceeds its budget (each line costs its text, capped, plus a 3-token gutter)
    assert sum(est_tokens(t[:400]) + 3 for _, t in ex.lines) <= budget
    # the last line survives whenever it fits at all
    if strategy != "errors" and est_tokens(lines[-1][:400]) + 3 <= budget // 10:
        assert nums and nums[-1] == len(lines)
