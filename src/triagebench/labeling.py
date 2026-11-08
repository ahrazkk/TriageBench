"""Labelling helpers: render a case for a human (or a drafting model), and the review loop.

`render_pack()` shows everything a labeller may use, including the future-only hints.
`review()` walks unverified labels so a human can accept or correct each one; only then is a
label marked `verified`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from triagebench.agent.schema import CauseClass, FailureType
from triagebench.config import settings
from triagebench.data import Case, CaseStore, Label, load_labels, read_jsonl, write_jsonl
from triagebench.logs.reduce import reduce_log
from triagebench.tools.impl import Toolbox


def render_pack(case: Case, store: CaseStore, excerpt_tokens: int = 2800) -> str:
    box = Toolbox(store, allow_live=False)
    lines = store.lines(case.job_id)
    ex = reduce_log(lines, "hybrid", excerpt_tokens)
    h = case.hints
    parts = [
        f"# {case.id}",
        f"url: {case.html_url}",
        box.get_run_overview(str(case.job_id)),
        "",
        "## Hints for the labeller (from the future; systems never see these)",
        f"source: {case.source}  (rerun_passed = a later attempt of this same run passed)",
        f"rerun_succeeded: {h.rerun_succeeded}",
        f"next_success_sha: {h.next_success_sha}  same_sha: {h.next_success_same_sha}",
        f"fix_files ({len(h.fix_files)}): {', '.join(h.fix_files[:25])}",
        f"same_day_repo_failures: {h.same_day_repo_failures}",
        "",
        "## Change under test",
        box.get_change_summary(case.job_id)[:3000],
        "",
        "## Run history",
        box.get_run_history(case.job_id)[:2500],
        "",
        "## Log excerpt",
        ex.header(),
        ex.render(),
    ]
    return "\n".join(parts)


def merge_labels(new: list[Label]) -> int:
    """Append labels as new versions (never overwrite history). Returns how many were added."""
    path = settings().labels_file
    rows = read_jsonl(path)
    current = load_labels()
    added = 0
    for lab in new:
        old = current.get(lab.case_id)
        lab.version = (old.version + 1) if old else 1
        rows.append(lab.model_dump(mode="json"))
        added += 1
    write_jsonl(path, rows)
    return added


def _draft_rank(notes: str) -> int:
    n = notes.lower()
    if "low" in n:
        return 0
    if "medium" in n or "review" in n:
        return 1
    return 2


def review(
    cases: list[Case],
    store: CaseStore,
    ask: Callable[[str, str], str],
    show: Callable[[str], None],
    who: str = "ahraz",
) -> int:
    """Accept/correct unverified labels one by one. Returns how many were verified."""
    labels = load_labels()
    todo = [c for c in cases if c.id in labels and not labels[c.id].verified]
    # Hardest first: drafts whose notes say "low" confidence, then "medium", then the rest.
    todo.sort(key=lambda c: _draft_rank(labels[c.id].notes))
    done = 0
    for i, c in enumerate(todo, 1):
        lab = labels[c.id]
        show(render_pack(c, store))
        show(
            f"\n--- draft label ({i}/{len(todo)}, by {lab.labeller}) ---\n"
            f"failure_type: {lab.failure_type}\ncause_class:  {lab.cause_class}\n"
            f"root_cause:   {lab.root_cause}\nevidence:     {lab.evidence_lines}\n"
            f"group:        {lab.duplicate_group}\nnotes:        {lab.notes}"
        )
        choice = ask("[a]ccept, [e]dit, [s]kip, [q]uit", "a").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            continue
        fixed = lab.model_copy()
        if choice == "e":
            ft = ask(f"failure_type {[x.value for x in FailureType]}", lab.failure_type)
            cc = ask(f"cause_class {[x.value for x in CauseClass]}", lab.cause_class)
            FailureType(ft), CauseClass(cc)  # validate
            fixed.failure_type, fixed.cause_class = ft, cc
            fixed.root_cause = ask("root_cause", lab.root_cause)
            ev = ask("evidence lines (comma separated)", ",".join(map(str, lab.evidence_lines)))
            fixed.evidence_lines = [int(x) for x in ev.split(",") if x.strip()]
            fixed.duplicate_group = ask("duplicate group", lab.duplicate_group or "") or None
        fixed.verified = True
        fixed.labeller = who
        fixed.created_at = datetime.now(UTC)
        fixed.notes = lab.notes + f" | reviewed by {who}" + (" (edited)" if choice == "e" else "")
        merge_labels([fixed])
        done += 1
    return done
