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
    variant: str | None = None  # the mechanism's form when it matters, such as a hook's handler
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    reuse: dict | None = None
    reuse_conflicts: list[dict] = field(default_factory=list)
    pending_question: str | None = None
    unasked_facts: list[str] = field(default_factory=list)
    assessment: list[dict] = field(default_factory=list)
    builder_available: bool = False
    no_standalone_mechanism: bool = False  # every matching mechanism is ruled out; combinations are not assessed


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
    source_role: str = "primary"

    def to_dict(self) -> dict:
        value = dict(self.__dict__)
        value["pass"] = value.pop("pass_name")
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "Finding":
        fields = dict(value)
        fields["pass_name"] = fields.pop("pass")
        return cls(**fields)


@dataclass(frozen=True)
class AcceptedRevision:
    rule_ids: list[str]
    rules: list[dict]
    brief_digest: str
    digest: str


@dataclass(frozen=True)
class Contract:
    """The full current contract shown for confirmation; ``digest`` binds it."""

    parts: list[dict]
    overrides: list[dict]
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
    """Everything one verification run decided before any worker ran."""

    run_id: str
    decision_digest: str
    contract_digest: str
    applicable_rules: dict[str, list[str]]
    frozen_inputs: dict
    static_results: list[CheckResult] = field(default_factory=list)
    judged_requests: list[dict] = field(default_factory=list)
    runtime_results: list["RuntimeResult"] = field(default_factory=list)
    confirmed_parts: list[str] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)
    not_applicable: dict[str, dict[str, str]] = field(default_factory=dict)
    part_files: dict[str, list[str]] = field(default_factory=dict)
    part_dirs: dict[str, str] = field(default_factory=dict)
    part_links: dict[str, list[str]] = field(default_factory=dict)
    requirement_parts: list[str] = field(default_factory=list)
    rule_index: dict[str, dict] = field(default_factory=dict)
    worker_evidence: list[dict] = field(default_factory=list)
    approved_workers: tuple[str, ...] = ()  # digests of approved configurations; immutable
    worker_violations: list[str] = field(default_factory=list)
    kb_version: str = ""
    scope: str = "Extensive review (rarely needed)"
    tool_versions: dict[str, str] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    whole_idea: dict = field(default_factory=dict)
    unconfirmed_reason: str = ""
    overrides: list[dict] = field(default_factory=list)  # each adopted override's rule id and digest
    scope_parts: list[str] | None = None  # the parts judged in a Review run; None judges every part


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
