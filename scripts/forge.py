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

from harness_forge.contracts import RELAY_KEYS, load_decision, render_contract  # noqa: E402
from harness_forge.documents import load_document, parse_json, safe_path, validate, validate_kb  # noqa: E402
from harness_forge.models import AcceptedRevision, Finding, Rule  # noqa: E402
from harness_forge.report import render_report, write_report  # noqa: E402
from harness_forge.research import finding_problem, load_revision  # noqa: E402
from harness_forge.scope import scope_options  # noqa: E402
from harness_forge.verify import finish_run, part_kind, prepare_run  # noqa: E402
from harness_forge.models import ReviewResponse  # noqa: E402
from harness_forge.selection import next_question, recommend  # noqa: E402

MAX_OUTPUT_BYTES = 64 * 1024
SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
DERIVED_FACTS = ("reuse_confirmed", "outcome")
MAX_LISTED_ERRORS = 100


class _InvalidInput(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _InvalidInput(message)


def emit(payload: dict) -> None:
    """Print payload as JSON, dropping listed errors until it fits the bound."""
    errors = list(payload.get("errors", []))[:MAX_LISTED_ERRORS]
    while True:
        text = json.dumps({**payload, "errors": errors} if "errors" in payload else payload, ensure_ascii=False)
        if len(text.encode("utf-8")) <= MAX_OUTPUT_BYTES or not errors:
            print(text)
            return
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
        overrides = []
        if arguments.overrides:
            for path in sorted(Path(arguments.overrides).glob("*.yaml")):
                overrides.append(load_document(path, "override"))
    except ValueError as error:
        raise _InvalidInput(str(error)) from error
    revision = None
    if arguments.revision:
        value = _read_json(arguments.revision)
        try:
            revision = AcceptedRevision(**value)
        except TypeError as error:
            raise _InvalidInput(f"--revision: {error}") from error
    evidence = []
    if arguments.evidence:
        value = _read_json(arguments.evidence)
        evidence = value.get("worker_evidence", [])
        try:
            for record in evidence:
                validate(record, "worker-evidence")
        except ValueError as error:
            raise _InvalidInput(str(error)) from error
    contract = render_contract(decision, revision, overrides, worker_evidence=evidence)
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
    prepare.add_argument("--revision", help="JSON accepted Idea KB revision")
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
