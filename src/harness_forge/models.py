"""Shared types. JSON Schema in ``schemas/`` defines the wire formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness_forge.documents import validate

_RULE_REQUIRED = (
    "id", "concept", "kind", "severity", "check", "statement",
    "applies_to", "evidence", "sources", "contested",
)
_RULE_OPTIONAL = ("checker", "params", "severity_rationale", "positions")


@dataclass(frozen=True)
class Rule:
    id: str
    concept: str
    kind: str
    severity: str
    check: str
    statement: str
    applies_to: dict
    evidence: str
    sources: list[str]
    contested: bool
    checker: str | None = None
    params: dict | None = None
    severity_rationale: str | None = None
    positions: list[dict] | None = None
    declared_applicability: str | None = None

    @property
    def applicability(self) -> str:
        """``script`` unless the rule says applicability needs judgment."""
        return self.declared_applicability or "script"

    @classmethod
    def from_dict(cls, value: dict) -> "Rule":
        validate(value, "rule")
        return cls(
            **{name: value[name] for name in _RULE_REQUIRED},
            **{name: value[name] for name in _RULE_OPTIONAL if name in value},
            declared_applicability=value.get("applicability"),
        )

    def to_dict(self) -> dict:
        result = {name: getattr(self, name) for name in _RULE_REQUIRED}
        result.update({name: getattr(self, name) for name in _RULE_OPTIONAL if getattr(self, name) is not None})
        if self.declared_applicability is not None:
            result["applicability"] = self.declared_applicability
        return result


@dataclass(frozen=True)
class WorkerEvidence:
    """Worker launch evidence; ``isolation_status`` is always ``not-certified``."""

    role: str
    harness: str
    harness_version: str
    model: str
    launcher: str
    configuration_digest: str
    configured_controls: list[str]
    observed_tools: dict
    observed_behavior: list[str]
    unknowns: list[str]
    evidence_refs: list[str]
    worker_policy: str = "best-effort-v1"
    isolation_status: str = "not-certified"

    @classmethod
    def from_dict(cls, value: dict) -> "WorkerEvidence":
        validate(value, "worker-evidence")
        return cls(**value)

    def to_dict(self) -> dict:
        value = dict(self.__dict__)
        validate(value, "worker-evidence")
        return value


@dataclass(frozen=True)
class Recommendation:
    """A part's computed recommendation, or the fact that still decides it."""

    outcome: str | None
    mechanism: str | None
    rule_ids: list[str]
    pages: list[str]
    alternatives: list[dict] = field(default_factory=list)
    controls: list[dict] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    reuse: dict | None = None
    pending_question: str | None = None


@dataclass(frozen=True)
class Decision:
    """Decision record frontmatter; ``body`` holds the user's own words."""

    record: dict
    body: str = ""

    def part(self, part_id: str) -> dict:
        for part in self.record["parts"]:
            if part["id"] == part_id:
                return part
        raise KeyError(part_id)


@dataclass(frozen=True)
class ResearchBrief:
    pass_name: str
    part_id: str | None
    content: dict
    digest: str


@dataclass(frozen=True)
class Finding:
    id: str
    pass_name: str
    part_id: str | None
    claim: str
    source: str
    fetched: str
    quote: str
    context: str
    brief_digest: str


@dataclass(frozen=True)
class AcceptedRevision:
    rule_ids: list[str]
    rules: list[dict]
    brief_digest: str
    digest: str


@dataclass(frozen=True)
class Contract:
    parts: list[dict]
    worker_policy: dict
    text: str
    digest: str


@dataclass(frozen=True)
class Confirmation:
    run_id: str
    contract_digest: str
    part_ids: list[str]


@dataclass(frozen=True)
class CheckResult:
    """A script rule's result: ``pass | fail | not-applicable | error``."""

    part_id: str | None
    rule_id: str
    status: str
    severity: str
    reason: str
    sources: list[str]
    checker: str | None = None
    checker_version: str | None = None


@dataclass(frozen=True)
class ReviewResponse:
    run_id: str
    verdicts: list[dict]
    goal_verdicts: list[dict]


@dataclass
class VerificationRun:
    run_id: str
    decision_digest: str
    contract_digest: str
    applicable_rules: dict[str, list[str]]
    frozen_inputs: dict[str, str]
    static_results: list[CheckResult] = field(default_factory=list)
    judged_requests: list[dict] = field(default_factory=list)
    runtime_results: list["RuntimeResult"] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeResult:
    run_id: str
    part_id: str
    case_id: str
    prompt: str
    expected: str
    observed_activation: str | None
    result: str
    reason: str
    file_hashes: dict[str, str]
    harness: str
    harness_version: str
    environment: str


@dataclass(frozen=True)
class PartResult:
    part_id: str
    status: str
    harness_result: str
    goal_result: str
    outside_dependencies: list[str]
    rule_results: list[dict]
    goal_verdict: dict | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)
