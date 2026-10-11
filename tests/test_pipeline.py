"""The offline vertical workflow through the real adapters with a scripted launcher.

Survey and deep research, independent source support, explicit acceptance,
a skill build, a fresh reviewer and runtime cases run in order; no model
is called. The live counterpart is tools/live_checks.py --case vertical.
"""

import copy
import json
import re
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed, preflight, run_worker
from harness_forge.build_skill import plan_skill
from harness_forge.contracts import confirm_contract, render_contract, update_outside_section
from harness_forge.documents import validate
from harness_forge.report import report_data
from harness_forge.research import (
    accept_requirements, finding_problem, make_brief, research_request, support_request, worker_finding,
)
from harness_forge.runtime import BASELINE_PROMPT, load_cases, run_skill_runtime
from harness_forge.verify import bound_inputs, finish_run, prepare_run, review_request, validate_review
from tests.test_adapters import PARENT, FakeRunner, claude_stream
from tests.test_build_skill import DESCRIPTION, NAME, PROCEDURE, idea_rule
from tests.test_research import PAGE
from tests.test_runtime import claude_run
from tests.test_verify import RULES, skill_decision

FORGE = Path(__file__).resolve().parents[1]
ROLES = ("research", "source-support", "review")
SOURCE = "https://docs.example-vendor.com/repro/platforms"
QUOTE = "The reproducer is supported only on Linux."
CONTEXT = "Platform support. The reproducer is supported only on Linux. macOS and Windows are not tested."


def staged_request(prompt: str) -> dict:
    """The request block the adapter appends after the worker instructions' own examples."""
    return json.loads(re.findall(r"```json\n(.*?)\n```", prompt, re.S)[-1])


class ScriptedRunner(FakeRunner):
    """Answers each worker by its staged request, and runtime cases with an activation."""

    def __init__(self):
        super().__init__()
        self.roles = []

    def __call__(self, command, *, cwd, env, stdin, timeout):
        launched = len(self.worker_calls)
        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if len(self.worker_calls) == launched:
            return answer
        if stdin == BASELINE_PROMPT:
            self.roles.append("baseline")
            return Completed(0, claude_run(skills=(), skill_call=None), "", False)
        if "```json" not in stdin:
            self.roles.append("runtime")
            near_miss = "do not need reproduction steps" in stdin
            return Completed(0, claude_run(skill_call=None if near_miss else NAME), "", False)
        request = staged_request(stdin)
        self.roles.append(request["role"])
        inputs = request["inputs"]
        if request["role"] == "research":
            number = "1" if inputs["pass"] == "survey" else "2"
            finding = {"claim": f"The reproducer tool is supported only on Linux ({number})", "source": SOURCE,
                       "fetched": "2026-10-10", "quote": QUOTE, "context": CONTEXT, "source_role": "primary"}
            return Completed(0, claude_stream({"findings": [finding]}, tools=("WebFetch", "WebSearch"),
                                              calls=("WebFetch",)), "", False)
        if request["role"] == "source-support":
            verdict = {"verdicts": [{"finding_id": inputs["finding_id"], "verdict": "supported",
                                     "reasoning": "The quote in its context states this platform limit."}]}
            return Completed(0, claude_stream(verdict, tools=()), "", False)
        output = {"run_id": request["run_id"],
                  "verdicts": [{"part_id": r["part_id"], "rule_id": r["rule_id"], "verdict": "pass",
                                "reason": "Meets it.", "citation": "SKILL.md"} for r in inputs["rules"]],
                  "goal_verdicts": [{"part_id": g["part_id"], "verdict": "met", "reason": "It writes the checklist."}
                                    for g in inputs["goals"]]}
        return Completed(0, claude_stream(output, calls=("Read",)), "", False)


def confirmed(record, revision, evidence, run_id):
    contract = render_contract(record, revision, [], worker_evidence=evidence)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": run_id}
    return contract, confirm_contract(contract, relay, run_id=run_id)


class VerticalWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.runner = ScriptedRunner()
        self.record = skill_decision(status="decided")
        self.record.record["accepted_revision"] = None
        self.planned = [preflight("claude-code", role, runner=self.runner, environ=PARENT)["configuration"]
                        for role in ROLES]

    def launch(self, request, contract, confirmation, **extra):
        return run_worker(request, "claude-code", contract=contract, confirmation=confirmation,
                          worker_evidence=self.planned, runner=self.runner, environ=PARENT, **extra)

    def test_no_worker_starts_before_the_contract_is_confirmed(self):
        contract = render_contract(self.record, None, [], worker_evidence=self.planned)
        request = research_request(make_brief(self.record, "survey", None), "intake-1", "survey-1")
        with self.assertRaises(ValueError):
            self.launch(request, contract, None)
        self.assertFalse(self.runner.worker_was_started)

    def test_a_contract_without_the_policy_disclosure_or_asking_for_certified_isolation_launches_nothing(self):
        for change in ({"disclosure": "Workers are fully isolated."}, {"isolation_status": "certified"},
                       {"policy": "strict-v2"}):
            contract, confirmation = confirmed(self.record, None, self.planned, "intake-1")
            changed = copy.deepcopy(contract)
            changed.worker_policy.update(change)
            request = research_request(make_brief(self.record, "survey", None), "intake-1", "survey-1")
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.launch(request, changed, confirmation)
        self.assertFalse(self.runner.worker_was_started)

    def test_the_vertical_workflow(self):
        record = self.record
        contract, confirmation = confirmed(record, None, self.planned, "intake-1")

        findings = []
        for pass_name, part_id in (("survey", None), ("deep", "A")):
            brief = make_brief(record, pass_name, part_id)
            response = self.launch(research_request(brief, "intake-1", f"{pass_name}-1"), contract, confirmation)
            self.assertEqual(response["status"], "completed", response.get("reason"))
            findings += [worker_finding(item, brief, f"F{len(findings) + 1}") for item in response["output"]["findings"]]
        self.assertEqual([f.pass_name for f in findings], ["survey", "deep"])

        kept = []
        for finding in findings:
            response = self.launch(support_request(finding, "intake-1", f"support-{finding.id}"), contract, confirmation)
            self.assertEqual(response["status"], "completed", response.get("reason"))
            if finding_problem(finding, PAGE, response["output"]["verdicts"][0]) is None:
                kept.append(finding)
        self.assertEqual([f.id for f in kept], ["F1", "F2"])

        deep = make_brief(record, "deep", "A")
        proposed = [idea_rule("linux-note", "The checklist names the platform the reproducer runs on",
                              sources=[kept[1].source]),
                    idea_rule("upload-logs", "The checklist uploads logs to the tracker")]
        revision = accept_requirements(proposed, {"idea.repro.a.linux-note": "accept",
                                                  "idea.repro.a.upload-logs": "reject"}, deep.digest)
        self.assertEqual(revision.rule_ids, ["idea.repro.a.linux-note"])
        record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
        contract, confirmation = confirmed(record, revision, self.planned, "verify-1")

        planned_files = plan_skill(record, "A", revision, RULES, worker_evidence=self.planned, confirmation=confirmation,
                                   description=DESCRIPTION, procedure=PROCEDURE)
        files = {key: value.encode() for key, value in planned_files.items() if not key.startswith(".claude/")}
        symlinks = {key: str(value) for key, value in planned_files.items() if key.startswith(".claude/")}
        files["README.md"] = update_outside_section("# Repro\n", record).encode()
        record.part("A")["status"] = "built"
        contract, confirmation = confirmed(record, revision, self.planned, "verify-1")
        run = prepare_run(record, files, RULES, confirmation, symlinks=symlinks, revision=revision, kb_version="0.1.0",
                          worker_evidence=self.planned)

        request = review_request(run, "review-1")
        reviewed = self.launch(request, contract, confirmation,
                               files={entry["path"]: files[entry["path"]] for entry in request["inputs"]["files"]})
        self.assertEqual(reviewed["status"], "completed", reviewed.get("reason"))
        review = validate_review(run, reviewed["output"])

        skill_files = {path: data for path, data in files.items() if path.startswith(f".agents/skills/{NAME}/")}
        runtime = run_skill_runtime(run, "claude-code", load_cases(FORGE / "evals" / "smoke" / "vertical.yaml"),
                                    files=skill_files, runner=self.runner, environ=PARENT)
        self.assertEqual([r.result for r in runtime], ["pass", "pass", "pass"], [r.reason for r in runtime])

        current = bound_inputs(record, files, RULES, symlinks=symlinks, revision=revision, kb_version="0.1.0",
                               worker_evidence=self.planned)
        results = finish_run(run, review, runtime, current, worker_observations=[reviewed["evidence"]])
        part = {result.part_id: result for result in results}["A"]
        self.assertEqual((part.harness_result, part.goal_result), ("verified", "met by the verified build"),
                         part.rule_results)

        data = report_data(run, results)
        validate(data, "report")
        self.assertEqual(data["revision_digest"], record.record["accepted_revision"]["digest"])
        rule_ids = [entry["rule_id"] for entry in data["parts"][0]["rule_results"]]
        self.assertEqual(len(rule_ids), len(set(rule_ids)), "every rule id appears exactly once")
        self.assertEqual(rule_ids.count("idea.repro.a.linux-note"), 1)
        self.assertNotIn("idea.repro.a.upload-logs", rule_ids, "a rejected requirement never reaches the report")
        self.assertIn(reviewed["evidence"], data["worker_evidence"])

        self.assertEqual(self.runner.roles,
                         ["research", "research", "source-support", "source-support", "review",
                          "baseline", "runtime", "runtime", "runtime"])
        workspaces = [call["cwd"] for call in self.runner.worker_calls]
        self.assertEqual(len(workspaces), len(set(workspaces)), "each worker is a separate process and workspace")
        support_prompts = [call["stdin"] for call, role in zip(self.runner.worker_calls, self.runner.roles)
                           if role == "source-support"]
        for prompt in support_prompts:
            self.assertNotIn(SOURCE, prompt, "the support worker judges the quote, not the researcher's source")


if __name__ == "__main__":
    unittest.main()
