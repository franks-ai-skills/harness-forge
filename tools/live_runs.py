"""Automated live runs for T11: the vertical workflow and the routing smoke.

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
from harness_forge.contracts import load_decision, update_outside_section, write_decision  # noqa: E402
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


def vertical(harness: str, out: Path, *, forge: Forge | None = None,
             inputs_path: Path = ROOT / "evals" / "smoke" / "vertical-inputs.yaml",
             cases_path: Path = ROOT / "evals" / "smoke" / "vertical.yaml") -> dict:
    """Intake record, both research passes, acceptance, build and verification in one throwaway repository."""
    forge = forge or Forge()
    forge.module.RUNNER = Recorder(forge.module.RUNNER, out / "streams")
    inputs = yaml.safe_load(inputs_path.read_text(encoding="utf-8"))
    repo, scratch = out / "repo", out / "inputs"
    repo.mkdir(parents=True)
    summary = {"case": "vertical", "harness": harness, "confirmation": TEST_RELAY, "stages": [], "status": "stopped",
               "clean": None, "repository": str(repo)}

    def stage(name, result):
        code, payload, stderr = result
        summary["stages"].append({"stage": name, "exit": code, "output": payload, "stderr": stderr[-4000:]})
        return code, payload

    def finish(status):
        summary["status"] = status
        _write_json(out / "summary.json", summary)
        return summary

    manifest = json.loads((ROOT / "snapshot" / "kb-manifest.json").read_text(encoding="utf-8"))
    record = {**inputs["decision"], "kb_version": manifest["kb_version"]}
    date = datetime.date.today().isoformat()
    decision = write_decision(repo, Decision(record=record, body="Recorded reference idea for the automated run.\n"),
                              slug=inputs["slug"], date=date)
    (repo / "README.md").write_text("# Reproduction checklists\n", encoding="utf-8")

    code, workers = stage("workers", forge("workers", "--harness", harness))
    if code:
        return finish("stopped: a worker is unavailable or the login needs the cost question")
    evidence = _write_json(scratch / "workers.json", workers)

    def confirm(run_id, *extra):
        """The runner's own relay for the contract it just rendered (see TEST_RELAY)."""
        code, contract = stage(f"prepare-contract {run_id}", forge("prepare-contract", "--decision", decision,
                                                                      "--evidence", evidence, *extra))
        if code:
            return None
        return _write_json(scratch / f"relay-{run_id}.json", {
            "choice": "confirm", "displayed_digest": contract["digest"],
            "part_ids": [part["id"] for part in contract["parts"]], "run_id": run_id})

    intake = confirm("vertical-intake")
    if intake is None:
        return finish("stopped: the contract could not be prepared")
    for pass_name, part_id in (("survey", None), ("deep", "A")):
        arguments = ["research", "--decision", decision, "--root", repo, "--pass", pass_name, "--harness", harness,
                     "--evidence", evidence, "--relay", intake, "--slug", inputs["slug"], "--write"]
        if part_id:
            arguments += ["--part", part_id]
        code, _ = stage(f"research {pass_name}", forge(*arguments))
        if code:
            return finish(f"stopped: the {pass_name} pass did not complete")

    research = load_document(repo / ".harness" / "research" / f"{inputs['slug']}.md", "research")
    sources = sorted({f["source"] for f in research["findings"] if (f["pass"], f["part_id"]) == ("deep", "A")})
    if not sources:
        return finish("stopped: the deep pass kept no finding a requirement could cite")
    targets = inputs["decision"]["targets"]
    proposed = [{**requirement, "concept": "idea", "severity": "error", "check": "judged",
                 "applies_to": {"harnesses": targets}, "evidence": "Research", "sources": sources, "contested": False}
                for requirement in inputs["requirements"]]
    code, _ = stage("accept", forge("accept", "--decision", decision, "--root", repo, "--part", "A",
                                    "--proposed", _write_json(scratch / "proposed.json", {"requirements": proposed}),
                                    "--choices", _write_json(scratch / "choices.json", inputs["choices"]),
                                    "--slug", inputs["slug"]))
    if code:
        return finish("stopped: the requirements were not accepted")
    revision = repo / ".harness" / "research" / f"{inputs['slug']}.rules.yaml"

    verify_relay = confirm("vertical-verify", "--revision", revision)
    description = scratch / "description.txt"
    description.write_text(inputs["description"].strip(), encoding="utf-8")
    procedure = scratch / "procedure.md"
    procedure.write_text(inputs["procedure"], encoding="utf-8")
    code, _ = stage("build", forge("build", "--decision", decision, "--root", repo, "--part", "A",
                                   "--revision", revision, "--evidence", evidence, "--relay", verify_relay,
                                   "--description", description, "--procedure", procedure))
    if code:
        return finish("stopped: the build was refused")
    (repo / "README.md").write_text(update_outside_section((repo / "README.md").read_text(encoding="utf-8"),
                                                           load_decision(decision)), encoding="utf-8")

    verify_relay = confirm("vertical-verify", "--revision", revision)
    verify = ["verify", "--decision", decision, "--root", repo, "--revision", revision, "--evidence", evidence,
              "--relay", verify_relay, "--harness", harness, "--cases", cases_path, "--slug", inputs["slug"], "--write"]
    code, verified = stage("verify", forge(*verify))
    if verified is None:
        return finish("stopped: verification did not run")
    if verified["unresolved"]:
        # Like the contract: the runner confirms what a user would be asked, and says so.
        summary["not_applicable_confirmed"] = {"by": TEST_RELAY, "rules": verified["unresolved"]}
        confirmations = [argument for rule in verified["unresolved"]
                         for argument in ("--confirm-not-applicable", f"{rule['part_id']}:{rule['rule_id']}")]
        code, verified = stage("verify with confirmations", forge(*verify, *confirmations))
        if verified is None:
            return finish("stopped: verification with confirmations did not run")
    summary["parts"] = verified["parts"]
    summary["runtime"] = verified.get("runtime", [])
    summary["clean"] = _clean([case["reason"] for case in summary["runtime"]])
    return finish("completed")


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
    summary["status"] = "completed"
    _write_json(out / "summary.json", summary)
    return summary
