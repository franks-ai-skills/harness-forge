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

from harness_forge.documents import validate_kb  # noqa: E402

MAX_OUTPUT_BYTES = 64 * 1024
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


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="forge.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    validate = commands.add_parser("validate-kb", help="validate a Harness KB checkout or snapshot")
    validate.add_argument("--kb", required=True, help="KB root directory")
    validate.set_defaults(handler=_validate_kb)
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
