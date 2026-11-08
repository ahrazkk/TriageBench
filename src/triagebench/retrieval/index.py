"""Build the knowledge base in PostgreSQL + pgvector: one chunk per labelled case.

Each chunk = repo + job name + failed step + the case's error region + its label and root cause.
The row also stores its split and run id, so search can filter to `kb` and exclude the query's own
run (the leakage rules in PLAN section 5.8).
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg
from pgvector.psycopg import register_vector

from triagebench.config import settings
from triagebench.data import Case, CaseStore, Label, load_cases, load_labels
from triagebench.logs.reduce import reduce_log
from triagebench.retrieval.embed import Embedder, get_embedder

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
  id           bigserial PRIMARY KEY,
  case_id      text NOT NULL,
  run_id       bigint NOT NULL,
  repo         text NOT NULL,
  split        text NOT NULL,
  failure_type text NOT NULL,
  cause_class  text NOT NULL,
  root_cause   text NOT NULL,
  text         text NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
  embedding    vector({dim}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_split ON chunks (split);
"""


def chunk_text(case: Case, label: Label, lines: list[str]) -> str:
    excerpt = reduce_log(lines, "errors", max_tokens=600)
    # Cap each line like the excerpts do: some logs have 100K-character minified lines.
    body = "\n".join(t[:400] for _, t in excerpt.lines if t.strip())
    return (
        f"repo: {case.repo}\njob: {case.job_name}\nfailed step: {', '.join(case.failed_steps)}\n"
        f"root cause: {label.root_cause}\n{body}"
    )


def connect(dsn: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(dsn or settings().database_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def build_index(
    fake: bool = False,
    embedder: Embedder | None = None,
    cases: list[Case] | None = None,
    labels: dict[str, Label] | None = None,
    store: CaseStore | None = None,
    dsn: str | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    emb = embedder or get_embedder(fake)
    cases = cases if cases is not None else load_cases()
    labels = labels if labels is not None else load_labels()
    store = store or CaseStore(cases)
    todo = [c for c in cases if c.id in labels and c.split in ("kb", "dev", "test")]
    texts = [chunk_text(c, labels[c.id], store.lines(c.job_id)) for c in todo]
    echo(f"embedding {len(texts)} chunks with {emb.name} ...")
    vecs = emb.embed_documents(texts)
    with connect(dsn) as conn:
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute(SCHEMA.format(dim=emb.dim))
        with conn.cursor() as cur:
            for c, t, v in zip(todo, texts, vecs, strict=True):
                lab = labels[c.id]
                cur.execute(
                    "INSERT INTO chunks (case_id, run_id, repo, split, failure_type, cause_class,"
                    " root_cause, text, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        c.id,
                        c.run_id,
                        c.repo,
                        c.split,
                        lab.failure_type,
                        lab.cause_class,
                        lab.root_cause,
                        t,
                        v,
                    ),
                )
        # Remember which embedder built the index: queries must use the same one.
        conn.execute("CREATE TABLE IF NOT EXISTS chunks_meta (k text PRIMARY KEY, v text)")
        conn.execute(
            "INSERT INTO chunks_meta (k, v) VALUES ('embedder', %s) "
            "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
            (emb.name,),
        )
    return len(todo)
