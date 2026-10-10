"""Contract-bound verification: applicability, checks, verdicts and results.

prepare_run decides applicability for every rule before any review,
runs the applicable script rules and freezes every bound input.
finish_run combines script results, the review worker's verdicts and
runtime results into each part's harness result and goal result, in the
design's exact precedence. A model verdict never reverses a script
failure, and nothing here certifies worker containment.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from harness_forge.checks import REGISTRY
from harness_forge.checks.runner import evaluate_applicability, run_check
from harness_forge.contracts import confirmation_problem
from harness_forge.documents import canonical_digest, validate
from harness_forge.models import (
    AcceptedRevision, CheckResult, Confirmation, Decision, PartResult, ReviewResponse, Rule, RuntimeResult,
    VerificationRun,
)
from harness_forge.predicates import UnknownFact
from harness_forge.scope import affected_parts

BUILDABLE = ("skills", "commands")
README = "README.md"
_BLOCKING = ("missing", "error", "unresolved")
_OBSERVATIONS = ("observed_tools", "observed_behavior", "unknowns", "evidence_refs")


def part_kind(part: dict) -> str:
    """build, local-reuse, remote-reuse, outside-only, nothing-fits or unavailable."""
    if part["outcome"] == "reuse":
        return "local-reuse" if (part.get("reuse") or {}).get("path") else "remote-reuse"
    if part["outcome"] == "outside-instead":
        return "outside-only"
    if part["outcome"] == "nothing-fits":
        return "nothing-fits"
    return "build" if part["chosen"] in BUILDABLE else "unavailable"


def _skill_dir(part: dict) -> str | None:
    kind = part_kind(part)
    if kind == "build":
        return f".agents/skills/{part['slug']}"
    if kind == "local-reuse":
        return part["reuse"]["path"].rstrip("/")
    return None


def _idea_rules(revision: AcceptedRevision | None) -> dict[str, list[Rule]]:
    rules: dict[str, list[Rule]] = {}
    for entry in revision.rules if revision else []:
        rules.setdefault(entry["part_id"], []).append(Rule.from_dict(entry["rule"]))
    return rules


def _rules_for(part: dict, rules: list[Rule], idea: dict[str, list[Rule]]) -> list[Rule]:
    kind = part_kind(part)
    selected = []
    if kind in ("build", "local-reuse"):
        selected += [rule for rule in rules if rule.concept in ("skills", "security")]
        selected += idea.get(part["id"], [])
    if part["outside_controls"]:
        selected += [rule for rule in rules if rule.concept == "outside"]
    return selected


def worker_configurations(worker_evidence: list[dict] | tuple) -> list[dict]:
    """Each worker's configuration without what it observed: the part a run is bound to."""
    configurations = {canonical_digest(c): c for c in
                      ({k: v for k, v in evidence.items() if k not in _OBSERVATIONS} for evidence in worker_evidence)}
    return [configurations[key] for key in sorted(configurations)]


def bound_inputs(
    decision: Decision, files: dict[str, bytes], rules: list[Rule], *, symlinks: dict[str, str] | None = None,
    revision: AcceptedRevision | None = None, overrides: list[dict] | tuple = (), kb_version: str = "",
    worker_evidence: list[dict] | tuple = (),
) -> dict:
    """Digests of every input a result is bound to."""
    return {
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())},
        "symlinks": dict(sorted((symlinks or {}).items())),
        "kb_version": kb_version,
        "rules": canonical_digest(sorted((rule.to_dict() for rule in rules), key=lambda r: r["id"])),
        "overrides": canonical_digest(list(overrides)),
        "revision": revision.digest if revision else None,
        "idea": canonical_digest({key: value for key, value in decision.record.items()
                                  if key not in ("parts", "confirmations_history")}),
        "parts": {part["id"]: canonical_digest(part) for part in decision.record["parts"]},
        "checkers": canonical_digest({checker_id: version for checker_id, (version, _) in sorted(REGISTRY.items())}),
        "worker": canonical_digest(worker_configurations(worker_evidence)),
    }


def prepare_run(
    decision: Decision, files: dict[str, bytes], rules: list[Rule], confirmation: Confirmation | None, *,
    symlinks: dict[str, str] | None = None, revision: AcceptedRevision | None = None,
    overrides: list[dict] | tuple = (), kb_version: str = "", worker_evidence: list[dict] | tuple = (),
    scope: set[str] | None = None, scope_label: str = "Extensive review (rarely needed)",
) -> VerificationRun:
    """Decide applicability, run script rules and freeze the bound inputs."""
    if overrides:
        raise ValueError("rule overrides block verification until their adoption flow exists")
    for evidence in worker_evidence:
        validate(evidence, "worker-evidence")
    symlinks = dict(symlinks or {})
    record = decision.record
    inputs = bound_inputs(decision, files, rules, symlinks=symlinks, revision=revision, overrides=overrides,
                          kb_version=kb_version, worker_evidence=worker_evidence)
    unconfirmed_reason = confirmation_problem(confirmation, decision, revision, overrides, worker_evidence) or ""
    confirmed = [] if unconfirmed_reason else sorted(confirmation.part_ids)
    run_id = confirmation.run_id if confirmation else "unattended-" + canonical_digest(inputs)[:12]
    run = VerificationRun(
        run_id=run_id, decision_digest=canonical_digest(record),
        contract_digest=confirmation.contract_digest if confirmed else "",
        applicable_rules={}, frozen_inputs=inputs, confirmed_parts=confirmed, unconfirmed_reason=unconfirmed_reason,
        targets=list(record["targets"]),
        whole_idea={"targets": list(record["targets"]), "reach": record["reach"], "hosting": dict(record["hosting"]),
                    "facts": dict(record["facts"])},
        parts=[dict(part) for part in record["parts"]], worker_evidence=list(worker_evidence),
        approved_workers=worker_configurations(worker_evidence), kb_version=kb_version,
        scope=scope_label, tool_versions={"harness-forge-checkers": inputs["checkers"]},
    )
    idea = _idea_rules(revision)
    run.requirement_parts = sorted(idea)
    for part in record["parts"]:
        part_id = part["id"]
        run.not_applicable[part_id] = {}
        run.applicable_rules[part_id] = []
        directory = _skill_dir(part)
        owned = sorted(path for path in files if directory and path.startswith(directory + "/"))
        if part["outside_controls"] and README in files:
            owned.append(README)
        run.part_files[part_id] = owned
        if directory:
            run.part_dirs[part_id] = directory
            name = directory.rsplit("/", 1)[-1]
            run.part_links[part_id] = [link for link in symlinks if link == f".claude/skills/{name}"]
        if scope is not None and part_id not in scope:
            continue
        facts = {
            **record["facts"], **part["answers"], "targets": record["targets"], "outcome": part["outcome"],
            "part_id": part_id, "symlinks": {k: v for k, v in symlinks.items() if k in run.part_links.get(part_id, [])},
            "outside_controls": [{"part_id": part_id, "control": c["control"]} for c in part["outside_controls"]],
            "readme_path": README,
        }
        part_files = {path: files[path] for path in owned}
        if part_kind(part) == "remote-reuse":
            continue
        for rule in _rules_for(part, rules, idea):
            run.rule_index[rule.id] = {"severity": rule.severity, "check": rule.check, "statement": rule.statement,
                                       "applicability": rule.applicability, "sources": list(rule.sources),
                                       "positions": rule.positions or []}
            if rule.check == "script":
                result = run_check(rule, part_files, facts)
                if result.status == "not-applicable":
                    run.not_applicable[part_id][rule.id] = result.reason
                else:
                    run.applicable_rules[part_id].append(rule.id)
                    run.static_results.append(result)
                continue
            try:
                applies, reason = evaluate_applicability(rule, facts)
            except UnknownFact as error:
                run.applicable_rules[part_id].append(rule.id)
                run.static_results.append(CheckResult(part_id, rule.id, "error", rule.severity,
                                                      f"applicability needs the unconfirmed fact {error.args[0]!r}", list(rule.sources)))
                continue
            if not applies:
                run.not_applicable[part_id][rule.id] = reason
                continue
            run.applicable_rules[part_id].append(rule.id)
            run.judged_requests.append({"kind": "rule", "part_id": part_id, "rule_id": rule.id, "rule_kind": rule.kind,
                                        "statement": rule.statement, "applicability": rule.applicability,
                                        "severity": rule.severity})
        if part_kind(part) in ("build", "local-reuse"):
            run.judged_requests.append({"kind": "goal", "part_id": part_id, "goal": part["goal"],
                                        "requirement_ids": [rule.id for rule in idea.get(part_id, [])]})
    return run


def validate_review(run: VerificationRun, response: dict) -> ReviewResponse:
    """Check the review worker's response: one verdict per request, nothing else."""
    validate(response, "review")
    if response["run_id"] != run.run_id:
        raise ValueError("the review answers another run")
    expected = {(r["part_id"], r["rule_id"]) for r in run.judged_requests if r["kind"] == "rule"}
    goals = {r["part_id"] for r in run.judged_requests if r["kind"] == "goal"}
    seen = set()
    for verdict in response["verdicts"]:
        key = (verdict["part_id"], verdict["rule_id"])
        if key in seen:
            raise ValueError(f"duplicate verdict for {key}")
        if key not in expected:
            raise ValueError(f"verdict for a rule that was not requested: {key}")
        seen.add(key)
    seen_goals = set()
    for verdict in response["goal_verdicts"]:
        if verdict["part_id"] in seen_goals or verdict["part_id"] not in goals:
            raise ValueError(f"unexpected or duplicate goal verdict for part {verdict['part_id']}")
        seen_goals.add(verdict["part_id"])
    return ReviewResponse(run_id=response["run_id"], verdicts=response["verdicts"], goal_verdicts=response["goal_verdicts"])


def _check_runtime(run: VerificationRun, results: list[RuntimeResult]) -> None:
    """A runtime result counts only for this run, a target harness and exactly the part's frozen files."""
    for result in results:
        validate(asdict(result), "runtime-result")
        if result.run_id != run.run_id:
            raise ValueError(f"runtime result {result.case_id} belongs to another run")
        if result.part_id not in run.part_dirs:
            raise ValueError(f"runtime result {result.case_id} names a part without a built artifact")
        if result.harness not in run.targets:
            raise ValueError(f"runtime result {result.case_id} ran in {result.harness}, which is not a target")
        directory = run.part_dirs[result.part_id] + "/"
        expected = {path: digest for path, digest in run.frozen_inputs["files"].items() if path.startswith(directory)}
        if not expected or result.file_hashes != expected:
            raise ValueError(f"runtime result {result.case_id} does not name exactly the part's frozen files")


def _goal_result(kind: str, status: str, harness: str, dependencies: list[str]) -> str:
    if status == "blocked" or harness == "not checked":
        return "not assessed"
    if harness == "not verified" or kind == "nothing-fits":
        return "not met"
    if dependencies or kind == "outside-only":
        return "depends on outside controls" if dependencies else "not met"
    return "met by the verified build"


def finish_run(
    run: VerificationRun, review: ReviewResponse, runtime_results: list[RuntimeResult], current_inputs: dict, *,
    confirmed_exclusions: set[tuple[str, str]] | frozenset = frozenset(),
    worker_observations: list[dict] | tuple = (),
) -> list[PartResult]:
    """Each part's harness and goal result. confirmed_exclusions are live user confirmations.

    worker_observations are evidence records from workers that ran after the
    run was prepared. They join the report. A configuration that was not
    approved with the run (a changed control, model or launcher) invalidates
    every part, like any other changed bound input, and keeps doing so when
    the run is finalized again: approval is kept apart from report evidence.
    """
    _check_runtime(run, runtime_results)
    for evidence in worker_observations:
        validate(evidence, "worker-evidence")
    run.runtime_results = list(runtime_results)
    changed = affected_parts(run, current_inputs)
    run.worker_evidence += [evidence for evidence in worker_observations if evidence not in run.worker_evidence]
    if any(configuration not in run.approved_workers for configuration in worker_configurations(run.worker_evidence)):
        changed = {part["id"] for part in run.parts}
    verdicts = {} if run.worker_violations else {(v["part_id"], v["rule_id"]): v for v in review.verdicts}
    goal_verdicts = {} if run.worker_violations else {v["part_id"]: v for v in review.goal_verdicts}
    statics = {(r.part_id, r.rule_id): r for r in run.static_results}
    results = []
    for part in run.parts:
        part_id, kind = part["id"], part_kind(part)
        dependencies = [c["control"] for c in part["outside_controls"] if c["required_for_goal"]]
        confirmed = part_id in run.confirmed_parts
        rule_results = []
        for rule_id in run.applicable_rules.get(part_id, []):
            info = run.rule_index[rule_id]
            if info["check"] == "script" or (part_id, rule_id) in statics:
                static = statics[(part_id, rule_id)]
                rule_results.append({"rule_id": rule_id, "check": "script", "result": static.status, "reason": static.reason})
                continue
            verdict = verdicts.get((part_id, rule_id))
            if verdict is None:
                entry = {"result": "missing", "reason": "no valid verdict"}
            elif verdict["verdict"] == "not-applicable":
                if info["applicability"] != "judged":
                    entry = {"result": "error", "reason": "not applicable is allowed only for judged applicability"}
                elif info["severity"] == "error" and (part_id, rule_id) not in confirmed_exclusions:
                    entry = {"result": "unresolved", "reason": verdict["reason"] + " (needs the user's confirmation)"}
                else:
                    entry = {"result": "not-applicable", "reason": verdict["reason"]}
            else:
                entry = {"result": verdict["verdict"], "reason": verdict["reason"]}
            if verdict is not None:
                entry["citation"] = verdict["citation"]
            rule_results.append({"rule_id": rule_id, "check": "judged", **entry})
        for rule_id, reason in sorted(run.not_applicable.get(part_id, {}).items()):
            rule_results.append({"rule_id": rule_id, "check": run.rule_index[rule_id]["check"], "result": "not-applicable", "reason": reason})

        goal_verdict = goal_verdicts.get(part_id)
        goal = {"verdict": goal_verdict["verdict"], "reason": goal_verdict["reason"]} if goal_verdict else None
        blocked = part["status"] == "blocked"
        if kind in ("build", "local-reuse"):
            failures = [
                r for r in rule_results
                if r["result"] in _BLOCKING or (r["result"] == "fail" and run.rule_index[r["rule_id"]]["severity"] == "error")
            ]
            runtime_failed = any(r.part_id == part_id and r.result in ("fail", "error") for r in runtime_results)
            ok = confirmed and not blocked and not failures and not runtime_failed and goal is not None \
                and goal["verdict"] == "met" and part_id not in changed and not run.worker_violations
            harness = "verified" if ok else "not verified"
            status = "verified" if ok else ("built" if confirmed and not blocked else "blocked")
        else:
            harness = "not checked" if kind == "remote-reuse" else "not applicable"
            status = "blocked" if (not confirmed or kind == "unavailable") else part["status"]
        if part_id in changed:
            rule_results.append({"rule_id": "inputs.bound", "check": "script", "result": "fail",
                                 "reason": "a bound input changed after the run was prepared"})
        results.append(PartResult(
            part_id=part_id, status=status, harness_result=harness,
            goal_result=_goal_result(kind, status, harness, dependencies),
            outside_dependencies=dependencies, rule_results=rule_results, goal_verdict=goal,
        ))
    return results
