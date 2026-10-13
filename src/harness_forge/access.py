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


_OPERATORS = {";", "|", "&&", "||", "&", "|&", "\n"}
# Commands that run the next word as the command: the client behind them is what runs.
_WRAPPERS = {"env", "command", "exec", "nohup", "xargs", "sudo", "time", "nice", "timeout", "stdbuf"}
DEVICES = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero", "/dev/urandom"}


def simple_commands(command: str) -> list[list[str]]:
    """The words of each simple command a shell line runs, split at ;, |, &&, || and &, `sh -c '...'` unwrapped.

    A line the lexer cannot split comes back as its whitespace words, one command."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token in _OPERATORS or (token and set(token) <= set(";|&()")):
                segments.append([])  # ( and ) open and close a subshell or a $( ) substitution: a new command
            elif token != "$":
                segments[-1].append(token.lstrip("`"))
    except ValueError:
        return [command.split()]
    commands = []
    for words in segments:
        if len(words) >= 3 and os.path.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
            commands += simple_commands(words[2])
        elif words:
            commands.append(words)
    return commands


def _executables(words: list[str]) -> list[str]:
    """The command a simple command runs, behind assignments and wrappers."""
    names = []
    for word in words:
        if "=" in word and word.split("=", 1)[0].isidentifier():
            continue  # VAR=value before the command
        if os.path.basename(word) in _WRAPPERS or word.startswith("-") or word.isdigit():
            continue
        names.append(word)
        break
    return [os.path.basename(name) for name in names if name]


def command_problems(command: str, workspace: Path, home: str) -> list[str]:
    """Network clients run, and paths reached outside the workspace, in one shell command line.

    A path counts when it starts with ~, leaves the workspace with .., or is
    an absolute path outside it that exists or lies in the user's home; a
    word like a search pattern "/api/" names no file and does not count.
    """
    problems = []
    for words in simple_commands(command):
        problems += [f"network client {name}" for name in _executables(words) if name in NETWORK_CLIENTS]
        for word in words[1:]:
            token = word.split("=", 1)[1] if word.startswith("-") and "=" in word else word
            if not token or token in DEVICES:
                continue
            if token.startswith("~"):
                problems.append(token)
            elif os.path.isabs(token):
                if inside(token, workspace):
                    continue
                in_home = os.path.commonpath([os.path.abspath(token), os.path.abspath(home)]) == os.path.abspath(home)
                if in_home or os.path.lexists(token):
                    problems.append(token)
            elif ".." in Path(token).parts and not inside(token, workspace):
                problems.append(token)
    return problems


def codex_outside(stdout: str, workspace: Path, home: str) -> list[str]:
    """What Codex commands reached outside the workspace.

    A successful command counts; so does a compound line whatever its exit
    code, since a later failure does not show that an earlier part failed.
    A single command that failed reached nothing.
    """
    found = []
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if not (event.get("type") == "item.completed" and item.get("type") == "command_execution"
                and isinstance(item.get("command"), str)):
            continue
        if item.get("exit_code") == 0 or len(simple_commands(item["command"])) > 1:
            found += command_problems(item["command"], workspace, home)
    return found
