"""The structured answer every system must return, and the JSON schema we hand the model.

Two label axes (PLAN section 5.9):
- failure_type ("what failed?") - LogDx-CI's 8 categories + ci_infra_or_config
- cause_class  ("why, and whose problem?") - TriageBench's own axis, with an honest "unknown"
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FailureType(StrEnum):
    TEST_ASSERTION = "test_assertion"
    COMPILE_ERROR = "compile_error"
    TYPE_ERROR = "type_error"
    LINT_FAILURE = "lint_failure"
    DEPENDENCY_INSTALL = "dependency_install"
    DOCKER_BUILD = "docker_build"
    TIMEOUT_OR_OOM = "timeout_or_oom"
    MULTI_FAILURE = "multi_failure"
    CI_INFRA_OR_CONFIG = "ci_infra_or_config"


class CauseClass(StrEnum):
    REGRESSION = "regression"
    FLAKY = "flaky"
    ENVIRONMENT = "environment"
    TEST_DEFECT = "test_defect"
    CI_CONFIG = "ci_config"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    job_id: int
    line: int  # 1-based line number in the cleaned log
    text: str  # must appear verbatim on that line (checked by agent/verify.py)


class TriageResult(BaseModel):
    failure_type: FailureType
    cause_class: CauseClass
    root_cause: str = Field(max_length=1200)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    similar_cases: list[str] = Field(default_factory=list)
    next_step: str = Field(max_length=600)
    confidence: float = Field(ge=0, le=1)


def output_json_schema() -> dict[str, Any]:
    """Hand-written schema for structured outputs (output_config.format).

    Written by hand rather than TriageResult.model_json_schema() so it only uses keywords the
    structured-output decoder supports; length and range limits are enforced by pydantic after.
    """
    return {
        "type": "object",
        "properties": {
            "failure_type": {"type": "string", "enum": [x.value for x in FailureType]},
            "cause_class": {"type": "string", "enum": [x.value for x in CauseClass]},
            "root_cause": {
                "type": "string",
                "description": "At most 3 sentences: what failed, where, and why.",
            },
            "evidence": {
                "type": "array",
                "description": "1-5 log lines that prove the diagnosis, quoted exactly.",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "integer"},
                        "line": {"type": "integer"},
                        "text": {"type": "string"},
                    },
                    "required": ["job_id", "line", "text"],
                    "additionalProperties": False,
                },
            },
            "similar_cases": {"type": "array", "items": {"type": "string"}},
            "next_step": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": [
            "failure_type",
            "cause_class",
            "root_cause",
            "evidence",
            "similar_cases",
            "next_step",
            "confidence",
        ],
        "additionalProperties": False,
    }
