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
from pathlib import Path
from typing import Mapping


from harness_forge.adapters import codex
from harness_forge.adapters.base import Runner, preflight, run_command, worker_environment
from harness_forge.documents import load_document, safe_path
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

    Paths must be plain relative paths, compared without case: a "./" prefix
    or a case variant reaches the same file on a case-insensitive file system.
    """
    parts = path.split("/")
    if "\\" in path or any(part in ("", ".", "..") for part in parts):
        return f"fixture {path} is not a plain relative path"
    taken = {name.lower() for name in candidate or {}}
    if path.lower() in taken or parts[0].lower() in _HARNESS_DIRS or parts[-1].lower() in _INSTRUCTION_FILES:
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


def _claude_command(tools: list[str]) -> list[str]:
    """Project settings only: the candidate's skills load, the user's plugins, skills and hooks do not.

    `--restricted` cannot be used here: it also drops the project's skills.
    """
    listed = ",".join(tools)
    return [
        "claude", "--print", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        # disableSkillShellExecution: a candidate's !`cmd` lines would otherwise run before the model reads it.
        "--settings", '{"disableAllHooks":true,"autoMemoryEnabled":false,"disableSkillShellExecution":true}',
        "--tools", listed, "--allowedTools", listed,
    ]


BASELINE_PROMPT = "Reply with OK."


def claude_baseline(runner: Runner, environ: Mapping[str, str], *, timeout: int = RUNTIME_TIMEOUT,
                    workspace_root: str | None = None) -> set[str]:
    """The skills Claude lists with the runtime cases' settings in an empty workspace: its built-ins.

    One tool-less launch; a skill it lists is part of every user's environment
    and never competes with a candidate.
    """
    with tempfile.TemporaryDirectory(prefix="forge-baseline-", dir=workspace_root) as temporary:
        completed = runner(_claude_command([]), cwd=temporary, env=worker_environment(environ, "claude-code"),
                           stdin=BASELINE_PROMPT, timeout=timeout)
    for event in _events(completed.stdout):
        if event.get("type") == "system" and event.get("subtype") == "init":
            return {str(skill) for skill in event.get("skills", [])}
    return set()


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


def _observe_claude(stdout: str, name: str, baseline: set[str], workspace: Path | None = None) -> dict:
    """Activation, tool calls and what else stayed loaded, from the init, assistant and tool-result events.

    A Skill or Read call activates only once its tool result arrives without
    an error: a failed call loaded nothing. On a near miss the attempt alone
    shows the skill was chosen (``attempted``).
    """
    observed = {"activation": None, "attempted": None, "calls": [], "unavailable": [], "competing": [],
                "loaded": None, "completed": False, "errored": False, "inventory": "not observable"}
    other_skill = False
    pending: dict[str, str] = {}
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
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                signal = None
                if block.get("name") == "Skill":
                    if name in (arguments.get("skill"), arguments.get("command")):
                        signal = f"Skill tool called for {name}"
                    else:
                        other_skill = True
                elif block.get("name") == "Read" and observed["activation"] is None and not other_skill:
                    # Reading the candidate's own SKILL.md brings its instructions to
                    # the model; a reference alone shows file access only. After
                    # another skill ran, such a read follows that skill's pointer.
                    path = posixpath.normpath(str(arguments.get("file_path", "")))
                    if path in {f"{base}/{folder}/{name}/SKILL.md" for base in roots
                                for folder in (".claude/skills", ".agents/skills")}:
                        signal = f"read SKILL.md from the {name} skill"
                if signal:
                    observed["attempted"] = observed["attempted"] or signal
                    pending[str(block.get("id"))] = signal
        if event.get("type") == "user":
            for block in blocks:
                if (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is not True
                        and str(block.get("tool_use_id")) in pending):
                    observed["activation"] = observed["activation"] or pending[str(block["tool_use_id"])]
            if f"<command-name>/{name}</command-name>" in json.dumps(event):
                observed["activation"] = observed["activation"] or f"/{name} was invoked"
        if event.get("type") == "result":
            observed["completed"] = event.get("is_error") is False and event.get("subtype") == "success"
            observed["errored"] = not observed["completed"]
    observed["attempted"] = observed["activation"] or observed["attempted"]
    return observed


_SKILL_PATH = re.compile(r"^(?:.*/)?\.agents/skills/([^/\s'\"]+)/SKILL\.md$")
_READERS = {"cat", "nl", "sed", "head", "tail", "less", "more", "bat", "awk"}
_SHELLS = {"sh", "bash", "zsh", "dash"}


def _segments(command: str) -> list[list[str]]:
    """The simple commands a shell command line runs, comments dropped, a `zsh -lc '...'` wrapper unwrapped."""
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
            if token in (";", "&&", "||", "|", "&", "\n"):
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


def _skills_read(item: dict) -> list[str]:
    """Skills whose SKILL.md a reading command printed: a mention, a comment or a failed read is no read."""
    if item.get("status") != "completed" or str(item.get("exit_code")) != "0":
        return []
    lines = _lines(str(item.get("aggregated_output", "")))
    names = []
    for segment in _segments(str(item.get("command", ""))):
        if posixpath.basename(segment[0]) not in _READERS:
            continue
        for word in segment[1:]:
            match = _SKILL_PATH.match(word)
            if match and any(re.fullmatch(rf"name:\s*['\"]?{re.escape(match.group(1))}['\"]?\s*", line)
                             for line in lines):
                names.append(match.group(1))
    return names


def _printed(item: dict, markers: list[str]) -> bool:
    """Whether the command printed every marker line of the candidate's SKILL.md."""
    lines = set(_lines(str(item.get("aggregated_output", ""))))
    return all(marker in lines for marker in markers)


def _observe_codex(stdout: str, directory: str, markers: list[str] | None = None) -> dict:
    """Activation is a read of the candidate's SKILL.md, unless another skill's SKILL.md was read first:
    a later read of the candidate then follows that skill's pointer.

    The read must be a reading command on the candidate's SKILL.md and
    print its own description line and first heading (``markers``), which
    a command that only names the file or prints a few lines does not.
    Codex reports no inventory of what it loaded.
    """
    observed = {"activation": None, "attempted": None, "calls": [], "unavailable": [], "competing": [],
                "loaded": None, "completed": False, "errored": False, "inventory": "not observable"}
    name = directory.rsplit("/", 1)[-1]
    first_skill = None
    for event in _events(stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if (event.get("type") == "item.completed" and item.get("type") not in ("agent_message", "reasoning", "todo_list")
                and not codex.empty_listing(item)):
            observed["calls"].append(str(item.get("type")))
            if item.get("type") == "command_execution":
                read = _skills_read(item)
                first_skill = first_skill or (read[0] if read else None)
                if name in read and first_skill == name and _printed(item, markers or []):
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
    attempted = observed.get("attempted") or observed["activation"]
    if attempted:
        return "fail", f"activated on a near miss: {attempted}"
    if harness == "codex":
        return "not-run", "Codex has no observable signal that proves non-activation"
    return "pass", "no Skill tool call for the candidate"


def _markers(files: Mapping[str, bytes], target: str) -> list[str]:
    """The candidate SKILL.md's description line and first heading, which a real read of it prints."""
    lines = files.get(f"{target}/SKILL.md", b"").decode("utf-8", "replace").splitlines()
    markers = [next((line for line in lines if line.startswith("description:")), None),
               next((line for line in lines if line.startswith("# ")), None)]
    return [marker for marker in markers if marker is not None] or ["\0no marker"]


def activation_case(
    harness: str, files: Mapping[str, bytes], skill_dirs: list[str], target: str, prompt: str, expected: str,
    permitted: list[str], fixtures: Mapping[str, str], *, runner: Runner, environ: Mapping[str, str],
    baseline: set[str], timeout: int = RUNTIME_TIMEOUT, workspace_root: str | None = None, slash: bool = False,
    links: Mapping[str, str] | None = None,
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
    observed signal it is not-run.
    """
    name = target.rsplit("/", 1)[-1]
    companions = {directory.rsplit("/", 1)[-1] for directory in skill_dirs} - {name}
    for path in fixtures:
        problem = fixture_problem(path, files)
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
        if harness == "claude-code":
            wanted = links if links is not None else {f".claude/skills/{d.rsplit('/', 1)[-1]}": f"../../{d}"
                                                      for d in skill_dirs}
            for path, link_target in wanted.items():
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), link_target))
                if path != f".claude/skills/{resolved.rsplit('/', 1)[-1]}" or resolved not in skill_dirs:
                    return "error", f"the link {path} does not point at the candidate it is named for", None, \
                        "not observable"
                link = safe_path(workspace, path)
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(link_target)
            command = _claude_command(permitted)
        else:
            command = codex.command("review", str(workspace), structured=False)
        try:
            completed = runner(command, cwd=str(workspace), env=worker_environment(environ, harness), stdin=prompt,
                               timeout=timeout)
        except OSError as error:
            return "not-run", f"unavailable: the harness could not start: {error}", None, "not observable"
    # What the harness did before it stopped counts, also when it timed out or failed.
    observed = (_observe_claude(completed.stdout, name, baseline | companions, workspace) if harness == "claude-code"
                else _observe_codex(completed.stdout, target, _markers(files, target)))
    outside = sorted(set(observed["calls"]) - set(permitted))
    if outside:
        return "fail", f"tool outside the declared list: {', '.join(outside)}", None, observed["inventory"]
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

    baseline_skills are the harness's built-in skills; they never count as
    competing. Left out, Claude's are taken from one empty launch with the
    cases' own settings (claude_baseline).
    """
    if harness not in run.targets:
        raise ValueError(f"{harness} is not a target of this run")
    shared = _shared_id(cases)
    if shared:
        raise ValueError(shared)
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
    if baseline_skills is None:
        baseline_skills = (claude_baseline(runner, environ, timeout=timeout, workspace_root=workspace_root)
                           if harness == "claude-code" and checked["available"] else set())
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

        def result(outcome: str, reason: str, activation: str | None = None,
                   inventory: str = "not observable") -> RuntimeResult:
            return RuntimeResult(run_id=run.run_id, part_id=test_case["part_id"], case_id=test_case["id"],
                                 prompt=test_case["prompt"], expected=test_case["expected"],
                                 observed_activation=activation, result=outcome, reason=reason, file_hashes=hashes,
                                 harness=harness, harness_version=checked["version"], environment=environment,
                                 inventory=inventory)

        if not checked["available"]:
            results.append(result("not-run", f"unavailable: {checked['reason']}"))
            continue
        uncontained = [tool for tool in permitted if harness == "claude-code" and tool not in CLAUDE_CONTAINED]
        if uncontained:
            results.append(result("not-run", f"unavailable: Claude runs the case without a sandbox; "
                                             f"{', '.join(uncontained)} cannot be permitted, only "
                                             f"{', '.join(CLAUDE_CONTAINED)}"))
            continue
        staged = {path: data for path, data in files.items() if path.startswith(directory + "/")}
        # The repository's own links for the part, as they are: a missing one stays missing.
        links = {link: run.frozen_inputs["symlinks"][link] for link in run.part_links.get(test_case["part_id"], [])
                 if link in run.frozen_inputs["symlinks"]}
        outcome, reason, activation, inventory = activation_case(
            harness, staged, [directory], directory, test_case["prompt"], test_case["expected"], permitted, fixtures,
            runner=runner, environ=environ, baseline=set(baseline_skills), timeout=timeout,
            workspace_root=workspace_root, links=links)
        results.append(result(outcome, reason, activation, inventory))
    return results
