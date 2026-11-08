"""Retrieval against a real PostgreSQL + pgvector, with the deterministic fake embedder.

The leakage test is the important one: no dev/test case, and nothing from the query's own run,
may ever come back from search.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from triagebench.config import settings
from triagebench.data import Case, CaseStore, Label
from triagebench.retrieval.embed import FakeEmbedder

pytestmark = pytest.mark.db


def _db_or_skip() -> str:
    # A separate database: these tests DROP and rebuild the chunks table.
    dsn = os.environ.get(
        "TRIAGEBENCH_TEST_DATABASE_URL",
        settings().database_url.rsplit("/", 1)[0] + "/triagebench_test",
    )
    try:
        import psycopg

        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception:  # noqa: BLE001
        pytest.skip("no PostgreSQL available")
    return dsn


@pytest.fixture
def retriever(dataset: tuple[list[Case], dict[str, Label], Path]):  # type: ignore[no-untyped-def]
    from triagebench.retrieval.index import build_index
    from triagebench.retrieval.search import Retriever

    dsn = _db_or_skip()
    cases, labels, data_dir = dataset
    n = build_index(
        embedder=FakeEmbedder(),
        cases=cases,
        labels=labels,
        store=CaseStore(cases, data_dir),
        dsn=dsn,
        echo=lambda _: None,
    )
    assert n == len(cases)
    return Retriever(embedder=FakeEmbedder(), dsn=dsn), cases


def test_finds_the_similar_kb_case(retriever) -> None:  # type: ignore[no-untyped-def]
    r, _ = retriever
    hits = r.search("AssertionError test_parse_dates datetime.date", k=3)
    assert hits and hits[0].case_id == "acme/widgets/jobs/101"
    assert hits[0].failure_type == "test_assertion"


def test_no_leakage(retriever) -> None:  # type: ignore[no-untyped-def]
    r, cases = retriever
    by_id = {c.id: c for c in cases}
    for query_case in [c for c in cases if c.split != "kb"]:
        for mode in ("hybrid", "vector", "keyword"):
            hits = r.search(
                f"{query_case.job_name} AssertionError F841 numpy",
                k=10,
                exclude_run=query_case.run_id,
                mode=mode,
            )
            for h in hits:
                assert by_id[h.case_id].split == "kb", (mode, h.case_id)
                assert by_id[h.case_id].run_id != query_case.run_id, (mode, h.case_id)


def test_hnsw_matches_exact_search(retriever) -> None:  # type: ignore[no-untyped-def]
    """With the index disabled, exact search must give the same top results."""
    from triagebench.retrieval.index import connect

    r, _ = retriever
    q = FakeEmbedder().embed_query("ERROR: No matching distribution found for numpy")
    import numpy as np

    sql = "SELECT case_id FROM chunks WHERE split='kb' ORDER BY embedding <=> %s LIMIT 3"
    with connect(r.dsn) as conn:
        approx = [x[0] for x in conn.execute(sql, (np.asarray(q, np.float32),)).fetchall()]
        conn.execute("SET enable_indexscan = off")
        exact = [x[0] for x in conn.execute(sql, (np.asarray(q, np.float32),)).fetchall()]
    assert approx == exact
