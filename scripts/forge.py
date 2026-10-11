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

import os  # noqa: E402
import re  # noqa: E402

from harness_forge.adapters.base import preflight, run_command  # noqa: E402
from harness_forge.build_skill import BuilderUnavailable, ConflictError, mark_built, plan_skill, write_skill  # noqa: E402
from harness_forge.contracts import (  # noqa: E402
    RELAY_KEYS, confirm_contract, load_decision, render_contract, write_decision,
)
from harness_forge.documents import load_document, parse_json, safe_path, validate, validate_kb  # noqa: E402
from harness_forge.fetch import fetch_text  # noqa: E402
from harness_forge.models import Decision, Finding, Rule  # noqa: E402
from harness_forge.report import render_report, write_report  # noqa: E402
from harness_forge.research import (  # noqa: E402
    accept_requirements, finding_problem, load_revision, make_brief, write_research, write_revision,
)
from harness_forge.runtime import load_cases  # noqa: E402
from harness_forge.workflow import research_pass, verify_live  # noqa: E402
from harness_forge.scope import scope_options  # noqa: E402
from harness_forge.verify import NEEDS_CONFIRMATION, finish_run, part_kind, prepare_run  # noqa: E402
from harness_forge.models import ReviewResponse  # noqa: E402
from harness_forge.selection import next_question, recommend  # noqa: E402

MAX_OUTPUT_BYTES = 64 * 1024
SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
DERIVED_FACTS = ("reuse_confirmed", "outcome")
MAX_LISTED_ERRORS = 100
HARNESSES = ("claude-code", "codex")
ROLES = ("research", "source-support", "review")
# Replaced by tests; the live commands launch real workers and fetch real pages.
RUNNER = run_command
FETCH = fetch_text
ENVIRON = None


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


def _select(arguments: argparse.Namespace) -> int:
    request = _read_json(arguments.input)
    unknown = set(request) - {"answers", "whole_idea", "candidates"}
    if unknown:
        raise _InvalidInput(f"unknown input keys: {sorted(unknown)}")
    selection = load_document(SNAPSHOT / "rules" / "selection.yaml", "selection")
    declared = {fact["id"]: fact for fact in selection["facts"]}
    answers, whole_idea = request.get("answers", {}), request.get("whole_idea", {})
    candidates = request.get("candidates", [])
    if not all(isinstance(value, dict) for value in (answers, whole_idea)) or not isinstance(candidates, list):
        raise _InvalidInput("answers and whole_idea must be objects, candidates a list")
    _check_declared(answers, "part", declared)
    _check_declared(whole_idea, "idea", declared)
    rules = [Rule.from_dict(rule) for rule in selection["rules"]]
    recommendation = recommend(answers, whole_idea, rules, candidates)
    payload = {"command": "select", **asdict(recommendation), "question": None}
    if recommendation.pending_question:
        payload["question"] = next_question({"answers": answers}, whole_idea, selection, candidates)
    emit(payload)
    return 0


def _prepare_contract(arguments: argparse.Namespace) -> int:
    """Render the current contract offline; confirmation needs a live relay."""
    try:
        decision = load_decision(Path(arguments.decision))
        revision = load_revision(Path(arguments.revision)) if arguments.revision else None
        overrides = []
        if arguments.overrides:
            for path in sorted(Path(arguments.overrides).glob("*.yaml")):
                overrides.append(load_document(path, "override"))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
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
        contract = render_contract(decision, revision, overrides, worker_evidence=evidence)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
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
    try:
        decision = load_decision(Path(arguments.decision), root=root)
        revision = load_revision(Path(arguments.revision), root=root) if arguments.revision else None
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    rules = []
    for name in ("skills", "security", "outside-controls"):
        rules += [Rule.from_dict(rule) for rule in load_document(SNAPSHOT / "rules" / f"{name}.yaml", "rules")]
    manifest = parse_json((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))
    files, symlinks = collect_target(root, decision)
    run = prepare_run(decision, files, rules, None, symlinks=symlinks, revision=revision, kb_version=manifest["kb_version"])
    results = finish_run(run, ReviewResponse(run_id=run.run_id, verdicts=[], goal_verdicts=[]), [], run.frozen_inputs)
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
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    emit({"command": "report", "run_id": run.run_id, "written": str(path) if path else None,
          "bytes": len(rendered.encode("utf-8"))})
    return 0


def _scope(arguments: argparse.Namespace) -> int:
    try:
        decision = load_decision(Path(arguments.decision))
        fixed = {item for item in (arguments.fixed or "").split(",") if item}
        changed = {item for item in (arguments.changed or "").split(",") if item}
        counts = _read_json(arguments.counts) if arguments.counts else {}
        options = scope_options(decision, fixed, changed, counts)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    emit({"command": "scope", "options": options})
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


def _decision_and_revision(arguments: argparse.Namespace, root: Path | None = None):
    try:
        decision = load_decision(Path(arguments.decision), root=root)
        revision = load_revision(Path(arguments.revision), root=root) if getattr(arguments, "revision", None) else None
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    return decision, revision


def _approval(arguments: argparse.Namespace, decision: Decision, revision) -> dict:
    """The contract the relay confirms, with the worker configurations it shows."""
    evidence = _read_json(arguments.evidence).get("worker_evidence", [])
    relay = _read_json(arguments.relay)
    try:
        for record in evidence:
            validate(record, "worker-evidence")
        contract = render_contract(decision, revision, [], worker_evidence=evidence)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    confirmation = confirm_contract(contract, relay, run_id=str(relay.get("run_id", "")))
    if confirmation is None:
        raise _InvalidInput("the relay does not confirm the current contract; show it with prepare-contract and ask again")
    return {"contract": contract, "confirmation": confirmation, "worker_evidence": evidence}


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
    approval = _approval(arguments, decision, revision)
    record_path = root / ".harness" / "research" / f"{arguments.slug}.md"
    existing = {"slug": arguments.slug, "search_mode": "live", "briefs": [], "findings": [], "support": [], "drift": []}
    if record_path.exists():
        try:
            existing = load_document(record_path, "research", root=root)
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    part_id = arguments.part if arguments.pass_name == "deep" else None
    try:
        result = research_pass(decision, arguments.pass_name, part_id, arguments.harness, **approval,
                               run_id=approval["confirmation"].run_id, runner=RUNNER, environ=_environ(), fetch=FETCH,
                               login_answer=arguments.login_answer,
                               existing_ids=[finding["id"] for finding in existing["findings"]])
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
    decision, _ = _decision_and_revision(arguments, root)
    previous = decision.record.get("accepted_revision") or {}
    others = [rule_id for rule_id in previous.get("rule_ids", []) if rule_id.split(".")[2:3] != [arguments.part.lower()]]
    if others:
        raise _InvalidInput("accepting requirements for a second part is not supported yet; "
                            f"the revision already holds {', '.join(others)}")
    proposed = _read_json(arguments.proposed).get("requirements", [])
    choices = _read_json(arguments.choices)
    try:
        rules = [Rule.from_dict(rule) for rule in proposed]
        revision = accept_requirements(rules, choices, make_brief(decision, "deep", arguments.part).digest)
        path = write_revision(root, arguments.slug, revision)
    except (ValueError, KeyError, TypeError) as error:
        raise _InvalidInput(str(error)) from error
    decision.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
    _save_decision(arguments, decision)
    emit({"command": "accept", "digest": revision.digest, "rule_ids": revision.rule_ids, "written": str(path)})
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
    approval = _approval(arguments, decision, revision)
    if revision is None:
        raise _InvalidInput("--revision is required: a skill is built from the accepted requirements")
    try:
        description = Path(arguments.description).read_text(encoding="utf-8")
        procedure = Path(arguments.procedure).read_text(encoding="utf-8")
        planned = plan_skill(decision, arguments.part, revision, _pinned_rules(), confirmation=approval["confirmation"],
                             worker_evidence=approval["worker_evidence"], description=description, procedure=procedure)
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


def _verify(arguments: argparse.Namespace) -> int:
    """Live verification: static checks, a fresh review worker, runtime cases and the report."""
    root = Path(arguments.root).resolve()
    decision, revision = _decision_and_revision(arguments, root)
    approval = _approval(arguments, decision, revision)
    files, symlinks = collect_target(root, decision)
    try:
        cases = load_cases(Path(arguments.cases)) if arguments.cases else []
    except (OSError, ValueError) as error:
        raise _InvalidInput(str(error)) from error
    exclusions = set()
    for item in arguments.confirm_not_applicable:
        part_id, _, rule_id = item.partition(":")
        if not part_id or not rule_id:
            raise _InvalidInput(f"--confirm-not-applicable takes PART:RULE, not {item!r}")
        exclusions.add((part_id, rule_id))
    manifest = parse_json((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))
    try:
        run, results = verify_live(decision, files, symlinks, _pinned_rules(), revision, arguments.harness, **approval,
                                   cases=cases, runner=RUNNER, environ=_environ(), kb_version=manifest["kb_version"],
                                   login_answer=arguments.login_answer, collect=lambda: collect_target(root, decision),
                                   confirmed_exclusions=exclusions)
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    rendered = render_report(run, results)
    written = None
    if arguments.write:
        try:
            written = str(write_report(root, arguments.slug, rendered))
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    parts = [{"part_id": r.part_id, "status": r.status, "harness_result": r.harness_result, "goal_result": r.goal_result}
             for r in results]
    runtime = [{"case_id": r.case_id, "result": r.result, "reason": r.reason} for r in run.runtime_results]
    unresolved = [{"part_id": r.part_id, "rule_id": rule["rule_id"], "reason": rule["reason"].removesuffix(NEEDS_CONFIRMATION)}
                  for r in results for rule in r.rule_results if rule["result"] == "unresolved"]
    emit({"command": "verify", "run_id": run.run_id, "parts": parts, "runtime": runtime, "unresolved": unresolved,
          "worker_violations": list(run.worker_violations), "written": written})
    return 1 if any(part["harness_result"] == "not verified" for part in parts) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="forge.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    validate = commands.add_parser("validate-kb", help="validate a Harness KB checkout or snapshot")
    validate.add_argument("--kb", required=True, help="KB root directory")
    validate.set_defaults(handler=_validate_kb)
    select = commands.add_parser("select", help="recommend from confirmed, normalized answers")
    select.add_argument("--input", required=True, help="JSON file with answers, whole_idea and candidates")
    select.set_defaults(handler=_select)
    prepare = commands.add_parser("prepare-contract", help="render the current contract for confirmation")
    prepare.add_argument("--decision", required=True, help=".harness/decisions/<date>-<slug>.md")
    prepare.add_argument("--revision", help=".harness/research/<slug>.rules.yaml")
    prepare.add_argument("--overrides", help="directory with .harness/overrides/*.yaml")
    prepare.add_argument("--evidence", help="JSON object with worker_evidence records")
    prepare.set_defaults(handler=_prepare_contract)
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
    verify.add_argument("--confirm-not-applicable", action="append", default=[], metavar="PART:RULE",
                        help="a rule the user confirmed does not apply, after seeing the reviewer's reason; repeatable")
    verify.add_argument("--slug", required=True)
    verify.add_argument("--write", action="store_true", help="write .harness/reports/<slug>.md")
    verify.set_defaults(handler=_verify)
    scope = commands.add_parser("scope", help="show the review choices with exact parts and judged counts")
    scope.add_argument("--decision", required=True)
    scope.add_argument("--fixed", help="comma-separated fixed part ids")
    scope.add_argument("--changed", help="comma-separated part ids with changed inputs")
    scope.add_argument("--counts", help="JSON object of judged-rule counts per part")
    scope.set_defaults(handler=_scope)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        return arguments.handler(arguments)
    except _InvalidInput as error:
        print(f"forge.py: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
