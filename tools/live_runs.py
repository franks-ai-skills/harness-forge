"""Automated live runs: the vertical workflow and the routing smoke (T11), and T12's flows.

Development tooling, not part of the product, run without user input. Both
use the standard CLIs and the user's existing login; an API-key or unknown
login stops the run, because nobody is there to answer the cost question.

The vertical run goes through the same scripts/forge.py commands the skills
use, in a throwaway repository, with the recorded inputs in
evals/smoke/vertical-inputs.yaml. Nobody confirms its contract in a
session: the runner relays the confirmation itself, and every summary says
so. It also confirms each error rule the reviewer found not applicable,
recorded under `not_applicable_confirmed`. Its results show that the
workers, research, build and verification work live; the confirmation
ceremony is proven only by a real session.

The flows run adds a goal conflict, drift with an adopted override, a
broken skill, its repair and both re-verification scopes to the vertical.
Its conflict, drift, override and issue answer are fixtures, labelled as
such in the summary, and its issue search and publisher are stand-ins: it
never touches a real KB issue.

Every worker and runtime launch's raw stream is kept under `streams/`
next to the summary, in the git-ignored private results directory.

A run is clean only when every runtime or routing case observed the
harness's inventory and no user-level skill, plugin or MCP server stayed
loaded. Something loaded reports `clean: false` and fails the run. An
inventory the harness does not report, as Codex does not, reports
`clean: null` with an `inventory_gap` saying what stays unknown: such a
run is best-effort and passes, never a clean result.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness_forge.adapters.base import Completed, preflight, run_command  # noqa: E402
from harness_forge.contracts import write_decision  # noqa: E402
from harness_forge.documents import load_document  # noqa: E402
from harness_forge.models import Decision  # noqa: E402
from harness_forge.runtime import (  # noqa: E402
    BASELINE_PROMPT, UNOBSERVABLE, activation_case, claude_skill_origins, judge_run, sent_prompt, shadowing)

TEST_RELAY = "confirmed by the test runner it was started with, not by a user in a session"
UNCLEAN = ("competing skill", "executable components", "user-level skill named", "outside the throwaway workspace")
SKILLS = ("harness-intake", "harness-research", "build-skill", "harness-verify")


def load_forge():
    specification = importlib.util.spec_from_file_location("forge_cli_runner", ROOT / "scripts" / "forge.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class Forge:
    """scripts/forge.py in this process: the same commands the skills run."""

    def __init__(self, module=None):
        self.module = module or load_forge()

    def __call__(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main([str(argument) for argument in arguments])
        text = stdout.getvalue()
        return code, json.loads(text) if text.strip() else None, stderr.getvalue()


def _write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


class Recorder:
    """Keeps each worker and runtime launch's prompt, raw output and exit in ``directory`` for diagnosis.

    Metadata calls (version, help, login status) carry no prompt and are not
    kept; neither is the environment.
    """

    def __init__(self, runner, directory: Path):
        self.runner, self.directory, self.count = runner, directory, 0

    def __call__(self, command, *, cwd, env, stdin, timeout):
        completed = self.runner(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if stdin is not None:
            self.count += 1
            _write_json(self.directory / f"{self.count:03d}.json", {
                "command": list(command), "stdin": stdin, "returncode": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr, "timed_out": completed.timed_out})
        return completed


def _reviewed_parts(streams: Path, start: int) -> set[str]:
    """The parts the review workers launched after the first ``start`` recorded streams were asked to judge."""
    parts = set()
    for path in sorted(streams.glob("*.json"))[start:]:
        stdin = json.loads(path.read_text(encoding="utf-8")).get("stdin") or ""
        blocks = re.findall(r"```json\n(.*?)\n```", stdin, re.S)
        try:
            request = json.loads(blocks[-1]) if blocks else {}
        except ValueError:
            continue
        if isinstance(request, dict) and request.get("role") == "review":
            inputs = request.get("inputs", {})
            parts |= {item["part_id"] for item in inputs.get("rules", []) + inputs.get("goals", [])}
    return parts


def _clean(results: list[dict]) -> bool | None:
    """Clean only from observed inventories: False when anything stayed loaded, None when not observable."""
    if any(marker in result["reason"] for result in results for marker in UNCLEAN):
        return False
    if not results or any(result.get("inventory") != "observed" for result in results):
        return None
    return True


INVENTORY_GAP = ("the harness does not report the skills, plugins and MCP servers it loaded, so this run cannot rule "
                 "out a user-level one that stayed loaded; its results are best-effort")


def set_clean(summary: dict, results: list[dict]) -> None:
    """Record whether the run was clean, and the gap when the harness reported no inventory."""
    summary["clean"] = _clean(results)
    if summary["clean"] is None:
        summary["inventory_gap"] = INVENTORY_GAP
    else:
        summary.pop("inventory_gap", None)


def exit_code(summary: dict) -> int:
    """0 for a completed run unless something stayed loaded; an unobservable inventory is a stated gap."""
    return 0 if summary["status"] == "completed" and summary.get("clean") is not False else 1


# The forge.py commands that launch workers, and so check the login first.
WORKER_COMMANDS = ("workers", "research", "verify")


class _Session:
    """One throwaway repository driven through scripts/forge.py, with every stage kept in the summary."""

    def __init__(self, case: str, harness: str, out: Path, forge: Forge | None, inputs_path: Path,
                 cases_path: Path, login_answer: str | None = None):
        self.forge = forge or Forge()
        self.forge.module.RUNNER = Recorder(self.forge.module.RUNNER, out / "streams")
        self.harness, self.out, self.cases_path = harness, out, cases_path
        self.inputs = yaml.safe_load(inputs_path.read_text(encoding="utf-8"))
        self.repo, self.scratch = out / "repo", out / "inputs"
        self.repo.mkdir(parents=True)
        # The contributor's answer to the cost question, given for this run only; never read from disk.
        self.login_answer = login_answer
        self.summary = {"case": case, "harness": harness, "confirmation": TEST_RELAY, "stages": [],
                        "status": "stopped", "clean": None, "repository": str(self.repo), "login_answer": login_answer}
        self.decision = self.evidence = self.revision = None

    def stage(self, name, result):
        code, payload, stderr = result
        self.summary["stages"].append({"stage": name, "exit": code, "output": payload, "stderr": stderr[-4000:]})
        return code, payload

    def run(self, name, *arguments):
        if self.login_answer and arguments[0] in WORKER_COMMANDS:
            arguments += ("--login-answer", self.login_answer)
        return self.stage(name, self.forge(*arguments))

    def finish(self, status):
        self.summary["status"] = status
        _write_json(self.out / "summary.json", self.summary)
        return self.summary

    def record(self, decision: dict) -> None:
        manifest = json.loads((ROOT / "snapshot" / "kb-manifest.json").read_text(encoding="utf-8"))
        record = {**decision, "kb_version": manifest["kb_version"]}
        self.decision = write_decision(self.repo, Decision(record=record, body="Recorded reference idea for the "
                                                                                "automated run.\n"),
                                       slug=self.inputs["slug"], date=datetime.date.today().isoformat())
        if not (self.repo / "README.md").exists():
            (self.repo / "README.md").write_text("# Reproduction checklists\n", encoding="utf-8")

    def confirm(self, run_id, *extra):
        """The runner's own relay for the contract it just rendered (see TEST_RELAY)."""
        code, contract = self.run(f"prepare-contract {run_id}", "prepare-contract", "--decision", self.decision,
                                  "--evidence", self.evidence, *extra)
        if code:
            return None
        return _write_json(self.scratch / f"relay-{run_id}.json", {
            "choice": "confirm", "displayed_digest": contract["digest"],
            "part_ids": [part["id"] for part in contract["parts"]], "run_id": run_id})

    def through_build(self, run_prefix: str) -> str | None:
        """Workers, both research passes, acceptance, build and README; the reason it stopped, or None."""
        inputs, repo, scratch = self.inputs, self.repo, self.scratch
        code, workers = self.run("workers", "workers", "--harness", self.harness)
        if code:
            return "stopped: a worker is unavailable or the login needs the cost question"
        self.evidence = _write_json(scratch / "workers.json", workers)
        intake = self.confirm(f"{run_prefix}-intake")
        if intake is None:
            return "stopped: the contract could not be prepared"
        for pass_name, part_id in (("survey", None), ("deep", "A")):
            arguments = ["research", "--decision", self.decision, "--root", repo, "--pass", pass_name, "--harness",
                         self.harness, "--evidence", self.evidence, "--relay", intake, "--slug", inputs["slug"],
                         "--write"]
            if part_id:
                arguments += ["--part", part_id]
            code, _ = self.run(f"research {pass_name}", *arguments)
            if code:
                return f"stopped: the {pass_name} pass did not complete"
        research = load_document(repo / ".harness" / "research" / f"{inputs['slug']}.md", "research")
        sources = sorted({f["source"] for f in research["findings"] if (f["pass"], f["part_id"]) == ("deep", "A")})
        if not sources:
            return "stopped: the deep pass kept no finding a requirement could cite"
        targets = inputs["decision"]["targets"]
        proposed = [{**requirement, "concept": "idea", "severity": "error", "check": "judged",
                     "applies_to": {"harnesses": targets}, "evidence": "Research", "sources": sources,
                     "contested": False} for requirement in inputs["requirements"]]
        code, _ = self.run("accept", "accept", "--decision", self.decision, "--root", repo, "--part", "A",
                           "--proposed", _write_json(scratch / "proposed.json", {"requirements": proposed}),
                           "--choices", _write_json(scratch / "choices.json", inputs["choices"]),
                           "--slug", inputs["slug"])
        if code:
            return "stopped: the requirements were not accepted"
        self.revision = repo / ".harness" / "research" / f"{inputs['slug']}.rules.yaml"
        code = self.build(f"{run_prefix}-verify")
        if code:
            return "stopped: the build was refused"
        code, _ = self.run("readme", "readme", "--decision", self.decision, "--root", repo)
        if code:
            return "stopped: the README's outside controls were not written"
        return None

    def build(self, run_id: str, *extra) -> int:
        relay = self.confirm(run_id, "--revision", self.revision)
        description = self.scratch / "description.txt"
        description.write_text(self.inputs["description"].strip(), encoding="utf-8")
        procedure = self.scratch / "procedure.md"
        procedure.write_text(self.inputs["procedure"], encoding="utf-8")
        code, _ = self.run("build", "build", "--decision", self.decision, "--root", self.repo, "--part", "A",
                           "--revision", self.revision, "--evidence", self.evidence, "--relay", relay,
                           "--description", description, "--procedure", procedure, *extra)
        return code

    def verify(self, run_id: str, name: str, *extra, refresh=None):
        """verify with the runner's relay; error rules the reviewer found not applicable are confirmed and said so.

        ``refresh`` gives arguments asked again before each attempt, such as the
        scope choice, which every earlier run's report changes.
        """
        relay = self.confirm(run_id, "--revision", self.revision)
        verify = ["verify", "--decision", self.decision, "--root", self.repo, "--revision", self.revision,
                  "--evidence", self.evidence, "--relay", relay, "--harness", self.harness, "--cases",
                  self.cases_path, "--slug", self.inputs["slug"], "--write", *extra]
        code, verified = self.run(name, *verify, *(refresh(relay) if refresh else []))
        if verified is not None and verified["unresolved"]:
            # Like the contract: the runner confirms what a user would be asked, and says so.
            self.summary.setdefault("not_applicable_confirmed", {"by": TEST_RELAY, "rules": []})
            self.summary["not_applicable_confirmed"]["rules"] += verified["unresolved"]
            confirmations = [argument for rule in verified["unresolved"]
                             for argument in ("--confirm-not-applicable", rule["confirmation"])]
            code, verified = self.run(f"{name} with confirmations", *verify, *confirmations,
                                      *(refresh(relay) if refresh else []))
        return code, verified

    def verified(self, code, verified) -> bool:
        return code == 0 and verified is not None and all(part["harness_result"] == "verified"
                                                          for part in verified["parts"] if part["part_id"] == "A")


def vertical(harness: str, out: Path, *, forge: Forge | None = None,
             inputs_path: Path = ROOT / "evals" / "smoke" / "vertical-inputs.yaml",
             cases_path: Path = ROOT / "evals" / "smoke" / "vertical.yaml", login_answer: str | None = None) -> dict:
    """Intake record, both research passes, acceptance, build and verification in one throwaway repository."""
    session = _Session("vertical", harness, out, forge, inputs_path, cases_path, login_answer)
    session.record(session.inputs["decision"])
    stopped = session.through_build("vertical")
    if stopped:
        return session.finish(stopped)
    code, verified = session.verify("vertical-verify", "verify")
    if verified is None:
        return session.finish("stopped: verification did not run")
    session.summary["parts"] = verified["parts"]
    session.summary["runtime"] = verified.get("runtime", [])
    set_clean(session.summary, session.summary["runtime"])
    # The vertical proves the workflow only when verification succeeded: verify exits 1 otherwise.
    return session.finish("completed" if session.verified(code, verified) else "not verified")


FIXTURE = ("FIXTURE for Harness Forge's automated live check: synthetic, not a genuine finding about the Harness KB; "
           "nothing is published")


def _fixture_issues():
    """A KB issue search that finds nothing and a publisher that must never be called: no real KB issue is touched."""
    def publisher(proposal):
        raise AssertionError("the flows run never files a KB issue")
    return (lambda rule_id: []), publisher


def flows(harness: str, out: Path, *, forge: Forge | None = None,
          inputs_path: Path = ROOT / "evals" / "smoke" / "vertical-inputs.yaml",
          cases_path: Path = ROOT / "evals" / "smoke" / "vertical.yaml", login_answer: str | None = None) -> dict:
    """T12's flows live: a goal conflict, drift with an adopted override, a break and repair, and both scopes.

    The goal conflict, the drift, its override and the issue answer are
    labelled fixtures in the summary; the issue search and publisher are
    fixtures too, so nothing reaches the real KB. Research, review and
    runtime cases run live, as in the vertical.
    """
    session = _Session("flows", harness, out, forge, inputs_path, cases_path, login_answer)
    session.forge.module.ISSUES = _fixture_issues()
    inputs, repo, scratch = session.inputs, session.repo, session.scratch
    checks = session.summary["checks"] = {}
    session.summary["fixtures"] = {
        "goal conflict": f"{FIXTURE}: the recorded part chose a skill although it needs its own context",
        "drift": f"{FIXTURE}: drift recorded against skills.description.length from a kept deep finding",
        "override": f"{FIXTURE}: the adopted override raises the description limit",
        "issue": f"{FIXTURE}: the issue search and publisher are stand-ins, and the proposal is declined"}

    # A goal conflict: the part chose a skill, but its own-context answer rules skills out.
    decision = json.loads(json.dumps(inputs["decision"]))
    part = decision["parts"][0]
    part["answers"]["own_context"] = True
    part.update(recommended="subagents", override_reason=session.summary["fixtures"]["goal conflict"])
    session.record(decision)
    facts = decision["facts"]
    request = _write_json(scratch / "select-conflict.json", {"answers": part["answers"], "whole_idea": facts,
                                                             "chosen": "skills", "goal": part["goal"]})
    code, selected = session.run("select with the chosen mechanism", "select", "--input", request)
    checks["goal conflict asked"] = code == 0 and (selected.get("goal_conflict") or {}).get("severity") == "error"
    code, resolved = session.run("resolve-conflict", "resolve-conflict", "--decision", session.decision, "--part", "A",
                                 "--choice", "adjust-goal", "--adjusted-goal",
                                 "Write a reproduction checklist when asked, in the session's context",
                                 "--reason", session.summary["fixtures"]["goal conflict"])
    record = load_document(session.decision, "decision")
    answers = {**record["parts"][0]["answers"], "own_context": False}  # the re-asked question, answered
    request = _write_json(scratch / "select-again.json", {"answers": answers, "whole_idea": facts})
    code, again = session.run("select again", "select", "--input", request)
    checks["goal adjusted and selected again"] = (resolved is not None and resolved["next_stage"] == "questions"
                                                  and code == 0 and again["mechanism"] == "skills")
    record["parts"][0].update(answers=answers, outcome=again["outcome"], recommended=again["mechanism"],
                              override_reason=None)
    # A second, unlinked part an outside control owns, so a Review has a part to keep.
    publishing = {**answers, "irreversible": True}
    request = _write_json(scratch / "select-b.json", {"answers": publishing, "whole_idea": facts})
    _, selected_b = session.run("select part B", "select", "--input", request)
    record["parts"].append({
        "id": "B", "slug": "publish-checklist", "goal": "Publish approved checklists to the tracker",
        "status": "decided", "answers": publishing, "outcome": selected_b["outcome"], "recommended": None,
        "chosen": None, "override_reason": None, "alternatives": [], "connects_to": [],
        "outside_controls": [{"control": control["id"], "prerequisites": control["prerequisites"],
                              "availability": control["availability"], "required_for_goal": control["required_for_goal"]}
                             for control in selected_b["controls"]]})
    write_decision(repo, Decision(record=record, body="Recorded reference idea for the automated run.\n"),
                   slug=inputs["slug"], date=session.decision.name[:10])

    stopped = session.through_build("flows")
    if stopped:
        return session.finish(stopped)

    # Drift from a kept deep finding, a declined issue, and an adopted override.
    research = load_document(repo / ".harness" / "research" / f"{inputs['slug']}.md", "research")
    finding = next(f["id"] for f in research["findings"] if (f["pass"], f["part_id"]) == ("deep", "A"))
    code, _ = session.run("drift", "drift", "--root", repo, "--slug", inputs["slug"], "--finding", finding, "--rule",
                          "skills.description.length", "--category", "compatibility", "--summary",
                          session.summary["fixtures"]["drift"])
    checks["drift recorded"] = code == 0
    code, proposal = session.run("drift-issue", "drift-issue", "--root", repo, "--rule", "skills.description.length",
                                 "--finding", finding, "--run-id", "flows-drift", "--out", scratch / "proposal.json")
    issue = None
    if proposal:
        relay = _write_json(scratch / "issue-relay.json", {"choice": "decline", "displayed_digest": proposal["digest"],
                                                           "run_id": "flows-drift"})
        _, published = session.run("publish-issue", "publish-issue", "--root", repo, "--proposal",
                                    scratch / "proposal.json", "--relay", relay)
        issue = (published or {}).get("issue")
    checks["issue declined, nothing filed"] = issue == "not published"
    pinned = next(rule for rule in yaml.safe_load((ROOT / "snapshot/rules/skills.yaml").read_text(encoding="utf-8"))
                  if rule["id"] == "skills.description.length")
    request = _write_json(scratch / "override.json", {
        "rule_id": "skills.description.length", "finding_id": finding, "reason": session.summary["fixtures"]["override"],
        "rule": {**pinned, "params": {"max_chars": 2048},
                 "statement": "The description is present and at most 2,048 characters"}})
    session.run("override", "override", "--root", repo, "--input", request, "--write")
    session.run("drift-decision", "drift-decision", "--decision", session.decision, "--root", repo, "--rule",
                "skills.description.length", "--finding", finding, "--choice", "adopt", "--reason",
                session.summary["fixtures"]["override"])

    code, verified = session.verify("flows-verify-1", "verify with the override")
    report = (repo / ".harness" / "reports" / f"{inputs['slug']}.md").read_text(encoding="utf-8") \
        if (repo / ".harness" / "reports" / f"{inputs['slug']}.md").exists() else ""
    checks["verified with the adopted override"] = (session.verified(code, verified)
                                                    and "verified with 1 override" in report)

    # Break the skill, then repair only that file and re-verify with each scope.
    skill = repo / ".agents" / "skills" / record["parts"][0]["slug"] / "SKILL.md"
    original = skill.read_text(encoding="utf-8")
    skill.write_text(original.replace(f"name: {record['parts'][0]['slug']}", "name: Broken_Name"), encoding="utf-8")
    code, broken = session.verify("flows-verify-2", "verify the broken skill")
    checks["broken skill not verified"] = code == 1 and broken is not None and \
        broken["parts"][0]["harness_result"] == "not verified"
    session.build("flows-repair", "--replace", skill.relative_to(repo).as_posix())
    def review_choice(relay):
        """The scope question asked again before each attempt, answered by the runner (see TEST_RELAY)."""
        _, scope = session.run("scope", "scope", "--harness", session.harness, "--decision", session.decision,
                               "--root", repo, "--revision",
                               session.revision, "--evidence", session.evidence, "--relay", relay, "--slug",
                               inputs["slug"], "--fixed", "A", "--cases", session.cases_path)
        review = next((option for option in (scope or {}).get("options", [])
                       if option["label"].startswith("Review")), {})
        chosen = _write_json(scratch / "scope-relay.json", {"choice": "review", "displayed_digest": review.get("digest"),
                                                            "run_id": "flows-verify-3"})
        return ["--scope", "review", "--fixed", "A", "--scope-relay", chosen]

    streams = len(list((session.out / "streams").glob("*.json")))
    code, reviewed = session.verify("flows-verify-3", "verify a Review", refresh=review_choice)
    checks["review verified the repair"] = session.verified(code, reviewed)
    # What the reviewers were actually asked, and B's results kept with their provenance, not the reported scope.
    report = (repo / ".harness" / "reports" / f"{inputs['slug']}.md").read_text(encoding="utf-8")
    checks["review judged the repaired part and kept the other"] = (
        reviewed is not None and _reviewed_parts(session.out / "streams", streams) == {"A"}
        and (reviewed.get("judged_parts"), reviewed.get("kept_parts")) == (["A"], ["B"])
        and "(kept from run" in report.split("## Part B", 1)[-1].split("## Runtime tests", 1)[0])
    streams = len(list((session.out / "streams").glob("*.json")))
    code, extensive = session.verify("flows-verify-4", "verify an Extensive review", "--scope", "extensive")
    checks["extensive review verified"] = session.verified(code, extensive)
    checks["extensive review judged every part"] = extensive is not None and (
        extensive.get("judged_parts"), extensive.get("kept_parts")) == (["A", "B"], []) and \
        _reviewed_parts(session.out / "streams", streams) == {"A", "B"}

    runtime = [case for result in (verified, reviewed, extensive) if result for case in result.get("runtime", [])]
    session.summary["parts"] = (extensive or {}).get("parts")
    session.summary["runtime"] = runtime
    set_clean(session.summary, runtime)
    return session.finish("completed" if all(checks.values()) else "flows did not hold")


# The reasons for which Codex cannot show a routing result: it reports no inventory, so neither a
# non-activation nor an unread skill is observable. Counted in the pass rates, not as skipped cases.
CODEX_UNOBSERVABLE = UNOBSERVABLE["codex"]


def _all_routing_cases(skill: str) -> list[dict]:
    """Every case of the skill's routing file."""
    return yaml.safe_load((ROOT / "evals" / skill / "routing.yaml").read_text(encoding="utf-8"))["cases"]


def _routing_cases(skill: str) -> list[dict]:
    """One explicit, one implicit positive and one near miss from the skill's routing file."""
    cases = _all_routing_cases(skill)
    explicit = next(c for c in cases if c["mode"] == "explicit")
    implicit = next(c for c in cases if c["mode"] == "implicit" and c["expect"] == "trigger")
    near = next(c for c in cases if c["expect"] == "no-trigger")
    return [explicit, implicit, near]


def _baseline(harness: str, runner, environ) -> tuple[set[str], set[str] | None, list[str]]:
    """Claude's bundled and user-level skills, from two empty launches with the cases' settings (the second
    with bundled skills off); None for unknown origins. Codex lists none."""
    if harness != "claude-code":
        return set(), set(), []
    bundled, user = claude_skill_origins(runner, environ)
    return bundled, user, [f"bundled skills: {', '.join(sorted(bundled)) or 'none'}",
                           "user-level skills: " + (", ".join(sorted(user)) or "none" if user is not None
                                                    else "unknown")]


def routing_smoke(harness: str, out: Path, *, runner=run_command, environ=None,
                  login_answer: str | None = None) -> dict:
    """Forge's four skills installed together; per skill an explicit, an implicit and a near-miss prompt."""
    return _routing(harness, out, "routing-smoke", _routing_cases, 1, runner=runner, environ=environ,
                    login_answer=login_answer)


def routing(harness: str, out: Path, *, runner=run_command, environ=None, login_answer: str | None = None,
            repeats: int = 3) -> dict:
    """Plan task 14's routing evaluation: every case of every skill's routing file, each run ``repeats`` times
    in a fresh session, with the pass counts per skill and mode."""
    summary = _routing(harness, out, "routing", _all_routing_cases, repeats, runner=runner, environ=environ,
                       login_answer=login_answer)
    summary["rates"] = _rates(summary["results"])
    _write_json(out / "summary.json", summary)
    return summary


def _rates(results: list[dict]) -> dict[str, dict]:
    """Runs and passes per skill and mode; every near miss counts as one mode."""
    rates: dict[str, dict] = {}
    for result in results:
        mode = "near_miss" if result["expected"] != "activates" else result["mode"]
        counts = rates.setdefault(result["skill"], {}).setdefault(mode, {"runs": 0, "passed": 0})
        counts["runs"] += 1
        counts["passed"] += result["result"] == "pass"
    return rates


def _recorded_workspace(harness: str, stream: dict) -> Path | None:
    """The throwaway workspace a recorded case ran in: Codex's -C argument, Claude's init event cwd."""
    if harness == "codex":
        command = stream["command"]
        return Path(command[command.index("-C") + 1]) if "-C" in command else None
    for line in stream["stdout"].splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init" and isinstance(event.get("cwd"), str):
            return Path(event["cwd"])
    return None


def rescore(out: Path, *, scorer: str) -> dict:
    """Judge a recorded routing or routing-smoke run again with the current observer, launching nothing.

    Each case's recorded stream is judged as activation_case judges a live
    one, under its own workspace path. The result goes to ``rescored.json``
    beside the run, with each case's recorded result; the recorded files
    stay as they are. ``scorer`` names the Forge revision that judged, and
    ``skills_sha256`` the skill files it matched reads against: today's,
    which must be the ones the run installed.
    A run with a case that never launched is refused: its streams cannot be
    bound to the results one to one.
    """
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    harness = summary["harness"]
    # The recorder numbers launches in order: Claude's baseline launches, then one per case that launched.
    streams = [stream for stream in (json.loads(path.read_text(encoding="utf-8"))
                                     for path in sorted((out / "streams").glob("*.json")))
               if stream["stdin"] != BASELINE_PROMPT]
    if len(streams) != len(summary["results"]) or any(
            sent_prompt(stream["stdin"]) != result["prompt"] for stream, result in zip(streams, summary["results"])):
        # A case that never launched has no stream, and another case may share its prompt.
        raise ValueError(f"cannot bind {len(streams)} recorded case streams to {len(summary['results'])} results "
                         "one to one")
    files = {}
    for skill in SKILLS:
        source = ROOT / "skills" / skill
        for path in sorted(source.rglob("*")):
            if path.is_file():
                files[f".agents/skills/{skill}/{path.relative_to(source).as_posix()}"] = path.read_bytes()
    permitted = ["Skill", "Read"] if harness == "claude-code" else ["command_execution"]
    baseline = set(summary.get("baseline", {}).get("skills", []))
    results = []
    for recorded, stream in zip(summary["results"], streams):
        completed = Completed(stream["returncode"], stream["stdout"], stream["stderr"], stream["timed_out"])
        outcome, reason, activation, inventory = judge_run(
            harness, completed, files, f".agents/skills/{recorded['skill']}", _recorded_workspace(harness, stream),
            baseline | (set(SKILLS) - {recorded["skill"]}), recorded["expected"], permitted,
            slash=recorded["mode"] == "explicit")
        results.append({**recorded, "recorded_result": recorded["result"], "recorded_reason": recorded["reason"],
                        "result": outcome, "reason": reason, "observed_activation": activation,
                        "inventory": inventory})
    digest = hashlib.sha256()
    for path, data in sorted(files.items()):
        digest.update(path.encode() + b"\0" + hashlib.sha256(data).digest())
    rescored = {"case": summary["case"], "harness": harness, "scorer": scorer, "skills_sha256": digest.hexdigest(),
                "results": results, "rates": _rates(results)}
    _write_json(out / "rescored.json", rescored)
    return rescored


def _routing(harness: str, out: Path, name: str, select, repeats: int, *, runner, environ,
             login_answer: str | None) -> dict:
    environ = os.environ if environ is None else environ
    out.mkdir(parents=True, exist_ok=True)
    runner = Recorder(runner, out / "streams")
    summary = {"case": name, "harness": harness, "results": [], "status": "stopped", "clean": None,
               "login_answer": login_answer, "repeats": repeats}
    checked = preflight(harness, "review", runner=runner, environ=environ, login_answer=login_answer)
    summary["preflight"] = {key: checked[key] for key in ("available", "reason", "version", "login")}
    if not checked["available"]:
        _write_json(out / "summary.json", summary)
        return summary
    baseline, user, behavior = _baseline(harness, runner, environ)
    summary["baseline"] = {"skills": sorted(baseline), "user_skills": sorted(user) if user is not None else None,
                           "observed": behavior}
    if user is None:
        # Not Codex's accepted inventory gap: without the origins no Claude case can run, so the smoke stops.
        summary["status"] = "stopped: an empty Claude Code launch failed, timed out or listed no skills"
        _write_json(out / "summary.json", summary)
        return summary
    files, directories = {}, []
    for skill in SKILLS:
        source = ROOT / "skills" / skill
        directories.append(f".agents/skills/{skill}")
        for path in sorted(source.rglob("*")):
            if path.is_file():
                files[f".agents/skills/{skill}/{path.relative_to(source).as_posix()}"] = path.read_bytes()
    permitted = ["Skill", "Read"] if harness == "claude-code" else ["command_execution"]
    marker = "/" if harness == "claude-code" else "$"
    for skill, case, repeat in ((skill, case, repeat) for repeat in range(1, repeats + 1) for skill in SKILLS
                                for case in select(skill)):
            prompt = f"{marker}{skill} {case['prompt']}" if case["mode"] == "explicit" else case["prompt"]
            expected = "activates" if case["expect"] == "trigger" else "does-not-activate"
            # A user-level or bundled skill of its name would answer instead, as run_skill_runtime also refuses.
            blocked = shadowing(skill, user, baseline)
            if blocked:
                outcome, reason, activation, inventory = "not-run", blocked, None, "not observable"
            else:
                outcome, reason, activation, inventory = activation_case(
                    harness, files, directories, f".agents/skills/{skill}", prompt, expected, permitted, {},
                    runner=runner, environ=environ, baseline=baseline, slash=case["mode"] == "explicit",
                    off=user)
            summary["results"].append({"skill": skill, "case_id": case["id"], "repeat": repeat, "mode": case["mode"],
                                       "prompt": prompt,
                                       "expected": expected, "result": outcome, "reason": reason,
                                       "observed_activation": activation, "inventory": inventory})
    set_clean(summary, summary["results"])
    failed = any(result["result"] in ("fail", "error") for result in summary["results"])
    # A case that did not run blocks completion, except where Codex cannot show the result at all.
    skipped = [result for result in summary["results"] if result["result"] == "not-run"
               and not result["reason"].startswith(CODEX_UNOBSERVABLE)]
    summary["status"] = ("completed with failures" if failed else
                         f"incomplete: {len(skipped)} case(s) did not run" if skipped else "completed")
    _write_json(out / "summary.json", summary)
    return summary
