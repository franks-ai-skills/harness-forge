"""Codex worker launch: `codex exec` without user config, rules, apps, plugins or MCP.

Feature names are checked against `codex features list` of the installed
version, flags against its help; a missing or removed one blocks launch.
Codex does not report a tool inventory, so observed tools stay unknown.
`--output-schema` points at the role's output shape, written into the
workspace as `output-schema.json`.
"""

from __future__ import annotations

import json
import re

from harness_forge.adapters.output import output_schema

HARNESS = "codex"
EXECUTABLE = "codex"
ACCOUNT_LOCATION = "CODEX_HOME"
VERSION_COMMAND = ["codex", "--version"]
HELP_COMMANDS = (["codex", "--help"], ["codex", "exec", "--help"])
LOGIN_COMMAND = ["codex", "login", "status"]
REQUIRED_FLAGS = ("--no-daemon",)
REQUIRED_EXEC_FLAGS = ("--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--ephemeral", "--json",
                       "--color", "--strict-config", "--cd", "--output-schema")
DISABLED_FEATURES = ("apps", "plugins", "hooks", "multi_agent", "browser_use", "browser_use_external", "computer_use",
                     "image_generation", "remote_plugin", "tool_suggest", "view_image")
NO_SHELL = ("shell_tool", "unified_exec")
PROFILE = "forge-worker"
SCHEMA_FILE = "output-schema.json"
# Item types that are not tool use; anything else must be allowed for the role.
_NEUTRAL_ITEMS = {"agent_message", "reasoning", "todo_list"}
# Codex's own resource listing (server "codex"). Workers set mcp_servers={},
# so it can only list nothing; it is recorded, not counted as tool use.
_EMPTY_LISTINGS = {"list_mcp_resources", "list_mcp_resource_templates"}
_ROLE_ITEMS = {"research": {"web_search"}, "source-support": set(), "review": {"command_execution"}}


def _literal(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(key) + "=" + _literal(item) for key, item in value.items()) + "}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value)


def command(role: str, workspace: str, *, structured: bool = True) -> list[str]:
    """The worker command; reads are limited to the workspace by the permission profile.

    ``structured=False`` leaves out the output schema, for a runtime case
    that runs a skill rather than a worker.
    """
    settings = {
        "approval_policy": "never",
        "project_doc_max_bytes": 0,
        "mcp_servers": {},
        "apps._default.enabled": False,
        "web_search": "live" if role == "research" else "disabled",
        "default_permissions": PROFILE,
        f"permissions.{PROFILE}.filesystem": {":root": "deny", ":minimal": "read", workspace: "read"},
        f"permissions.{PROFILE}.network.enabled": False,
    }
    disabled = DISABLED_FEATURES + (NO_SHELL if role != "review" else ())
    settings.update({f"features.{feature}": False for feature in disabled})
    arguments = ["codex", "--no-daemon", "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                 "--ephemeral", "--json", "--color", "never", "--strict-config", "-C", workspace]
    if structured:
        arguments += ["--output-schema", f"{workspace}/{SCHEMA_FILE}"]
    for key, value in settings.items():
        arguments += ["-c", f"{key}={_literal(value)}"]
    return arguments + ["-"]


def workspace_files(role: str) -> dict[str, bytes]:
    return {SCHEMA_FILE: json.dumps(output_schema(role), indent=2).encode()}


def parse_login(stdout: str, stderr: str, returncode: int) -> tuple[str, str]:
    """(subscription | api-key | missing | unknown, detail) from `codex login status`."""
    text = f"{stdout}\n{stderr}"
    if "Not logged in" in text:
        return "missing", "not logged in"
    if "Logged in using ChatGPT" in text:
        return "subscription", "ChatGPT"
    if re.search(r"api key", text, re.I):
        return "api-key", "API key"
    return "unknown", "unrecognized login status"


def extra_problem(run) -> str | None:
    """A feature the command disables must exist and not be removed in this version."""
    listed = run(["codex", "features", "list"])
    stages = {}
    for line in listed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            stages[fields[0]] = " ".join(fields[1:-1]) if len(fields) > 2 else fields[1]
    missing = [name for name in DISABLED_FEATURES + NO_SHELL if name not in stages or stages[name] == "removed"]
    return f"the installed version cannot disable features {', '.join(missing)}" if missing else None


def failure_hint(stderr: str) -> str:
    """Codex run from inside a sandboxed Codex session cannot start its app-server (observed in codex-cli 0.162)."""
    if "Operation not permitted" in stderr:
        return ("the calling Codex session's sandbox blocked the worker; run this forge.py command outside the "
                "session's sandbox (ask the user to approve running it with escalated permissions)")
    return ""


def empty_listing(item: dict) -> bool:
    return item.get("type") == "mcp_tool_call" and item.get("server") == "codex" and item.get("tool") in _EMPTY_LISTINGS


def allowed_calls(role: str) -> set[str]:
    return set(_ROLE_ITEMS[role])


def observe(stdout: str) -> dict:
    """Tool-like items and the final agent message from the JSON events."""
    calls, behavior, text, error = [], [], None, None
    completed = errored = False
    parse_errors = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            parse_errors += 1
            continue
        if not isinstance(event, dict):
            parse_errors += 1
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if event.get("type") == "item.completed":
            kind = str(item.get("type"))
            if kind == "agent_message":
                text = item.get("text")
            elif empty_listing(item):
                behavior.append(f"listed MCP {str(item['tool']).removeprefix('list_mcp_')} on server codex "
                                "(none configured)")
            elif kind not in _NEUTRAL_ITEMS:
                calls.append(kind)
        if event.get("type") == "turn.completed":
            completed = True
        if event.get("type") in ("turn.failed", "error"):
            errored = True
            # Codex reports a failed turn, such as a reached usage limit, here and not on stderr.
            detail = event.get("error")
            reported = event.get("message") or (detail.get("message") if isinstance(detail, dict) else detail)
            error = error or (str(reported) if reported else None)
    if parse_errors:
        behavior.append(f"{parse_errors} unparseable event line(s)")
    return {"tools": None, "calls": calls, "text": text, "completed": completed, "errored": errored,
            "behavior": behavior, "error": error}
