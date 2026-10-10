"""Research passes and live verification as the CLI runs them, with scripted workers."""

import json
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed, preflight
from harness_forge.contracts import update_outside_section
from harness_forge.fetch import FetchError
from harness_forge.runtime import load_cases
from harness_forge.workflow import research_pass, verify_live
from tests.test_adapters import PARENT, claude_stream
from tests.test_build_skill import NAME, REVISION
from tests.test_pipeline import CONTEXT, QUOTE, ROLES, SOURCE, ScriptedRunner, confirmed, staged_request
from tests.test_research import PAGE
from tests.test_verify import RULES, build_files, skill_decision

FORGE = Path(__file__).resolve().parents[1]


def fetch_page(url):
    if url == SOURCE:
        return PAGE
    raise FetchError(f"no page for {url}")


class ResearchRunner(ScriptedRunner):
    """Research answers with the findings the test sets; support answers each finding."""

    def __init__(self, findings, support="supported"):
        super().__init__()
        self.findings, self.support, self.support_ids = findings, support, []

    def __call__(self, command, *, cwd, env, stdin, timeout):
        if stdin and "```json" in stdin:
            request = staged_request(stdin)
            if request["role"] == "research":
                self.roles.append("research")
                self.worker_calls.append({"command": command, "cwd": cwd, "stdin": stdin, "env": env, "files": []})
                findings = [{**f, "brief_digest": request["inputs"]["brief_digest"], "pass": request["inputs"]["pass"],
                             "part_id": request["inputs"]["part_id"]} if "brief_digest" not in f else f
                            for f in self.findings]
                return Completed(0, claude_stream({"findings": findings}, tools=("WebFetch",)), "", False)
            if request["role"] == "source-support":
                self.roles.append("source-support")
                self.support_ids.append(request["inputs"]["finding_id"])
                self.worker_calls.append({"command": command, "cwd": cwd, "stdin": stdin, "env": env, "files": []})
                verdict = {"verdicts": [{"finding_id": request["inputs"]["finding_id"], "verdict": self.support,
                                         "reasoning": "Judged from the quote and its context."}]}
                return Completed(0, claude_stream(verdict, tools=()), "", False)
        return super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)


def finding(number=1, **changes):
    value = {"id": f"F{number}", "claim": "The reproducer tool is supported only on Linux", "source": SOURCE,
             "fetched": "2026-10-10", "quote": QUOTE, "context": CONTEXT, "source_role": "primary"}
    value.update(changes)
    return value


class ResearchPassTest(unittest.TestCase):
    def setUp(self):
        self.record = skill_decision(status="decided")
        self.record.record["accepted_revision"] = None

    def run_pass(self, findings, support="supported", existing=(), fetch=fetch_page):
        runner = ResearchRunner(findings, support)
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(self.record, None, planned, "intake-1")
        result = research_pass(self.record, "deep", "A", "claude-code", contract=contract, confirmation=confirmation,
                               worker_evidence=planned, run_id="intake-1", runner=runner, environ=PARENT,
                               fetch=fetch, existing_ids=list(existing))
        return result, runner

    def test_kept_findings_passed_both_checks_and_ids_continue_the_record(self):
        result, runner = self.run_pass([finding(1), finding(2, quote="A sentence the page does not contain.")],
                                       existing=["F1", "F2"])
        self.assertEqual([f["id"] for f in result["kept"]], ["F3"])
        self.assertEqual([d["id"] for d in result["dropped"]], ["F4"])
        self.assertEqual(runner.support_ids, ["F3"], "no support worker for a quote the page lacks")
        self.assertEqual({entry["finding_id"]: entry["quote_check"] for entry in result["support"]},
                         {"F3": "pass", "F4": "fail"})
        self.assertEqual(len(result["worker_evidence"]), 2)

    def test_unsupported_findings_are_dropped(self):
        result, _ = self.run_pass([finding(1)], support="unsupported")
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["support"][0]["verdict"], "unsupported")

    def test_rejected_sources_are_never_fetched(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return PAGE

        result, runner = self.run_pass([finding(1, source="https://x.com/someone/status/1")], fetch=fetch)
        self.assertEqual(fetched, [])
        self.assertEqual(runner.support_ids, [])
        self.assertIn("X platform", result["dropped"][0]["reason"])

    def test_a_finding_for_another_brief_is_dropped(self):
        result, _ = self.run_pass([finding(1, brief_digest="0" * 64, **{"pass": "deep", "part_id": "A"})])
        self.assertEqual(result["kept"], [])
        self.assertIn("brief", result["dropped"][0]["reason"])

    def test_an_unfetchable_source_is_dropped(self):
        result, _ = self.run_pass([finding(1, source="https://docs.example-vendor.com/gone")])
        self.assertEqual(result["kept"], [])
        self.assertIn("could not be fetched", result["dropped"][0]["reason"])


class VerifyLiveTest(unittest.TestCase):
    def test_a_confirmed_build_is_reviewed_tested_and_reported(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()
        runner = ScriptedRunner()
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
        run, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                   confirmation=confirmation, worker_evidence=planned,
                                   cases=load_cases(FORGE / "evals" / "smoke" / "vertical.yaml"), runner=runner,
                                   environ=PARENT, kb_version="0.1.0")
        part = {result.part_id: result for result in results}["A"]
        self.assertEqual((part.harness_result, part.goal_result), ("verified", "met by the verified build"),
                         part.rule_results)
        self.assertEqual(runner.roles, ["review", "runtime", "runtime", "runtime"])
        self.assertEqual(len(run.runtime_results), 3)
        self.assertTrue(any(e["role"] == "review" and e["observed_tools"]["status"] == "observed"
                            for e in run.worker_evidence))

    def test_a_file_changed_while_workers_ran_invalidates_the_part(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()
        runner = ScriptedRunner()
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
        changed = {**files, f".agents/skills/{NAME}/SKILL.md": files[f".agents/skills/{NAME}/SKILL.md"] + b"\nMore."}
        run, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                   confirmation=confirmation, worker_evidence=planned, cases=[], runner=runner,
                                   environ=PARENT, kb_version="0.1.0", collect=lambda: (changed, symlinks))
        part = {result.part_id: result for result in results}["A"]
        self.assertEqual(part.harness_result, "not verified")
        self.assertIn("inputs.bound", [r["rule_id"] for r in part.rule_results])
        self.assertEqual(runner.roles, ["review"], "the reviewer runs once")

    def test_an_out_of_scope_reviewer_voids_every_verdict(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()

        class Misbehaving(ScriptedRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "review" and len(self.roles) == 1 and stdin and "```json" in stdin:
                    output = json.loads(json.loads(answer.stdout.splitlines()[-1])["result"])
                    return Completed(0, claude_stream(output, calls=("Write",)), "", False)
                return answer

        runner = Misbehaving()
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
        run, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                   confirmation=confirmation, worker_evidence=planned, cases=[], runner=runner,
                                   environ=PARENT, kb_version="0.1.0")
        self.assertTrue(run.worker_violations)
        self.assertEqual({r.part_id: r for r in results}["A"].harness_result, "not verified")


if __name__ == "__main__":
    unittest.main()
