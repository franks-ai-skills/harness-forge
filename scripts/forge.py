"""Harness Forge command line: bounded JSON on stdout, diagnostics on stderr.

Exit codes: 0 success, 1 failed checks, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
if _SOURCE not in sys.path:
    sys.path.insert(0, _SOURCE)

from dataclasses import asdict  # noqa: E402

import datetime  # noqa: E402
import functools  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

from harness_forge.adapters.base import preflight, run_command  # noqa: E402
from harness_forge.build_skill import BuilderUnavailable, ConflictError, mark_built, plan_skill, write_skill  # noqa: E402
from harness_forge.contracts import (  # noqa: E402
    RELAY_KEYS, confirm_contract, load_decision, render_contract, resolve_goal_conflict, update_outside_section,
    write_decision,
)
from harness_forge.documents import (  # noqa: E402
    canonical_digest, load_document, parse_json, safe_path, split_frontmatter, validate, validate_kb,
)
from harness_forge.issues import find_issue, gh_publisher, gh_search, propose, publish  # noqa: E402
from harness_forge.fetch import fetch_text  # noqa: E402
from harness_forge.models import Confirmation, Decision, Finding, Rule  # noqa: E402
from harness_forge.overrides import (  # noqa: E402
    OverridesPending, apply_overrides, check_override, describe, effective_rules, load_overrides, write_override,
)
from harness_forge.report import render_report, write_report  # noqa: E402
from harness_forge.research import (  # noqa: E402
    _BLOCKING_DRIFT, _catalog, accept_requirements, drift_category, drift_digest, drift_state, finding_problem,
    load_revision, source_rejection,
    make_brief,
    record_drift, write_research, write_revision,
)
from harness_forge.runtime import load_cases  # noqa: E402
from harness_forge.workflow import research_pass, verify_live  # noqa: E402
from harness_forge.scope import affected_parts, earlier_run, preserved_parts, review_scope, settled  # noqa: E402
from harness_forge.verify import (  # noqa: E402
    NEEDS_CONFIRMATION, affecting_rules, bound_inputs, exclusion_token, finish_run, open_blocking_drift, part_kind,
    prepare_run, selection_citations, selection_failures,
)
from harness_forge.models import ReviewResponse  # noqa: E402
from harness_forge.selection import (  # noqa: E402
    CONFLICT_CHOICES, answers_problem, conflict_kept, goal_conflict, next_question, recommend, selection_state,
)

MAX_OUTPUT_BYTES = 64 * 1024
SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
DERIVED_FACTS = ("reuse_confirmed", "outcome")
MAX_LISTED_ERRORS = 100
HARNESSES = ("claude-code", "codex")
ROLES = ("research", "source-support", "review")
# Replaced by tests; the live commands launch real workers and fetch real pages.
RUNNER = run_command
FETCH = functools.partial(fetch_text, reject=source_rejection)  # every redirect hop meets the source policy
ENVIRON = None
# (search, publisher) for KB issues; None uses the GitHub CLI with the user's existing login.
ISSUES = None


class _InvalidInput(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _InvalidInput(message)


def emit(payload: dict) -> None:
    """Print payload as JSON, dropping listed errors until it fits the bound.

    A payload that still exceeds the bound is not printed at all.
    """
    errors = list(payload.get("errors", []))[:MAX_LISTED_ERRORS]
    while True:
        text = json.dumps({**payload, "errors": errors} if "errors" in payload else payload, ensure_ascii=False)
        size = len(text.encode("utf-8"))
        if size <= MAX_OUTPUT_BYTES:
            print(text)
            return
        if not errors:
            raise _InvalidInput(f"output of {size} bytes exceeds the {MAX_OUTPUT_BYTES}-byte bound")
        errors = errors[: len(errors) // 2]


def _validate_kb(arguments: argparse.Namespace) -> int:
    root = Path(arguments.kb)
    if not root.is_dir():
        raise _InvalidInput(f"--kb {root}: not a directory")
    errors = validate_kb(root)
    emit({"command": "validate-kb", "ok": not errors, "error_count": len(errors), "errors": errors})
    return 0 if not errors else 1


def _read_json(path: str) -> dict:
    try:
        source = Path(path)
        if source.stat().st_size > 1024 * 1024:
            raise _InvalidInput(f"{path}: larger than 1 MiB")
        value = parse_json(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    if not isinstance(value, dict):
        raise _InvalidInput(f"{path}: expected a JSON object")
    return value


def _check_declared(values: dict, scope: str, declared: dict) -> None:
    for fact, value in values.items():
        if fact in DERIVED_FACTS:
            raise _InvalidInput(f"{fact} is derived, not an answer")
        if fact not in declared or declared[fact]["scope"] != scope:
            raise _InvalidInput(f"{fact} is not a declared {scope} fact")
        if value not in declared[fact]["values"] or isinstance(value, bool) != any(isinstance(v, bool) for v in declared[fact]["values"]):
            raise _InvalidInput(f"{fact}: {value!r} is not one of {declared[fact]['values']}")


def _selection_document() -> dict:
    return load_document(SNAPSHOT / "rules" / "selection.yaml", "selection")


def _selection_rules() -> list[Rule]:
    return [Rule.from_dict(rule) for rule in _selection_document()["rules"]]


def _domains(selection: dict) -> dict[str, list]:
    return {fact["id"]: list(fact["values"]) for fact in selection["facts"]}


def _select(arguments: argparse.Namespace) -> int:
    """Recommend from confirmed answers; with the user's ``chosen`` mechanism, also its goal conflict."""
    request = _read_json(arguments.input)
    unknown = set(request) - {"answers", "whole_idea", "candidates", "chosen", "goal"}
    if unknown:
        raise _InvalidInput(f"unknown input keys: {sorted(unknown)}")
    selection = _selection_document()
    declared = {fact["id"]: fact for fact in selection["facts"]}
    answers, whole_idea = request.get("answers", {}), request.get("whole_idea", {})
    candidates = request.get("candidates", [])
    if not all(isinstance(value, dict) for value in (answers, whole_idea)) or not isinstance(candidates, list):
        raise _InvalidInput("answers and whole_idea must be objects, candidates a list")
    _check_declared(answers, "part", declared)
    _check_declared(whole_idea, "idea", declared)
    pinned = [Rule.from_dict(rule) for rule in selection["rules"]]
    rules, applied, pending = _selection_under_confirmation(arguments)
    chosen = request.get("chosen")
    if chosen is not None and (not isinstance(chosen, str) or not isinstance(request.get("goal", ""), str)):
        raise _InvalidInput("chosen and goal must be strings")

    def outcome(rules: list[Rule]) -> dict:
        recommendation = recommend(answers, whole_idea, rules, candidates)
        result = {**asdict(recommendation), "question": None, "goal_conflict": None}
        if recommendation.pending_question:
            effective = {**selection, "rules": [rule.to_dict() for rule in rules]}
            result["question"] = next_question({"answers": answers}, whole_idea, effective, candidates)
        elif chosen is not None:
            part = {"id": "part", "goal": request.get("goal", ""), "answers": answers, "chosen": chosen}
            result["goal_conflict"] = goal_conflict(part, whole_idea, rules, candidates, domains=_domains(selection))
        return result

    payload = {"command": "select", **outcome(rules), "overrides": [o["rule_id"] for o in applied],
               "override_preview": None}
    if pending:
        preview = outcome(apply_overrides(pinned, pending))
        payload["override_preview"] = {
            "overrides": [o["rule_id"] for o in pending],
            "note": "a preview only: it applies once the contract that shows these overrides is confirmed",
            **{key: preview[key] for key in ("outcome", "mechanism", "pending_question", "question", "goal_conflict")}}
    emit(payload)
    return 0


def _selection_under_confirmation(arguments: argparse.Namespace) -> tuple[list[Rule], list[dict], list[dict]]:
    """The selection rules, with the repository's overrides only when a relay confirms the contract showing them.

    Returns (rules, applied overrides, overrides left pending). Without a
    confirmation the pinned rules decide; the overrides are only previewed.
    """
    pinned = _selection_rules()
    if not arguments.root:
        return pinned, [], []
    root = Path(arguments.root).resolve()
    overrides = _checked_overrides(root)[0]
    if not overrides:
        return pinned, [], []
    if not arguments.relay:
        return pinned, [], overrides
    if not (arguments.decision and arguments.evidence):
        raise _InvalidInput("a confirmation relay needs --decision and --evidence for the contract it confirms")
    decision, revision = _decision_and_revision(arguments, root)
    approval, confirmed = _approval(arguments, decision, revision)
    return effective_rules(pinned, confirmed, approval["confirmation"]), confirmed, []


def _resolve_conflict(arguments: argparse.Namespace) -> int:
    """Record the user's answer to a part's goal-conflict question in the decision."""
    try:
        decision = load_decision(Path(arguments.decision))
        # The repository's overrides apply as in select: only under a confirmation of the contract showing them.
        arguments.root = str(Path(arguments.decision).resolve().parents[2])
        rules, _, _ = _selection_under_confirmation(arguments)
        result = resolve_goal_conflict(decision, arguments.part, arguments.choice, arguments.adjusted_goal,
                                       arguments.reason, rules=rules, date=datetime.date.today().isoformat())
    except (KeyError, StopIteration) as error:
        raise _InvalidInput(f"unknown part {arguments.part!r}") from error
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    _save_decision(arguments, result["decision"])
    emit({"command": "resolve-conflict", "part_id": arguments.part, "choice": arguments.choice,
          "next_stage": result["next_stage"], "invalidated_parts": result["invalidated_parts"],
          "remaining_error_ids": result["remaining_error_ids"],
          "contract": "changed: show it with prepare-contract and ask for a new confirmation"})
    return 0


def _selection_problem(decision: Decision, part_id: str, rules: list[Rule]) -> str | None:
    """Why a part's recorded selection cannot be built: stale, open, or an unanswered goal conflict."""
    part = decision.part(part_id)
    state = selection_state(part, decision.record["facts"], rules)
    if not state["current"]:
        return state["problem"]
    conflict = goal_conflict(part, decision.record["facts"], rules)
    if conflict and conflict["severity"] == "error" and not conflict_kept(part, conflict):
        return (f"part {part_id} has a goal conflict: {conflict['chosen']} is ruled out by "
                f"{', '.join(item['rule_id'] for item in conflict['failing'])}; ask the goal-conflict question "
                f"and record the answer with resolve-conflict")
    return None


def _kb_version() -> str:
    return parse_json((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))["kb_version"]


def _research_records(root: Path) -> list[dict]:
    """Every research record in the repository."""
    records = []
    for path in sorted((root / ".harness" / "research").glob("*.md")):
        try:
            records.append(load_document(path, "research", root=root))
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    return records


def _check_all(root: Path) -> list[tuple[dict, dict]]:
    """Each override of the repository with its check against the pinned rules, the KB version and the research."""
    try:
        overrides = load_overrides(root)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    pinned, records = _pinned_rules() + _selection_rules(), _research_records(root) if overrides else []
    return [(override, check_override(override, pinned, kb_version=_kb_version(), catalog=_catalog(),
                                      research=records, answerable=_answerable())) for override in overrides]


def _answerable() -> dict[str, list]:
    """Each fact an intake question asks, and each Forge derives, with its possible answers."""
    selection = _selection_document()
    asked = {question["id"] for question in selection["questions"]}
    return {**{fact["id"]: list(fact["values"]) for fact in selection["facts"] if fact["id"] in asked},
            "reuse_confirmed": [True, False]}


def _checked_overrides(root: Path) -> tuple[list[dict], dict[str, list[str]]]:
    """The repository's overrides and their descriptions; any override that cannot be adopted refuses the command."""
    checked = _check_all(root)
    problems = [f"{override['rule_id']}: {problem}" for override, result in checked for problem in result["problems"]]
    if problems:
        raise _InvalidInput("rule overrides in .harness/overrides cannot apply: " + "; ".join(problems)
                            + "; run forge.py overrides to see them")
    return [override for override, _ in checked], {override["rule_id"]: describe(override, result)
                                                   for override, result in checked}


def _override(arguments: argparse.Namespace) -> int:
    """Propose an override from a kept, supported finding; --write records it after the user agreed."""
    root = Path(arguments.root).resolve()
    request = _read_json(arguments.input)
    unknown = set(request) - {"rule_id", "finding_id", "slug", "reason", "rule", "retired", "link"}
    if unknown:
        raise _InvalidInput(f"unknown input keys: {sorted(unknown)}")
    records = [record for record in _research_records(root) if request.get("slug") in (None, record["slug"])]
    found = [item for record in records for item in record["findings"] if item["id"] == request.get("finding_id")]
    if len(found) != 1:
        raise _InvalidInput(f"finding {request.get('finding_id')!r} is not in exactly one research record of this "
                            "repository; name its record with slug")
    # An issue answer given before adoption carries over to the override.
    issue = next((entry.get("issue") for entry in _drift(root) if entry["rule_id"] == request.get("rule_id")
                  and entry["finding"] == found[0] and entry.get("issue")), "pending")
    override = {"rule_id": request.get("rule_id"), "pinned_kb_version": _kb_version(),
                "drift": {"finding": found[0], "support_verdict": "supported"}, "reason": request.get("reason"),
                "date": datetime.date.today().isoformat(), "link": request.get("link", issue)}
    override.update({"rule": request["rule"]} if "rule" in request else {"retired": request.get("retired")})
    try:
        validate(override, "override")
        result = check_override(override, _pinned_rules() + _selection_rules(), kb_version=_kb_version(),
                                catalog=_catalog(), research=_research_records(root), answerable=_answerable())
        written = (str(write_override(root, override).relative_to(root))
                   if arguments.write and result["status"] == "valid" else None)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    emit({"command": "override", "status": result["status"], "warnings": result["warnings"],
          "problems": result["problems"], "text": describe(override, result), "override": override,
          "written": written})
    return 0 if result["status"] == "valid" else 1


def _overrides_command(arguments: argparse.Namespace) -> int:
    """Each override's state; after a KB update, --keep or --remove records the user's answer."""
    root = Path(arguments.root).resolve()
    if arguments.keep or arguments.remove:
        target = arguments.keep or arguments.remove
        try:
            matching = [override for override in load_overrides(root) if override["rule_id"] == target]
            if not matching:
                raise _InvalidInput(f"no override for {target}")
            if arguments.remove:
                safe_path(root, f".harness/overrides/{target}.yaml").unlink()
            else:
                write_override(root, {**matching[0], "pinned_kb_version": _kb_version()})
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    checked = _check_all(root)
    emit({"command": "overrides", "overrides": [
        {"rule_id": override["rule_id"], "status": result["status"], "kb_comparison": result["kb_comparison"],
         "warnings": result["warnings"], "problems": result["problems"], "text": describe(override, result)}
        for override, result in checked]})
    return 0 if all(result["status"] == "valid" for _, result in checked) else 1


def _prepare_contract(arguments: argparse.Namespace) -> int:
    """Render the current contract offline; confirmation needs a live relay."""
    try:
        decision = load_decision(Path(arguments.decision))
        revision = load_revision(Path(arguments.revision)) if arguments.revision else None
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    problem = answers_problem(decision.record, _selection_document())
    if problem:
        raise _InvalidInput(problem)
    overrides, described = _checked_overrides(Path(arguments.decision).resolve().parents[2])
    evidence = []
    if arguments.evidence:
        value = _read_json(arguments.evidence)
        evidence = value.get("worker_evidence", [])
        try:
            for record in evidence:
                validate(record, "worker-evidence")
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    try:
        contract = render_contract(decision, revision, overrides, worker_evidence=evidence, described=described,
                                   drift=_drift(Path(arguments.decision).resolve().parents[2]))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    problem = _revision_problem(decision, revision)
    if problem:
        raise _InvalidInput(problem)
    emit({
        "command": "prepare-contract", "digest": contract.digest, "text": contract.text,
        "parts": contract.parts, "overrides": contract.overrides, "worker_policy": contract.worker_policy,
        "confirmed_part_ids": [], "relay_fields": sorted(RELAY_KEYS),
    })
    return 0


def _research_check(arguments: argparse.Namespace) -> int:
    """Deterministic source, quote and support checks for findings."""
    request = _read_json(arguments.input)
    if set(request) != {"findings", "fetched", "support"}:
        raise _InvalidInput("input needs exactly findings, fetched and support")
    try:
        for item in request["findings"]:
            validate({"slug": "check", "search_mode": "live", "briefs": [{"pass": item["pass"], "part_id": item["part_id"],
                      "digest": item["brief_digest"]}], "findings": [item], "support": [], "drift": []}, "research")
        findings = [Finding.from_dict(item) for item in request["findings"]]
        validate(request["support"], "source-support")
    except (ValueError, KeyError, TypeError) as error:
        raise _InvalidInput(f"invalid findings or support: {error}") from error
    verdicts: dict[str, list[dict]] = {}
    for verdict in request["support"]["verdicts"]:
        verdicts.setdefault(verdict["finding_id"], []).append(verdict)
    kept, dropped = [], []
    for finding in findings:
        matching = verdicts.get(finding.id, [])
        problem = "duplicate support verdicts" if len(matching) > 1 else finding_problem(
            finding, request["fetched"].get(finding.id), matching[0] if matching else None)
        if problem:
            dropped.append({"id": finding.id, "reason": problem})
        else:
            kept.append(finding.id)
    unknown = sorted(set(verdicts) - {finding.id for finding in findings})
    emit({"command": "research-check", "kept": kept, "dropped": dropped, "unknown_verdicts": unknown})
    return 0 if not dropped and not unknown else 1


MAX_COLLECTED_FILES = 2000


def collect_target(root: Path, decision) -> tuple[dict[str, bytes], dict[str, str]]:
    """Read the checked files of a target repository without following links."""
    files: dict[str, bytes] = {}
    symlinks: dict[str, str] = {}
    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink():
        files["README.md"] = readme.read_bytes()
    for part in decision.record["parts"]:
        kind = part_kind(part)
        directory = f".agents/skills/{part['slug']}" if kind == "build" else (part.get("reuse") or {}).get("path")
        if not directory or kind not in ("build", "local-reuse"):
            continue
        name = directory.rsplit("/", 1)[-1]
        try:
            base = safe_path(root, directory)
            safe_path(root, ".claude/skills")
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
        link = root / ".claude" / "skills" / name
        if link.is_symlink():
            symlinks[link.relative_to(root).as_posix()] = str(link.readlink())
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                symlinks[relative] = str(path.readlink())
            elif path.is_file():
                if path.stat().st_size > 1024 * 1024 or len(files) >= MAX_COLLECTED_FILES:
                    raise _InvalidInput(f"{relative}: too large, or too many files")
                files[relative] = path.read_bytes()
    return files, symlinks


def _unattended(arguments: argparse.Namespace):
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    rules = []
    for name in ("skills", "security", "outside-controls"):
        rules += [Rule.from_dict(rule) for rule in load_document(SNAPSHOT / "rules" / f"{name}.yaml", "rules")]
    manifest = parse_json((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))
    files, symlinks = collect_target(root, decision)
    try:
        effective_rules(rules, load_overrides(root), None)
    except OverridesPending as error:
        raise _InvalidInput(f"{error}: run verify") from error
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    run = prepare_run(decision, files, rules, None, symlinks=symlinks, revision=revision, kb_version=manifest["kb_version"])
    failures, _ = selection_failures(decision, _selection_rules())
    results = finish_run(run, ReviewResponse(run_id=run.run_id, verdicts=[], goal_verdicts=[]), [], run.frozen_inputs,
                         selection=failures)
    return root, run, results


def _check(arguments: argparse.Namespace) -> int:
    """Unattended static checks: no live confirmation, so no goal is assessed."""
    _, run, results = _unattended(arguments)
    parts = []
    failed_any = False
    for result in results:
        failed = [r["rule_id"] for r in result.rule_results if r["check"] == "script"
                  and (r["result"] == "error" or (r["result"] == "fail" and run.rule_index.get(r["rule_id"], {}).get("severity") == "error"))]
        failed_any = failed_any or bool(failed)
        parts.append({"part_id": result.part_id, "harness_result": result.harness_result, "goal_result": result.goal_result,
                      "failed_script_rules": failed})
    emit({"command": "check", "run_id": run.run_id, "confirmed": False, "parts": parts})
    return 1 if failed_any else 0


def _report(arguments: argparse.Namespace) -> int:
    root, run, results = _unattended(arguments)
    rendered = render_report(run, results)
    path = None
    if arguments.write:
        try:
            path = write_report(root, arguments.slug, rendered)
        except (OSError, ValueError) as error:
            raise _InvalidInput(f"the report could not be written: {error}") from error
    emit({"command": "report", "run_id": run.run_id, "written": str(path) if path else None,
          "bytes": len(rendered.encode("utf-8"))})
    return 0


def _earlier(root: Path, slug: str, *, required: bool = False) -> tuple | None:
    """The run and part results that .harness/reports/<slug>.md records, or None.

    Only a Review needs them (``required``): an earlier report that cannot
    be compared, such as one written before re-verification existed, rules
    out a Review and leaves an Extensive review possible.
    """
    try:
        path = safe_path(root, f".harness/reports/{slug}.md")
        return earlier_run(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError) as error:
        if required:
            raise _InvalidInput(f"the earlier report cannot be compared: {error}; run an Extensive review") from error
        return None


def _fixed(arguments: argparse.Namespace, decision: Decision) -> set[str]:
    fixed = {item for item in (arguments.fixed or "").split(",") if item}
    unknown = fixed - {part["id"] for part in decision.record["parts"]}
    if unknown:
        raise _InvalidInput(f"unknown parts: {', '.join(sorted(unknown))}")
    return fixed


def _review_plan(earlier, decision: Decision, current: dict, fixed: set[str], contract_digest: str,
                 confirmed_parts, owners) -> dict:
    """The Review a re-verification runs, computed the same way for scope and for verify.

    It judges the fixed parts, the parts linked to them and every part whose
    bound inputs changed; any part that the confirmation does not preserve
    (an unchanged contract confirmed for it) is judged too. The others keep
    their earlier results, which the user sees and chooses through the
    plan's digest: a repository report proves nothing on its own.
    ``owners`` is a run prepared from the current decision, whose file
    ownership counts beside the earlier report's. Only a settled earlier
    result is kept; anything unresolved or failed is judged again.
    """
    run, results = earlier
    every = {part["id"] for part in decision.record["parts"]}
    # A retired part's history stays behind; only current parts can be judged or kept.
    changed = affected_parts(run, current, owners) & every
    confirmation = Confirmation(run_id="", contract_digest=contract_digest, part_ids=sorted(confirmed_parts))
    preserved = {part_id for part_id in preserved_parts(run, current, confirmation, owners) & set(results)
                 if settled(results[part_id])}
    judged = review_scope(decision, fixed, changed) | (every - preserved)
    kept = {part_id: results[part_id] for part_id in sorted(every - judged)}
    # The user's choice binds the parts, the kept results, the earlier inputs they rest on and the inputs now:
    # anything that changes after the choice makes it another plan.
    digest = canonical_digest({"earlier_run": run.run_id, "judged": sorted(judged),
                               "kept": {part_id: canonical_digest(result) for part_id, result in kept.items()},
                               "earlier_inputs": run.frozen_inputs, "current_inputs": current,
                               "contract": contract_digest, "confirmed": sorted(confirmed_parts)})
    return {"earlier_run": run.run_id, "changed": sorted(changed), "judged": sorted(judged), "kept": kept,
            "digest": digest}


def _current(decision: Decision, files, symlinks, rules, revision, overrides, evidence) -> dict:
    return bound_inputs(decision, files, rules, symlinks=symlinks, revision=revision, overrides=overrides,
                        kb_version=_kb_version(), worker_evidence=evidence)


def _scope(arguments: argparse.Namespace) -> int:
    """The two re-verification choices, each with its exact parts, kept results and judged-rule count."""
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    # The scope is computed for the contract confirmed in this run, as verify then computes it.
    approval, overrides = _approval(arguments, decision, revision)
    confirmation, evidence = approval["confirmation"], approval["worker_evidence"]
    rules = effective_rules(_pinned_rules(), overrides, confirmation)
    files, symlinks = collect_target(root, decision)
    fixed = _fixed(arguments, decision)
    try:
        run = prepare_run(decision, files, rules, None, symlinks=symlinks, revision=revision,
                          kb_version=_kb_version(), worker_evidence=evidence)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    earlier = _earlier(root, arguments.slug)
    every = sorted(part["id"] for part in decision.record["parts"])
    counts: dict[str, int] = {}
    for request in run.judged_requests:
        counts[request["part_id"]] = counts.get(request["part_id"], 0) + 1
    options = []
    if earlier is not None:
        plan = _review_plan(earlier, decision, _current(decision, files, symlinks, rules, revision, overrides,
                                                        evidence), fixed, confirmation.contract_digest,
                            confirmation.part_ids, run)
        options.append({"label": "Review (recommended)", "parts": plan["judged"],
                        "judged_rules": sum(counts.get(p, 0) for p in plan["judged"]), "link_checks": True,
                        "kept": [{"part_id": part_id, "run_id": plan["earlier_run"], "status": result["status"],
                                  "harness_result": result["harness_result"], "goal_result": result["goal_result"],
                                  "goal_verdict": result.get("goal_verdict"),
                                  # Every kept judgment, as the user must see what the choice keeps.
                                  "judgments": [{key: item.get(key) for key in ("rule_id", "result", "reason",
                                                                                 "citation")}
                                                for item in result["rule_results"] if item["check"] == "judged"]}
                                 for part_id, result in plan["kept"].items()],
                        "digest": plan["digest"]})
    options.append({"label": "Extensive review (rarely needed)", "parts": every,
                    "judged_rules": sum(counts.get(p, 0) for p in every), "link_checks": True, "kept": []})
    emit({"command": "scope", "fixed": sorted(fixed), "changed": plan["changed"] if earlier else every,
          "earlier_run": earlier[0].run_id if earlier else None, "options": options})
    return 0


def _environ():
    return os.environ if ENVIRON is None else ENVIRON


def _workers(arguments: argparse.Namespace) -> int:
    """The planned worker configurations for a harness, to show in the contract."""
    checks = [preflight(arguments.harness, role, runner=RUNNER, environ=_environ(), login_answer=arguments.login_answer)
              for role in ROLES]
    ready = all(check["available"] for check in checks)
    emit({"command": "workers", "harness": arguments.harness, "ready": ready,
          "roles": [{key: check[key] for key in ("role", "available", "reason", "version", "login", "needs_cost_question")}
                    for check in checks],
          "worker_evidence": [check["configuration"] for check in checks]})
    return 0 if ready else 1


def _revision_problem(decision: Decision, revision) -> str | None:
    """The requirements file must be the one the decision records as accepted, never left out."""
    recorded = (decision.record.get("accepted_revision") or {}).get("digest")
    if recorded != (revision.digest if revision else None):
        return (f"the decision records accepted requirements {recorded}; pass that rules file as --revision"
                if recorded else "the decision records no accepted requirements; leave out --revision")
    return None


def _stale_requirements(decision: Decision, revision, covered: list[str]) -> str | None:
    """Accepted requirements count only for the parts and the brief they were researched for.

    ``covered`` are the parts the requirements must hold for: the part being
    built, or each part verification checks. A revision covers one part's
    deep brief, empty or not, so each covered part's brief must be the
    revision's; a second skill part is refused until several parts can hold
    requirements.
    """
    if revision is None:
        return None
    for part_id in sorted({entry["part_id"] for entry in revision.rules} | set(covered)):
        try:
            brief = make_brief(decision, "deep", part_id)
        except ValueError:
            return f"the accepted requirements are for part {part_id}, which the decision does not have"
        if brief.digest != revision.brief_digest:
            return (f"the accepted requirements were researched for another brief than part {part_id}'s: the goals, "
                    f"picture, targets, reach or the part's goal, answers or mechanism changed, or they belong to "
                    f"another part; research part {part_id} again and accept its requirements")
    return None


def _decision_and_revision(arguments: argparse.Namespace, root: Path | None = None, *, matching: bool = True):
    try:
        decision = load_decision(Path(arguments.decision), root=root)
        revision = load_revision(Path(arguments.revision), root=root) if getattr(arguments, "revision", None) else None
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    problem = answers_problem(decision.record, _selection_document())
    problem = problem or (_revision_problem(decision, revision) if matching else None)
    if problem:
        raise _InvalidInput(problem)
    return decision, revision


def _approval(arguments: argparse.Namespace, decision: Decision, revision) -> tuple[dict, list[dict]]:
    """The contract the relay confirms, with the worker configurations and the overrides it shows.

    Returns the approval and the overrides it covers; they apply from here on.
    """
    overrides, _ = _checked_overrides(Path(arguments.root).resolve())
    evidence = _read_json(arguments.evidence).get("worker_evidence", [])
    relay = _read_json(arguments.relay)
    try:
        for record in evidence:
            validate(record, "worker-evidence")
        contract = render_contract(decision, revision, overrides, worker_evidence=evidence,
                                   drift=_drift(Path(arguments.root).resolve()))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    confirmation = confirm_contract(contract, relay, run_id=str(relay.get("run_id", "")))
    if confirmation is None:
        raise _InvalidInput("the relay does not confirm the current contract; show it with prepare-contract and ask again")
    return {"contract": contract, "confirmation": confirmation, "worker_evidence": evidence}, overrides


def _decision_name(path: Path) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]*)\.md", path.name)
    if not match:
        raise _InvalidInput(f"{path.name}: decision records are named <date>-<slug>.md")
    return match.group(1), match.group(2)


def _save_decision(arguments: argparse.Namespace, decision: Decision) -> None:
    path = Path(arguments.decision)
    date, slug = _decision_name(path)
    root = path.resolve().parents[2]
    write_decision(root, decision, slug=slug, date=date)


def _research(arguments: argparse.Namespace) -> int:
    """One research pass with live workers; --write merges it into .harness/research/<slug>.md."""
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    approval, _ = _approval(arguments, decision, revision)
    record_path = root / ".harness" / "research" / f"{arguments.slug}.md"
    existing = {"slug": arguments.slug, "search_mode": "live", "briefs": [], "findings": [], "support": [], "drift": []}
    if record_path.exists():
        try:
            existing = load_document(record_path, "research", root=root)
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    part_id = arguments.part if arguments.pass_name == "deep" else None
    confirmed = set(approval["confirmation"].part_ids)
    if part_id is not None and part_id not in confirmed:
        raise _InvalidInput(f"part {part_id} is not confirmed in this session; confirm it before researching it")
    unconfirmed = sorted({part["id"] for part in decision.record["parts"]} - confirmed)
    if part_id is None and unconfirmed:
        raise _InvalidInput(f"the survey brief holds part {', '.join(unconfirmed)}, which is not confirmed in this "
                            "session; confirm every part before the survey")
    try:
        result = research_pass(decision, arguments.pass_name, part_id, arguments.harness, **approval,
                               run_id=approval["confirmation"].run_id, runner=RUNNER, environ=_environ(), fetch=FETCH,
                               login_answer=arguments.login_answer,
                               # Every id the record ever issued, dropped findings' support entries included.
                               existing_ids=list(dict.fromkeys([finding["id"] for finding in existing["findings"]]
                                                               + [entry["finding_id"] for entry in existing["support"]])))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    written = None
    if arguments.write and result["status"] == "completed":
        same = lambda brief: (brief["pass"], brief["part_id"]) == (arguments.pass_name, part_id)  # noqa: E731
        replaced = {f["id"] for f in existing["findings"] if (f["pass"], f["part_id"]) == (arguments.pass_name, part_id)}
        record = {**existing, "search_mode": "live",
                  "briefs": [b for b in existing["briefs"] if not same(b)] + [result["brief"]],
                  "findings": [f for f in existing["findings"] if f["id"] not in replaced] + result["kept"],
                  "support": [s for s in existing["support"] if s["finding_id"] not in replaced] + result["support"]}
        body = "Findings grouped by pass and part are in the frontmatter; dropped findings are not kept.\n"
        written = str(write_research(root, record, body))
    emit({"command": "research", "status": result["status"], "reason": result["reason"],
          "brief_digest": result["brief"]["digest"],
          "kept": [{key: f[key] for key in ("id", "claim", "source", "quote")} for f in result["kept"]],
          "dropped": result["dropped"], "worker_evidence": result["worker_evidence"], "written": written})
    return 0 if result["status"] == "completed" else 1


def _accept(arguments: argparse.Namespace) -> int:
    """Write the accepted revision from the user's choices and record it in the decision."""
    root = Path(arguments.root).resolve()
    decision, _ = _decision_and_revision(arguments, root, matching=False)
    previous = decision.record.get("accepted_revision") or {}
    others = [rule_id for rule_id in previous.get("rule_ids", []) if rule_id.split(".")[2:3] != [arguments.part.lower()]]
    if others:
        raise _InvalidInput("accepting requirements for a second part is not supported yet; "
                            f"the revision already holds {', '.join(others)}")
    proposed = _read_json(arguments.proposed).get("requirements", [])
    choices = _read_json(arguments.choices)
    try:
        rules = [Rule.from_dict(rule) for rule in proposed]
        revision = accept_requirements(rules, choices, make_brief(decision, "deep", arguments.part).digest,
                                       part_id=arguments.part)
        path = write_revision(root, arguments.slug, revision)
    except (ValueError, KeyError, TypeError) as error:
        raise _InvalidInput(str(error)) from error
    decision.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
    _save_decision(arguments, decision)
    emit({"command": "accept", "digest": revision.digest, "rule_ids": revision.rule_ids, "written": str(path)})
    return 0


def _readme(arguments: argparse.Namespace) -> int:
    """Write the result README's Outside controls section from the decision record."""
    root = Path(arguments.root).resolve()
    try:
        decision = load_decision(Path(arguments.decision))
        readme = safe_path(root, "README.md")
        current = readme.read_text(encoding="utf-8") if readme.is_file() else f"# {decision.record['idea']}\n"
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    conditions = {rule.id: rule.statement for rule in _pinned_rules()}
    readme.write_text(update_outside_section(current, decision, conditions), encoding="utf-8")
    emit({"command": "readme", "written": "README.md"})
    return 0


def _pinned_rules() -> list[Rule]:
    rules = []
    for name in ("skills", "security", "outside-controls"):
        rules += [Rule.from_dict(rule) for rule in load_document(SNAPSHOT / "rules" / f"{name}.yaml", "rules")]
    return rules


def _build(arguments: argparse.Namespace) -> int:
    """Build a confirmed skill part; conflicts are reported as a diff, never overwritten."""
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    approval, overrides = _approval(arguments, decision, revision)
    pinned = effective_rules(_pinned_rules(), overrides, approval["confirmation"])
    try:
        problem = _selection_problem(decision, arguments.part,
                                     effective_rules(_selection_rules(), overrides, approval["confirmation"]))
    except KeyError as error:
        raise _InvalidInput(f"unknown part {arguments.part!r}") from error
    if problem:
        raise _InvalidInput(problem)
    selection_rules = effective_rules(_selection_rules(), overrides, approval["confirmation"])
    # The same drift policy as verify: the rules that apply to the part now, and those its selection rests on.
    files, symlinks = collect_target(root, decision)
    try:
        current = prepare_run(decision, files, pinned, None, symlinks=symlinks, revision=revision,
                              kb_version=_kb_version())
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    affecting = affecting_rules(current, selection_citations(decision, selection_rules))
    for entry in open_blocking_drift(_drift(root), decision, overrides, affecting).get(arguments.part, []):
        raise _InvalidInput(f"part {arguments.part} is blocked by open {drift_category(entry)} drift on "
                            f"{entry['rule_id']} (finding {entry['finding_id']}); the user decides first")
    if revision is None:
        raise _InvalidInput("--revision is required: a skill is built from the accepted requirements")
    stale = _stale_requirements(decision, revision, [arguments.part])
    if stale:
        raise _InvalidInput(stale)
    try:
        description = Path(arguments.description).read_text(encoding="utf-8")
        procedure = Path(arguments.procedure).read_text(encoding="utf-8")
        planned = plan_skill(decision, arguments.part, revision, pinned, confirmation=approval["confirmation"],
                             worker_evidence=approval["worker_evidence"], description=description, procedure=procedure,
                             overrides=overrides, drift=_drift(root))
        written = write_skill(root, planned, replace=[item for item in (arguments.replace or "").split(",") if item])
    except BuilderUnavailable as error:
        emit({"command": "build", "built": False, "reason": str(error)})
        return 1
    except ConflictError as error:
        emit({"command": "build", "built": False, "reason": str(error), "diff": error.diff[:20000]})
        return 1
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    _save_decision(arguments, mark_built(decision, arguments.part))
    emit({"command": "build", "built": True, "written": [str(path.relative_to(root)) for path in written]})
    return 0


def _drift(root: Path) -> list[dict]:
    """The drift of every research record in the repository, each entry with its finding and record slug.

    Drift contradicts a pinned KB rule, so it counts whichever idea's research
    found it and whatever the report is named.
    """
    drift = []
    for record in _research_records(root):
        findings = {finding["id"]: finding for finding in record["findings"]}
        drift += [{**entry, "finding": findings.get(entry["finding_id"]), "slug": record["slug"]}
                  for entry in record["drift"]]
    return drift


def _drift_entry(root: Path, rule_id: str, finding_id: str) -> dict:
    matching = [entry for entry in _drift(root) if (entry["rule_id"], entry["finding_id"]) == (rule_id, finding_id)]
    if len(matching) != 1:
        raise _InvalidInput(f"no single recorded drift of {rule_id} from finding {finding_id}; record it with drift")
    return matching[0]


def _drift_report(drift: list[dict], decision: Decision, overrides: list[dict],
                  blocking: dict[str, list[dict]]) -> list[dict]:
    """Each drift entry's state for the agent: open, kept or adopted, and the parts it blocks (open_blocking_drift)."""
    report = []
    for entry in drift:
        parts = sorted(part_id for part_id, entries in blocking.items() if entry in entries)
        report.append({"rule_id": entry["rule_id"], "finding_id": entry["finding_id"],
                       "category": drift_category(entry), "state": drift_state(entry, decision.record, overrides),
                       "blocking": bool(parts), "blocking_parts": parts, "issue": entry.get("issue")})
    return report


def _record_drift(arguments: argparse.Namespace) -> int:
    """Record a kept, supported finding that contradicts a pinned rule as drift in its research record."""
    root = Path(arguments.root).resolve()
    path = root / ".harness" / "research" / f"{arguments.slug}.md"
    try:
        record = load_document(path, "research", root=root)
        updated = record_drift(record, finding_id=arguments.finding, rule_id=arguments.rule,
                               category=arguments.category, summary=arguments.summary,
                               pinned_ids={rule.id for rule in _pinned_rules() + _selection_rules()})
        write_research(root, updated, split_frontmatter(path.read_text(encoding="utf-8"))[1])
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    entry = next(item for item in updated["drift"] if (item["rule_id"], item["finding_id"]) == (arguments.rule,
                                                                                                arguments.finding))
    emit({"command": "drift", "entry": entry, "blocking": entry["category"] in _BLOCKING_DRIFT})
    return 0


def _drift_decision(arguments: argparse.Namespace) -> int:
    """Record the user's answer to drift: keep the pinned rule, or adopt the override written for it."""
    root = Path(arguments.root).resolve()
    entry = _drift_entry(root, arguments.rule, arguments.finding)
    if arguments.choice == "adopt":
        try:
            overrides = load_overrides(root)
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
        if not any(drift_state(entry, {}, [override]) == "adopted" for override in overrides):
            raise _InvalidInput(f"adopting needs the override of {arguments.rule} from finding {arguments.finding}: "
                                "write it with forge.py override --write first")
    try:
        decision = load_decision(Path(arguments.decision))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    if not arguments.reason.strip():
        raise _InvalidInput("record the user's reason for the answer")
    decisions = [item for item in decision.record.get("drift_decisions", [])
                 if (item["rule_id"], item["finding_id"]) != (arguments.rule, arguments.finding)]
    decision.record["drift_decisions"] = decisions + [{
        "rule_id": arguments.rule, "finding_id": arguments.finding, "choice": arguments.choice,
        "reason": arguments.reason.strip(), "date": datetime.date.today().isoformat(), "slug": entry["slug"],
        "summary": entry["summary"], "source": entry["finding"]["source"], "quote": entry["finding"]["quote"],
        "drift_digest": drift_digest(entry)}]
    _save_decision(arguments, decision)
    emit({"command": "drift-decision", "rule_id": arguments.rule, "finding_id": arguments.finding,
          "choice": arguments.choice, "contract": "changed: show it with prepare-contract and ask for a new confirmation"})
    return 0


def _issues():
    if ISSUES is not None:
        return ISSUES
    return gh_search(RUNNER, environ=_environ()), gh_publisher(RUNNER, environ=_environ())


def _drift_issue(arguments: argparse.Namespace) -> int:
    """Propose the KB issue or comment for a drift entry; the user approves its exact text before publish-issue."""
    root = Path(arguments.root).resolve()
    entry = _drift_entry(root, arguments.rule, arguments.finding)
    search, _ = _issues()
    try:
        existing = find_issue(arguments.rule, search)
    except (OSError, ValueError) as error:
        raise _InvalidInput(f"searching the KB's issues failed: {error}") from error
    proposal = {**propose(entry, entry["finding"], existing, kb_version=_kb_version()), "run_id": arguments.run_id}
    try:
        Path(arguments.out).write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    except OSError as error:
        raise _InvalidInput(str(error)) from error
    emit({"command": "drift-issue", **proposal})
    return 0


def _publish_issue(arguments: argparse.Namespace) -> int:
    """File an approved proposal, or record a decline or open answer, in the drift entry and its override."""
    root = Path(arguments.root).resolve()
    proposal = _read_json(arguments.proposal)
    relay = _read_json(arguments.relay)
    fields = ("kind", "target", "title", "body")
    if not set(fields) | {"digest", "run_id", "rule_id", "finding_id"} <= set(proposal) or proposal["digest"] != \
            canonical_digest({key: proposal[key] for key in fields}):
        raise _InvalidInput("the proposal file does not match its digest; propose the text again with drift-issue")
    entry = _drift_entry(root, proposal["rule_id"], proposal["finding_id"])
    _, publisher = _issues()
    try:
        result = publish(proposal, relay, publisher, run_id=proposal["run_id"])
    except (OSError, ValueError) as error:
        raise _InvalidInput(f"filing the KB issue failed: {error}") from error
    try:
        path = root / ".harness" / "research" / f"{entry['slug']}.md"
        record = load_document(path, "research", root=root)
        for item in record["drift"]:
            if (item["rule_id"], item["finding_id"]) == (entry["rule_id"], entry["finding_id"]):
                item["issue"] = result
        write_research(root, record, split_frontmatter(path.read_text(encoding="utf-8"))[1])
        changed = False
        for override in load_overrides(root):
            if drift_state(entry, {}, [override]) == "adopted" and override["link"] != result:
                write_override(root, {**override, "link": result})
                changed = True
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    emit({"command": "publish-issue", "issue": result,
          "contract": "changed: the override's link changed; ask for a new confirmation" if changed else "unchanged"})
    return 0


def _current_inputs(arguments: argparse.Namespace, root: Path, drift: list[dict], overrides: list[dict]):
    """The target as it is now: files, links, decision record and requirements file, read again.

    Overrides or drift that changed while the workers ran make the inputs
    unusable, which invalidates every part.
    """
    try:
        decision, revision = _decision_and_revision(arguments, root)
        try:
            current = load_overrides(root)
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
        if current != overrides:
            raise _InvalidInput("the repository's rule overrides changed during verification")
        if _drift(root) != drift:
            raise _InvalidInput("the research records' drift changed during verification")
        return (*collect_target(root, decision), decision, revision)
    except _InvalidInput as error:
        raise ValueError(str(error)) from error


def _verify(arguments: argparse.Namespace) -> int:
    """Live verification: static checks, a fresh review worker, runtime cases and the report."""
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    approval, overrides = _approval(arguments, decision, revision)
    stale = _stale_requirements(decision, revision, [part["id"] for part in decision.record["parts"]
                                                     if part_kind(part) in ("build", "local-reuse")])
    if stale:
        raise _InvalidInput(stale)
    files, symlinks = collect_target(root, decision)
    try:
        cases = load_cases(Path(arguments.cases)) if arguments.cases else []
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    covered = {case["part_id"] for case in cases}
    for part in decision.record["parts"]:
        if part_kind(part) == "build" and arguments.harness in decision.record["targets"] and part["id"] not in covered:
            raise _InvalidInput(f"part {part['id']} is a built skill: pass its runtime cases with --cases")
    for part_id in sorted(covered):
        kinds = {(case["mode"], case["expected"]) for case in cases if case["part_id"] == part_id}
        if not {("explicit", "activates"), ("negative", "does-not-activate")} <= kinds:
            raise _InvalidInput(f"part {part_id} needs an explicit invocation and a near miss among its cases")
    exclusions = set()
    for item in arguments.confirm_not_applicable:
        fields = item.split(":")
        if len(fields) != 3 or not all(fields):
            raise _InvalidInput(f"--confirm-not-applicable takes PART:RULE:TOKEN as verify printed it, not {item!r}")
        exclusions.add(tuple(fields))
    manifest = parse_json((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))
    drift = _drift(root)
    unresolved = [entry for entry in drift if drift_state(entry, decision.record, overrides) == "open"]
    rules = effective_rules(_pinned_rules(), overrides, approval["confirmation"])
    every = {part["id"] for part in decision.record["parts"]}
    earlier = _earlier(root, arguments.slug, required=arguments.scope == "review")
    fixed = _fixed(arguments, decision)
    scope, kept = None, {}
    if arguments.scope == "review":
        if earlier is None:
            raise _InvalidInput("a Review compares with an earlier run, and there is no earlier report "
                                f".harness/reports/{arguments.slug}.md; run an Extensive review")
        if not arguments.scope_relay:
            raise _InvalidInput("a Review keeps earlier results only on the user's choice: show scope's options and "
                                "pass the answer with --scope-relay")
        confirmation = approval["confirmation"]
        try:
            owners = prepare_run(decision, files, rules, None, symlinks=symlinks, revision=revision,
                                 kb_version=_kb_version(), worker_evidence=approval["worker_evidence"])
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
        plan = _review_plan(earlier, decision, _current(decision, files, symlinks, rules, revision, overrides,
                                                        approval["worker_evidence"]),
                            fixed, confirmation.contract_digest, confirmation.part_ids, owners)
        relay = _read_json(arguments.scope_relay)
        if (set(relay) != {"choice", "displayed_digest", "run_id"} or relay["choice"] != "review"
                or relay["displayed_digest"] != plan["digest"] or relay["run_id"] != confirmation.run_id):
            raise _InvalidInput("this is not the Review the user chose: its parts or kept results differ from the "
                                "options shown; run scope again and ask")
        scope = set(plan["judged"])
        kept = {part_id: {"run_id": plan["earlier_run"], "result": result} for part_id, result in plan["kept"].items()}
        label = f"Review (recommended): judged {', '.join(sorted(scope)) or 'no part'}"
        if kept:
            label += f"; kept from run {earlier[0].run_id}: {', '.join(sorted(kept))}"
    elif earlier is None:
        label = f"First run: judged {', '.join(sorted(every))}"
    else:
        label = f"Extensive review (rarely needed): judged {', '.join(sorted(every))}"
    selection_rules = effective_rules(_selection_rules(), overrides, approval["confirmation"])
    failures, conflicts = selection_failures(decision, selection_rules, domains=_domains(_selection_document()))
    cited = selection_citations(decision, selection_rules)
    try:
        run, results = verify_live(decision, files, symlinks, rules, revision,
                                   arguments.harness, **approval, overrides=overrides, scope=scope,
                                   scope_label=label, kept=kept, cited=cited, recorded_drift=drift,
                                   cases=cases, runner=RUNNER, environ=_environ(), kb_version=manifest["kb_version"],
                                   login_answer=arguments.login_answer, collect=lambda: _current_inputs(arguments, root, drift, overrides),
                                   confirmed_exclusions=exclusions, drift=unresolved, selection=failures)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    written = None
    try:
        rendered = render_report(run, results)
        if arguments.write:
            written = str(write_report(root, arguments.slug, rendered))
    except (OSError, ValueError) as error:
        raise _InvalidInput(f"the report could not be written: {error}") from error
    parts = [{"part_id": r.part_id, "status": r.status, "harness_result": r.harness_result, "goal_result": r.goal_result}
             for r in results]
    runtime = [{"case_id": r.case_id, "result": r.result, "reason": r.reason, "inventory": r.inventory}
               for r in run.runtime_results]
    unresolved = [{"part_id": r.part_id, "rule_id": rule["rule_id"], "reason": rule["reason"].removesuffix(NEEDS_CONFIRMATION),
                   "confirmation": f"{r.part_id}:{rule['rule_id']}:{exclusion_token(run, r.part_id, rule['rule_id'])}"}
                  for r in results for rule in r.rule_results if rule["result"] == "unresolved"]
    emit({"command": "verify", "run_id": run.run_id, "parts": parts, "runtime": runtime, "unresolved": unresolved,
          "goal_conflicts": conflicts,
          "drift": _drift_report(drift, decision, overrides,
                                 open_blocking_drift(drift, decision, overrides, affecting_rules(run, cited))),
          "judged_parts": sorted(scope) if scope is not None else sorted(every), "kept_parts": sorted(kept),
          "worker_violations": list(run.worker_violations), "written": written})
    return 1 if any(part["harness_result"] == "not verified" for part in parts) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="forge.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    validate = commands.add_parser("validate-kb", help="validate a Harness KB checkout or snapshot")
    validate.add_argument("--kb", required=True, help="KB root directory")
    validate.set_defaults(handler=_validate_kb)
    select = commands.add_parser("select", help="recommend from confirmed, normalized answers")
    select.add_argument("--input", required=True,
                        help="JSON file with answers, whole_idea and candidates; chosen and goal for a goal conflict")
    select.add_argument("--root", help="target repository; its rule overrides are previewed, or applied with --relay")
    for command in (select,):
        command.add_argument("--decision", help="the decision whose contract --relay confirms")
        command.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
        command.add_argument("--evidence", help="JSON with the worker_evidence the contract shows")
        command.add_argument("--relay", help="JSON relay confirming the contract that shows the overrides")
    select.set_defaults(handler=_select)
    override = commands.add_parser("override", help="propose a rule override from a kept, supported finding")
    override.add_argument("--root", required=True, help="target repository root")
    override.add_argument("--input", required=True,
                          help="JSON: rule_id, finding_id, reason, the new rule or retired: true, optional slug, link")
    override.add_argument("--write", action="store_true", help="write .harness/overrides/<rule-id>.yaml")
    override.set_defaults(handler=_override)
    listing = commands.add_parser("overrides", help="show the repository's overrides and their state")
    listing.add_argument("--root", required=True, help="target repository root")
    answer = listing.add_mutually_exclusive_group()
    answer.add_argument("--keep", metavar="RULE_ID", help="after a KB update: keep this override for the new release")
    answer.add_argument("--remove", metavar="RULE_ID", help="remove this override")
    listing.set_defaults(handler=_overrides_command)
    drift = commands.add_parser("drift", help="record a kept finding that contradicts a pinned rule")
    drift.add_argument("--root", required=True, help="target repository root")
    drift.add_argument("--slug", required=True, help="the research record, .harness/research/<slug>.md")
    drift.add_argument("--finding", required=True, help="the kept finding's id, such as F3")
    drift.add_argument("--rule", required=True, help="the pinned rule it contradicts")
    drift.add_argument("--category", required=True, choices=("security", "compatibility", "ordinary"))
    drift.add_argument("--summary", required=True, help="what changed, in one or two sentences")
    drift.set_defaults(handler=_record_drift)
    decide = commands.add_parser("drift-decision", help="record the user's answer to drift: keep or adopt")
    decide.add_argument("--decision", required=True, help=".harness/decisions/<date>-<slug>.md")
    decide.add_argument("--root", required=True, help="target repository root")
    decide.add_argument("--rule", required=True)
    decide.add_argument("--finding", required=True)
    decide.add_argument("--choice", required=True, choices=("keep", "adopt"))
    decide.add_argument("--reason", required=True, help="the user's reason")
    decide.set_defaults(handler=_drift_decision)
    issue = commands.add_parser("drift-issue", help="propose the KB issue or comment for a drift entry")
    issue.add_argument("--root", required=True, help="target repository root")
    issue.add_argument("--rule", required=True)
    issue.add_argument("--finding", required=True)
    issue.add_argument("--run-id", required=True, help="this run; the approval relay must name it")
    issue.add_argument("--out", required=True, help="file for the proposal that publish-issue takes")
    issue.set_defaults(handler=_drift_issue)
    file_issue = commands.add_parser("publish-issue", help="file an approved issue proposal, or record the answer")
    file_issue.add_argument("--root", required=True, help="target repository root")
    file_issue.add_argument("--proposal", required=True, help="the drift-issue proposal file")
    file_issue.add_argument("--relay", required=True,
                            help="JSON relay of the user's answer: choice (approve|decline|pending), "
                                 "displayed_digest, run_id")
    file_issue.set_defaults(handler=_publish_issue)
    resolve = commands.add_parser("resolve-conflict", help="record the user's answer to a goal-conflict question")
    resolve.add_argument("--decision", required=True, help=".harness/decisions/<date>-<slug>.md")
    resolve.add_argument("--part", required=True)
    resolve.add_argument("--choice", required=True, choices=CONFLICT_CHOICES)
    resolve.add_argument("--adjusted-goal", help="the adjusted goal, for adjust-goal")
    resolve.add_argument("--reason", required=True, help="the user's reason for the answer")
    resolve.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
    resolve.add_argument("--evidence", help="JSON with the worker_evidence the contract shows")
    resolve.add_argument("--relay", help="JSON relay confirming the contract; the repository's overrides apply only then")
    resolve.set_defaults(handler=_resolve_conflict)
    prepare = commands.add_parser("prepare-contract", help="render the current contract for confirmation")
    prepare.add_argument("--decision", required=True, help=".harness/decisions/<date>-<slug>.md")
    prepare.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
    prepare.add_argument("--evidence", help="JSON object with worker_evidence records")
    prepare.set_defaults(handler=_prepare_contract)
    readme = commands.add_parser("readme", help="write the result README's Outside controls section")
    readme.add_argument("--decision", required=True, help=".harness/decisions/<date>-<slug>.md")
    readme.add_argument("--root", required=True, help="target repository root")
    readme.set_defaults(handler=_readme)
    research = commands.add_parser("research-check", help="check findings' sources, quotes and support verdicts")
    research.add_argument("--input", required=True, help="JSON with findings, fetched texts by finding id and support verdicts")
    research.set_defaults(handler=_research_check)
    for name, handler, helptext in (("check", _check, "run static checks on a target repository (no goals assessed)"),
                                    ("report", _report, "render the verification report (no goals assessed)")):
        command = commands.add_parser(name, help=helptext)
        command.add_argument("--decision", required=True)
        command.add_argument("--root", required=True, help="target repository root")
        command.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
        if name == "report":
            command.add_argument("--slug", required=True)
            command.add_argument("--write", action="store_true", help="write .harness/reports/<slug>.md")
        command.set_defaults(handler=handler)
    workers = commands.add_parser("workers", help="check a harness and plan the worker configurations to confirm")
    workers.add_argument("--harness", required=True, choices=HARNESSES)
    workers.add_argument("--login-answer", choices=("continue", "stop"),
                         help="the user's answer to the cost question for an API-key or unknown login")
    workers.set_defaults(handler=_workers)
    live = {"--evidence": "JSON with the worker_evidence the confirmed contract showed",
            "--relay": "JSON relay of the user's confirmation: choice, displayed_digest, part_ids, run_id"}
    research = commands.add_parser("research", help="run one research pass with live workers")
    accept = commands.add_parser("accept", help="write the accepted requirements from the user's choices")
    build = commands.add_parser("build", help="build a confirmed skill part")
    verify = commands.add_parser("verify", help="verify a confirmed build with a review worker and runtime cases")
    for command in (research, accept, build, verify):
        command.add_argument("--decision", required=True)
        command.add_argument("--root", required=True, help="target repository root")
    for command in (research, build, verify):
        command.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
        for flag, helptext in live.items():
            command.add_argument(flag, required=True, help=helptext)
    for command in (research, verify):
        command.add_argument("--harness", required=True, choices=HARNESSES)
        command.add_argument("--login-answer", choices=("continue", "stop"))
    research.add_argument("--pass", dest="pass_name", required=True, choices=("survey", "deep"))
    research.add_argument("--part", help="the part of a deep pass")
    research.add_argument("--slug", required=True)
    research.add_argument("--write", action="store_true", help="merge into .harness/research/<slug>.md")
    research.set_defaults(handler=_research)
    accept.add_argument("--part", required=True)
    accept.add_argument("--proposed", required=True, help="JSON with the proposed requirements as idea rules")
    accept.add_argument("--choices", required=True, help="JSON: rule id to accept, reject or {edit: rule}")
    accept.add_argument("--slug", required=True)
    accept.set_defaults(handler=_accept)
    build.add_argument("--part", required=True)
    build.add_argument("--description", required=True, help="file with the one-line skill description")
    build.add_argument("--procedure", required=True, help="file with the procedure")
    build.add_argument("--replace", help="comma-separated paths the user agreed to replace")
    build.set_defaults(handler=_build)
    verify.add_argument("--cases", help="runtime cases, such as evals/smoke/vertical.yaml")
    verify.add_argument("--confirm-not-applicable", action="append", default=[], metavar="PART:RULE:TOKEN",
                        help="an unresolved rule's confirmation as verify printed it, after the user confirmed "
                             "that the rule does not apply; repeatable")
    verify.add_argument("--slug", required=True)
    verify.add_argument("--write", action="store_true", help="write .harness/reports/<slug>.md")
    verify.add_argument("--scope", choices=("review", "extensive"), default="extensive",
                        help="the user's re-verification choice; review needs an earlier report")
    verify.add_argument("--fixed", help="comma-separated ids of the parts the builder fixed")
    verify.add_argument("--scope-relay",
                        help="JSON relay of the user's scope answer: choice review, displayed_digest of the "
                             "Review option scope showed, run_id")
    verify.set_defaults(handler=_verify)
    scope = commands.add_parser("scope", help="show the re-verification choices with exact parts and judged counts")
    scope.add_argument("--decision", required=True)
    scope.add_argument("--root", required=True, help="target repository root")
    scope.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
    scope.add_argument("--evidence", required=True, help="JSON with the worker_evidence the contract shows")
    scope.add_argument("--relay", required=True, help="JSON relay of the user's confirmation of the current contract")
    scope.add_argument("--slug", required=True, help="the report to compare with, .harness/reports/<slug>.md")
    scope.add_argument("--fixed", help="comma-separated ids of the parts the builder fixed")
    scope.set_defaults(handler=_scope)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        return arguments.handler(arguments)
    except _InvalidInput as error:
        print(f"forge.py: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        # A file Forge reads or writes failed, such as a read-only record: a diagnostic, never a traceback.
        print(f"forge.py: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
