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
from harness_forge.documents import validate
from harness_forge.fetch import FetchError, fetch_text
from harness_forge.models import AcceptedRevision, Confirmation, Contract, Decision, ReviewResponse, Rule
from harness_forge.research import (
    _normalized, finding_problem, make_brief, match_support, research_request, source_rejection, support_request,
    worker_finding,
)
from harness_forge.runtime import run_skill_runtime
from harness_forge.verify import bound_inputs, finish_run, part_kind, prepare_run, review_request, validate_review

def _next_ids(existing: list[str], count: int) -> list[str]:
    numbers = [int(item[1:]) for item in existing if re.fullmatch(r"F[0-9]+", item)]
    start = max(numbers, default=0) + 1
    return [f"F{start + offset}" for offset in range(count)]


def research_pass(
    decision: Decision, pass_name: str, part_id: str | None, harness: str, *, contract: Contract,
    confirmation: Confirmation | None, worker_evidence: list[dict] | tuple, run_id: str,
    runner: Runner = run_command, environ: Mapping[str, str] | None = None,
    fetch: Callable[[str], str] = fetch_text, login_answer: str | None = None, existing_ids: list[str] | tuple = (),
) -> dict:
    """One pass: the research worker, Forge's own fetch and quote check, then a support worker per quoted finding.

    Forge numbers the findings after ``existing_ids``, so both passes share
    one record, and adds the pass, part and brief digest it sent.
    A support worker runs only for findings whose quote the page contains;
    sources the policy rejects are never fetched.
    """
    environ = os.environ if environ is None else environ
    brief = make_brief(decision, pass_name, part_id)
    approval = {"contract": contract, "confirmation": confirmation, "worker_evidence": worker_evidence,
                "runner": runner, "environ": environ, "login_answer": login_answer}
    request = research_request(brief, run_id, f"{pass_name}-{(part_id or 'idea').lower()}")
    response = run_worker(request, harness, **approval)
    result = {"status": response["status"], "reason": response.get("reason"),
              "brief": {"pass": pass_name, "part_id": part_id, "digest": brief.digest},
              "kept": [], "dropped": [], "support": [], "worker_evidence": [response["evidence"]]}
    if response["status"] != "completed":
        return result
    raw = response["output"]["findings"]
    for item, new_id in zip(raw, _next_ids(list(existing_ids), len(raw))):
        finding = worker_finding(item, brief, new_id)
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
        result["support"].append({"finding_id": new_id, "quote_check": "pass" if quoted else "fail",
                                  "verdict": verdict["verdict"] if verdict else "missing",
                                  "worker_ref": f"support-{new_id.lower()}"})
        problem = finding_problem(finding, page, verdict)
        if problem:
            result["dropped"].append({"id": new_id, "reason": problem})
        else:
            result["kept"].append(finding.to_dict())
    return result


def verify_live(
    decision: Decision, files: dict[str, bytes], symlinks: dict[str, str], rules: list[Rule],
    revision: AcceptedRevision | None, harness: str, *, contract: Contract, confirmation: Confirmation | None,
    worker_evidence: list[dict] | tuple, cases: list[dict], runner: Runner = run_command,
    environ: Mapping[str, str] | None = None, kb_version: str = "", login_answer: str | None = None,
    collect: Callable[[], tuple[dict[str, bytes], dict[str, str], Decision, AcceptedRevision | None]] | None = None,
    confirmed_exclusions: set[tuple[str, str, str]] | frozenset = frozenset(), drift: list[dict] | tuple = (),
    selection: dict[str, list[dict]] | None = None, overrides: list[dict] | tuple = (),
    scope: set[str] | None = None, scope_label: str = "Extensive review (rarely needed)",
    kept: dict[str, dict] | None = None, cited: dict[str, list[str]] | None = None,
    recorded_drift: list[dict] | tuple = (),
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
                      scope_label=scope_label, recorded_drift=recorded_drift, cases=cases)
    review = ReviewResponse(run_id=run.run_id, verdicts=[], goal_verdicts=[])
    observations = []
    if any(request["kind"] == "rule" or request["kind"] == "goal" for request in run.judged_requests):
        request = review_request(run, f"review-{run.run_id}")
        staged = {entry["path"]: files[entry["path"]] for entry in request["inputs"]["files"]}
        response = run_worker(request, harness, contract=contract, confirmation=confirmation,
                              worker_evidence=worker_evidence, runner=runner, environ=environ, files=staged,
                              login_answer=login_answer)
        observations.append(response["evidence"])
        if response["status"] == "completed":
            try:
                review = validate_review(run, response["output"])
            except ValueError as error:
                run.worker_violations.append(f"review output rejected: {error}")
        else:
            # Out of scope voids the verdicts; any other failure leaves none, and the report says why.
            run.worker_violations.append(f"review worker: {response['reason']}")
    runtime = []
    # A Review run tests the parts in its scope; a kept part's earlier result stands for its runtime tests.
    built = {part["id"] for part in run.parts if part_kind(part) in ("build", "local-reuse")
             and part["id"] in run.confirmed_parts and (scope is None or part["id"] in scope)}
    part_cases = [case for case in cases if case["part_id"] in built]
    if part_cases and harness in run.targets:
        runtime = run_skill_runtime(run, harness, part_cases, files=files, runner=runner, environ=environ,
                                    login_answer=login_answer)
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
                             and case["mode"] == "explicit" and case["expected"] == "activates"}
                for part in run.parts if part_kind(part) == "build" and part["id"] in run.confirmed_parts
                and harness in run.targets and (scope is None or part["id"] in scope)}
    results = finish_run(run, review, runtime, current, worker_observations=observations,
                         confirmed_exclusions=frozenset(confirmed_exclusions), required_activation=required,
                         drift=drift, selection=selection, kept=kept, cited=cited)
    return run, results
