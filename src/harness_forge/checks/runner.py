"""Run one script rule in a separate, bounded process.

The child process gets the file bytes as data on stdin, an empty working
directory, an empty environment and no network; it imports only Forge's
checker modules, so checked files are never executed or imported.
Timeouts, oversized output, crashes and malformed results are errors,
never passes.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from harness_forge.models import CheckResult, Rule
from harness_forge.predicates import UnknownFact, evaluate

TIMEOUT_SECONDS = 5.0
OUTPUT_LIMIT = 64 * 1024
STATUSES = ("pass", "fail", "not-applicable")
SOURCE = str(Path(__file__).resolve().parents[2])

_CHILD = r"""
import base64, contextlib, json, socket, sys

def _no_network(*args, **kwargs):
    raise OSError("network access is disabled for checkers")

socket.socket = _no_network
socket.create_connection = _no_network
socket.getaddrinfo = _no_network
request = json.loads(sys.stdin.buffer.read())
sys.path[:0] = request["path"]
import importlib
version, function = importlib.import_module(request["registry"]).REGISTRY[request["checker"]]
files = {path: base64.b64decode(data) for path, data in request["files"].items()}
with contextlib.redirect_stdout(sys.stderr):
    status, reason = function(files, request["facts"], request["params"])
sys.stdout.write(json.dumps({"status": status, "reason": str(reason), "version": version}))
"""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _version_matches(installed: str, ranges: str) -> bool:
    for condition in ranges.split(","):
        operator, bound = re.match(r"\s*(>=|<=|>|<|==)\s*(.+)", condition).groups()
        left, right = _version_key(installed), _version_key(bound)
        if not {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right, "==": left == right}[operator]:
            return False
    return True


def evaluate_applicability(rule: Rule, facts: dict) -> tuple[bool, str]:
    """Decide applies_to from confirmed facts; raise UnknownFact if one is missing."""
    targets = facts.get("targets")
    if targets is None:
        raise UnknownFact("targets")
    harnesses = set(rule.applies_to["harnesses"]) & set(targets)
    if not harnesses:
        return False, f"applies to {', '.join(rule.applies_to['harnesses'])}, not the targets"
    versions = facts.get("harness_versions", {})
    for harness, ranges in rule.applies_to.get("versions", {}).items():
        if harness not in harnesses:
            continue
        if harness not in versions or not re.search(r"\d", str(versions[harness])):
            # A range decides nothing without a version, "unknown" included: unresolved, never silently applicable.
            raise UnknownFact(f"harness_versions.{harness}")
        if not _version_matches(versions[harness], ranges):
            harnesses.discard(harness)
    if not harnesses:
        return False, "no target harness version is in the rule's range"
    for predicate in rule.applies_to.get("when", []):
        if not evaluate(predicate, facts):
            return False, "a condition in applies_to.when does not hold"
    return True, "applies"


def _result(rule: Rule, facts: dict, status: str, reason: str, version: str | None = None) -> CheckResult:
    return CheckResult(
        part_id=facts.get("part_id"), rule_id=rule.id, status=status, severity=rule.severity,
        reason=reason[:2000], sources=list(rule.sources), checker=rule.checker, checker_version=version,
    )


def run_check(
    rule: Rule,
    files: dict[str, bytes],
    facts: dict,
    *,
    timeout: float = TIMEOUT_SECONDS,
    output_limit: int = OUTPUT_LIMIT,
    registry: str = "harness_forge.checks",
    extra_path: list[str] | tuple[str, ...] = (),
) -> CheckResult:
    if rule.check != "script":
        raise ValueError(f"{rule.id} is a judged rule")
    try:
        applies, reason = evaluate_applicability(rule, facts)
    except UnknownFact as error:
        return _result(rule, facts, "error", f"applicability needs the unconfirmed fact {error.args[0]!r}")
    if not applies:
        return _result(rule, facts, "not-applicable", reason)

    request = json.dumps({
        "path": [SOURCE, *extra_path],
        "registry": registry,
        "checker": rule.checker,
        "params": rule.params or {},
        "facts": facts,
        "files": {path: base64.b64encode(data).decode("ascii") for path, data in files.items()},
    }).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="forge-check-") as workdir:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _CHILD],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workdir, env={},
        )
        captured: dict[str, bytes] = {}

        def read(name, stream, limit):
            captured[name] = stream.read(limit)
            stream.read()  # drain so the child never blocks on a full pipe

        readers = [
            threading.Thread(target=read, args=("out", process.stdout, output_limit + 1), daemon=True),
            threading.Thread(target=read, args=("err", process.stderr, 4096), daemon=True),
        ]
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(request)
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            timed_out = True
        for reader in readers:
            reader.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
        if timed_out:
            return _result(rule, facts, "error", f"checker {rule.checker} timed out after {timeout:g} s")

    output, errors = captured.get("out", b""), captured.get("err", b"").decode("utf-8", "replace")
    if len(output) > output_limit:
        return _result(rule, facts, "error", f"checker output exceeded {output_limit} bytes")
    if process.returncode != 0:
        last = errors.strip().splitlines()[-1] if errors.strip() else f"exit code {process.returncode}"
        return _result(rule, facts, "error", f"checker {rule.checker} failed: {last}")
    try:
        reply = json.loads(output)
    except json.JSONDecodeError:
        return _result(rule, facts, "error", "checker returned malformed output")
    if reply.get("status") not in STATUSES:
        return _result(rule, facts, "error", f"checker returned invalid status {reply.get('status')!r}")
    return _result(rule, facts, reply["status"], reply.get("reason", ""), reply.get("version"))
