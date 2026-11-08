"""Retrieval quality on its own, separate from whether it helps the agent (PLAN M5).

For every dev case, query the kb with its own error region (the same text an index chunk would
hold, minus labels) and check the top hits:
- recall@5 on cases whose labelled duplicate group has a member in kb (the plan's metric; few
  such cases in dataset v0, so it's reported with its denominator)
- label agreement: share of dev cases whose top-1 / majority-of-top-5 kb hit has the same
  failure_type / cause_class (a proxy for "would the retrieved examples point the right way")
- latency per query
for keyword-only, vector-only and hybrid (RRF). Also checks HNSW top-5 == exact top-5.

    bash scripts/dev.sh python scripts/retrieval_eval.py
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime

import numpy as np

from triagebench.config import settings
from triagebench.data import CaseStore, load_cases, load_labels
from triagebench.evals.stats import wilson
from triagebench.logs.reduce import reduce_log
from triagebench.retrieval.index import connect
from triagebench.retrieval.search import Retriever


def main() -> None:
    cases, labels = load_cases(), load_labels()
    store = CaseStore(cases)
    kb = [c for c in cases if c.split == "kb" and c.id in labels]
    dev = [c for c in cases if c.split == "dev" and c.id in labels]
    kb_groups = {labels[c.id].duplicate_group for c in kb if labels[c.id].duplicate_group}
    r = Retriever()
    out: dict[str, object] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedder": r.embedder.name,
        "n_kb": len(kb),
        "n_dev": len(dev),
    }
    for mode in ("keyword", "vector", "hybrid"):
        a1 = b1 = a5 = b5 = 0
        dup_hits, dup_n = 0, 0
        lat: list[float] = []
        for c in dev:
            ex = reduce_log(store.lines(c.job_id), "errors", max_tokens=600)
            q = f"{c.job_name}\n" + "\n".join(t[:400] for _, t in ex.lines if t.strip())
            t0 = time.perf_counter()
            hits = r.search(q, k=5, exclude_run=c.run_id, mode=mode)  # type: ignore[arg-type]
            lat.append((time.perf_counter() - t0) * 1000)
            lab = labels[c.id]
            if hits:
                a1 += hits[0].failure_type == lab.failure_type
                b1 += hits[0].cause_class == lab.cause_class
                a5 += Counter(h.failure_type for h in hits).most_common(1)[0][0] == lab.failure_type
                b5 += Counter(h.cause_class for h in hits).most_common(1)[0][0] == lab.cause_class
            if lab.duplicate_group and lab.duplicate_group in kb_groups:
                dup_n += 1
                ids = {h.case_id for h in hits}
                dup_hits += any(labels[i].duplicate_group == lab.duplicate_group for i in ids)
        n = len(dev)
        out[mode] = {
            "top1_same_failure_type": a1 / n,
            "top1_same_failure_type_ci95": wilson(a1, n),
            "top1_same_cause_class": b1 / n,
            "top5_majority_same_failure_type": a5 / n,
            "top5_majority_same_cause_class": b5 / n,
            "recall_at_5_duplicates": (dup_hits / dup_n) if dup_n else None,
            "duplicate_queries": dup_n,
            "latency_ms_p50": float(np.median(lat)),
        }
        print(mode, json.dumps(out[mode]))
    # HNSW vs exact search on the same queries
    agree = 0
    with connect() as conn:
        for c in dev:
            ex = reduce_log(store.lines(c.job_id), "errors", max_tokens=600)
            q = np.asarray(
                r.embedder.embed_query("\n".join(t[:400] for _, t in ex.lines)), np.float32
            )
            sql = "SELECT case_id FROM chunks WHERE split='kb' ORDER BY embedding <=> %s LIMIT 5"
            approx = [x[0] for x in conn.execute(sql, (q,)).fetchall()]
            conn.execute("SET enable_indexscan = off")
            exact = [x[0] for x in conn.execute(sql, (q,)).fetchall()]
            conn.execute("SET enable_indexscan = on")
            agree += len(set(approx) & set(exact)) / 5
    out["hnsw_recall_at_5_vs_exact"] = agree / len(dev)
    print("hnsw recall@5 vs exact:", out["hnsw_recall_at_5_vs_exact"])
    path = settings().results_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-retrieval-dev.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("saved", path.name)


if __name__ == "__main__":
    main()
