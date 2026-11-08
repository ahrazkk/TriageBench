"""Hybrid search: PostgreSQL full-text + pgvector cosine, fused with reciprocal rank fusion.

Why both: CI logs mix exact identifiers (test names, error codes, package versions), which keyword
search matches exactly, with paraphrased messages ("connection refused" vs "could not connect"),
which embeddings catch.

RRF (Cormack et al. 2009): score(d) = sum over lists of 1 / (k + rank_d), k = 60. It needs only
ranks, so a cosine distance and a ts_rank never have to be put on the same scale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from triagebench.config import settings
from triagebench.retrieval.embed import Embedder, FakeEmbedder, get_embedder
from triagebench.retrieval.index import connect

RRF_K = 60
Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class Hit:
    case_id: str
    repo: str
    failure_type: str
    cause_class: str
    root_cause: str
    score: float


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked lists of ids. Pure function, unit-tested with known values."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


_WORD = re.compile(r"[A-Za-z0-9_]{3,}")


def or_tsquery(text: str, max_terms: int = 40) -> str:
    """'TypeError in test_parse' -> 'typeerror | test_parse' (OR, so long queries still match)."""
    seen: list[str] = []
    for w in _WORD.findall(text.lower()):
        if w not in seen:
            seen.append(w)
    return " | ".join(seen[:max_terms])


VECTOR_SQL = """
SELECT case_id FROM chunks
WHERE split = 'kb' AND run_id <> %(exclude)s
ORDER BY embedding <=> %(qvec)s LIMIT %(n)s
"""
KEYWORD_SQL = """
SELECT case_id FROM chunks, to_tsquery('simple', %(q)s) AS q
WHERE split = 'kb' AND run_id <> %(exclude)s AND tsv @@ q
ORDER BY ts_rank_cd(tsv, q) DESC, case_id LIMIT %(n)s
"""


class Retriever:
    def __init__(self, embedder: Embedder | None = None, dsn: str | None = None) -> None:
        self.dsn = dsn or settings().database_url
        if embedder is None:
            with connect(self.dsn) as conn:
                row = conn.execute("SELECT v FROM chunks_meta WHERE k = 'embedder'").fetchone()
            embedder = FakeEmbedder() if row and row[0] == "fake-hash" else get_embedder()
        self.embedder = embedder

    def search(
        self, query: str, k: int = 5, exclude_run: int = -1, mode: Mode = "hybrid", pool: int = 50
    ) -> list[Hit]:
        rankings: list[list[str]] = []
        with connect(self.dsn) as conn:
            if mode in ("hybrid", "vector"):
                qvec = self.embedder.embed_query(query)
                rows = conn.execute(
                    VECTOR_SQL, {"exclude": exclude_run, "qvec": _vec(qvec), "n": pool}
                ).fetchall()
                rankings.append([r[0] for r in rows])
            if mode in ("hybrid", "keyword"):
                q = or_tsquery(query)
                if q:
                    rows = conn.execute(
                        KEYWORD_SQL, {"exclude": exclude_run, "q": q, "n": pool}
                    ).fetchall()
                    rankings.append([r[0] for r in rows])
            fused = rrf(rankings)[:k]
            if not fused:
                return []
            ids = [cid for cid, _ in fused]
            meta = {
                r[0]: r
                for r in conn.execute(
                    "SELECT case_id, repo, failure_type, cause_class, root_cause FROM chunks "
                    "WHERE case_id = ANY(%s)",
                    (ids,),
                ).fetchall()
            }
        return [
            Hit(cid, meta[cid][1], meta[cid][2], meta[cid][3], meta[cid][4], score)
            for cid, score in fused
        ]


def _vec(v: list[float]) -> object:
    import numpy as np

    return np.asarray(v, dtype=np.float32)
