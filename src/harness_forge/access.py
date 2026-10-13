"""Where a harness's file tools and commands reached, judged against the workspace Forge staged.

Workers and runtime cases run in a throwaway workspace. Isolation is
best-effort, so what the harness reports doing is checked here: a
successful file access or command outside that workspace is an observed
violation, whatever the worker answered.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

_GLOB_CHARACTERS = re.compile(r"[*?\[{]")
_SHELLS = {"sh", "bash", "zsh", "dash"}
# Clients that send or fetch over the network; a worker's shell has no reason to run them.
NETWORK_CLIENTS = {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "ftp", "rsync", "http",
                   "https", "socat"}


def tool_paths(tool: str, arguments: dict) -> list[str]:
    """Every location a Claude file tool can reach with these arguments; other tools name no path.

    Glob searches under ``path`` unless its pattern is absolute, so the pattern's directory part counts
    too; Grep also accepts ``file_path``.
    """
    def text(key):
        value = arguments.get(key)
        return value if isinstance(value, str) and value else None

    if tool == "Read":
        return [path for path in (text("file_path"),) if path]
    if tool == "Grep":
        return [path for path in (text("path"), text("file_path")) if path]
    if tool == "Glob":
        base, pattern = text("path") or ".", text("pattern") or ""
        prefix = _GLOB_CHARACTERS.split(pattern, maxsplit=1)[0]
        directory = os.path.dirname(prefix) if prefix != pattern else prefix
        if os.path.isabs(pattern):
            return [directory or os.sep]  # an absolute pattern replaces path as the search directory
        # A relative pattern, also one starting with ~, searches under path.
        return [base, os.path.join(base, directory)] if directory else [base]
    return []


def inside(path: str, workspace: Path) -> bool:
    """Whether a path stays in the workspace once links resolve; relative paths start there."""
    if path.startswith("~"):
        return False
    try:
        root = os.path.realpath(workspace)
        target = os.path.realpath(os.path.join(root, path))
        return os.path.commonpath([root, target]) == root
    except (ValueError, OSError):  # another drive on Windows, or a path that cannot be resolved
        return False


def _events(stdout: str):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def claude_outside(stdout: str, workspace: Path) -> list[str]:
    """The paths of successful Read, Grep and Glob calls that left the workspace."""
    calls: dict[str, tuple[str, dict]] = {}
    outside = []
    for event in _events(stdout):
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) and isinstance(message.get("content"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if tool_paths(str(block.get("name")), arguments):
                    calls[str(block.get("id"))] = (str(block.get("name")), arguments)
            elif (event.get("type") == "user" and block.get("type") == "tool_result"
                  and block.get("is_error") is not True and str(block.get("tool_use_id")) in calls):
                tool, arguments = calls[str(block["tool_use_id"])]
                outside += [path for path in tool_paths(tool, arguments) if not inside(path, workspace)]
    return outside


def _words(command: str) -> list[str]:
    try:
        words = shlex.split(command)
    except ValueError:
        return command.split()
    if len(words) >= 3 and os.path.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
        return _words(words[2])
    return words


def command_problems(command: str, workspace: Path, home: str) -> list[str]:
    """A network client, or a path in the user's home or above the workspace, in one shell command."""
    problems = []
    for word in _words(command):
        token = word.split("=", 1)[1] if word.startswith("-") and "=" in word else word
        if os.path.basename(token) in NETWORK_CLIENTS:
            problems.append(f"network client {os.path.basename(token)}")
        elif token.startswith("~") or (os.path.isabs(token) and not inside(token, workspace)
                                       and os.path.commonpath([os.path.abspath(token), home]) == home):
            problems.append(token)
        elif ".." in Path(token).parts and not inside(token, workspace):
            problems.append(token)
    return problems


def codex_outside(stdout: str, workspace: Path, home: str) -> list[str]:
    """What successful Codex commands reached outside the workspace."""
    found = []
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if (event.get("type") == "item.completed" and item.get("type") == "command_execution"
                and item.get("exit_code") == 0 and isinstance(item.get("command"), str)):
            found += command_problems(item["command"], workspace, home)
    return found
