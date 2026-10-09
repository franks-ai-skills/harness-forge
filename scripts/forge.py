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

from harness_forge.documents import load_document, parse_json, validate_kb  # noqa: E402
from harness_forge.models import Rule  # noqa: E402
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
        payload["question"] = next_question({"answers": answers}, whole_idea, selection)
    emit(payload)
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
