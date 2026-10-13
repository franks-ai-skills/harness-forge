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
# Clients that send or fetch over the network; named in the reason when a worker runs one.
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


_OPERATORS = {";", "|", "&&", "||", "&", "|&"}
DEVICES = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"}
# What a worker's shell may run: commands that only read and print. Anything else, a shell, a wrapper
# such as env or xargs, or a form that runs code (command substitution, sed's e and w), cannot be verified
# from the event and fails the worker; a rerun is cheaper than trusting it.
READERS = {"cat", "nl", "head", "tail", "sed", "grep", "rg", "ls", "wc", "sort", "uniq", "cut", "tr", "diff",
           "echo", "printf", "pwd", "true"}
_TEXT_ARGUMENTS = {"echo", "printf", "pwd", "true", "tr"}
_PATTERN_FIRST = {"grep", "rg"}
_PATTERN_OPTIONS = {"-e", "--regexp"}
# sed scripts that only print: a line range with p, or a substitution whose flags are g, p, I or a number.
_SED_PRINT = re.compile(r"^(\d+|\$)?(,(\d+|\$))?p$|^s(.)(?:(?!\4).)*\4(?:(?!\4).)*\4[gpI0-9]*$")


def _scan(script: str) -> tuple[str, bool]:
    """The script with unquoted newlines as `;`, and whether it substitutes a command outside single quotes."""
    out, quote, substitutes, index = [], None, False, 0
    while index < len(script):
        character = script[index]
        if quote == "'":
            quote = None if character == "'" else quote
        elif character == "\\" and index + 1 < len(script):
            out.append(script[index:index + 2])
            index += 2
            continue
        elif quote == '"':
            if character == '"':
                quote = None
            elif character == "`" or script.startswith("$(", index):
                substitutes = True
        elif character in "'\"":
            quote = character
        elif character == "`" or script.startswith(("$(", "<(", ">("), index):
            substitutes = True
        elif character == "\n":
            out.append(";")
            index += 1
            continue
        out.append(character)
        index += 1
    return "".join(out), substitutes


def _script(command: str) -> str:
    """The script of the `zsh -lc '...'` wrapper Codex runs every command in; other lines as they are."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if len(words) == 3 and os.path.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
        return words[2]
    return command


def simple_commands(command: str) -> tuple[list[list[str]], bool]:
    """The words of each simple command a line runs, split at ;, |, &&, ||, &, ( ), ) and unquoted newlines,
    and whether it substitutes a command. A line the lexer cannot split is one command of its words."""
    script, substitutes = _scan(_script(command))
    lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token in _OPERATORS or (token and set(token) <= set(";|&()")):
                segments.append([])
            else:
                segments[-1].append(token)
    except ValueError:
        return [script.split()], substitutes
    return [words for words in segments if words], substitutes


def _operands(name: str, arguments: list[str]) -> tuple[list[str], list[str]]:
    """(paths, sed scripts) among a reader's arguments; options and search patterns are neither."""
    paths, scripts, explicit, skip_next = [], [], False, False
    for word in arguments:
        if skip_next:
            (scripts if name == "sed" else []).append(word)
            skip_next = False
            continue
        if name == "sed" and word in ("-e", "--expression") or name in _PATTERN_FIRST and word in _PATTERN_OPTIONS:
            explicit, skip_next = True, True
            continue
        if word.startswith("-") and word != "-":
            continue
        paths.append(word)
    if name == "sed" and not explicit and paths:
        scripts.append(paths.pop(0))
    elif name in _PATTERN_FIRST and not explicit and paths:
        paths.pop(0)  # the search pattern
    return paths, scripts


def _outside(path: str, workspace: Path) -> bool:
    """A path operand outside the workspace; a glob is judged by its root, existing or not."""
    if path in DEVICES or path in ("-", ">", ">>", "<", "2", "1"):
        return False
    root = _GLOB_CHARACTERS.split(path, maxsplit=1)[0]
    if root != path:
        root = os.path.dirname(root) or "."
    return root.startswith("~") or not inside(root, workspace)


def command_problems(command: str, workspace: Path, home: str = "") -> list[str]:
    """Why a worker's shell line cannot be accepted: a network client, a command that is not a reader or
    cannot be verified, or a path outside the workspace. Paths are judged by name, not by whether the file
    exists afterwards."""
    commands, substitutes = simple_commands(command)
    problems = ["command substitution"] if substitutes else []
    for words in commands:
        while words and "=" in words[0] and words[0].split("=", 1)[0].isidentifier():
            words = words[1:]  # VAR=value before the command
        if not words:
            continue
        name = os.path.basename(words[0])
        if name in NETWORK_CLIENTS:
            problems.append(f"network client {name}")
            continue
        if name not in READERS:
            problems.append(f"{name} is not a reading command")
            continue
        if name == "sed" and any(word == "-i" or word.startswith(("-i", "--in-place")) for word in words[1:]):
            problems.append("sed -i writes")
        paths, scripts = _operands(name, words[1:])
        problems += [f"sed script {script!r} may run or write" for script in scripts if not _SED_PRINT.match(script)]
        if name not in _TEXT_ARGUMENTS:
            problems += [path for path in paths if _outside(path, workspace)]
    return problems


def codex_outside(stdout: str, workspace: Path, home: str = "") -> list[str]:
    """What Codex commands did that the worker may not.

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
        if item.get("exit_code") == 0 or len(simple_commands(item["command"])[0]) > 1:
            found += command_problems(item["command"], workspace, home)
    return found
