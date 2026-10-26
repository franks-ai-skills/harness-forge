"""Builders for decision records and revisions used across tests."""

import copy

from harness_forge.documents import canonical_digest
from harness_forge.models import AcceptedRevision, Decision

EVIDENCE = [{
    "worker_policy": "best-effort-v1", "isolation_status": "not-certified", "role": "review",
    "harness": "claude-code", "harness_version": "2.1.292", "model": "claude-opus-5-5",
    "launcher": "independent-process", "configuration_digest": "0" * 64,
    "configured_controls": ["tools: Read, Grep, Glob"], "observed_tools": {"status": "unknown"},
    "observed_behavior": [], "unknowns": ["context isolation"], "evidence_refs": [],
}]


# The whole-idea answers of a public repository on a plan with merge controls.
FACTS = {"target_set": "both", "reach": "repository", "plan_controls": "available", "opens_prs": False,
         "bypass_mode": False, "untrusted_repo": False, "lethal_trifecta": False}

# Every answer selection needs for a model-started procedure skill, as the Codex trial confirmed them.
ANSWERS = {"job": "procedure", "event_without_agent": False, "must_survive_compromise": "not-required",
           "who_disables": "contributor", "every_time": "usually", "irreversible": False, "side_effects": False,
           "own_context": False,
           "trigger": "model", "external_system": "none"}


def part(part_id: str, goal: str, **extra) -> dict:
    value = {
        "id": part_id, "slug": f"part-{part_id.lower()}", "goal": goal, "status": "decided",
        "answers": dict(ANSWERS), "outcome": "harness-only", "recommended": "skills", "chosen": "skills",
        "override_reason": None, "alternatives": [], "connects_to": [],
        "outside_controls": [{"control": "codeowners-agent-config",
                              "prerequisites": ["outside.ci.workflow-protected", "outside.codeowners.agent-config",
                                                "outside.gate.no-broader-bypass", "outside.gate.plan-enforced",
                                                "outside.merge.no-agent-bypass",
                                                "outside.merge.requester-cannot-approve"],
                              "availability": "available", "required_for_goal": False}],
    }
    value.update(extra)
    return value


def decision(*parts: dict, **extra) -> Decision:
    record = {
        "idea": "Turn approved bug reports into reproduction checklists",
        "picture": "I ask for a checklist and get environment, steps, expected and observed behavior.",
        "goals": ["Reproduction checklists from approved bug reports"],
        "targets": ["claude-code", "codex"], "reach": "repository",
        "hosting": {"platform": "github", "plan": "free", "visibility": "public"},
        "kb_version": "0.1.0", "facts": dict(FACTS),
        "parts": list(parts) or [part("A", "Write a reproduction checklist when asked")],
        "retired_ids": [], "research": {"survey_digest": None, "deep_digests": {}},
        "accepted_revision": None,
    }
    record.update(extra)
    return Decision(record=copy.deepcopy(record), body="The user's own description.")


def revision(*requirements: tuple[str, str, str]) -> AcceptedRevision:
    rules = [{"part_id": part_id, "rule": {"id": rule_id, "kind": "must", "statement": statement}}
             for part_id, rule_id, statement in requirements]
    digest = canonical_digest(rules)
    briefs = {part_id: "1" * 64 for part_id, _, _ in requirements}
    return AcceptedRevision(rule_ids=[r["rule"]["id"] for r in rules], rules=rules, brief_digests=briefs, digest=digest)


def with_run_dir(arguments) -> list[str]:
    """prepare-contract's arguments with a run directory of the decision's repository, as run-dir allocates one."""
    arguments = [str(argument) for argument in arguments]
    if arguments[:1] == ["prepare-contract"] and "--run-dir" not in arguments and "--decision" in arguments:
        from pathlib import Path
        run = Path(arguments[arguments.index("--decision") + 1]).resolve().parents[2] / ".harness-forge/runs/test-run"
        run.mkdir(parents=True, exist_ok=True)
        arguments += ["--run-dir", str(run)]
    return arguments
