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

A run is clean only when no user-level skill, plugin or MCP server stayed
loaded in a runtime or routing case; otherwise it reports `clean: false`
for the user to decide, never a clean result.
"""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness_forge.adapters.base import preflight, run_command  # noqa: E402
from harness_forge.contracts import write_decision  # noqa: E402
from harness_forge.documents import load_document  # noqa: E402
from harness_forge.models import Decision  # noqa: E402
from harness_forge.runtime import activation_case, claude_baseline  # noqa: E402

TEST_RELAY = "confirmed by the test runner it was started with, not by a user in a session"
UNCLEAN = ("competing skill", "executable components")
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


def _clean(reasons: list[str]) -> bool:
    return not any(marker in reason for reason in reasons for marker in UNCLEAN)


class _Session:
    """One throwaway repository driven through scripts/forge.py, with every stage kept in the summary."""

    def __init__(self, case: str, harness: str, out: Path, forge: Forge | None, inputs_path: Path,
                 cases_path: Path):
        self.forge = forge or Forge()
        self.forge.module.RUNNER = Recorder(self.forge.module.RUNNER, out / "streams")
        self.harness, self.out, self.cases_path = harness, out, cases_path
        self.inputs = yaml.safe_load(inputs_path.read_text(encoding="utf-8"))
        self.repo, self.scratch = out / "repo", out / "inputs"
        self.repo.mkdir(parents=True)
        self.summary = {"case": case, "harness": harness, "confirmation": TEST_RELAY, "stages": [],
                        "status": "stopped", "clean": None, "repository": str(self.repo)}
        self.decision = self.evidence = self.revision = None

    def stage(self, name, result):
        code, payload, stderr = result
        self.summary["stages"].append({"stage": name, "exit": code, "output": payload, "stderr": stderr[-4000:]})
        return code, payload

    def run(self, name, *arguments):
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

    def verify(self, run_id: str, name: str, *extra):
        """verify with the runner's relay; error rules the reviewer found not applicable are confirmed and said so."""
        relay = self.confirm(run_id, "--revision", self.revision)
        verify = ["verify", "--decision", self.decision, "--root", self.repo, "--revision", self.revision,
                  "--evidence", self.evidence, "--relay", relay, "--harness", self.harness, "--cases",
                  self.cases_path, "--slug", self.inputs["slug"], "--write", *extra]
        code, verified = self.run(name, *verify)
        if verified is not None and verified["unresolved"]:
            # Like the contract: the runner confirms what a user would be asked, and says so.
            self.summary.setdefault("not_applicable_confirmed", {"by": TEST_RELAY, "rules": []})
            self.summary["not_applicable_confirmed"]["rules"] += verified["unresolved"]
            confirmations = [argument for rule in verified["unresolved"]
                             for argument in ("--confirm-not-applicable", rule["confirmation"])]
            code, verified = self.run(f"{name} with confirmations", *verify, *confirmations)
        return code, verified

    def verified(self, code, verified) -> bool:
        return code == 0 and verified is not None and all(part["harness_result"] == "verified"
                                                          for part in verified["parts"] if part["part_id"] == "A")


def vertical(harness: str, out: Path, *, forge: Forge | None = None,
             inputs_path: Path = ROOT / "evals" / "smoke" / "vertical-inputs.yaml",
             cases_path: Path = ROOT / "evals" / "smoke" / "vertical.yaml") -> dict:
    """Intake record, both research passes, acceptance, build and verification in one throwaway repository."""
    session = _Session("vertical", harness, out, forge, inputs_path, cases_path)
    session.record(session.inputs["decision"])
    stopped = session.through_build("vertical")
    if stopped:
        return session.finish(stopped)
    code, verified = session.verify("vertical-verify", "verify")
    if verified is None:
        return session.finish("stopped: verification did not run")
    session.summary["parts"] = verified["parts"]
    session.summary["runtime"] = verified.get("runtime", [])
    session.summary["clean"] = _clean([case["reason"] for case in session.summary["runtime"]])
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
          cases_path: Path = ROOT / "evals" / "smoke" / "vertical.yaml") -> dict:
    """T12's flows live: a goal conflict, drift with an adopted override, a break and repair, and both scopes.

    The goal conflict, the drift, its override and the issue answer are
    labelled fixtures in the summary; the issue search and publisher are
    fixtures too, so nothing reaches the real KB. Research, review and
    runtime cases run live, as in the vertical.
    """
    session = _Session("flows", harness, out, forge, inputs_path, cases_path)
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
    session.run("scope", "scope", "--decision", session.decision, "--root", repo, "--revision", session.revision,
                "--evidence", session.evidence, "--slug", inputs["slug"], "--fixed", "A")
    code, reviewed = session.verify("flows-verify-3", "verify a Review", "--scope", "review", "--fixed", "A")
    checks["review verified the repair"] = session.verified(code, reviewed)
    code, extensive = session.verify("flows-verify-4", "verify an Extensive review", "--scope", "extensive")
    checks["extensive review verified"] = session.verified(code, extensive)

    runtime = [case for result in (verified, reviewed, extensive) if result for case in result.get("runtime", [])]
    session.summary["parts"] = (extensive or {}).get("parts")
    session.summary["runtime"] = runtime
    session.summary["clean"] = _clean([case["reason"] for case in runtime])
    return session.finish("completed" if all(checks.values()) else "flows did not hold")


def _routing_cases(skill: str) -> list[dict]:
    """One explicit, one implicit positive and one near miss from the skill's routing file."""
    cases = yaml.safe_load((ROOT / "evals" / skill / "routing.yaml").read_text(encoding="utf-8"))["cases"]
    explicit = next(c for c in cases if c["mode"] == "explicit")
    implicit = next(c for c in cases if c["mode"] == "implicit" and c["expect"] == "trigger")
    near = next(c for c in cases if c["expect"] == "no-trigger")
    return [explicit, implicit, near]


def _baseline(harness: str, runner, environ) -> tuple[set[str], list[str]]:
    """Claude's built-in skills, from one empty launch with the cases' settings; Codex lists none."""
    if harness != "claude-code":
        return set(), []
    skills = claude_baseline(runner, environ)
    return skills, [f"skills listed: {', '.join(sorted(skills))}"]


def routing_smoke(harness: str, out: Path, *, runner=run_command, environ=None) -> dict:
    """Forge's four skills installed together; per skill an explicit, an implicit and a near-miss prompt."""
    environ = os.environ if environ is None else environ
    out.mkdir(parents=True, exist_ok=True)
    runner = Recorder(runner, out / "streams")
    summary = {"case": "routing-smoke", "harness": harness, "results": [], "status": "stopped", "clean": None}
    checked = preflight(harness, "review", runner=runner, environ=environ)
    summary["preflight"] = {key: checked[key] for key in ("available", "reason", "version", "login")}
    if not checked["available"]:
        _write_json(out / "summary.json", summary)
        return summary
    baseline, behavior = _baseline(harness, runner, environ)
    summary["baseline"] = {"skills": sorted(baseline), "observed": behavior}
    files, directories = {}, []
    for skill in SKILLS:
        source = ROOT / "skills" / skill
        directories.append(f".agents/skills/{skill}")
        for path in sorted(source.rglob("*")):
            if path.is_file():
                files[f".agents/skills/{skill}/{path.relative_to(source).as_posix()}"] = path.read_bytes()
    permitted = ["Skill", "Read"] if harness == "claude-code" else ["command_execution"]
    marker = "/" if harness == "claude-code" else "$"
    for skill in SKILLS:
        for case in _routing_cases(skill):
            prompt = f"{marker}{skill} {case['prompt']}" if case["mode"] == "explicit" else case["prompt"]
            expected = "activates" if case["expect"] == "trigger" else "does-not-activate"
            outcome, reason, activation = activation_case(
                harness, files, directories, f".agents/skills/{skill}", prompt, expected, permitted, {},
                runner=runner, environ=environ, baseline=baseline, slash=case["mode"] == "explicit")
            summary["results"].append({"skill": skill, "case_id": case["id"], "mode": case["mode"], "prompt": prompt,
                                       "expected": expected, "result": outcome, "reason": reason,
                                       "observed_activation": activation})
    summary["clean"] = _clean([result["reason"] for result in summary["results"]])
    failed = any(result["result"] in ("fail", "error") for result in summary["results"])
    summary["status"] = "completed with failures" if failed else "completed"
    _write_json(out / "summary.json", summary)
    return summary
