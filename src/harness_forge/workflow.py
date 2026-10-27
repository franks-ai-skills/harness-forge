# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""The worker-backed stages the CLI runs: a research pass and a live verification.

Both run only under a contract the user confirmed in this session (the
adapters refuse anything else) and return evidence for the report. Fetched
pages, worker output and candidate files stay data throughout.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Mapping

from harness_forge.adapters.base import Runner, run_command, run_worker
from harness_forge.documents import canonical_digest, canonical_json, validate
from harness_forge.fetch import FetchError, fetch_text
from harness_forge.models import AcceptedRevision, Confirmation, Contract, Decision, ReviewResponse, Rule
from harness_forge.research import (
    _normalized, finding_problem, make_brief, match_support, research_request, source_rejection, support_request,
    worker_finding,
)
from harness_forge.diagnostics import Diagnostics
from harness_forge.behavior import BEHAVIOR
from harness_forge.runtime import run_skill_runtime
from harness_forge.verify import bound_inputs, finish_run, part_kind, prepare_run, review_request, validate_review

def _next_ids(existing: list[str], count: int) -> list[str]:
    numbers = [int(item[1:]) for item in existing if re.fullmatch(r"F[0-9]+", item)]
    start = max(numbers, default=0) + 1
    return [f"F{start + offset}" for offset in range(count)]


def _binding_problem(requirement_id: str | None, source: str, lost: dict[str, list[str]]) -> str | None:
    """Why a re-check finding does not check a lost source of the requirement it names, if it does not."""
    if requirement_id is None:
        return "the re-check finding names no requirement"
    if requirement_id not in lost:
        return f"{requirement_id} is not a requirement to re-check"
    if source not in lost[requirement_id]:
        return f"{source} is not a source {requirement_id} lost"
    return None


def _keep_rejected_review(diagnostics: Diagnostics | None, run_id: str, attempt: int, request_id: str,
                          output: dict, error: str) -> None:
    """The rejected reply goes to the run's diagnostics outside the repository; failing to keep it changes nothing."""
    if diagnostics is None:
        return
    diagnostics.open(run_id)
    diagnostics.keep("review", f"attempt-{attempt}", {"request_id": request_id, "error": error, "output": output})


def _brief_problem(brief, contract: Contract) -> bool:
    """Whether the brief holds anything other than the confirmed contract shows."""
    content = contract.content
    if canonical_digest(content) != contract.digest:
        return True
    shown = {part["id"]: {"id": part["id"], "goal": part["goal"], "answers": part["answers"],
                          "outcome": part["outcome"], "chosen": part["mechanism"]} for part in content["parts"]}
    return (any(brief.content[key] != content[key] for key in ("goals", "picture", "targets", "reach"))
            or any(shown.get(part["id"]) != part for part in brief.content["parts"]))


def research_pass(
    decision: Decision, pass_name: str, part_id: str | None, harness: str, *, contract: Contract,
    confirmation: Confirmation | None, worker_evidence: list[dict] | tuple, run_id: str,
    runner: Runner = run_command, environ: Mapping[str, str] | None = None,
    fetch: Callable[[str], str] = fetch_text, login_answer: str | None = None, existing_ids: list[str] | tuple = (),
    recheck: list[dict] | None = None,
) -> dict:
    """One pass: the research worker, Forge's own fetch and quote check, then a support worker per quoted finding.

    Forge numbers the findings after ``existing_ids``, so both passes share
    one record, and adds the pass, part and brief digest it sent.
    A support worker runs only for findings whose quote the page contains;
    sources the policy rejects are never fetched.
    ``recheck`` limits a deep pass to earlier requirements' lost sources:
    each finding must name one of those requirements and one of its lost
    sources, or it is dropped before any fetch. ``bindings`` maps each
    finding that passed this filter to its requirement.
    """
    environ = os.environ if environ is None else environ
    brief = make_brief(decision, pass_name, part_id)
    # The brief may hold only parts the user confirmed in this session; nothing launches otherwise.
    covered = set(confirmation.part_ids) if confirmation is not None else set()
    needed = {part_id} if part_id is not None else {part["id"] for part in decision.record["parts"]}
    if needed - covered:
        raise ValueError(f"part {', '.join(sorted(needed - covered))} is not confirmed in this session; "
                         "confirm it before researching it")
    if _brief_problem(brief, contract):
        raise ValueError("the decision changed after the confirmation: the goals, picture, targets, reach "
                         "or a part's goal, answers or mechanism; confirm the current research proposal")
    approval = {"contract": contract, "confirmation": confirmation, "worker_evidence": worker_evidence,
                "runner": runner, "environ": environ, "login_answer": login_answer}
    request = research_request(brief, run_id, f"{'recheck' if recheck else pass_name}-{(part_id or 'idea').lower()}",
                               recheck)
    response = run_worker(request, harness, **approval)
    result = {"status": response["status"], "reason": response.get("reason"),
              "brief": {"pass": pass_name, "part_id": part_id, "digest": brief.digest},
              "kept": [], "dropped": [], "support": [], "bindings": {}, "worker_evidence": [response["evidence"]]}
    if response["status"] != "completed":
        return result
    lost = {target["requirement_id"]: target["sources"] for target in recheck or []}
    raw = response["output"]["findings"]
    for item, new_id in zip(raw, _next_ids(list(existing_ids), len(raw))):
        item = dict(item)
        requirement_id = item.pop("requirement_id", None)
        binding_problem = _binding_problem(requirement_id, item["source"], lost) if recheck else (
            "only a re-check binds findings to requirements" if requirement_id is not None else None)
        if binding_problem:
            result["dropped"].append({"id": new_id, "reason": binding_problem})
            continue
        if recheck:
            result["bindings"][new_id] = requirement_id
        finding = worker_finding(item, brief, new_id)
        try:
            canonical_json(finding.to_dict())
        except ValueError:  # a lone surrogate from the worker's JSON can be neither sent, hashed nor written
            result["dropped"].append({"id": new_id, "reason": "the finding holds text that is not valid Unicode"})
            continue
        rejection = source_rejection(finding.source)
        if rejection:
            result["dropped"].append({"id": new_id, "reason": rejection})
            continue
        try:
            page = fetch(finding.source)
        except FetchError:
            page = None
        quoted = page is not None and _normalized(finding.quote) in _normalized(page)
        verdict = None
        if quoted:
            support = run_worker(support_request(finding, run_id, f"support-{new_id.lower()}"), harness, **approval)
            result["worker_evidence"].append(support["evidence"])
            # Exactly the requested finding's verdict: an extra, missing or duplicate verdict voids the answer.
            try:
                verdict = match_support([finding], support["output"])[new_id] if support["status"] == "completed" \
                    else None
            except ValueError:
                verdict = None
        support = {"finding_id": new_id, "quote_check": "pass" if quoted else "fail",
                   "verdict": verdict["verdict"] if verdict else "missing", "worker_ref": f"support-{new_id.lower()}"}
        problem = finding_problem(finding, page, verdict)
        if problem:
            result["dropped"].append({"id": new_id, "reason": problem})
        else:
            # Only a finding that passed every check binds its entry; a dropped one's entry supports nothing.
            support["finding_digest"] = canonical_digest(finding.to_dict())
            result["kept"].append(finding.to_dict())
        result["support"].append(support)
    return result


def behavior_obligations(run, cases: list[dict], harness: str, scope: set[str] | None = None) -> dict[str, set[str]]:
    """The behavior case ids each judged artifact must pass in ``harness``: a built part's and a local reuse's
    alike, since behavior cases run on both (#54)."""
    obligations = {}
    for part in run.parts:
        if part_kind(part) not in ("build", "local-reuse") or part["id"] not in run.confirmed_parts \
                or harness not in run.targets or (scope is not None and part["id"] not in scope):
            continue
        ids = {case["id"] for case in cases if case["part_id"] == part["id"] and case.get("kind") == BEHAVIOR
               and harness in case["harnesses"]}
        if ids:
            obligations[part["id"]] = ids
    return obligations


def verify_live(
    decision: Decision, files: dict[str, bytes], symlinks: dict[str, str], rules: list[Rule],
    revision: AcceptedRevision | None, harness: str, *, contract: Contract, confirmation: Confirmation | None,
    worker_evidence: list[dict] | tuple, cases: list[dict], runner: Runner = run_command,
    environ: Mapping[str, str] | None = None, kb_version: str = "", login_answer: str | None = None,
    collect: Callable[[], tuple[dict[str, bytes], dict[str, str], Decision, AcceptedRevision | None]] | None = None,
    confirmed_exclusions: set[tuple[str, str, str]] | frozenset = frozenset(), drift: list[dict] | tuple = (),
    selection: dict[str, list[dict]] | None = None, overrides: list[dict] | tuple = (),
    scope: set[str] | None = None, scope_label: str = "Every part judged",
    kept: dict[str, dict] | None = None, cited: dict[str, list[str]] | None = None,
    recorded_drift: list[dict] | tuple = (), diagnostics: Diagnostics | None = None, attempt_id: str | None = None,
    runtime_scope: set[str] | None = None,
):
    """Static checks, a fresh review worker, runtime cases, then results in the design's precedence.

    A reviewer seen acting outside its role voids every judged verdict of the
    run. Runtime cases measure competing skills against Claude's built-ins,
    which run_skill_runtime observes with the cases' own settings.
    ``collect`` reads the target's files, links, decision record and
    requirements file again after the workers ran, so a change made
    meanwhile invalidates the affected parts; one that cannot be read
    invalidates every part.
    ``confirmed_exclusions`` are (part, rule, token) triples the user confirmed
    do not apply after seeing the reviewer's reason, each bound to the inputs
    by its exclusion_token; a rule the reviewer now finds applicable keeps
    its verdict.
    """
    environ = os.environ if environ is None else environ
    run = prepare_run(decision, files, rules, confirmation, symlinks=symlinks, revision=revision,
                      overrides=overrides, kb_version=kb_version, worker_evidence=worker_evidence, scope=scope,
                      scope_label=scope_label, recorded_drift=recorded_drift, cases=cases, attempt_id=attempt_id)
    review = ReviewResponse(run_id=run.run_id, verdicts=[], goal_verdicts=[])
    observations = []
    if any(request["kind"] == "rule" or request["kind"] == "goal" for request in run.judged_requests):
        # A completed reply that validation rejects gets one fresh reviewer with the same request; nothing else
        # is retried, and a second failure leaves both in the violations.
        attempts, problems = [], []
        for attempt, request_id in enumerate((f"review-{run.run_id}", f"review-{run.run_id}-retry"), start=1):
            request = review_request(run, request_id)
            staged = {entry["path"]: files[entry["path"]] for entry in request["inputs"]["files"]}
            response = run_worker(request, harness, contract=contract, confirmation=confirmation,
                                  worker_evidence=worker_evidence, runner=runner, environ=environ, files=staged,
                                  login_answer=login_answer)
            observations.append(response["evidence"])
            retry = " on retry" if attempt > 1 else ""
            if response["status"] != "completed":
                # Out of scope voids the verdicts; any other failure leaves none, and the report says why.
                attempts.append({"attempt": attempt, "outcome": "failed",
                                 "error": response["reason"] or "the worker did not complete",
                                 "evidence": response["evidence"]})
                problems.append(f"review worker{retry}: {response['reason']}")
                break
            try:
                review = validate_review(run, response["output"])
            except ValueError as error:
                attempts.append({"attempt": attempt, "outcome": "rejected", "error": str(error),
                                 "evidence": response["evidence"]})
                problems.append(f"review output rejected{retry}: {error}")
                _keep_rejected_review(diagnostics, run.run_id, attempt, request_id, response["output"], str(error))
                continue
            attempts.append({"attempt": attempt, "outcome": "accepted", "evidence": response["evidence"]})
            problems = []
            break
        run.worker_violations += problems
        if attempts[0]["outcome"] == "rejected":
            run.review_attempts = attempts
    runtime = []
    # A Review run tests the parts in its runtime scope: the judged ones and those keeping their file judgments
    # whose cases changed (#107); a kept part's earlier result stands for the other runtime tests.
    tested = scope if runtime_scope is None else runtime_scope
    built = {part["id"] for part in run.parts if part_kind(part) in ("build", "local-reuse")
             and part["id"] in run.confirmed_parts and (tested is None or part["id"] in tested)}
    part_cases = [case for case in cases if case["part_id"] in built]
    if part_cases and harness in run.targets:
        runtime = run_skill_runtime(run, harness, part_cases, files=files, runner=runner, environ=environ,
                                    login_answer=login_answer, diagnostics=diagnostics)
    try:
        current_files, current_links, current_decision, current_revision = (
            collect() if collect else (files, symlinks, decision, revision))
        current = bound_inputs(current_decision, current_files, rules, symlinks=current_links,
                               revision=current_revision, overrides=overrides, kb_version=kb_version,
                               worker_evidence=worker_evidence, cases=cases)
    except (OSError, ValueError):
        current = {**run.frozen_inputs, "idea": "unreadable"}
    for evidence in observations:
        validate(evidence, "worker-evidence")
    # The spec's live build requires an observed explicit activation in each target harness.
    required = {part["id"]: {case["id"] for case in cases if case["part_id"] == part["id"]
                             and case.get("kind") != BEHAVIOR
                             and case["mode"] == "explicit" and case["expected"] == "activates"}
                for part in run.parts if part_kind(part) == "build" and part["id"] in run.confirmed_parts
                and harness in run.targets and (tested is None or part["id"] in tested)}
    behavior = behavior_obligations(run, cases, harness, tested)
    results = finish_run(run, review, runtime, current, worker_observations=observations,
                         confirmed_exclusions=frozenset(confirmed_exclusions), required_activation=required,
                         required_behavior=behavior,
                         drift=drift, selection=selection, kept=kept, cited=cited)
    return run, results
