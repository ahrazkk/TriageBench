"""Label review order: disputed drafts first, then low/medium-confidence drafts."""

from __future__ import annotations

from pathlib import Path

from triagebench.data import Case, Label
from triagebench.labeling import review_order


def test_disputed_then_low_confidence_first(
    dataset: tuple[list[Case], dict[str, Label], Path],
) -> None:
    cases, labels, _ = dataset
    ids = [c.id for c in cases]
    labels[ids[0]].notes = "solid evidence; high confidence"
    labels[ids[1]].notes = "low confidence on cause"
    labels[ids[2]].notes = "medium confidence"
    labels[ids[3]].notes = "high confidence"
    labels[ids[4]].verified = True  # already reviewed: never shown again
    flags = {ids[3]: "draft_likely_wrong", ids[0]: "draft_upheld"}
    order = [c.id for c in review_order(cases, labels, flags)]
    assert order == [ids[3], ids[1], ids[2], ids[0]]
