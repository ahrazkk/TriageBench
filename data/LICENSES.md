# Data sources and licences

## What this repository contains

| File | What it is | Whose |
|---|---|---|
| `data/cases.jsonl` | One row per failed GitHub Actions job: repository, run and job ids, workflow and job names, branch, commit SHA, timestamps, URL, failed step names, log length, redaction counts, and labeller hints (did a re-run pass, the next green commit, file paths it changed). No log text. | Facts about public runs, collected by this project |
| `data/labels.jsonl` | Two labels per case, a root-cause sentence and evidence line numbers. **Drafted by an AI model (Claude) for this project and not yet human-verified** (`ai_drafted: true, verified: false`). | This project |
| `data/label_flags.jsonl` | Notes from AI reviewers on disputed labels, quoting a few log lines. | This project (quotes: see below) |
| `results/*.json` | Every evaluation run: settings, metrics and each system's answer, including short verbatim quotes of log lines used as evidence. | This project (quotes: see below) |

## What it does not contain

Raw job logs, diffs, commit messages and recorded model prompts stay local (`data/raw/` and
`cassettes/` are git-ignored). Logs were redacted (emails and token-shaped strings) before being
stored, even locally.

## Sources

98 failed jobs collected on 2026-11-07 from 18 public repositories through the
GitHub REST API:

| Repository | Repository licence (SPDX id from the GitHub API) | Cases |
|---|---|---|
| [aio-libs/aiohttp](https://github.com/aio-libs/aiohttp) | Apache-2.0 | 6 |
| [apache/airflow](https://github.com/apache/airflow) | Apache-2.0 | 6 |
| [apache/kafka](https://github.com/apache/kafka) | Apache-2.0 | 5 |
| [astral-sh/ruff](https://github.com/astral-sh/ruff) | MIT | 6 |
| [docker/compose](https://github.com/docker/compose) | Apache-2.0 | 6 |
| [eslint/eslint](https://github.com/eslint/eslint) | MIT | 3 |
| [gin-gonic/gin](https://github.com/gin-gonic/gin) | MIT | 5 |
| [golangci/golangci-lint](https://github.com/golangci/golangci-lint) | GPL-3.0 | 5 |
| [home-assistant/core](https://github.com/home-assistant/core) | Apache-2.0 | 6 |
| [pandas-dev/pandas](https://github.com/pandas-dev/pandas) | BSD-3-Clause | 6 |
| [prettier/prettier](https://github.com/prettier/prettier) | MIT | 6 |
| [prometheus/prometheus](https://github.com/prometheus/prometheus) | Apache-2.0 | 6 |
| [pydantic/pydantic](https://github.com/pydantic/pydantic) | MIT | 6 |
| [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | MIT | 2 |
| [python/mypy](https://github.com/python/mypy) | custom, MIT-style (see the repository's LICENSE) | 6 |
| [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | BSD-3-Clause | 6 |
| [tokio-rs/tokio](https://github.com/tokio-rs/tokio) | MIT | 6 |
| [vitejs/vite](https://github.com/vitejs/vite) | MIT | 6 |

CI logs are output of those projects' CI runs. The short log lines quoted in `results/` and
`data/label_flags.jsonl` are excerpts used as evidence for evaluation research and remain subject
to their projects' terms.

## This project's own material

Code, labels, flags and results written by this project are covered by the repository's MIT
licence ([LICENSE](../LICENSE)). Credit for benchmark ideas: LogDx-CI (failure taxonomy and
log-reduction findings) and LogChunks. No data from either is included.
