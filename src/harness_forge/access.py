# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
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


def inside(path: str, workspace: Path, *, literal: bool = False) -> bool:
    """Whether a path stays in the workspace once links resolve; relative paths start there.

    A tool's ~ path means the home folder; a ``literal`` word from the shell grammar, where ~ was quoted,
    is a file name like any other."""
    if path.startswith("~") and not literal:
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


# A Codex worker's shell lines are accepted only in this grammar, never by emulating the shell: bare
# reading commands with known options, plain path operands inside the workspace, quotes without
# expansion, and ;, | or a newline between commands. Whatever it does not cover fails the worker; a
# rerun of the reviewer is cheaper than trusting a line Forge cannot read exactly.
_SAFE = re.compile(r"[A-Za-z0-9._/,=:+@%-]")
# The wrapper Codex runs commands in; a program elsewhere named like a shell is not unwrapped.
_SYSTEM_SHELLS = {"/bin/zsh", "/bin/bash", "/bin/sh", "/bin/dash", "/usr/bin/zsh", "/usr/bin/bash", "/usr/bin/sh"}
_DEVNULL = "2>/dev/null"
_NUMBER = re.compile(r"^\d+$")
# Per command: options without a value, options that take one (as the next word or attached), and
# what its positional words are: paths, a pattern then paths, a sed script then paths, or text.
# Not sort, which can spill its input into temporary files, nor diff, which picks files in a directory itself.
_COMMANDS = {
    "cat": ({"-n", "-b"}, set(), "paths"),
    "nl": ({"-ba"}, {"-b"}, "paths"),
    "head": (set(), {"-n"}, "paths"),
    "tail": (set(), {"-n"}, "paths"),
    "wc": ({"-l", "-w", "-c", "-m"}, set(), "paths"),
    "ls": ({"-l", "-a", "-la", "-al", "-1", "-R"}, set(), "paths"),
    "uniq": ({"-c", "-d", "-u"}, set(), "one path"),
    "cut": (set(), {"-d", "-f"}, "paths"),
    "grep": ({"-n", "-i", "-F", "-E", "-w", "-c", "-l", "-H", "-h", "-r", "-v", "-o", "-s"},
             {"-A", "-B", "-C", "-m", "-e", "--regexp"}, "pattern"),
    "rg": ({"-n", "-i", "-F", "-w", "-c", "-l", "-H", "-v", "-o", "-N", "--no-heading", "--hidden"},
           {"-A", "-B", "-C", "-m", "-e", "--regexp", "-g", "--glob"}, "pattern"),
    "sed": ({"-n"}, {"-e"}, "script"),
    "tr": ({"-d", "-s"}, set(), "text"),
    # Not printf: its %n assigns to a variable named by an argument, such as PATH.
    "echo": ({"-n"}, set(), "text"),
    "pwd": (set(), set(), "text"),
    "true": (set(), set(), "text"),
}
_NUMERIC = {"-n", "-A", "-B", "-C", "-m"}
_PATTERN_OPTIONS = {"-e", "--regexp"}
# sed scripts that only print: a line range with p, or s/// with g, p, I or a number as flags.
_SED_PRINT = re.compile(r"^(\d+|\$)?(,(\d+|\$))?p$|^s/(?:[^/\\]|\\.)*/(?:[^/\\]|\\.)*/[gpI0-9]*$")


class Unreadable(ValueError):
    """A shell line outside the grammar Forge can read exactly."""


def _script(command: str) -> str:
    """The script of the `zsh -lc '...'` wrapper Codex runs every command in; other lines as they are."""
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise Unreadable(f"unbalanced quoting: {error}") from error
    if len(words) == 3 and os.path.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
        if words[0] not in _SYSTEM_SHELLS:
            raise Unreadable(f"a shell at {words[0]}")
        return words[2]
    return command


def _commands(script: str) -> list[list[str]]:
    """The words of each command, split at unquoted ;, | and newlines; 2>/dev/null dropped.

    A pipe needs a command on both sides; its state is kept apart from the words, so a quoted "|" stays
    a word."""
    commands: list[list[str]] = [[]]
    pending = False  # a | that still waits for its command
    index, length = 0, len(script)
    while index < length:
        character = script[index]
        if character in " \t":
            index += 1
        elif script.startswith("||", index):
            raise Unreadable("||")
        elif character in ";\n":
            if pending:
                raise Unreadable("a pipe without a command after it")
            commands.append([])
            index += 1
        elif character == "|":
            if not commands[-1]:
                raise Unreadable("a pipe without a command before it")
            commands.append([])
            pending = True
            index += 1
        elif script.startswith(_DEVNULL, index) and (index + len(_DEVNULL) == length
                                                     or script[index + len(_DEVNULL)] in " \t;\n|"):
            index += len(_DEVNULL)
        else:
            word = ""
            while index < length and script[index] not in " \t;\n|":
                character = script[index]
                if character in "'\"":
                    end = script.find(character, index + 1)
                    if end < 0:
                        raise Unreadable("an unclosed quote")
                    inner = script[index + 1:end]
                    if character == '"' and any(mark in inner for mark in "$`\\"):
                        raise Unreadable("an expansion inside double quotes")
                    word += inner
                    index = end + 1
                elif character == "=" and not word:
                    raise Unreadable("a word starting with = outside quotes, which zsh expands to a program's path")
                elif _SAFE.match(character):
                    word += character
                    index += 1
                else:
                    raise Unreadable(f"the character {character!r} outside quotes")
            commands[-1].append(word)
            pending = False
    if pending:
        raise Unreadable("a pipe without a command after it")
    return [words for words in commands if words]


def _arguments(name: str, words: list[str]) -> tuple[list[str], list[str]]:
    """(positional words, option values that are patterns or scripts) after checking every option."""
    flags, valued, _ = _COMMANDS[name]
    positionals, given = [], []
    index = 0
    while index < len(words):
        word = words[index]
        if not word.startswith("-") or word == "-":
            positionals.append(word)
        elif positionals:
            # BSD readers stop reading options at the first file, so a later -x would be a file name.
            raise Unreadable(f"{name} option {word} after a file")
        else:
            option, value = (word.split("=", 1) + [None])[:2] if word.startswith("--") else (word, None)
            attached = next((prefix for prefix in sorted(valued, key=len, reverse=True)
                             if not prefix.startswith("--") and word.startswith(prefix) and word != prefix), None)
            if word in flags:
                pass
            elif option in valued and value is not None:
                given.append((option, value))
            elif word in valued:
                if index + 1 >= len(words):
                    raise Unreadable(f"{name} {word} needs a value")
                index += 1
                given.append((word, words[index]))
            elif attached:
                given.append((attached, word[len(attached):]))
            else:
                raise Unreadable(f"{name} option {word} is not one Forge can read")
        index += 1
    for option, value in given:
        if option in _NUMERIC and not _NUMBER.fullmatch(value):
            raise Unreadable(f"{name} {option} needs a number")
        if name == "nl" and option == "-b" and value != "a":
            raise Unreadable("nl -b takes only a")
    return positionals, [value for option, value in given if option in _PATTERN_OPTIONS or name == "sed"]


def _path_problem(path: str, workspace: Path) -> str | None:
    if path.startswith("-"):
        return f"{path}: a path may not start with -"
    if ".." in Path(path).parts:
        return f"{path}: leaves the directory with .."
    if not inside(path, workspace, literal=True):  # resolved through links, which may point out of it
        return f"{path}: outside the workspace"
    return None


def command_problems(command: str, workspace: Path, home: str = "") -> list[str]:
    """Why a worker's shell line cannot be accepted, or nothing when the grammar covers all of it."""
    try:
        commands = _commands(_script(command))
    except Unreadable as error:
        return [f"unreadable command: {error}"]
    problems = []
    for words in commands:
        name = words[0]
        if os.path.basename(name) in NETWORK_CLIENTS:
            problems.append(f"network client {os.path.basename(name)}")
            continue
        if name not in _COMMANDS:
            problems.append(f"{name} is not a reading command Forge accepts")
            continue
        try:
            positionals, values = _arguments(name, words[1:])
        except Unreadable as error:
            problems.append(str(error))
            continue
        kind = _COMMANDS[name][2]
        if kind == "text":
            continue
        if kind == "pattern" and not values:
            if not positionals:
                problems.append(f"{name} needs a pattern")
                continue
            positionals = positionals[1:]
        if kind == "script":
            if "-n" not in words[1:]:
                problems.append("sed must run with -n")
            if values:
                scripts = values  # given with -e
            else:
                scripts, positionals = positionals[:1], positionals[1:]
            if not scripts:
                problems.append("sed needs a script")
            problems += [f"sed script {script!r} may run or write" for script in scripts
                         if not _SED_PRINT.fullmatch(script)]
        if kind == "one path" and len(positionals) > 1:
            problems.append(f"{name} with a second file writes to it")
        problems += [problem for problem in (_path_problem(path, workspace) for path in positionals) if problem]
    return problems


def codex_outside(stdout: str, workspace: Path, home: str = "") -> list[str]:
    """Every Codex command line the grammar does not accept, whatever its exit code: a failed command can
    already have read or sent something, and an attempt is an observed violation too."""
    found = []
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if (event.get("type") == "item.completed" and item.get("type") == "command_execution"
                and isinstance(item.get("command"), str)):
            found += command_problems(item["command"], workspace, home)
    return found
