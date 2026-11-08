"""`triagebench <command>`: doctor, collect, split, stats, triage, eval, report, index, mcp."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

import typer
import yaml

from triagebench.config import REPO_ROOT, secrets, settings
from triagebench.data import Case, load_cases, load_labels, save_cases

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


# ---- doctor ----------------------------------------------------------------------------------
@app.command()
def doctor() -> None:
    """Check the GitHub token, the database, the model API and the GPU."""
    ok = True

    def report(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        ok &= passed
        typer.echo(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    from triagebench.github.client import GitHubClient

    try:
        gh = GitHubClient(secrets().github_token)
        core = gh.rate_limit()
        report("github", bool(secrets().github_token), f"{core['remaining']}/{core['limit']} left")
    except Exception as e:  # noqa: BLE001 - doctor reports every failure
        report("github", False, str(e))

    try:
        import psycopg

        with psycopg.connect(settings().database_url, connect_timeout=5) as conn:
            v = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()
        report("database", v is not None, f"pgvector {v[0] if v else 'missing'}")
    except Exception as e:  # noqa: BLE001
        report("database", False, str(e).splitlines()[0])

    if secrets().anthropic_api_key:
        try:
            from triagebench.agent.llm import AnthropicLLM

            llm = AnthropicLLM()
            reply = llm.generate(
                system="Reply with the single word pong.",
                messages=[{"role": "user", "content": "ping"}],
                tools=[],
                max_tokens=64,
            )
            report("model", True, f"{settings().model} said {reply.text.strip()[:20]!r}")
        except Exception as e:  # noqa: BLE001
            report("model", False, str(e)[:200])
    else:
        report("model", False, "ANTHROPIC_API_KEY not set in .env")

    gpu = shutil.which("nvidia-smi")
    detail = "no nvidia-smi"
    if gpu:
        out = subprocess.run(
            [gpu, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
        detail = out.stdout.strip() or out.stderr.strip()
    typer.echo(f"[INFO] gpu: {detail} (only needed for local embeddings)")
    raise typer.Exit(0 if ok else 1)


# ---- collect ---------------------------------------------------------------------------------
@app.command()
def collect(
    days: int = 30,
    per_repo: int = 6,
    workers: int = 4,
    repos_file: Path = REPO_ROOT / "data" / "repos.yaml",
    only: Annotated[list[str] | None, typer.Option(help="collect just these repos")] = None,
) -> None:
    """Snapshot failed jobs from the repos in data/repos.yaml (adds to data/cases.jsonl)."""
    from triagebench.github.client import GitHubClient
    from triagebench.github.collect import collect_repo, norm_job

    s = settings()
    repos: list[str] = only or yaml.safe_load(repos_file.read_text())["repos"]
    cases = {c.id: c for c in load_cases()}
    gh = GitHubClient(secrets().github_token, cache_dir=s.data_dir / "_http_cache")
    lock = threading.Lock()

    def one(repo: str) -> None:
        with lock:
            have = [c for c in cases.values() if c.repo == repo]
        if len(have) >= per_repo:
            typer.echo(f"{repo}: already have {len(have)}")
            return
        new = collect_repo(
            gh,
            repo,
            s.data_dir,
            days=days,
            max_cases=per_repo - len(have),
            known_runs={c.run_id for c in have},
            known_job_names={norm_job(c.job_name) for c in have},
            log=lambda m: typer.echo(f"[{repo}] {m.strip()}"),
        )
        with lock:
            for c in new:
                cases[c.id] = c
            save_cases(list(cases.values()))  # save after every repo: never lose collected data
            typer.echo(f"[{repo}] done: +{len(new)} ({gh.calls} API calls, {gh.remaining} left)")

    # A few repos at a time: polite to GitHub, and ~4x faster than one by one.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, repos))
    typer.echo(f"total cases: {len(cases)}")


# ---- split -----------------------------------------------------------------------------------
@app.command()
def split(kb_fraction: float = 0.5) -> None:
    """Time-based split per repo: the oldest cases become retrieval memory (kb), the rest dev.

    Cases that already have a split keep it, so adding new data never moves old cases around.
    """
    cases = load_cases()
    by_repo: dict[str, list[Case]] = {}
    for c in cases:
        by_repo.setdefault(c.repo, []).append(c)
    for group in by_repo.values():
        group.sort(key=lambda c: c.created_at)
        n_kb = int(round(len(group) * kb_fraction))
        for i, c in enumerate(group):
            if c.split is None:
                c.split = "kb" if i < n_kb else "dev"
    save_cases(cases)
    typer.echo(dict(Counter(c.split for c in cases)))


# ---- labelling -------------------------------------------------------------------------------
@app.command("label-packs")
def label_packs(out_dir: Path = REPO_ROOT / "data" / "raw" / "_label_packs") -> None:
    """Write one review file per unlabelled case (metadata, hints, diff, history, excerpt)."""
    from triagebench.data import CaseStore
    from triagebench.labeling import render_pack

    cases, labels = load_cases(), load_labels()
    store = CaseStore(cases)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for c in cases:
        if c.id in labels:
            continue
        (out_dir / f"{c.job_id}.md").write_text(render_pack(c, store), encoding="utf-8")
        n += 1
    typer.echo(f"wrote {n} packs to {out_dir}")


@app.command("import-drafts")
def import_drafts(
    files: Annotated[list[Path], typer.Argument(help="jsonl files of drafted labels")],
    labeller: str = "claude-draft",
) -> None:
    """Import AI-drafted labels (ai_drafted=true, verified=false) after validating them."""
    from datetime import UTC, datetime

    from triagebench.agent.schema import CauseClass, FailureType
    from triagebench.data import Label, read_jsonl
    from triagebench.labeling import merge_labels

    known = {c.id for c in load_cases()}
    new: list[Label] = []
    for f in files:
        for row in read_jsonl(f):
            if row["case_id"] not in known:
                typer.echo(f"skip unknown case {row['case_id']}")
                continue
            FailureType(row["failure_type"]), CauseClass(row["cause_class"])  # validate enums
            new.append(
                Label(
                    case_id=row["case_id"],
                    failure_type=row["failure_type"],
                    cause_class=row["cause_class"],
                    root_cause=row["root_cause"],
                    evidence_lines=[int(x) for x in row.get("evidence_lines", [])],
                    duplicate_group=row.get("duplicate_group") or None,
                    labeller=labeller,
                    ai_drafted=True,
                    verified=False,
                    notes=row.get("notes", ""),
                    created_at=datetime.now(UTC),
                )
            )
    typer.echo(f"imported {merge_labels(new)} draft labels")


@app.command()
def review(who: str = "ahraz") -> None:
    """Walk unverified labels: accept or correct each one (marks it human-verified)."""
    from triagebench.data import CaseStore
    from triagebench.labeling import review as run_review

    cases = load_cases()
    n = run_review(
        cases,
        CaseStore(cases),
        ask=lambda q, d: str(typer.prompt(q, default=d)),
        show=typer.echo,
        who=who,
    )
    typer.echo(f"verified {n} labels")


# ---- stats -----------------------------------------------------------------------------------
@app.command()
def stats() -> None:
    """Dataset statistics (cases per repo, split, label class; log length P50/P95)."""
    from triagebench.evals.report import dataset_stats

    typer.echo(dataset_stats(load_cases(), load_labels()))


# ---- triage ----------------------------------------------------------------------------------
@app.command()
def triage(
    target: Annotated[str, typer.Argument(help="a run URL, a job id, or a case id")],
    system: str = "b2",
) -> None:
    """Triage one failure and print the validated result as JSON."""
    from triagebench.evals.run import make_system
    from triagebench.tools.impl import Toolbox

    box = Toolbox.default()
    job_id = box.resolve(target)
    sys_ = make_system(system, box)
    out = sys_(box.store.get(job_id))
    typer.echo(json.dumps(out.model_dump(mode="json"), indent=2))


# ---- eval ------------------------------------------------------------------------------------
@app.command("eval")
def eval_cmd(
    system: Annotated[str, typer.Option(help="comma list: b0,b1,b2,b3")] = "b0",
    split_name: Annotated[str, typer.Option("--split")] = "dev",
    limit: int = 0,
    concurrency: int = 4,
    tag: str = "",
    replay_only: bool = False,
    tools: Annotated[
        str, typer.Option(help="direct | mcp (agents call tools over MCP)")
    ] = "direct",
) -> None:
    """Run systems on a split and save results/<run_id>.json."""
    from triagebench.evals.run import run_eval

    for name in system.split(","):
        path = run_eval(
            name.strip(),
            split_name,
            limit=limit,
            concurrency=concurrency,
            tag=tag,
            replay_only=replay_only,
            tools=tools,
            echo=typer.echo,
        )
        typer.echo(f"saved {path.relative_to(REPO_ROOT)}")


@app.command()
def report(
    runs: Annotated[list[Path], typer.Argument(help="result JSON files to tabulate")],
    compare: bool = True,
) -> None:
    """Markdown tables (with 95% CIs and McNemar tests) from saved result files."""
    from triagebench.evals.report import render_report

    typer.echo(render_report(runs, compare=compare))


# ---- index / mcp -----------------------------------------------------------------------------
@app.command()
def index(fake: bool = False) -> None:
    """Embed the kb split into pgvector (chunks table with HNSW + GIN indexes)."""
    from triagebench.retrieval.index import build_index

    n = build_index(fake=fake, echo=typer.echo)
    typer.echo(f"indexed {n} chunks")


@app.command()
def mcp(http: bool = False, port: int = 3001) -> None:
    """Run the MCP server (stdio by default; --http for Streamable HTTP)."""
    from triagebench.mcp_server import server

    if http:
        server.run(transport="streamable-http", port=port)
    else:
        server.run()


if __name__ == "__main__":
    app()
