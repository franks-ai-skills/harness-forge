"""Contract-bound verification: applicability, checks, verdicts and results.

prepare_run decides applicability for every rule before any review,
runs the applicable script rules and freezes every bound input.
finish_run combines script results, the review worker's verdicts and
runtime results into each part's harness result and goal result, in the
design's exact precedence. A model verdict never reverses a script
failure, and nothing here certifies worker containment.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict

from harness_forge.checks import REGISTRY
from harness_forge.checks.runner import evaluate_applicability, run_check
from harness_forge.contracts import confirmation_problem, part_ids_problem
from harness_forge.documents import canonical_digest, validate
from harness_forge.models import (
    AcceptedRevision, CheckResult, Confirmation, Decision, PartResult, ReviewResponse, Rule, RuntimeResult,
    VerificationRun,
)
from harness_forge.predicates import UnknownFact
from harness_forge.research import blocked_parts, drift_category, drift_state
from harness_forge.selection import conflict_kept, goal_conflict, selection_state
from harness_forge.scope import affected_parts

BUILDABLE = ("skills", "commands")
README = "README.md"
_BLOCKING = ("missing", "error", "unresolved")
_OBSERVATIONS = ("observed_tools", "observed_behavior", "unknowns", "evidence_refs")


# Appended to the reviewer's reason when an error rule's "not applicable" awaits the user.
NEEDS_CONFIRMATION = " (needs the user's confirmation)"

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


# A report reason stays within the schema's bound: the drift result names this many, verify's output all.
_NAMED_DRIFT = 50

# Results Forge itself decides, beside the KB's rules; each is an error and keeps the part from verified.
FORGE_RULES = {
    "selection.current": "The recorded selection is what the part's confirmed answers give under the effective rules",
    "selection.goal-conflict": "No confirmed answer rules out the chosen mechanism",
    "research.drift": "No open security or compatibility drift touches the part's rules",
    "runtime.explicit-activation": "A live build's explicit invocation was observed to activate the skill",
    "inputs.bound": "No bound input changed after the run was prepared",
}


def _forge_rule(run: VerificationRun, rule_id: str, sources: list[str] | tuple = ()) -> None:
    """Index one of Forge's own results so the report shows its severity, statement and sources."""
    entry = run.rule_index.setdefault(rule_id, {"severity": "error", "check": "script", "applicability": "script",
                                                "statement": FORGE_RULES[rule_id], "sources": [], "positions": [],
                                                "digest": canonical_digest(f"{rule_id} v1")})
    entry["sources"] = sorted(set(entry["sources"]) | set(sources))


# Links between parts are checked by Forge itself: a named part exists, and its artifact carries its name.
LINK_RULE = {"id": "links.parts", "info": {
    "severity": "error", "check": "script", "applicability": "script", "sources": [], "positions": [],
    "statement": "Each part this part links to exists, and a linked part's artifact is installed under its name",
    "digest": canonical_digest("links.parts v1")}}


def _link_check(part: dict, record: dict, files: dict[str, bytes]) -> CheckResult | None:
    """The part's links between parts, as one script result; None when it links to nothing."""
    if not part["connects_to"]:
        return None
    parts = {item["id"]: item for item in record["parts"]}
    problems, unchecked = [], []
    for link in part["connects_to"]:
        target = parts.get(link["to"])
        if target is None:
            problems.append(f"part {link['to']} does not exist")
            continue
        kind = part_kind(target)
        if kind == "unavailable":
            unchecked.append(f"part {link['to']}'s {target['chosen']} has no builder in this release, so its name "
                             "cannot be checked")
            continue
        if kind == "remote-reuse":
            unchecked.append(f"part {link['to']} is reused from {target['reuse']['source']} and not installed here")
            continue
        directory = _skill_dir(target)
        if directory is None:
            continue  # an outside control or nothing-fits builds nothing that could be named
        skill = files.get(f"{directory}/SKILL.md")
        if skill is None:
            problems.append(f"part {link['to']} has no {directory}/SKILL.md")
            continue
        match = re.search(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", skill.decode("utf-8", "replace"), re.M)
        name = match.group(1) if match else None
        if name != directory.rsplit("/", 1)[-1]:
            problems.append(f"part {link['to']}'s skill names itself {name or 'nothing'}, not "
                            f"{directory.rsplit('/', 1)[-1]}")
    status = "fail" if problems else "error" if unchecked else "pass"
    reason = "; ".join(problems + unchecked) or "every linked part exists under its recorded name"
    return CheckResult(part["id"], LINK_RULE["id"], status, "error", reason, [], checker="forge.links", checker_version="1")


def _approval_digests(worker_evidence: list[dict] | tuple) -> tuple[str, ...]:
    return tuple(canonical_digest(configuration) for configuration in worker_configurations(worker_evidence))


def worker_configurations(worker_evidence: list[dict] | tuple) -> list[dict]:
    """Each worker's configuration without what it observed: the part a run is bound to."""
    configurations = {canonical_digest(c): c for c in
                      ({k: v for k, v in evidence.items() if k not in _OBSERVATIONS} for evidence in worker_evidence)}
    return [configurations[key] for key in sorted(configurations)]


def selection_failures(decision: Decision, rules: list[Rule],
                       domains: dict[str, list] | None = None) -> tuple[dict[str, list[dict]], list[dict]]:
    """Each part's selection problems as failing script results, and its goal conflicts to show.

    A recorded recommendation the answers no longer give, or an open
    question, fails as ``selection.current``. A chosen mechanism a confirmed
    answer rules out fails as ``selection.goal-conflict``, also when the user
    kept it: the error stays and the part is never verified.
    """
    failures: dict[str, list[dict]] = {}
    conflicts = []
    facts = decision.record["facts"]
    for part in decision.record["parts"]:
        state = selection_state(part, facts, rules)
        if not state["current"]:
            failures.setdefault(part["id"], []).append({"rule_id": "selection.current", "reason": state["problem"],
                                                        "sources": []})
            continue
        conflict = goal_conflict(part, facts, rules, domains=domains)
        if conflict is None:
            continue
        kept = conflict_kept(part, conflict)
        ids = [item["rule_id"] for item in conflict["failing"]]
        # Everything the goal-conflict question shows, so the agent can ask it from here.
        conflicts.append({**conflict, "kept": kept, "rule_ids": ids})
        if conflict["severity"] == "error":
            answer = "the user kept it" if kept else "the goal-conflict question is unanswered"
            failures.setdefault(part["id"], []).append({
                "rule_id": "selection.goal-conflict",
                "reason": f"{conflict['chosen']} is ruled out by {', '.join(ids)}; {answer}",
                "sources": sorted({source for item in conflict["failing"] for source in item["sources"]})})
    return failures, conflicts


def selection_citations(decision: Decision, rules: list[Rule]) -> dict[str, list[str]]:
    """The selection rules each part's selection rests on: those its recommendation cites and those that
    rule its chosen or recommended mechanism in or out. Drift on any of them affects the part."""
    cited = {}
    for part in decision.record["parts"]:
        recommendation = selection_state(part, decision.record["facts"], rules)["recommendation"]
        ids = set(recommendation.rule_ids)
        for row in recommendation.assessment:
            if row["concept"] in (part["chosen"], recommendation.mechanism):
                ids |= set(row["rules"])
        cited[part["id"]] = sorted(ids)
    return cited


def open_blocking_drift(drift: list[dict], decision: Decision, overrides: list[dict] | tuple,
                        affecting: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Per part, the open drift that blocks it: one policy for build, verify and the drift report.

    A security rule's drift is security drift whatever its record says
    (drift_category); kept or adopted drift is not open; a drift affects a
    part when its rule applies to the part or its selection rests on it.
    """
    blocking: dict[str, list[dict]] = {}
    for entry in drift:
        if drift_category(entry) == "ordinary" or drift_state(entry, decision.record, list(overrides)) != "open":
            continue
        for part_id, rule_ids in affecting.items():
            if entry["rule_id"] in rule_ids:
                blocking.setdefault(part_id, []).append(entry)
    return blocking


def affecting_rules(run: VerificationRun, cited: dict[str, list[str]]) -> dict[str, list[str]]:
    """Per part, the rules drift can affect: those that apply to it and those its selection rests on."""
    return {part["id"]: sorted(set(run.applicable_rules.get(part["id"], [])) | set(cited.get(part["id"], [])))
            for part in run.parts}


def bound_inputs(
    decision: Decision, files: dict[str, bytes], rules: list[Rule], *, symlinks: dict[str, str] | None = None,
    revision: AcceptedRevision | None = None, overrides: list[dict] | tuple = (), kb_version: str = "",
    worker_evidence: list[dict] | tuple = (), cases: list[dict] | tuple = (),
) -> dict:
    """Digests of every input a result is bound to; a part's runtime cases and fixtures are among them."""
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
        "cases": {part["id"]: canonical_digest(sorted((case for case in cases if case["part_id"] == part["id"]),
                                                      key=lambda case: case["id"]))
                  for part in decision.record["parts"]},
    }


def prepare_run(
    decision: Decision, files: dict[str, bytes], rules: list[Rule], confirmation: Confirmation | None, *,
    symlinks: dict[str, str] | None = None, revision: AcceptedRevision | None = None,
    overrides: list[dict] | tuple = (), kb_version: str = "", worker_evidence: list[dict] | tuple = (),
    scope: set[str] | None = None, scope_label: str = "Extensive review (rarely needed)",
    recorded_drift: list[dict] | tuple = (), cases: list[dict] | tuple = (),
) -> VerificationRun:
    """Decide applicability, run script rules and freeze the bound inputs."""
    duplicate = part_ids_problem(decision.record)
    if duplicate:
        raise ValueError(duplicate)
    for evidence in worker_evidence:
        validate(evidence, "worker-evidence")
    symlinks = dict(symlinks or {})
    worker_evidence = copy.deepcopy(list(worker_evidence))  # later changes by the caller must not reach the run
    record = copy.deepcopy(decision.record)
    inputs = bound_inputs(decision, files, rules, symlinks=symlinks, revision=revision, overrides=overrides,
                          kb_version=kb_version, worker_evidence=worker_evidence, cases=cases)
    unconfirmed_reason = confirmation_problem(confirmation, decision, revision, overrides, worker_evidence,
                                              recorded_drift) or ""
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
        approved_workers=_approval_digests(worker_evidence), kb_version=kb_version,
        scope=scope_label, tool_versions={"harness-forge-checkers": inputs["checkers"]},
        overrides=[{"rule_id": override["rule_id"], "digest": canonical_digest(override)} for override in overrides],
        scope_parts=sorted(scope) if scope is not None else None,
    )
    idea = _idea_rules(revision)
    rule_ids = [rule.id for rule in rules] + [rule.id for part_rules in idea.values() for rule in part_rules]
    duplicates = sorted({rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1})
    if duplicates:
        raise ValueError(f"rule ids used more than once: {', '.join(duplicates)}")
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
        in_scope = scope is None or part_id in scope
        link = _link_check(part, record, files)
        if link is not None:
            run.rule_index[LINK_RULE["id"]] = dict(LINK_RULE["info"])
            run.applicable_rules[part_id].append(LINK_RULE["id"])
            run.static_results.append(link)
        facts = {
            **record["facts"], **part["answers"], "targets": record["targets"], "outcome": part["outcome"],
            # The harness versions the approved workers ran; a version range for another harness stays unresolved.
            "harness_versions": {evidence["harness"]: evidence["harness_version"] for evidence in worker_evidence},
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
                                       "positions": rule.positions or [], "digest": canonical_digest(rule.to_dict())}
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
            if in_scope:
                run.judged_requests.append({"kind": "rule", "part_id": part_id, "rule_id": rule.id,
                                            "rule_kind": rule.kind, "statement": rule.statement,
                                            "applicability": rule.applicability, "severity": rule.severity})
        if in_scope and part_kind(part) in ("build", "local-reuse"):
            run.judged_requests.append({"kind": "goal", "part_id": part_id, "goal": part["goal"],
                                        "requirement_ids": [rule.id for rule in idea.get(part_id, [])]})
    return run


def review_request(run: VerificationRun, request_id: str) -> dict:
    """The review worker's request: the frozen files, each rule to judge and each goal.

    Rules carry their kind and applicability, as the worker instructions
    promise; severity stays out, so it cannot change how strictly a rule is
    judged.
    """
    requested = {request["part_id"] for request in run.judged_requests}
    paths = sorted({path for part_id in requested for path in run.part_files.get(part_id, [])})
    inputs = {
        "files": [{"path": path, "sha256": run.frozen_inputs["files"][path]} for path in paths],
        "rules": [{"part_id": r["part_id"], "rule_id": r["rule_id"], "statement": r["statement"],
                   "rule_kind": r["rule_kind"], "applicability": r["applicability"]}
                  for r in run.judged_requests if r["kind"] == "rule"],
        "goals": [{"part_id": r["part_id"], "goal": r["goal"], "requirement_ids": r["requirement_ids"]}
                  for r in run.judged_requests if r["kind"] == "goal"],
        "decision_digest": run.decision_digest,
        # The confirmed facts a rule's judgment can depend on, such as the targets a flag is needed for.
        "context": {"targets": list(run.whole_idea["targets"]), "reach": run.whole_idea["reach"],
                    "facts": dict(run.whole_idea["facts"]),
                    "parts": [{"part_id": part["id"], "goal": part["goal"], "answers": dict(part["answers"]),
                               "outcome": part["outcome"], "mechanism": part["chosen"],
                               "reuse": dict(part["reuse"]) if part.get("reuse") else None}
                              for part in run.parts if part["id"] in requested]},
    }
    request = {"role": "review", "run_id": run.run_id, "request_id": request_id,
               "input_digest": canonical_digest(inputs), "inputs": inputs}
    validate(request, "worker-request")
    return request


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


def _goal_result(kind: str, status: str, harness: str, dependencies: list[str], stale: bool) -> str:
    if status == "blocked" or harness == "not checked" or stale:
        return "not assessed"
    if harness == "not verified" or kind == "nothing-fits":
        return "not met"
    if dependencies or kind == "outside-only":
        return "depends on outside controls" if dependencies else "not met"
    return "met by the verified build"


def exclusion_token(run: VerificationRun, part_id: str, rule_id: str) -> str:
    """What the user's "does not apply" was given for: the whole rule, the idea, the part's record, files and requirements.

    Not the reviewer's wording: a fresh reviewer words its reason anew on every
    run, so a token bound to it would never carry over to the next run.
    A change to any of these inputs makes the confirmation stale.
    """
    inputs = run.frozen_inputs
    return canonical_digest({
        "part_id": part_id, "rule_id": rule_id, "rule": run.rule_index.get(rule_id, {}).get("digest"),
        "idea": inputs["idea"], "part": inputs["parts"].get(part_id), "revision": inputs["revision"],
        "files": {path: inputs["files"][path] for path in run.part_files.get(part_id, [])},
    })[:16]


def finish_run(
    run: VerificationRun, review: ReviewResponse, runtime_results: list[RuntimeResult], current_inputs: dict, *,
    confirmed_exclusions: set[tuple[str, str, str]] | frozenset = frozenset(),
    worker_observations: list[dict] | tuple = (),
    required_activation: dict[str, set[str]] | None = None,
    drift: list[dict] | tuple = (),
    selection: dict[str, list[dict]] | None = None,
    kept: dict[str, dict] | None = None,
    cited: dict[str, list[str]] | None = None,
) -> list[PartResult]:
    """Each part's harness and goal result.

    kept maps a part outside a Review run's scope to its earlier result,
    ``{"run_id": ..., "result": PartResult.to_dict()}``, for a part whose
    bound inputs and contract are unchanged. Its judged results and goal
    verdict carry over, marked as kept; its script and link checks ran now
    and must pass, and only an earlier ``verified`` can stay verified.

    confirmed_exclusions are live user confirmations as (part, rule, token):
    one counts only while exclusion_token still gives its token.

    selection maps a part to its selection failures (selection_failures):
    each fails the part.

    cited maps a part to the selection rules its selection rests on
    (selection_citations); drift on them affects the part like drift on
    its applicable rules.

    drift is the research records' drift. Unresolved security or
    compatibility drift on a rule that applies to a part blocks that part
    until the user decides; the decision flow is not available yet.

    required_activation maps a part to its explicit runtime case ids: a live
    build is verified only when one of them passed. Offline runs leave it
    out and report their runtime tests as none run.

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
    run.worker_evidence += [copy.deepcopy(evidence) for evidence in worker_observations
                            if evidence not in run.worker_evidence]
    if any(digest not in run.approved_workers for digest in _approval_digests(run.worker_evidence)):
        changed = {part["id"] for part in run.parts}
    verdicts = {} if run.worker_violations else {(v["part_id"], v["rule_id"]): v for v in review.verdicts}
    goal_verdicts = {} if run.worker_violations else {v["part_id"]: v for v in review.goal_verdicts}
    statics = {(r.part_id, r.rule_id): r for r in run.static_results}
    affecting = {part_id: list(run.applicable_rules.get(part_id, [])) + list((cited or {}).get(part_id, []))
                 for part_id in {part["id"] for part in run.parts}}
    drifted = blocked_parts(list(drift), affecting)
    outside = set() if run.scope_parts is None else {part["id"] for part in run.parts} - set(run.scope_parts)
    kept_rules, kept_goals, kept_verified = {}, {}, set()
    for part_id, entry in (kept or {}).items():
        if part_id not in outside:
            continue
        def noted(reason: str) -> str:
            """A result kept twice names the run that judged it."""
            return reason if "(kept from run " in reason else f"{reason} (kept from run {entry['run_id']})"

        for item in entry["result"]["rule_results"]:
            if item["check"] == "judged":
                if (part_id, item["rule_id"]) in kept_rules:
                    # Two kept judgments of one rule: neither may silently replace the other.
                    kept_rules[(part_id, item["rule_id"])] = {**item, "result": "error",
                                                              "reason": "the earlier report judges this rule twice"}
                    continue
                kept_rules[(part_id, item["rule_id"])] = {**item, "reason": noted(item["reason"])}
        if entry["result"].get("goal_verdict"):
            goal = entry["result"]["goal_verdict"]
            kept_goals[part_id] = {"part_id": part_id, "verdict": goal["verdict"], "reason": noted(goal["reason"])}
        if entry["result"]["harness_result"] == "verified":
            kept_verified.add(part_id)
    goal_verdicts.update(kept_goals)
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
            if part_id in outside:
                previous = kept_rules.get((part_id, rule_id))
                if previous is None:
                    rule_results.append({"rule_id": rule_id, "check": "judged", "result": "missing",
                                         "reason": "outside this Review run's scope, with no earlier result to keep"})
                    continue
                if previous["result"] != "not-applicable":
                    rule_results.append(previous)
                    continue
                # A kept exclusion meets the same policy as a fresh one, the user's confirmation included.
                verdict = {"verdict": "not-applicable", "reason": previous["reason"],
                           "citation": previous.get("citation", "-")}
            else:
                verdict = verdicts.get((part_id, rule_id))
            if verdict is None:
                entry = {"result": "missing", "reason": "no valid verdict"}
            elif verdict["verdict"] == "not-applicable":
                if info["applicability"] != "judged":
                    entry = {"result": "error", "reason": "not applicable is allowed only for judged applicability"}
                elif (info["severity"] == "error"
                      and (part_id, rule_id, exclusion_token(run, part_id, rule_id)) not in confirmed_exclusions):
                    entry = {"result": "unresolved", "reason": verdict["reason"] + NEEDS_CONFIRMATION}
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
        blocked = part["status"] == "blocked" or part_id in drifted
        if kind in ("build", "local-reuse"):
            failures = [
                r for r in rule_results
                if r["result"] in _BLOCKING or (r["result"] == "fail" and run.rule_index[r["rule_id"]]["severity"] == "error")
            ]
            runtime_failed = any(r.part_id == part_id and r.result in ("fail", "error") for r in runtime_results)
            no_activation = part_id in (required_activation or {}) and not any(
                r.part_id == part_id and r.case_id in required_activation[part_id] and r.result == "pass"
                for r in runtime_results)
            if no_activation:
                _forge_rule(run, "runtime.explicit-activation")
                rule_results.append({"rule_id": "runtime.explicit-activation", "check": "script", "result": "fail",
                                     "reason": "the live build needs an observed explicit activation, and none passed"})
            ok = confirmed and not blocked and not failures and not runtime_failed and not no_activation \
                and not (selection or {}).get(part_id) \
                and (part_id not in outside or part_id in kept_verified) \
                and goal is not None \
                and goal["verdict"] == "met" and part_id not in changed and not run.worker_violations
            harness = "verified" if ok else "not verified"
            status = "verified" if ok else ("built" if confirmed and not blocked else "blocked")
        else:
            harness = "not checked" if kind == "remote-reuse" else "not applicable"
            stale = bool((selection or {}).get(part_id))
            status = ("blocked" if (not confirmed or kind == "unavailable" or part_id in drifted or stale)
                      else part["status"])
        for failure in (selection or {}).get(part_id, []):
            _forge_rule(run, failure["rule_id"], failure.get("sources", []))
            rule_results.append({"rule_id": failure["rule_id"], "check": "script", "result": "fail",
                                 "reason": failure["reason"]})
        # One result for all blocking drift of the part, so a rule id appears once per part in the report.
        blocking = [entry for entry in drift if part_id in drifted and drift_category(entry) != "ordinary"
                    and entry["rule_id"] in affecting.get(part_id, [])]
        if blocking:
            _forge_rule(run, "research.drift", [entry["finding"]["source"] for entry in blocking if entry.get("finding")])
            named = "; ".join(f"unresolved {drift_category(entry)} drift on {entry['rule_id']} "
                              f"(finding {entry['finding_id']})" for entry in blocking[:_NAMED_DRIFT])
            more = len(blocking) - _NAMED_DRIFT
            rule_results.append({"rule_id": "research.drift", "check": "script", "result": "fail",
                                 "reason": named + (f"; and {more} more, all in verify's drift output" if more > 0
                                                    else "") + " blocks the part until the user decides"})
        if part_id in changed:
            _forge_rule(run, "inputs.bound")
            rule_results.append({"rule_id": "inputs.bound", "check": "script", "result": "fail",
                                 "reason": "a bound input changed after the run was prepared"})
        results.append(PartResult(
            part_id=part_id, status=status, harness_result=harness,
            goal_result=_goal_result(kind, status, harness, dependencies,
                                     stale=part_id in changed and harness == "not applicable"),
            outside_dependencies=dependencies, rule_results=rule_results, goal_verdict=goal,
        ))
    return results
