"""Runtime tests of a built skill in throwaway workspaces.

Each case runs the target harness once in a fresh workspace that holds
only the part's frozen files and the case's fixtures, with the existing
login and the case's declared tools. Activation counts only when it is
observed: Claude's Skill tool call, or Codex reading the skill's SKILL.md.
No observable signal is `not-run`, never a pass. Executable components
that stay loaded (user plugins, MCP servers) make a case unavailable; a
competing skill makes its activation test `not-run`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

import yaml

from harness_forge.adapters import codex
from harness_forge.adapters.base import Runner, preflight, run_command, worker_environment
from harness_forge.documents import safe_path
from harness_forge.models import RuntimeResult, VerificationRun

MODES = ("explicit", "implicit", "negative")
EXPECTED = ("activates", "does-not-activate")
RUNTIME_TIMEOUT = 600


def load_cases(path: Path) -> list[dict]:
    """Cases from an evals/smoke file, checked for the keys a run needs."""
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: expected a non-empty cases list")
    for item in cases:
        missing = {"id", "part_id", "mode", "prompt", "expected", "permitted_tools"} - set(item)
        if missing or item["mode"] not in MODES or item["expected"] not in EXPECTED:
            raise ValueError(f"{path}: case {item.get('id')!r} is incomplete or has an unknown mode or expectation")
    return cases


def _claude_command(tools: list[str]) -> list[str]:
    listed = ",".join(tools)
    return [
        "claude", "--print", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--settings", '{"disableAllHooks":true,"autoMemoryEnabled":false}',
        "--tools", listed, "--allowedTools", listed,
    ]


def _events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _observe_claude(stdout: str, name: str, baseline: set[str]) -> dict:
    """Activation, tool calls and what else stayed loaded, from the init and assistant events."""
    observed = {"activation": None, "calls": [], "unavailable": [], "competing": [], "loaded": None,
                "completed": False, "errored": False}
    for event in _events(stdout):
        if event.get("type") == "system" and event.get("subtype") == "init":
            skills = [str(skill) for skill in event.get("skills", [])]
            observed["loaded"] = name in skills
            observed["competing"] = sorted(set(skills) - {name} - baseline)
            plugins = [p for p in event.get("plugins", []) if not (isinstance(p, dict) and p.get("path") == "builtin")]
            observed["unavailable"] += [f"plugin {p.get('name', p) if isinstance(p, dict) else p}" for p in plugins]
            observed["unavailable"] += [f"MCP server {s.get('name', s) if isinstance(s, dict) else s}"
                                        for s in event.get("mcp_servers", [])]
        message = event.get("message")
        if event.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("content"), list):
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                observed["calls"].append(str(block.get("name")))
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if block.get("name") == "Skill" and name in (arguments.get("skill"), arguments.get("command")):
                    observed["activation"] = f"Skill tool called for {name}"
        if event.get("type") == "user" and f"<command-name>/{name}</command-name>" in json.dumps(event):
            observed["activation"] = observed["activation"] or f"/{name} was invoked"
        if event.get("type") == "result":
            observed["completed"] = event.get("is_error") is False and event.get("subtype") == "success"
            observed["errored"] = not observed["completed"]
    return observed


def _observe_codex(stdout: str, directory: str) -> dict:
    observed = {"activation": None, "calls": [], "unavailable": [], "competing": [], "loaded": None,
                "completed": False, "errored": False}
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if event.get("type") == "item.completed" and item.get("type") not in ("agent_message", "reasoning", "todo_list"):
            observed["calls"].append(str(item.get("type")))
            if item.get("type") == "command_execution" and f"{directory}/SKILL.md" in str(item.get("command", "")):
                observed["activation"] = f"read {directory}/SKILL.md"
        observed["completed"] = observed["completed"] or event.get("type") == "turn.completed"
        observed["errored"] = observed["errored"] or event.get("type") in ("turn.failed", "error")
    return observed


def _decide(harness: str, test_case: dict, observed: dict, permitted: list[str]) -> tuple[str, str]:
    outside = sorted(set(observed["calls"]) - set(permitted))
    if outside:
        return "fail", f"tool outside the declared list: {', '.join(outside)}"
    if observed["unavailable"]:
        return "not-run", f"unavailable: executable components stayed loaded: {', '.join(observed['unavailable'])}"
    if observed["errored"] or not observed["completed"]:
        return "error", "the harness did not complete the case"
    if observed["loaded"] is False:
        return "error", "the candidate skill was not loaded"
    if observed["competing"]:
        return "not-run", f"a competing skill was loaded: {', '.join(observed['competing'])}"
    activated = observed["activation"] is not None
    if test_case["expected"] == "activates":
        if activated:
            return "pass", observed["activation"]
        if harness == "codex":
            return "not-run", "Codex showed no observable activation signal"
        return "fail", "the skill was not activated"
    if activated:
        return "fail", f"activated on a near miss: {observed['activation']}"
    if harness == "codex":
        return "not-run", "Codex has no observable signal that proves non-activation"
    return "pass", "no Skill tool call for the candidate"


def activation_case(
    harness: str, files: Mapping[str, bytes], skill_dirs: list[str], target: str, prompt: str, expected: str,
    permitted: list[str], fixtures: Mapping[str, str], *, runner: Runner, environ: Mapping[str, str],
    baseline: set[str], timeout: int = RUNTIME_TIMEOUT, workspace_root: str | None = None, slash: bool = False,
) -> tuple[str, str, str | None]:
    """Run one prompt in a throwaway workspace holding ``files``; judge whether ``target`` activated.

    Skills in ``skill_dirs`` other than the target are installed on purpose
    and never count as competing; Claude finds each through its
    .claude/skills link. A slash or $ invocation (``slash``) may load the
    skill without a tool call; with no observed signal it is not-run.
    """
    name = target.rsplit("/", 1)[-1]
    companions = {directory.rsplit("/", 1)[-1] for directory in skill_dirs} - {name}
    with tempfile.TemporaryDirectory(prefix="forge-runtime-", dir=workspace_root) as temporary:
        workspace = Path(temporary)
        for path, data in files.items():
            destination = safe_path(workspace, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        for path, text in fixtures.items():
            destination = safe_path(workspace, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        if harness == "claude-code":
            for directory in skill_dirs:
                link = workspace / ".claude" / "skills" / directory.rsplit("/", 1)[-1]
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(f"../../{directory}")
            command = _claude_command(permitted)
        else:
            command = codex.command("review", str(workspace))
        try:
            completed = runner(command, cwd=str(workspace), env=worker_environment(environ, harness), stdin=prompt,
                               timeout=timeout)
        except OSError as error:
            return "not-run", f"unavailable: the harness could not start: {error}", None
    if completed.timed_out:
        return "not-run", f"timed out after {timeout} s", None
    if completed.returncode:
        return "error", f"the harness exited with {completed.returncode}", None
    observed = (_observe_claude(completed.stdout, name, baseline | companions) if harness == "claude-code"
                else _observe_codex(completed.stdout, target))
    outcome, reason = _decide(harness, {"expected": expected}, observed, permitted)
    if slash and outcome == "fail" and observed["activation"] is None and expected == "activates":
        return "not-run", "the explicit invocation left no observable activation signal", None
    return outcome, reason, observed["activation"]


def run_skill_runtime(
    run: VerificationRun, harness: str, cases: list[dict], *, files: Mapping[str, bytes], runner: Runner = run_command,
    environ: Mapping[str, str] | None = None, login_answer: str | None = None,
    baseline_skills: set[str] | frozenset = frozenset(), timeout: int = RUNTIME_TIMEOUT,
    workspace_root: str | None = None,
) -> list[RuntimeResult]:
    """One RuntimeResult per case, bound to the run and the part's frozen files.

    baseline_skills are the harness's built-in skills, as a launch with all
    customizations disabled lists them; they never count as competing.
    """
    if harness not in run.targets:
        raise ValueError(f"{harness} is not a target of this run")
    frozen = run.frozen_inputs["files"]
    for test_case in cases:
        directory = run.part_dirs.get(test_case["part_id"])
        if directory is None:
            raise ValueError(f"case {test_case['id']}: part {test_case['part_id']} has no built artifact")
        expected = {path: digest for path, digest in frozen.items() if path.startswith(directory + "/")}
        provided = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()
                    if path.startswith(directory + "/")}
        if not expected or provided != expected:
            raise ValueError(f"case {test_case['id']}: the files are not exactly the part's frozen files")
    environ = os.environ if environ is None else environ
    checked = preflight(harness, "review", runner=runner, environ=environ, login_answer=login_answer)
    results = []
    for test_case in cases:
        directory = run.part_dirs[test_case["part_id"]]
        name = directory.rsplit("/", 1)[-1]
        hashes = {path: digest for path, digest in frozen.items() if path.startswith(directory + "/")}
        permitted = list(test_case["permitted_tools"][harness])
        fixtures = dict(test_case.get("fixtures") or {})
        environment = "; ".join([
            "throwaway workspace with only the frozen skill and fixtures",
            f"tools: {', '.join(permitted) or 'none'}", "network: none requested",
            *[f"{path} sha256 {hashlib.sha256(text.encode()).hexdigest()}" for path, text in sorted(fixtures.items())],
        ])

        def result(outcome: str, reason: str, activation: str | None = None) -> RuntimeResult:
            return RuntimeResult(run_id=run.run_id, part_id=test_case["part_id"], case_id=test_case["id"],
                                 prompt=test_case["prompt"], expected=test_case["expected"],
                                 observed_activation=activation, result=outcome, reason=reason, file_hashes=hashes,
                                 harness=harness, harness_version=checked["version"], environment=environment)

        if not checked["available"]:
            results.append(result("not-run", f"unavailable: {checked['reason']}"))
            continue
        staged = {path: data for path, data in files.items() if path.startswith(directory + "/")}
        outcome, reason, activation = activation_case(
            harness, staged, [directory], directory, test_case["prompt"], test_case["expected"], permitted, fixtures,
            runner=runner, environ=environ, baseline=set(baseline_skills), timeout=timeout,
            workspace_root=workspace_root)
        results.append(result(outcome, reason, activation))
    return results
