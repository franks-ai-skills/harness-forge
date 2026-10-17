# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Runtime tests of a built skill in throwaway workspaces.

Each case runs the target harness once in a fresh workspace that holds
only the part's frozen files and the case's fixtures, with the existing
login and the case's declared tools. Activation counts only when it is
observed: a Skill tool call or skill-file read that Claude completed
without an error, or Codex reading the skill's SKILL.md. No observable
signal is `not-run`, never a pass. Executable components
that stay loaded (user plugins, MCP servers) make a case unavailable; a
competing skill makes its activation test `not-run`.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping


from harness_forge.access import inside, tool_paths
from harness_forge.adapters import codex
from harness_forge.adapters.base import Runner, preflight, run_command, worker_environment
from harness_forge.documents import fold_name, load_document, safe_path
from harness_forge.models import RuntimeResult, VerificationRun

MODES = ("explicit", "implicit", "negative")
EXPECTED = ("activates", "does-not-activate")
RUNTIME_TIMEOUT = 600


# Claude's sandbox covers only Bash and is off unless configured, and its file
# tools are outside it; a case may permit only tools that neither run code,
# write nor reach the network. Codex cases run in its read-only sandbox.
CLAUDE_CONTAINED = ("Skill", "Read", "Grep", "Glob")
_HARNESS_DIRS = {".agents", ".claude", ".codex"}
_INSTRUCTION_FILES = {"agents.md", "agents.override.md", "claude.md", "claude.local.md"}


def fixture_problem(path: str, candidate: Mapping[str, bytes] | None = None) -> str | None:
    """A fixture may not replace a candidate file or add skills, settings or instructions the harness reads.

    Paths must be plain relative paths, compared folded: a "./" prefix, a case
    variant or a Unicode variant reaches the same file on a case-insensitive
    file system.
    """
    parts = path.split("/")
    if "\\" in path or any(part in ("", ".", "..") for part in parts):
        return f"fixture {path} is not a plain relative path"
    taken = {fold_name(name) for name in candidate or {}}
    if fold_name(path) in taken or fold_name(parts[0]) in _HARNESS_DIRS or fold_name(parts[-1]) in _INSTRUCTION_FILES:
        return f"fixture {path} would replace the candidate or change what the harness loads"
    return None


def _shared_id(cases: list[dict]) -> str | None:
    """Results bind to a case by its id, so an id used twice lets one case stand in for another."""
    seen = set()
    for item in cases:
        if item.get("id") in seen:
            return f"case id {item['id']!r} is used twice"
        seen.add(item.get("id"))
    return None


def load_cases(path: Path) -> list[dict]:
    """Cases from an evals/smoke file: bounded, strict YAML against the closed runtime-cases schema."""
    cases = load_document(Path(path), "runtime-cases")["cases"]
    shared = _shared_id(cases)
    if shared:
        raise ValueError(f"{path}: {shared}")
    for item in cases:
        for fixture in item.get("fixtures") or {}:
            problem = fixture_problem(fixture)
            if problem:
                raise ValueError(f"{path}: case {item['id']!r}: {problem}")
    return cases


def _claude_command(tools: list[str], *, bundled: bool = True, off: Iterable[str] = ()) -> list[str]:
    """Project settings only: the candidate's skills load, the user's plugins and hooks do not.

    `--restricted` cannot be used here: it also drops the project's skills.
    Personal and managed skills still load; claude_skill_origins tells them
    from the bundled ones. ``bundled=False`` turns the bundled skills off;
    ``off`` turns the named skills off for this launch only.
    """
    listed = ",".join(tools)
    settings = {"disableAllHooks": True, "autoMemoryEnabled": False, "disableSkillShellExecution": True}
    if not bundled:
        settings["disableBundledSkills"] = True
    if off:
        settings["skillOverrides"] = {name: "off" for name in sorted(off)}
    return [
        "claude", "--print", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        # disableSkillShellExecution: a candidate's !`cmd` lines would otherwise run before the model reads it.
        "--settings", json.dumps(settings, separators=(",", ":")),
        "--tools", listed, "--allowedTools", listed,
    ]


BASELINE_PROMPT = "Reply with OK."


def _listed_skills(runner: Runner, environ: Mapping[str, str], *, bundled: bool, timeout: int,
                   workspace_root: str | None) -> set[str] | None:
    """The skills one tool-less launch with the runtime cases' settings lists in an empty workspace;
    None when the launch failed or listed no skills field, which is no evidence of an empty listing."""
    with tempfile.TemporaryDirectory(prefix="forge-baseline-", dir=workspace_root) as temporary:
        completed = runner(_claude_command([], bundled=bundled), cwd=temporary,
                           env=worker_environment(environ, "claude-code"), stdin=BASELINE_PROMPT, timeout=timeout)
    if completed.returncode != 0 or completed.timed_out:
        return None
    for event in _events(completed.stdout):
        if event.get("type") == "system" and event.get("subtype") == "init":
            skills = event.get("skills")
            return {str(skill) for skill in skills} if isinstance(skills, list) else None
    return None


def claude_skill_origins(runner: Runner, environ: Mapping[str, str], *, timeout: int = RUNTIME_TIMEOUT,
                         workspace_root: str | None = None) -> tuple[set[str], set[str] | None]:
    """(bundled, user-level) skills: an empty workspace lists both; with bundled skills off, only the
    personal and managed ones remain. Only bundled skills are part of every user's environment.

    When either launch shows no listing, the origins are unknown: (set(), None), and nothing is exempt.
    """
    listed = _listed_skills(runner, environ, bundled=True, timeout=timeout, workspace_root=workspace_root)
    user = _listed_skills(runner, environ, bundled=False, timeout=timeout, workspace_root=workspace_root)
    if listed is None or user is None:
        return set(), None
    return listed - user, user


UNKNOWN_ORIGINS = "unavailable: Forge could not tell bundled skills from the user's, so nothing is exempt"


def shadowing(name: str, user_skills: set[str] | None) -> str | None:
    """Why a case of the skill ``name`` cannot run: unknown skill origins, or a user-level skill of its name."""
    if user_skills is None:
        return UNKNOWN_ORIGINS
    if name in user_skills:
        return f"unavailable: a user-level skill named {name} takes precedence over the candidate"
    return None


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


def _invoked(text: str) -> str | None:
    """The command a prompt invoked: its one command name outside the arguments, which may quote anything."""
    names = re.findall(r"<command-name>/([^<]*)</command-name>", re.sub(r"<command-args>.*?</command-args>", "",
                                                                         text, flags=re.S))
    return names[0] if len(names) == 1 else None


# Shared with the worker launch: where a file tool reached, judged through the workspace's links.
_tool_paths, _inside = tool_paths, inside


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(item.get("text", "") for item in content or [] if isinstance(item, dict))


_CLAUDE_SKILL_PATH = re.compile(r"^(?:.*/)?\.(?:claude|agents)/skills/([^/]+)/SKILL\.md$")


def _observe_claude(stdout: str, name: str, baseline: set[str], workspace: Path | None = None,
                    markers: list[str] | None = None, directory: str | None = None) -> dict:
    """Activation, tool calls and what else stayed loaded, from the init, assistant and tool-result events.

    A Skill or Read call activates only once its tool result arrives without
    an error: a failed call loaded nothing. On a near miss the attempt alone
    shows the skill was chosen (``attempted``).
    """
    observed = {"activation": None, "attempted": None, "calls": [], "unavailable": [], "competing": [],
                "loaded": None, "completed": False, "errored": False, "inventory": "not observable", "outside": []}
    other_skill = False
    pending: dict[str, str] = {}
    reads: dict[str, tuple[str, dict]] = {}
    skill_reads: set[str] = set()
    other_reads: set[str] = set()
    # Each call's place in the order the model made them: a skill-choosing call counts from that place once its
    # result is in, so results that arrive in another order cannot change which skill came first.
    order: dict[str, int] = {}
    credited: list[tuple[int, str]] = []
    chosen_other: list[int] = []
    # Only the candidate in the throwaway workspace counts; a same-named skill elsewhere is not it.
    roots = ({str(workspace), str(Path(workspace).resolve())} if workspace is not None else {""})
    for event in _events(stdout):
        if event.get("type") == "system" and event.get("subtype") == "init":
            skills = [str(skill) for skill in event.get("skills", [])]
            # Observed only when the event lists every surface: skills, plugins and MCP servers.
            if all(isinstance(event.get(key), list) for key in ("skills", "plugins", "mcp_servers")):
                observed["inventory"] = "observed"
            observed["loaded"] = name in skills
            observed["competing"] = sorted(set(skills) - {name} - baseline)
            plugins = [p for p in event.get("plugins", []) if not (isinstance(p, dict) and p.get("path") == "builtin")]
            observed["unavailable"] += [f"plugin {p.get('name', p) if isinstance(p, dict) else p}" for p in plugins]
            observed["unavailable"] += [f"MCP server {s.get('name', s) if isinstance(s, dict) else s}"
                                        for s in event.get("mcp_servers", [])]
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) and isinstance(message.get("content"), list) else []
        if event.get("type") == "assistant":
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                observed["calls"].append(str(block.get("name")))
                order.setdefault(str(block.get("id")), len(order))
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if workspace is not None and _tool_paths(str(block.get("name")), arguments):
                    reads[str(block.get("id"))] = (str(block.get("name")), arguments)
                signal = None
                if block.get("name") == "Skill":
                    if name in (arguments.get("skill"), arguments.get("command")):
                        if other_skill:
                            # Another skill was chosen first: the target ran after it, as Codex's first read decides.
                            observed["attempted"] = observed["attempted"] or f"Skill tool called for {name} after " \
                                                                              "another skill"
                        else:
                            signal = f"Skill tool called for {name}"
                    else:
                        other_skill = True
                elif block.get("name") == "Read" and observed["activation"] is None and not other_skill:
                    # Reading the candidate's own SKILL.md brings its instructions to
                    # the model; a reference alone shows file access only. After
                    # another skill ran, such a read follows that skill's pointer.
                    path = posixpath.normpath(str(arguments.get("file_path", "")))
                    # The candidate's own directory too: a skill reused in place is staged under its path.
                    if path in {f"{base}/{folder}/SKILL.md" for base in roots
                                for folder in (f".claude/skills/{name}", f".agents/skills/{name}", directory)
                                if folder}:
                        signal = f"read SKILL.md from the {name} skill"
                    elif (_CLAUDE_SKILL_PATH.match(path) or [None, name])[1] != name:
                        # Another skill's SKILL.md: reading it chooses that skill, as a Skill call would.
                        # The candidate's name elsewhere is no other skill's choice.
                        other_reads.add(str(block.get("id")))
                if signal:
                    observed["attempted"] = observed["attempted"] or signal
                    pending[str(block.get("id"))] = signal
                    if block.get("name") == "Read":
                        skill_reads.add(str(block.get("id")))
        if event.get("type") == "user":
            for block in blocks:
                if not (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is not True):
                    continue
                if str(block.get("tool_use_id")) in other_reads:
                    other_skill = True
                    chosen_other.append(order[str(block.get("tool_use_id"))])
                signal = pending.get(str(block.get("tool_use_id")))
                # A read activates only when it returned the whole SKILL.md: a line or two loads no instructions.
                if signal and (str(block.get("tool_use_id")) not in skill_reads
                               or _whole(_result_text(block), markers or ["\0no marker"])):
                    credited.append((order[str(block.get("tool_use_id"))], signal))
                if str(block.get("tool_use_id")) in reads:
                    # Judged only once the call succeeded: a failed call reached nothing.
                    tool, arguments = reads[str(block["tool_use_id"])]
                    outside = [path for path in _tool_paths(tool, arguments) if not _inside(path, Path(workspace))]
                    if outside:
                        observed["outside"].append(", ".join(repr(path)[1:-1] for path in outside))
            # The invocation marker counts only in the prompt's own text: a tool result is data, such as a
            # fixture that quotes a transcript.
            content = message.get("content") if isinstance(message, dict) else None
            prompt = [content] if isinstance(content, str) else [
                block.get("text") for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
            # The whole prompt at once: an argument span may cross text blocks.
            if _invoked("".join(text for text in prompt if isinstance(text, str))) == name:
                observed["activation"] = observed["activation"] or f"/{name} was invoked"
        if event.get("type") == "result":
            observed["completed"] = event.get("is_error") is False and event.get("subtype") == "success"
            observed["errored"] = not observed["completed"]
    # The candidate activated when its call came before every other skill's read that succeeded.
    first = [signal for place, signal in sorted(credited) if place < min(chosen_other, default=len(order))]
    observed["activation"] = observed["activation"] or (first[0] if first else None)
    observed["attempted"] = observed["activation"] or observed["attempted"]
    return observed


_SKILL_PATH = re.compile(r"^(?:.*/)?\.agents/skills/([^/\s'\"]+)/SKILL\.md$")
_READERS = {"cat", "nl", "sed", "head", "tail", "less", "more", "bat", "awk"}
_SHELLS = {"sh", "bash", "zsh", "dash"}


def _segments(command: str) -> list[list[str]]:
    """The simple commands a shell command line runs, comments dropped, a `zsh -lc '...'` wrapper unwrapped.

    A line with a conditional, background or redirection operator (``&&``,
    ``||``, ``&``, ``>``, ``<`` and the like) gives none: which of its
    commands ran, or where a read's output went, cannot be told.
    """
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        return []
    if len(words) >= 3 and posixpath.basename(words[0]) in _SHELLS and words[1] in ("-c", "-lc"):
        command = words[2]
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.commenters = "#"
    lexer.whitespace_split = True
    segments, current = [], []
    try:
        for token in lexer:
            # A conditional, a background job or a redirection hides which output a read produced.
            if token in ("&&", "||", "&") or (token not in (";", "|", "\n") and set(token) <= set("<>&|;")):
                return []
            if token in (";", "|", "\n"):
                segments.append(current)
                current = []
            else:
                current.append(token)
    except ValueError:
        return []
    return [segment for segment in segments + [current] if segment]


def _lines(output: str) -> list[str]:
    """Output lines without the line numbers `nl` or `cat -n` add."""
    return [re.sub(r"^\s*\d+\t", "", line) for line in output.splitlines()]


def _skills_read(item: dict) -> list[tuple[str, str]]:
    """Skills whose SKILL.md a reading command printed, with the normalized path read.

    A mention, a comment or a failed read is no read; nor is a line that
    also runs a command other than a reader, which could print the lines.
    """
    if item.get("status") != "completed" or str(item.get("exit_code")) != "0":
        return []
    lines = _lines(str(item.get("aggregated_output", "")))
    segments = _segments(str(item.get("command", "")))
    if any(posixpath.basename(segment[0]) not in _READERS for segment in segments):
        return []
    read = []
    for segment in segments:
        for word in segment[1:]:
            match = _SKILL_PATH.match(word)
            if match and any(re.fullmatch(rf"name:\s*['\"]?{re.escape(match.group(1))}['\"]?\s*", line)
                             for line in lines):
                read.append((match.group(1), posixpath.normpath(word)))
    return read


# The reading commands whose arguments Forge can attribute exactly: each option form a reader accepts
# without naming another input. Anything else, awk and sed scripts other than printing included,
# makes the read unattributable.
_PRINT_SCRIPT = r"(?:\d+|\$)?(?:,(?:\d+|\$))?p"
_READ_OPTIONS = {
    "cat": (r"-[nbsuvetA]+",),
    "nl": (r"-b[atn]", r"-n(?:ln|rn|rz)", r"-[wvi]\d+", r"-s[ \t:|]?"),
    "head": (r"-[nc]\d+", r"-\d+"),
    "tail": (r"-[nc][+-]?\d+", r"-[+-]?\d+"),
    "sed": (r"-n",),
}
_OPTION_WITH_NUMBER = {"head": ("-n", "-c"), "tail": ("-n", "-c"), "nl": ("-w", "-v", "-i")}


def _reads_only(item: dict, candidate: set[str]) -> bool:
    """Whether the command is one reading command whose only file is the candidate's SKILL.md.

    Then its output is that file's: with another command, another file, or
    a script that reads one (sed's r command), their output could complete
    the skill's text instead. Every word must be an option form the reader
    is known to accept, a number right after an option that takes one, the
    one print-only sed script, or the candidate.
    """
    segments = _segments(str(item.get("command", "")))
    if len(segments) != 1:
        return False
    reader, *words = segments[0]
    reader = posixpath.basename(reader)
    if reader not in _READ_OPTIONS:
        return False
    # BSD getopt, as on macOS, ends the options at "--" or at the first other word; later words are files.
    operands, needs_number, script, options = [], False, reader != "sed", True
    for word in words:
        if not options:
            operands.append(posixpath.normpath(word))
        elif word == "--" and not needs_number:
            options = False
        elif needs_number:
            if not re.fullmatch(r"[+-]?\d+", word):
                return False
            needs_number = False
        elif word in _OPTION_WITH_NUMBER.get(reader, ()):
            needs_number = True
        elif reader == "sed" and word == "-e" and not script:
            continue
        elif any(re.fullmatch(pattern, word) for pattern in _READ_OPTIONS[reader]):
            continue
        elif not script:
            if not re.fullmatch(_PRINT_SCRIPT, word):
                return False
            script, options = True, False
        else:
            operands.append(posixpath.normpath(word))
            options = False
    return not needs_number and script and len(operands) == 1 and operands[0] in candidate


def _whole(output: str, markers: list[str]) -> bool:
    """Whether output holds the candidate's SKILL.md (_markers) whole: its non-empty lines in order, repeated
    lines included, as one run of the output's non-empty lines (line numbers removed)."""
    lines = [line.rstrip() for line in _lines(output) if line.strip()]
    return any(lines[start:start + len(markers)] == markers for start in range(len(lines) - len(markers) + 1))


def _printed(item: dict, markers: list[str]) -> bool:
    """Whether the command printed the candidate's SKILL.md whole."""
    return _whole(str(item.get("aggregated_output", "")), markers)


def _observe_codex(stdout: str, directory: str, markers: list[str] | None = None, *workspaces: Path) -> dict:
    """Activation is a read of the candidate's SKILL.md, unless another skill's SKILL.md was read first:
    a later read of the candidate then follows that skill's pointer.

    The read must be a reading command on the staged candidate's own
    SKILL.md, relative to the workspace or under one of its
    ``workspaces`` paths (a fixture copy or another same-named skill is
    not the candidate), and print every line of it (``markers``), which a
    command that only names the file or prints part of it does not.
    Codex reports no inventory of what it loaded.
    """
    observed = {"activation": None, "attempted": None, "calls": [], "unavailable": [], "competing": [],
                "loaded": None, "completed": False, "errored": False, "inventory": "not observable"}
    name = directory.rsplit("/", 1)[-1]
    candidate = {f"{directory}/SKILL.md"} | {f"{root}/{directory}/SKILL.md" for root in workspaces}
    first_skill = None
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if (event.get("type") == "item.completed" and item.get("type") not in ("agent_message", "reasoning", "todo_list")
                and not codex.empty_listing(item)):
            observed["calls"].append(str(item.get("type")))
            if item.get("type") == "command_execution":
                read = _skills_read(item)
                first_skill = first_skill or (read[0][0] if read else None)
                if (first_skill == name and any(path in candidate for _, path in read)
                        and _reads_only(item, candidate) and _printed(item, markers or [])):
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
    if observed.get("outside"):
        # Claude runs the case without a sandbox: a skill turned off is only hidden, and its files stay readable.
        return "not-run", f"read outside the throwaway workspace: {', '.join(observed['outside'][:5])}"
    activated = observed["activation"] is not None
    if test_case["expected"] == "activates":
        if activated:
            return "pass", observed["activation"]
        if harness == "codex":
            return "not-run", UNOBSERVABLE["codex"][1]
        return "fail", "the skill was not activated"
    attempted = observed.get("attempted") or observed["activation"]
    if attempted:
        return "fail", f"activated on a near miss: {attempted}"
    if harness == "codex":
        return "not-run", UNOBSERVABLE["codex"][0]
    return "pass", "no Skill tool call for the candidate"


def _staging_problem(harness: str, files: Mapping[str, bytes], fixtures: Mapping[str, str], skill_dirs: list[str],
                     links: Mapping[str, str] | None) -> str | None:
    """Why the throwaway workspace cannot hold these files, fixtures and links, or None."""
    for path in fixtures:
        problem = fixture_problem(path, files)
        if problem:
            return problem
    folded = {fold_name(path): path for path in [*files, *fixtures]}
    for key, path in folded.items():
        parts = key.split("/")
        for count in range(1, len(parts)):
            above = folded.get("/".join(parts[:count]))
            if above:
                return f"fixture paths {above} and {path} cannot both be files"
    if harness == "claude-code":
        wanted = links if links is not None else {f".claude/skills/{d.rsplit('/', 1)[-1]}": f"../../{d}"
                                                  for d in skill_dirs}
        for path, link_target in wanted.items():
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), link_target))
            if path != f".claude/skills/{resolved.rsplit('/', 1)[-1]}" or resolved not in skill_dirs:
                return f"the link {path} does not point at the candidate it is named for"
    return None


def case_preflight(harness: str, case: dict, directory: str, candidate: Mapping[str, bytes] | None,
                   links: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """The result a case gets before it runs, from its own definition and the candidate, or None.

    One order for the producer and for replay: Claude runs only with
    contained tools (not-run), then the workspace must hold the candidate,
    the fixtures and the links (error). Whether the harness is available or
    a user skill shadows the candidate depends on the machine; those come
    before and after this, as not-run. Without the ``candidate`` files only
    the tools are checked.
    """
    permitted = case["permitted_tools"].get(harness, [])
    uncontained = [tool for tool in permitted if harness == "claude-code" and tool not in CLAUDE_CONTAINED]
    if uncontained:
        return "not-run", (f"unavailable: Claude runs the case without a sandbox; {', '.join(uncontained)} cannot "
                           f"be permitted, only {', '.join(CLAUDE_CONTAINED)}")
    problem = (_staging_problem(harness, candidate, case.get("fixtures") or {}, [directory], links)
               if candidate is not None else None)
    return ("error", problem) if problem else None


def outcome_problem(result, case: dict,
                    staged: tuple[str, Mapping[str, bytes], Mapping[str, str] | None] | None = None) -> str | None:
    """Why this runtime could not have produced ``result`` for ``case``, or None.

    What a case observed is the harness's to say; which outcomes follow from
    it, and which configurations run at all, is fixed here. With what was
    ``staged`` (the candidate's directory, its files and the repository's
    links for it), a case that case_preflight stops has that result, unless
    the machine stopped it first (not-run, "unavailable: ..."). An unobservable Codex result names
    the expectation it belongs to and observes no activation; a pass agrees
    with the activation it observed, and a near miss never passes in Codex.
    """
    permitted = case["permitted_tools"].get(result.harness)
    if permitted is None:
        return f"case {case['id']} names no permitted tools for {result.harness}"
    if (result.prompt, result.expected) != (case["prompt"], case["expected"]):
        return f"case {case['id']} ran another prompt or expectation than declared"
    stop = case_preflight(result.harness, case, *(staged or ("", None, None)))
    if stop:
        machine = result.result == "not-run" and result.reason.startswith("unavailable: ")
        if stop and (result.result, result.reason) != stop and not machine:
            return f"case {case['id']} stops before it runs ({stop[0]}: {stop[1]}), not with {result.result}"
    near_miss, unseen_activation = UNOBSERVABLE["codex"]
    if result.result == "not-run" and result.reason in (near_miss, unseen_activation):
        wanted = "does-not-activate" if result.reason == near_miss else "activates"
        if result.harness != "codex" or result.expected != wanted or result.observed_activation:
            return f"case {case['id']} is not-run as unobservable, which contradicts its expectation or observation"
    if result.result != "pass":
        return None
    if result.expected == "activates" and not result.observed_activation:
        return f"case {case['id']} passed an activation without observing one"
    if result.expected == "does-not-activate" and result.harness == "codex":
        return f"case {case['id']} passed a near miss in Codex, which has no signal that proves non-activation"
    if result.expected == "does-not-activate" and result.observed_activation:
        return f"case {case['id']} passed although it observed an activation ({result.observed_activation!r})"
    return None


# The not-run reasons that say Codex cannot observe the outcome, not that the case was skipped: a Codex run
# gives one for every near miss, and for an implicit activation it could not see.
UNOBSERVABLE = {"codex": ("Codex has no observable signal that proves non-activation",
                          "Codex showed no observable activation signal")}


def _markers(files: Mapping[str, bytes], target: str) -> list[str]:
    """Every non-empty line of the candidate's SKILL.md: a complete read prints them all, so its
    instructions reached the model, whichever command's output the lines came from."""
    lines = files.get(f"{target}/SKILL.md", b"").decode("utf-8", "replace").splitlines()
    return [line.rstrip() for line in lines if line.strip()] or ["\0no marker"]


def activation_case(
    harness: str, files: Mapping[str, bytes], skill_dirs: list[str], target: str, prompt: str, expected: str,
    permitted: list[str], fixtures: Mapping[str, str], *, runner: Runner, environ: Mapping[str, str],
    baseline: set[str], timeout: int = RUNTIME_TIMEOUT, workspace_root: str | None = None, slash: bool = False,
    links: Mapping[str, str] | None = None, off: Iterable[str] = (),
) -> tuple[str, str, str | None, str]:
    """Run one prompt in a throwaway workspace holding ``files``; judge whether ``target`` activated.

    Returns (outcome, reason, observed activation, inventory), the inventory
    ``observed`` only when the harness reported what it loaded.

    Skills in ``skill_dirs`` other than the target are installed on purpose
    and never count as competing. ``links`` are the repository's own
    symbolic links, recreated as they are, so a missing .claude/skills link
    stays missing; without them, each skill directory gets its link (the
    routing smoke installs Forge's own skills that way). A slash or $
    invocation (``slash``) may load the skill without a tool call; with no
    observed signal it is not-run. ``off`` names the user-level skills
    turned off for Claude; one the case still lists competes.
    """
    name = target.rsplit("/", 1)[-1]
    companions = {directory.rsplit("/", 1)[-1] for directory in skill_dirs} - {name}
    problem = _staging_problem(harness, files, fixtures, skill_dirs, links)
    if problem:
        return "error", problem, None, "not observable"
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
        # Whatever names a file system folds together, the candidate must still hold its frozen bytes.
        changed = [path for path, data in files.items() if safe_path(workspace, path).read_bytes() != data]
        if changed:
            return "error", f"a fixture replaced the candidate file {changed[0]}", None, "not observable"
        if harness == "claude-code":
            wanted = links if links is not None else {f".claude/skills/{d.rsplit('/', 1)[-1]}": f"../../{d}"
                                                      for d in skill_dirs}
            # _staging_problem checked that each link points at the candidate it is named for.
            for path, link_target in wanted.items():
                link = safe_path(workspace, path)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(link_target)
            command = _claude_command(permitted, off=off)
        else:
            command = codex.command("review", str(workspace), structured=False)
        try:
            completed = runner(command, cwd=str(workspace), env=worker_environment(environ, harness), stdin=prompt,
                               timeout=timeout)
        except OSError as error:
            return "not-run", f"unavailable: the harness could not start: {error}", None, "not observable"
        # What the harness did before it stopped counts, also when it timed out or failed. Observed while the
        # workspace and its links exist, so paths resolve where the harness found them.
        observed = (_observe_claude(completed.stdout, name, baseline | companions, workspace, _markers(files, target),
                                    directory=target)
                    if harness == "claude-code"
                    else _observe_codex(completed.stdout, target, _markers(files, target), workspace,
                                        workspace.resolve()))
    outside = sorted(set(observed["calls"]) - set(permitted))
    if outside:
        return "fail", f"tool outside the declared list: {', '.join(outside)}", None, observed["inventory"]
    if observed.get("outside"):
        # The case's evidence is spoiled whatever happened next; the reason keeps how the run ended.
        ending = (f"; the run then timed out after {timeout} s" if completed.timed_out else
                  f"; the harness exited with {completed.returncode}" if completed.returncode else "")
        return ("not-run", f"read outside the throwaway workspace: {', '.join(observed['outside'][:5])}{ending}",
                None, observed["inventory"])
    if completed.timed_out:
        return "not-run", f"timed out after {timeout} s", None, observed["inventory"]
    if completed.returncode:
        return "error", f"the harness exited with {completed.returncode}", None, observed["inventory"]
    outcome, reason = _decide(harness, {"expected": expected}, observed, permitted)
    if slash and outcome == "fail" and observed["activation"] is None and expected == "activates":
        return "not-run", "the explicit invocation left no observable activation signal", None, observed["inventory"]
    return outcome, reason, observed["activation"], observed["inventory"]


def run_skill_runtime(
    run: VerificationRun, harness: str, cases: list[dict], *, files: Mapping[str, bytes], runner: Runner = run_command,
    environ: Mapping[str, str] | None = None, login_answer: str | None = None,
    baseline_skills: set[str] | frozenset | None = None, timeout: int = RUNTIME_TIMEOUT,
    workspace_root: str | None = None,
) -> list[RuntimeResult]:
    """One RuntimeResult per case, bound to the run and the part's frozen files.

    baseline_skills are the harness's bundled skills; they never count as
    competing. Left out, Claude's come from empty launches with the cases'
    own settings (claude_skill_origins), and a personal or managed skill
    with the candidate's name, which takes precedence, stops its cases.
    Every other skill that launch lists without the bundled ones is turned
    off for each case; one that stays listed competes.
    """
    if harness not in run.targets:
        raise ValueError(f"{harness} is not a target of this run")
    shared = _shared_id(cases)
    if shared:
        raise ValueError(shared)
    # A case file names tools only for the harnesses its idea targets.
    for test_case in cases:
        if harness not in test_case["permitted_tools"]:
            raise ValueError(f"case {test_case['id']} names no permitted tools for {harness}")
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
    user_skills: set[str] | None = set()
    if baseline_skills is None:
        baseline_skills, user_skills = (
            claude_skill_origins(runner, environ, timeout=timeout, workspace_root=workspace_root)
            if harness == "claude-code" and checked["available"] else (set(), set()))
    results = []
    for test_case in cases:
        directory = run.part_dirs[test_case["part_id"]]
        name = directory.rsplit("/", 1)[-1]
        hashes = {path: digest for path, digest in frozen.items() if path.startswith(directory + "/")}
        staged = {path: data for path, data in files.items() if path.startswith(directory + "/")}
        # The repository's own links for the part, as they are: a missing one stays missing.
        links = {link: run.frozen_inputs["symlinks"][link] for link in run.part_links.get(test_case["part_id"], [])
                 if link in run.frozen_inputs["symlinks"]}
        permitted = list(test_case["permitted_tools"][harness])
        fixtures = dict(test_case.get("fixtures") or {})
        environment = "; ".join([
            "throwaway workspace with only the frozen skill and fixtures",
            f"tools: {', '.join(permitted) or 'none'}", "network: none requested",
            *[f"{path} sha256 {hashlib.sha256(text.encode()).hexdigest()}" for path, text in sorted(fixtures.items())],
        ])

        def result(outcome: str, reason: str, activation: str | None = None,
                   inventory: str = "not observable") -> RuntimeResult:
            built = RuntimeResult(run_id=run.run_id, part_id=test_case["part_id"], case_id=test_case["id"],
                                  prompt=test_case["prompt"], expected=test_case["expected"],
                                  observed_activation=activation, result=outcome, reason=reason, file_hashes=hashes,
                                  harness=harness, harness_version=checked["version"], environment=environment,
                                  inventory=inventory)
            # The rule the reference checker applies to stored results holds for every result made here.
            problem = outcome_problem(built, test_case, (directory, staged, links))
            return built if problem is None else replace(built, result="error", reason=f"inconsistent: {problem}")

        if not checked["available"]:
            results.append(result("not-run", f"unavailable: {checked['reason']}"))
            continue
        stop = case_preflight(harness, test_case, directory, staged, links)
        if stop:
            results.append(result(*stop))
            continue
        blocked = shadowing(directory.rsplit("/", 1)[-1], user_skills)
        if blocked:
            results.append(result("not-run", blocked))
            continue
        outcome, reason, activation, inventory = activation_case(
            harness, staged, [directory], directory, test_case["prompt"], test_case["expected"], permitted, fixtures,
            runner=runner, environ=environ, baseline=set(baseline_skills), timeout=timeout,
            workspace_root=workspace_root, links=links, off=user_skills or ())
        results.append(result(outcome, reason, activation, inventory))
    return results
