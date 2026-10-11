"""Claude Code worker launch: `claude -p` with the role's tools and no customizations.

`--safe-mode` keeps the user's existing login (unlike `--bare`, which needs
an API key) while disabling CLAUDE.md, skills, plugins, hooks and MCP
servers; `--restricted` removes the command-running tools and ignores the
settings files. `--json-schema` makes the answer a validated
`StructuredOutput` call, reported as the result's `structured_output`.
These are starting settings verified against the installed version's help,
not permanent vendor guarantees.
"""

from __future__ import annotations

import json

from harness_forge.adapters.output import output_schema

HARNESS = "claude-code"
EXECUTABLE = "claude"
ACCOUNT_LOCATION = "CLAUDE_CONFIG_DIR"
VERSION_COMMAND = ["claude", "--version"]
HELP_COMMANDS = (["claude", "--help"],)
LOGIN_COMMAND = ["claude", "auth", "status", "--json"]
REQUIRED_FLAGS = (
    "--print", "--output-format", "--verbose", "--no-session-persistence", "--safe-mode", "--restricted",
    "--setting-sources", "--strict-mcp-config", "--mcp-config", "--disable-slash-commands", "--permission-mode",
    "--permission-prompts", "--settings", "--tools", "--allowedTools", "--json-schema",
)
ROLE_TOOLS = {"research": ["WebFetch", "WebSearch"], "source-support": [], "review": ["Read", "Grep", "Glob"]}
_API_KEY_METHODS = ("api_key", "apiKey", "api-key", "apiKeyHelper", "api_key_helper")


def command(role: str, workspace: str) -> list[str]:
    """The worker command; the workspace is its working directory, not an argument."""
    tools = ",".join(ROLE_TOOLS[role])
    return [
        "claude", "--print", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--safe-mode", "--restricted", "--setting-sources", "", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--settings", '{"disableAllHooks":true,"autoMemoryEnabled":false}',
        "--tools", tools, "--allowedTools", tools,
        "--json-schema", json.dumps(output_schema(role), separators=(",", ":")),
    ]


def workspace_files(role: str) -> dict[str, bytes]:
    return {}


def parse_login(stdout: str, stderr: str, returncode: int) -> tuple[str, str]:
    """(subscription | api-key | missing | unknown, detail) from `claude auth status --json`."""
    try:
        status = json.loads(stdout)
    except ValueError:
        return "unknown", "unparseable auth status"
    if not isinstance(status, dict):
        return "unknown", "unparseable auth status"
    if status.get("loggedIn") is False:
        return "missing", "not logged in"
    method = str(status.get("authMethod", ""))
    if status.get("loggedIn") is True and method == "claude.ai":
        return "subscription", "claude.ai"
    if method in _API_KEY_METHODS:
        return "api-key", method
    return "unknown", method or "no auth method reported"


def extra_problem(run) -> str | None:
    return None


def failure_hint(stderr: str) -> str:
    return ""


def allowed_calls(role: str) -> set[str]:
    return set(ROLE_TOOLS[role]) | {"StructuredOutput"}


def observe(stdout: str) -> dict:
    """Tool inventory, tool calls and the final text from the stream-json events."""
    tools, calls, behavior, text = None, [], [], None
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
        if event.get("type") == "system" and event.get("subtype") == "init":
            if isinstance(event.get("tools"), list):
                tools = sorted({str(tool) for tool in event["tools"]})
            if event.get("model"):
                behavior.append(f"model reported: {event['model']}")
            if "apiKeySource" in event:
                behavior.append(f"apiKeySource: {event['apiKeySource']}")
            if isinstance(event.get("skills"), list):
                behavior.append(f"skills listed: {', '.join(sorted(str(skill) for skill in event['skills']))}")
            plugins = [p for p in event.get("plugins", []) if isinstance(p, dict)]
            if plugins:
                behavior.append("plugins listed: " + ", ".join(sorted(f"{p.get('name')} ({p.get('path')})"
                                                                       for p in plugins)))
        message = event.get("message")
        if event.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("content"), list):
            calls += [str(block.get("name")) for block in message["content"]
                      if isinstance(block, dict) and block.get("type") == "tool_use"]
        if event.get("type") == "result":
            if event.get("is_error") is False and event.get("subtype") == "success":
                structured = event.get("structured_output")
                completed = True
                text = json.dumps(structured) if isinstance(structured, dict) else event.get("result")
            else:
                errored = True
    if parse_errors:
        behavior.append(f"{parse_errors} unparseable event line(s)")
    return {"tools": tools, "calls": calls, "text": text, "completed": completed, "errored": errored,
            "behavior": behavior}
