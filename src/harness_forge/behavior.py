# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Behavior tests of a built skill: what it does once it runs (#54).

Each case runs the target harness once in a fresh workspace that holds the
part's frozen files, the dependencies the case names and its fixtures,
never a copy of the checkout. The harness itself limits writes to the
paths the case declares while it runs: Claude Code through `Edit` path
rules in `dontAsk` mode, with reads blocked outside the working directory
and no shell; Codex through Forge's permission profile, which reads only
the workspace and writes only the declared paths, without network. A
comparison of the workspace before and after the run is further evidence,
not the limit: a file written and removed again leaves no trace in it.

A pass needs the candidate's observed activation, as an activation case
sees it, besides every assertion: files alone could come from the
fixtures. A requirement the harness cannot observe makes the case
`unavailable`; incomplete evidence makes it `error`. Neither passes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Mapping

from harness_forge.adapters import codex
from harness_forge.adapters.base import Completed, Runner, worker_environment
from harness_forge.documents import canonical_digest, fold_name, safe_path

BEHAVIOR = "behavior"
# The tools a Claude behavior case runs with: reading, the skill and file edits, never a shell or the web.
CLAUDE_TOOLS = ("Skill", "Read", "Grep", "Glob", "Write", "Edit")
# Codex items a behavior case may produce; web search and MCP stay off.
CODEX_ITEMS = ("command_execution", "file_change")
# Names a case's `no_tools` may use, per harness: anything else is a typo or another harness's tool.
KNOWN_TOOLS = {
    "claude-code": {"Agent", "Bash", "Edit", "Glob", "Grep", "MultiEdit", "NotebookEdit", "Read", "Skill", "Task",
                    "TodoWrite", "WebFetch", "WebSearch", "Write"},
    "codex": {"command_execution", "file_change", "mcp_tool_call", "web_search"},
}
# permissions.blockReadsOutsideWorkingDirectories, which keeps Claude's file tools in the workspace.
CLAUDE_READ_BLOCK = (2, 1, 257)
_PROTECTED = {".agents", ".claude", ".codex"}
_INSTRUCTION_FILES = {"agents.md", "agents.override.md", "claude.md", "claude.local.md"}
_GLOB = re.compile(r"[*?\[\]]")
# A declared write lands inside a Claude permission rule and a Codex profile key: only these characters, so that
# no path can close the rule and add another one ("out),Write,Edit(other").
_WRITE_PART = re.compile(r"[A-Za-z0-9._-]+")
GAPS = {
    "claude-code": [
        "Claude Code's file tools are limited by its permission rules, not by an operating-system sandbox",
        "denials are those Claude Code reports for its own tool calls",
    ],
    "codex": [
        "a write made through a shell command shows only in the before-and-after comparison, not as a tool call",
        "refused file operations inside a shell command are not reported, so no_denied cannot be checked",
    ],
}
COMMON_GAP = "a file written and removed again during the run is not in the before-and-after comparison"
OUTPUT_GAP = ("absence from recorded output does not prove a file was never read, nor that unreported or truncated "
              "output did not contain the text")
# Claude Code's file tools and the input fields that name a path (#112). Grep's pattern is what it searches for.
FILE_TOOLS = {"Read": ("file_path",), "Edit": ("file_path",), "Write": ("file_path",), "Grep": ("path",),
              "Glob": ("path", "pattern")}


def plain_path(path: str) -> bool:
    """A relative path without empty, dot or dot-dot parts, backslashes or control characters."""
    parts = path.split("/")
    return (bool(path) and "\\" not in path and not path.startswith("/")
            and all(part not in ("", ".", "..") for part in parts)
            and not any(ord(character) < 0x20 or ord(character) == 0x7f for character in path))


def write_scope(entry: str) -> tuple[str, bool] | None:
    """(path, subtree) for a declared write: an exact relative path, or a directory with a final ``/**``."""
    subtree = entry.endswith("/**")
    path = entry[:-3] if subtree else entry
    if not plain_path(path) or _GLOB.search(path) or not all(_WRITE_PART.fullmatch(part) for part in path.split("/")):
        return None
    return path, subtree


def permits(path: str, writes: Iterable[str]) -> bool:
    """Whether a changed path lies in a declared write; ``d/**`` also covers creating ``d`` itself."""
    for entry in writes:
        scope = write_scope(entry)
        if scope is None:
            continue
        base, subtree = scope
        if path == base or (subtree and path.startswith(base + "/")):
            return True
    return False


# The repository a case's git fixture makes: fixed identity and time, so commit ids are the same on every run.
FIXTURE_IDENTITY = ("Forge Fixture", "fixture@example.invalid")
FIXTURE_TIME = "2026-01-01T00:00:00+0000"
INDEX_GAP = "a change of .git/index, its deletion included, is not observed (a plain git status may rewrite it)"
OUTSIDE_GAP = "a change made outside the workspace and removed again before the run ends is not seen"


def _plain(path: str) -> bool:
    parts = path.split("/")
    return bool(path) and "\\" not in path and not any(part in ("", ".", "..") for part in parts) and not any(
        ord(character) < 0x20 or ord(character) == 0x7f for character in path)


def layout_problem(case: dict, protected: Iterable[str]) -> str | None:
    """Why the case's fixtures, commits and links cannot be laid out safely, or None (#111).

    Checked before anything is created: every path plain; no path twice across the staged files, the fixtures,
    the commits' files, the link paths and the workspace link targets (one path in several commits is history);
    nothing below a declared link or a file; with a git fixture, nothing the case provides under .git, and no
    write that could reach it.
    """
    from harness_forge.runtime import fixture_problem

    commits = (case.get("git") or {}).get("commits") or []
    links = case.get("links") or {}
    kinds: dict[str, str] = {}

    def claim(path: str, kind: str) -> str | None:
        folded = fold_name(path)
        if folded in kinds and not (kind == kinds[folded] == "commit"):
            return f"{path} is declared twice: as {kinds[folded]} and as {kind}"
        kinds[folded] = kind
        return None

    for path in protected:
        kinds[fold_name(path)] = "staged"
    named = [(path, "fixture") for path in case.get("fixtures") or {}]
    named += [(path, "commit") for commit in commits for path in commit["files"]]
    named += [(path, "link") for path in links]
    named += [(link["path"], "link target") for link in links.values() if link["in"] == "workspace"]
    for path, kind in named:
        if not _plain(path):
            return f"{kind} {path!r} is not a plain relative path"
        problem = fixture_problem(path, protected) if kind != "link target" or path not in protected else None
        if problem or (kind == "link target" and fold_name(path) in {fold_name(p) for p in protected}):
            return problem or f"link target {path} would replace a staged file"
        if commits and fold_name(path.split("/")[0]) == ".git":
            return f"{kind} {path} lies under .git, which a git fixture keeps to Forge"
        problem = claim(path, kind)
        if problem:
            return problem
    outside: dict[str, str] = {}
    for link in links.values():
        if link["in"] != "outside":
            continue
        if not _plain(link["path"]):
            return f"outside link target {link['path']!r} is not a plain relative path"
        folded = fold_name(link["path"])
        if folded in outside:
            return f"outside link target {link['path']} is declared twice"
        outside[folded] = link["kind"]
    # A target below a file cannot be made, and one at or below a missing target would make it present.
    for space, targets in (("outside", outside), ("workspace", {
            fold_name(link["path"]): link["kind"] for link in links.values() if link["in"] == "workspace"})):
        paths = set(targets) | (set(kinds) if space == "workspace" else set())
        for key in paths:
            parts = key.split("/")
            for count in range(1, len(parts)):
                above = "/".join(parts[:count])
                if targets.get(above) == "file":
                    return f"{space} path {key} lies below the link target {above}, which is a file"
                if targets.get(above) == "missing":
                    return f"{space} path {key} would make the missing link target {above} present"
    folded_links = {fold_name(path) for path in links}
    files = {key for key, kind in kinds.items() if kind != "link target"} | {
        fold_name(link["path"]) for link in links.values() if link["in"] == "workspace" and link["kind"] == "file"}
    for key in kinds:
        parts = key.split("/")
        for count in range(1, len(parts)):
            above = "/".join(parts[:count])
            if above in folded_links:
                return f"{key} lies below the declared link {above}"
            if above in files:
                return f"{key} lies below {above}, which is a file"
    if commits:
        for entry in case.get("writes") or []:
            scope = write_scope(entry)
            if scope and fold_name(scope[0]).split("/")[0] == ".git":
                return f"write {entry} would reach .git, which a git fixture keeps to Forge"
    return None


def _git_environment(home: Path) -> dict[str, str]:
    """A git environment built from nothing: no caller GIT_* variable, configuration, template or hook applies."""
    name, email = FIXTURE_IDENTITY
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_DATE": FIXTURE_TIME,
            "GIT_COMMITTER_DATE": FIXTURE_TIME, "LC_ALL": "C"}


def make_history(workspace: Path, commits: list[dict], home: Path) -> str | None:
    """The case's commits, one at a time: each one's files written, staged and committed before the next.

    Returns why the history could not be made, or None. Local commands only: nothing reaches a network.
    """
    environment = _git_environment(home)
    base = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]

    def git(*arguments: str) -> str | None:
        try:
            completed = subprocess.run([*base, *arguments], cwd=workspace, env=environment, capture_output=True,
                                       text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return f"git could not run: {error}"
        return None if completed.returncode == 0 else f"git {arguments[0]} failed: {completed.stderr.strip()[:300]}"

    problem = git("init", "-q", "--template=", "-b", "main")
    if problem:
        return problem
    for commit in commits:
        for path, text in commit["files"].items():
            destination = safe_path(workspace, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        problem = (git("--literal-pathspecs", "add", "--", *commit["files"])
                   or git("commit", "-q", "--no-verify", "-m", commit["message"]))
        if problem:
            return problem
    return None


def make_links(workspace: Path, outside: Path, links: Mapping[str, dict]) -> None:
    """Each declared link and its target: an empty file, an empty directory, or none for a dangling link."""
    for path, link in links.items():
        base = workspace if link["in"] == "workspace" else outside
        target = safe_path(base, link["path"])
        if link["kind"] == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        elif link["kind"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
        location = safe_path(workspace, path)
        location.parent.mkdir(parents=True, exist_ok=True)
        location.symlink_to(os.path.relpath(target, location.parent))


def case_problem(case: dict, harness: str, protected: Iterable[str]) -> str | None:
    """Why a behavior case cannot run as declared in ``harness``, or None.

    ``protected`` are the staged candidate and dependency paths: no declared
    write may reach them, the harness directories or an instruction file.
    """
    problem = layout_problem(case, protected)
    if problem:
        return problem
    taken = {fold_name(path) for path in protected}
    for entry in case.get("writes") or []:
        scope = write_scope(entry)
        if scope is None:
            return (f"write {entry!r} is neither an exact relative path nor a directory followed by /**, made of "
                    "letters, digits, dots, hyphens and underscores: the forms both harnesses can enforce")
        base, _ = scope
        folded = fold_name(base)
        parts = folded.split("/")
        if parts[0] in _PROTECTED or parts[-1] in _INSTRUCTION_FILES:
            return f"write {entry} would reach a harness directory or an instruction file"
        if any(path == folded or path.startswith(folded + "/") or folded.startswith(path + "/") for path in taken):
            return f"write {entry} would reach a staged skill or dependency file, which stay read-only"
    for item in (case.get("expect") or {}).get("files") or []:
        target = item.get("path") or item.get("glob")
        if not plain_path(target):
            return f"file assertion {target!r} is not a plain relative path"
        if item.get("absent") and set(item) - {"path", "glob", "absent"}:
            return f"file assertion {target}: absent cannot be combined with other checks"
    unknown = sorted(set(((case.get("expect") or {}).get("actions") or {}).get("no_tools") or [])
                     - KNOWN_TOOLS[harness])
    if unknown:
        return f"no_tools names {', '.join(unknown)}, which {harness} does not have"
    for key in ("no_commands_matching", "no_file_requests_matching"):
        for pattern in ((case.get("expect") or {}).get("actions") or {}).get(key) or []:
            try:
                re.compile(pattern)
            except re.error as error:
                return f"{key} {pattern!r} is not a regular expression: {error}"
    return None


def unsupported(case: dict, harness: str, version: str) -> str | None:
    """An observation or limit this harness cannot provide for the case, or None."""
    actions = (case.get("expect") or {}).get("actions") or {}
    if harness == "codex" and actions.get("no_denied"):
        return "Codex does not report refused file operations inside its commands, so no_denied cannot be observed"
    if harness == "codex" and actions.get("no_file_requests_matching"):
        return ("Codex reads files only through shell commands and records no file-tool request, so "
                "no_file_requests_matching cannot be observed")
    if harness == "codex":
        # Codex's sandbox grants a whole subtree for a directory or a path that does not exist yet, and a command
        # can turn a granted file into a directory before the next command: an exact write is never exact there.
        exact = [entry for entry in case.get("writes") or [] if (scope := write_scope(entry)) and not scope[1]]
        if exact:
            return (f"Codex cannot limit a write to the exact path {exact[0]}: its sandbox would grant the whole "
                    f"subtree; declare {exact[0]}/** if that may change, or test the case in Claude Code")
    if harness == "claude-code":
        found = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
        if not found or tuple(int(number) for number in found.groups()) < CLAUDE_READ_BLOCK:
            return ("Claude Code before 2.1.257, or of an unknown version, cannot keep file reads in the workspace "
                    f"(found {version or 'unknown'})")
    return None


def snapshot(root: Path) -> dict[str, str]:
    """Every entry under ``root`` without following links: ``dir``, ``link:<target>`` or ``sha256:<hex>``."""
    entries: dict[str, str] = {}

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as found:
            for entry in sorted(found, key=lambda item: item.name):
                relative = f"{prefix}{entry.name}"
                if entry.is_symlink():
                    entries[relative] = f"link:{os.readlink(entry.path)}"
                elif entry.is_dir(follow_symlinks=False):
                    entries[relative] = "dir"
                    walk(Path(entry.path), relative + "/")
                elif entry.is_file(follow_symlinks=False):
                    with open(entry.path, "rb") as handle:
                        entries[relative] = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
                else:
                    entries[relative] = "other"

    walk(root, "")
    return entries


def changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[tuple[str, str]]:
    """(``added`` | ``removed`` | ``changed``, path) for every entry that differs, in path order."""
    found = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            found.append(("added", path))
        elif path not in after:
            found.append(("removed", path))
        elif before[path] != after[path]:
            found.append(("changed", path))
    return found


def _events(stdout: str) -> tuple[list[dict], int]:
    events, broken = [], 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            broken += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            broken += 1
    return events, broken


def _short(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return " ".join(text.split())[:300]


def observe_claude(stdout: str) -> dict:
    """Every tool request when it was made, its result, and the denials Claude Code reported."""
    events, broken = _events(stdout)
    requests: dict[str, dict] = {}
    denied: list[str] = []
    outputs: list[tuple[str, str]] = []
    unreadable: list[str] = []
    complete = False
    for event in events:
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) and isinstance(message.get("content"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if str(block.get("id")) in requests:
                    broken += 1
                # The input as recorded, also when malformed: a path assertion must not read it as empty (#112).
                requests[str(block.get("id"))] = {"tool": str(block.get("name")), "input": arguments, "error": None,
                                                  "answered": False, "raw": block.get("input")}
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                request = requests.get(str(block.get("tool_use_id")))
                if request is None or request["answered"]:
                    # A result for no request, or a second one, breaks the correlation.
                    broken += 1
                    continue
                request["answered"] = True
                content = block.get("content")
                if isinstance(content, str):
                    outputs.append((request["tool"], content))
                elif isinstance(content, list) and all(isinstance(item, dict) for item in content):
                    outputs.append((request["tool"], "\n".join(str(item.get("text", "")) for item in content
                                                               if item.get("type", "text") == "text")))
                    if any(item.get("type", "text") != "text" for item in content):
                        unreadable.append(request["tool"])
                elif content is not None:
                    unreadable.append(request["tool"])
                if block.get("is_error") is True:
                    content = block.get("content")
                    request["error"] = _short(content if isinstance(content, str) else
                                              " ".join(item.get("text", "") for item in content or []
                                                       if isinstance(item, dict)))
        if event.get("type") == "result":
            complete = event.get("subtype") == "success" and event.get("is_error") is False
            for denial in event.get("permission_denials") or []:
                if isinstance(denial, dict):
                    denied.append(f"{denial.get('tool_name')} {_short(denial.get('tool_input'))}")
    for request in requests.values():
        if request["error"] and "permission" in request["error"].lower():
            entry = f"{request['tool']} {_short(request['input'])}"
            if entry not in denied:
                denied.append(entry)
    unanswered = [request for request in requests.values() if not request["answered"]]
    return {"requests": [f"{request['tool']} {_short(request['input'])}"
                         + (f" (error: {request['error']})" if request["error"] else "")
                         + ("" if request["answered"] else " (no result)") for request in requests.values()],
            "tools": [request["tool"] for request in requests.values()], "commands": [], "denied": denied,
            "file_requests": [{"tool": request["tool"], "input": request["raw"]} for request in requests.values()
                              if request["tool"] in FILE_TOOLS],
            "outputs": outputs, "unreadable": unreadable,
            "complete": complete and not broken and not unanswered, "broken": broken}


def observe_codex(stdout: str) -> dict:
    """Commands and file changes when Codex started or finished them, with how they ended."""
    events, broken = _events(stdout)
    items: dict[str, dict] = {}
    complete = errored = False
    for event in events:
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        if item is not None and event.get("type") in ("item.started", "item.completed", "item.updated"):
            key = str(item.get("id") or len(items))
            kept = items.setdefault(key, {"type": str(item.get("type")), "item": {}, "finished": False})
            kept["item"] = {**kept["item"], **item}
            kept["finished"] = kept["finished"] or event.get("type") == "item.completed"
        if event.get("type") == "turn.completed":
            complete = True
        if event.get("type") in ("turn.failed", "error"):
            errored = True
    requests, tools, commands, outputs, unreadable = [], [], [], [], []
    for kept in items.values():
        kind, item = kept["type"], kept["item"]
        if kind == "command_execution" and kept["finished"]:
            if isinstance(item.get("aggregated_output"), str):
                outputs.append((f"command {_short(item.get('command', ''))}", item["aggregated_output"]))
            else:
                unreadable.append(f"command {_short(item.get('command', ''))}")
        if kind in ("agent_message", "reasoning", "todo_list"):
            continue
        tools.append(kind)
        if kind == "command_execution":
            commands.append(str(item.get("command", "")))
            requests.append(f"command {_short(item.get('command', ''))} ({item.get('status', 'unknown')}, exit "
                            f"{item.get('exit_code', 'unknown')})")
        elif kind == "file_change":
            paths = [str(change.get("path")) for change in item.get("changes") or [] if isinstance(change, dict)]
            requests.append(f"file change {', '.join(paths) or 'unknown'} ({item.get('status', 'unknown')})")
        else:
            requests.append(f"{kind} {_short(item)}")
    # A command or file change counts only once Codex finished it; one still in progress leaves the record open.
    unfinished = [kept for kept in items.values() if kept["type"] in CODEX_ITEMS
                  and (not kept["finished"] or kept["item"].get("status") in (None, "in_progress"))]
    return {"requests": requests, "tools": tools, "commands": commands, "denied": [], "file_requests": [],
            "outputs": outputs, "unreadable": unreadable,
            "complete": complete and not errored and not broken and not unfinished, "broken": broken}


def evaluate(case: dict, harness: str, workspace: Path, after: Mapping[str, str], observed: dict) -> list[str]:
    """The case's failed assertions on the saved files and the records; empty when all hold."""
    failed = []
    expect = case.get("expect") or {}
    regular = {path for path, value in after.items() if value.startswith("sha256:")}
    for item in expect.get("files") or []:
        target = item.get("path") or item.get("glob")
        if "path" in item:
            entries = [target] if target in after else []
        else:
            entries = sorted(path for path in after if fnmatch.fnmatchcase(path, target))
        if item.get("absent"):
            if entries:
                failed.append(f"{target}: expected absent, found {', '.join(entries[:5])}")
            continue
        if not entries:
            # A content assertion over no file proves nothing.
            failed.append(f"{target}: no file found")
            continue
        for path in entries:
            if path not in regular:
                failed.append(f"{path}: not a regular file ({after[path].split(':', 1)[0]}), not inspected")
                continue
            # `exists` holds by now; every other check given beside it is evaluated too.
            text = safe_path(workspace, path).read_bytes().decode("utf-8", "replace")
            if "equals" in item and text != item["equals"]:
                failed.append(f"{path}: content differs from the expected text")
            for needle in item.get("contains") or []:
                if needle not in text:
                    failed.append(f"{path}: does not contain {needle!r}")
            for needle in item.get("not_contains") or []:
                if needle in text:
                    failed.append(f"{path}: contains {needle!r}")
            if "only_in" in item:
                failed += [f"{path}: {problem}" for problem in only_in_problems(text, item["only_in"])]
    actions = expect.get("actions") or {}
    for tool in actions.get("no_tools") or []:
        if tool in observed["tools"]:
            failed.append(f"used {tool}")
    if actions.get("no_denied") and observed["denied"]:
        failed.append(f"denied requests: {'; '.join(observed['denied'][:5])}")
    for pattern in actions.get("no_commands_matching") or []:
        hits = [command for command in observed["commands"] if re.search(pattern, command)]
        if hits:
            failed.append(f"ran a command matching {pattern!r}: {_short(hits[0])}")
    for needle in actions.get("no_output_containing") or []:
        # A match and missing evidence both fail, each with its own reason (#112).
        if not observed["complete"]:
            failed.append(f"absence of {needle!r} cannot be shown: the run's record is incomplete")
        elif observed.get("unreadable"):
            failed.append(f"absence of {needle!r} cannot be shown: the output of {observed['unreadable'][0]} could "
                          "not be read")
        else:
            hits = [source for source, output in observed.get("outputs") or [] if needle in output]
            if hits:
                failed.append(f"{needle!r} appears in recorded output of {hits[0]}")
    for pattern in actions.get("no_file_requests_matching") or []:
        if not observed["complete"]:
            failed.append(f"no file request matching {pattern!r} cannot be shown: the run's record is incomplete")
            continue
        for request in observed.get("file_requests") or []:
            fields = FILE_TOOLS[request["tool"]]
            arguments = request["input"]
            if not isinstance(arguments, dict):
                failed.append(f"cannot read the path of a {request['tool']} request")
                continue
            for field in fields:
                value = arguments.get(field, "." if request["tool"] in ("Grep", "Glob") and field == "path" else None)
                if not isinstance(value, str):
                    failed.append(f"cannot read the {field} of a {request['tool']} request")
                elif re.search(pattern, value):
                    failed.append(f"requested {request['tool']} {value} matching {pattern!r}")
    return failed


def only_in_problems(text: str, spec: dict) -> list[str]:
    """Each text must occur, and every occurrence lie wholly inside the named section, and with ``fenced`` inside
    one fenced block's content there (#112)."""
    from harness_forge.documents import UnreadableMarkdown, markdown_fence_contents, markdown_headings

    level, heading = spec.get("level", 2), spec["heading"]
    try:
        headings = markdown_headings(text)
        fences = markdown_fence_contents(text)
    except UnreadableMarkdown as error:
        return [f"cannot be read as Markdown: {error}"]
    found = [offset for offset, found_level, title in headings if found_level == level and title.strip() == heading]
    if len(found) != 1:
        return [f"the section {'#' * level} {heading} is {'missing' if not found else 'ambiguous'}"]
    start = found[0]
    end = next((offset for offset, found_level, _ in headings if offset > start and found_level <= level), len(text))
    problems = []
    for needle in spec["texts"]:
        positions = [match.start() for match in re.finditer(re.escape(needle), text)] if needle else []
        if not positions:
            problems.append(f"does not contain {needle!r}")
            continue
        for position in positions:
            stop = position + len(needle)
            inside = start <= position and stop <= end
            if inside and spec.get("fenced"):
                inside = any(begin <= position and stop <= finish and start <= begin and finish <= end
                             for begin, finish in fences)
            if not inside:
                where = "a fenced block of " if spec.get("fenced") else ""
                problems.append(f"{needle!r} occurs outside {where}the section {'#' * level} {heading}")
                break
    return problems


def case_digest(case: dict, dependency_hashes: Mapping[str, str]) -> str:
    """The case's whole definition and the bytes of the dependencies it names, for binding its result."""
    definition = {key: value for key, value in case.items() if key != "kind"}
    return canonical_digest({"case": definition, "dependencies": dict(sorted(dependency_hashes.items()))})


def claude_command(writes: Iterable[str], *, off: Iterable[str] = ()) -> list[str]:
    """The activation launch's settings, with file edits allowed only under the declared writes."""
    from harness_forge.runtime import _claude_command

    command = _claude_command(list(CLAUDE_TOOLS), off=off)
    allowed = ["Skill", "Read", "Grep", "Glob"]
    for entry in writes:
        base, subtree = write_scope(entry)
        # Edit rules govern every file-editing tool; Write's own path rules are never consulted.
        allowed.append(f"Edit(./{base}/**)" if subtree else f"Edit(./{base})")
    position = command.index("--allowedTools")
    command[position + 1] = ",".join(allowed)
    settings = json.loads(command[command.index("--settings") + 1])
    settings["permissions"] = {"blockReadsOutsideWorkingDirectories": True}
    command[command.index("--settings") + 1] = json.dumps(settings, separators=(",", ":"))
    return command


def behavior_case(
    harness: str, case: dict, files: Mapping[str, bytes], target: str, prompt: str, *, runner: Runner,
    environ: Mapping[str, str], version: str, baseline: set[str], links: Mapping[str, str] | None = None,
    off: Iterable[str] = (), slash: bool = False, timeout: int = 600, workspace_root: str | None = None,
    keep: Callable[[dict, Completed | None], None] | None = None,
) -> tuple[str, str, str | None, dict]:
    """Run one behavior case; returns (result, reason, observed activation, details).

    ``files`` are the part's frozen files and the case's dependencies. The
    result is pass, fail, error or unavailable.
    """
    from harness_forge.runtime import _staging_problem, judge_run

    fixtures = dict(case.get("fixtures") or {})
    writes = list(case.get("writes") or [])
    dependency_hashes = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()
                         if path in (case.get("dependencies") or [])}
    details = {"failed": [], "changes": [], "requests": [], "denied": [],
               "gaps": [*GAPS[harness], COMMON_GAP], "case_digest": case_digest(case, dependency_hashes)}
    if case.get("git"):
        details["gaps"].append(INDEX_GAP)
    if case.get("links"):
        details["gaps"].append(OUTSIDE_GAP)
    if ((case.get("expect") or {}).get("actions") or {}).get("no_output_containing"):
        details["gaps"].append(OUTPUT_GAP)

    def done(result: str, reason: str, activation: str | None = None, completed: Completed | None = None,
             **context) -> tuple[str, str, str | None, dict]:
        if keep is not None:
            keep({"harness": harness, "target": target, "kind": BEHAVIOR, **context, "outcome": result,
                  "reason": reason, "details": details}, completed)
        return result, reason, activation, details

    problem = case_problem(case, harness, files) or _staging_problem(harness, files, fixtures, [target], links)
    if problem:
        return done("error", problem)
    missing = unsupported(case, harness, version)
    if missing:
        return done("unavailable", f"unavailable: {missing}")
    with tempfile.TemporaryDirectory(prefix="forge-behavior-", dir=workspace_root) as temporary:
        # The run's workspace, and beside it what a declared link may point to outside the workspace (#111).
        workspace, outside = Path(temporary) / "workspace", Path(temporary) / "outside"
        workspace.mkdir()
        outside.mkdir()
        for path, data in files.items():
            destination = safe_path(workspace, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if case.get("git"):
            failed = make_history(workspace, case["git"]["commits"], Path(temporary))
            if failed:
                return done("unavailable", f"unavailable: the git fixture could not be made: {failed}")
        for path, text in fixtures.items():
            destination = safe_path(workspace, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        try:
            make_links(workspace, outside, case.get("links") or {})
        except OSError as error:  # layout_problem rules these out; nothing half made may run
            return done("error", f"the links could not be made: {error}")
        if harness == "claude-code":
            wanted = links if links is not None else {f".claude/skills/{target.rsplit('/', 1)[-1]}": f"../../{target}"}
            for path, link_target in wanted.items():
                link = safe_path(workspace, path)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(link_target)
            command = claude_command(writes, off=off)
            permitted = list(CLAUDE_TOOLS)
            if slash:
                from harness_forge.runtime import stream_input
                command += ["--input-format", "stream-json", "--replay-user-messages"]
                prompt = stream_input(prompt)
        else:
            granted = tuple(str(workspace / write_scope(entry)[0]) for entry in writes)
            command = codex.command("review", str(workspace), structured=False, writes=granted)
            permitted = list(CODEX_ITEMS)
        before, before_outside = snapshot(workspace), snapshot(outside)
        context = {"command": command, "cwd": str(workspace), "stdin": prompt}
        try:
            completed = runner(command, cwd=str(workspace), env=worker_environment(environ, harness), stdin=prompt,
                               timeout=timeout)
        except OSError as error:
            return done("unavailable", "unavailable: the harness could not start", **context, error=str(error))
        after = snapshot(workspace)
        # A plain git status may rewrite the index; every other change below .git counts.
        changed = [(kind, path) for kind, path in changes(before, after)
                   if not (case.get("git") and path == ".git/index")]
        details["changes"] = [f"{kind} {path}" for kind, path in changed]
        # Outside the workspace nothing may change, whatever the declared writes: they never reach there.
        escaped = changes(before_outside, snapshot(outside))
        if escaped:
            details["failed"] = [f"changed outside the workspace through a link: {kind} {path}"
                                 for kind, path in escaped[:50]]
            return done("fail", f"changed outside the workspace through a link: {escaped[0][1]}",
                        completed=completed, **context)
        observed = observe_claude(completed.stdout) if harness == "claude-code" else observe_codex(completed.stdout)
        details["requests"], details["denied"] = observed["requests"][:500], observed["denied"][:500]
        undeclared = [path for _, path in changed if not permits(path, writes)]
        if undeclared:
            details["failed"] = [f"changed outside the declared writes: {path}" for path in undeclared[:50]]
            return done("fail", f"changed outside the declared writes: {', '.join(undeclared[:5])}",
                        completed=completed, **context)
        if completed.timed_out:
            return done("error", f"timed out after {timeout} s", completed=completed, **context)
        if completed.returncode or not observed["complete"]:
            return done("error", "the harness did not complete the case, so its records are incomplete",
                        completed=completed, **context)
        outcome, reason, activation, _ = judge_run(harness, completed, {p: d for p, d in files.items()
                                                                        if p.startswith(target + "/")},
                                                   target, workspace, baseline, "activates", permitted,
                                                   slash=slash, timeout=timeout)
        if outcome == "not-run":
            return done("unavailable", f"unavailable: the candidate's activation was not observed: {reason}",
                        completed=completed, **context)
        if outcome != "pass":
            return done(outcome, f"activation: {reason}", completed=completed, **context)
        details["failed"] = evaluate(case, harness, workspace, after, observed)[:100]
        if details["failed"]:
            return done("fail", f"{len(details['failed'])} assertion(s) failed: {details['failed'][0]}", activation,
                        completed, **context)
        return done("pass", f"activated ({activation}) and every assertion held", activation, completed, **context)
