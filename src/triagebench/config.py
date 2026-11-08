"""Every knob in one place: models, dated prices, budgets and paths.

Values come from environment variables prefixed TRIAGEBENCH_ (or a .env file), so an eval run can
record exactly which settings produced its numbers.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# USD per 1M tokens (input, output). Checked 2026-11-07 against the Claude API pricing table.
# Haiku 5.5's price applies to prompts up to 100K tokens; our budget keeps every request below that.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PRICES_CHECKED_ON = "2026-11-07"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRIAGEBENCH_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    # --- model ---
    model: str = "claude-haiku-5-5"
    effort: str = "medium"  # Haiku 5.5's default; set explicitly so it's recorded with results
    max_output_tokens: int = 4000

    # --- agent budgets (same for every agent system, see PLAN section 10.2) ---
    max_steps: int = 8
    max_input_tokens: int = 60_000
    excerpt_tokens: int = 4_000

    # --- paths ---
    data_dir: Path = REPO_ROOT / "data" / "raw"  # raw logs + snapshots; git-ignored
    cases_file: Path = REPO_ROOT / "data" / "cases.jsonl"
    labels_file: Path = REPO_ROOT / "data" / "labels.jsonl"
    cassette_dir: Path = REPO_ROOT / "cassettes"
    results_dir: Path = REPO_ROOT / "results"
    prompts_dir: Path = REPO_ROOT / "prompts"

    # --- services ---
    database_url: str = "postgresql://triage:triage@localhost:5432/triagebench"
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embed_dim: int = 1024


class Secrets(BaseSettings):
    """Credentials, read from the environment or .env. Never logged or saved with results."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")
    github_token: str = ""
    anthropic_api_key: str = ""


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def secrets() -> Secrets:
    return Secrets()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES_USD_PER_MTOK.get(model, (0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout) / 1_000_000
