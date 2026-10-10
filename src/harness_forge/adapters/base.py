"""Independent-process workers under best-effort-v1.

A worker is a separate standard `claude` or `codex` process that sees only
its role's staged request, runs with the role's restrictions and uses the
user's existing login at the vendor's account location (a set
CLAUDE_CONFIG_DIR or CODEX_HOME passes through unchanged; Forge never sets
one). Nothing here logs in, logs out, switches accounts or edits saved
configuration, and nothing certifies containment: observed tools, denials
and unknowns are recorded as evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from harness_forge.adapters import claude_code, codex
from harness_forge.contracts import WORKER_DISCLOSURE, WORKER_POLICY
from harness_forge.documents import canonical_digest, safe_path, validate
from harness_forge.models import Confirmation, Contract
from harness_forge.verify import worker_configurations

ADAPTERS = {"claude-code": claude_code, "codex": codex}
ENVIRONMENT = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "TMPDIR")
WORKERS = Path(__file__).resolve().parents[3] / "workers"
INSTRUCTIONS = {"research": "researcher.md", "source-support": "source-support.md", "review": "reviewer.md"}
METADATA_TIMEOUT = 60
WORKER_TIMEOUT = 900
UNKNOWNS = ("context, file and network isolation are not certified",
            "not every tool-registration path is observable")


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


Runner = Callable[..., Completed]


def run_command(command: list[str], *, cwd: str | None, env: dict, stdin: str | None, timeout: int) -> Completed:
    """Run one process in its own session; a timeout kills the whole group."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
        return Completed(process.returncode, stdout, stderr, False)
    except subprocess.TimeoutExpired:
        import signal

        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        return Completed(process.returncode, stdout, stderr, True)


def worker_environment(parent: Mapping[str, str], harness: str) -> dict[str, str]:
    """Only the allowlisted names and, when the user set it, the harness's account location."""
    names = ENVIRONMENT + (ADAPTERS[harness].ACCOUNT_LOCATION,)
    return {name: parent[name] for name in names if name in parent}


def _flag_present(flag: str, text: str) -> bool:
    import re

    return re.search(r"(?<![\w-])" + re.escape(flag) + r"(?![\w-])", text) is not None


def _template(harness: str, role: str) -> list[str]:
    return ADAPTERS[harness].command(role, "<workspace>")


def _configuration(harness: str, role: str, version: str, environment: dict, login_control: str) -> dict:
    template = _template(harness, role)
    names = sorted(environment)
    configuration = {
        "worker_policy": "best-effort-v1", "isolation_status": "not-certified", "role": role, "harness": harness,
        "harness_version": version or "unknown", "model": "account default", "launcher": "independent-process",
        "configuration_digest": canonical_digest({"command": template, "environment": names}),
        "configured_controls": [f"command: {json.dumps(template)}",
                                f"environment names: {', '.join(names)}", login_control],
        "observed_tools": {"status": "unknown"}, "observed_behavior": [], "unknowns": list(UNKNOWNS),
        "evidence_refs": [],
    }
    validate(configuration, "worker-evidence")
    return configuration


def preflight(harness: str, role: str, *, runner: Runner = run_command, environ: Mapping[str, str] | None = None,
              login_answer: str | None = None) -> dict:
    """Check the installed CLI, its flags and the login type before any worker starts.

    A subscription login launches without a question. An API-key or unknown
    login type needs the user's continue or stop first (``login_answer``);
    without an answer, as in a run without a user, nothing launches.
    """
    adapter = ADAPTERS[harness]
    environment = worker_environment(os.environ if environ is None else environ, harness)

    def run(command):
        return runner(command, cwd=None, env=environment, stdin=None, timeout=METADATA_TIMEOUT)

    result = {"harness": harness, "role": role, "available": False, "reason": "", "version": "unknown",
              "login": {"type": "unknown", "detail": "not checked"}, "needs_cost_question": False}
    try:
        result["version"] = run(adapter.VERSION_COMMAND).stdout.strip() or "unknown"
        help_text = "\n".join(run(command).stdout for command in adapter.HELP_COMMANDS)
        required = adapter.REQUIRED_FLAGS + getattr(adapter, "REQUIRED_EXEC_FLAGS", ())
        missing = [flag for flag in required if not _flag_present(flag, help_text)]
        problem = (f"the installed version lacks {', '.join(missing)}" if missing else adapter.extra_problem(run))
        login = run(adapter.LOGIN_COMMAND)
    except (FileNotFoundError, PermissionError):
        result["reason"] = f"{adapter.EXECUTABLE} is not installed or not executable"
        result["configuration"] = _configuration(harness, role, "unknown", environment, "login: not checked")
        return result
    kind, detail = adapter.parse_login(login.stdout, login.stderr, login.returncode)
    result["login"] = {"type": kind, "detail": detail}
    control = f"login: {kind} ({detail})"
    if problem:
        result["reason"] = problem
    elif kind == "missing":
        result["reason"] = "no login: sign in with the harness itself, then run again"
    elif kind != "subscription":
        if login_answer == "continue":
            control = f"login: {kind}; the user chose to continue after the cost warning"
            result["available"] = True
        else:
            result["needs_cost_question"] = login_answer is None
            result["reason"] = ("an API-key or unknown login can cost extra; ask the user to continue or stop"
                                if login_answer is None else "the user stopped at the cost warning")
    else:
        result["available"] = True
    result["configuration"] = _configuration(harness, role, result["version"], environment, control)
    return result


def _response(request: dict, status: str, evidence: dict, *, output: dict | None = None, reason: str = "") -> dict:
    response = {"role": request["role"], "run_id": request["run_id"], "request_digest": canonical_digest(request),
                "status": status, "evidence": evidence}
    if status == "completed":
        response["output"] = output
    else:
        response["reason"] = reason
    validate(response, "worker-response")
    return response


def _prompt(role: str, request: dict) -> str:
    instructions = (WORKERS / INSTRUCTIONS[role]).read_text(encoding="utf-8")
    return (f"{instructions}\n\n## request.json\n\nThe staged request, also in your working directory:\n\n"
            f"```json\n{json.dumps(request, indent=2, ensure_ascii=False)}\n```\n")


def _approved(contract: Contract, confirmation: Confirmation | None, worker_evidence: list[dict] | tuple) -> set[str]:
    """Digests of the worker configurations the user confirmed with the contract."""
    policy = contract.worker_policy
    if (policy.get("policy"), policy.get("isolation_status"), policy.get("disclosure")) != (
            WORKER_POLICY, "not-certified", WORKER_DISCLOSURE):
        raise ValueError("workers launch only under the disclosed best-effort-v1 policy; certified isolation is not offered")
    if confirmation is None or confirmation.contract_digest != contract.digest:
        raise ValueError("no worker launches before the user confirms the current contract")
    if contract.worker_policy["evidence_digest"] != canonical_digest(list(worker_evidence)):
        raise ValueError("the worker configurations are not the ones the confirmed contract shows")
    return {canonical_digest(item) for item in worker_configurations(list(worker_evidence))}


def run_worker(request: dict, harness: str, *, contract: Contract, confirmation: Confirmation | None,
               worker_evidence: list[dict] | tuple, runner: Runner = run_command,
               environ: Mapping[str, str] | None = None, files: Mapping[str, bytes] | None = None,
               login_answer: str | None = None, launcher: str = "independent-process",
               timeout: int = WORKER_TIMEOUT, workspace_root: str | None = None) -> dict:
    """Run one worker in a throwaway workspace and return its validated response.

    Refused before spawning: a native-subagent launch, a request whose digest
    or staged files do not match, and a configuration that the contract the
    user confirmed in this session does not show. Out-of-scope tool use
    discards the output.
    """
    if launcher != "independent-process":
        raise ValueError("workers run as independent processes; native subagent launch is refused")
    validate(request, "worker-request")
    if request["input_digest"] != canonical_digest(request["inputs"]):
        raise ValueError("the request's inputs do not match its digest")
    role, files = request["role"], dict(files or {})
    expected = {entry["path"]: entry["sha256"] for entry in request["inputs"].get("files", [])} if role == "review" else {}
    if {path: hashlib.sha256(data).hexdigest() for path, data in files.items()} != expected:
        raise ValueError("the staged files are not exactly the request's frozen files")
    environ = os.environ if environ is None else environ
    checked = preflight(harness, role, runner=runner, environ=environ, login_answer=login_answer)
    configuration = checked["configuration"]
    if not checked["available"]:
        return _response(request, "unavailable", configuration, reason=checked["reason"])
    approved = _approved(contract, confirmation, worker_evidence)
    if canonical_digest(worker_configurations([configuration])[0]) not in approved:
        raise ValueError("this worker configuration was not approved with the confirmed contract")

    adapter = ADAPTERS[harness]
    with tempfile.TemporaryDirectory(prefix=f"forge-{role}-", dir=workspace_root) as directory:
        workspace = Path(directory)
        (workspace / "request.json").write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
        for path, data in files.items():
            target = safe_path(workspace, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            completed = runner(adapter.command(role, str(workspace)), cwd=str(workspace),
                               env=worker_environment(environ, harness), stdin=_prompt(role, request), timeout=timeout)
        except OSError as error:
            return _response(request, "unavailable", configuration, reason=f"the worker could not start: {error}")

    observation = adapter.observe(completed.stdout)
    outside = sorted(set(call for call in observation["calls"] if call not in adapter.allowed_calls(role)))
    behavior = list(observation["behavior"]) + [f"out of scope: {call} was used" for call in outside]
    tools = observation["tools"]
    if tools is not None:
        extra = sorted(set(tools) - adapter.allowed_calls(role))
        behavior += [f"inventory beyond the role's tools: {', '.join(extra)}"] if extra else []
    evidence = {**configuration, "observed_tools": {"status": "observed", "tools": tools} if tools is not None
                else {"status": "unknown"}, "observed_behavior": behavior}
    if outside:
        return _response(request, "failed", evidence, reason=f"out of scope: {', '.join(outside)}; output discarded")
    if completed.timed_out:
        return _response(request, "unavailable", evidence, reason=f"the worker timed out after {timeout} s")
    if completed.returncode or observation["errored"] or not observation["completed"]:
        return _response(request, "failed", evidence,
                         reason=f"the worker did not complete (exit {completed.returncode})")
    try:
        output = json.loads(observation["text"] or "")
        return _response(request, "completed", evidence, output=output)
    except ValueError as error:
        return _response(request, "failed", evidence, reason=f"the worker's output is not valid for its role: {error}"[:2000])
