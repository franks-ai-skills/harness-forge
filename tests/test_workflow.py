"""Research passes and live verification as the CLI runs them, with scripted workers."""

import copy
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
                return Completed(0, claude_stream({"findings": self.findings}, tools=("WebFetch",)), "", False)
            if request["role"] == "source-support":
                self.roles.append("source-support")
                self.support_ids.append(request["inputs"]["finding_id"])
                self.worker_calls.append({"command": command, "cwd": cwd, "stdin": stdin, "env": env, "files": []})
                verdict = {"verdicts": [{"finding_id": request["inputs"]["finding_id"], "verdict": self.support,
                                         "reasoning": "Judged from the quote and its context."}]}
                return Completed(0, claude_stream(verdict, tools=()), "", False)
        return super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)


def finding(**changes):
    """One finding as the research worker returns it: Forge adds the id and the brief fields."""
    value = {"claim": "The reproducer tool is supported only on Linux", "source": SOURCE,
             "fetched": "2026-10-10", "quote": QUOTE, "context": CONTEXT, "source_role": "primary"}
    value.update(changes)
    return value


class ExtraVerdictRunner(ResearchRunner):
    """The support worker adds a verdict for a finding nobody asked about."""

    def __call__(self, command, *, cwd, env, stdin, timeout):
        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if self.roles and self.roles[-1] == "source-support" and stdin and "```json" in stdin:
            output = json.loads(json.loads(answer.stdout.splitlines()[-1])["result"])
            output["verdicts"].append({"finding_id": "F999", "verdict": "supported", "reasoning": "Unasked."})
            return Completed(0, claude_stream(output, tools=()), "", False)
        return answer


class ResearchPassTest(unittest.TestCase):
    def setUp(self):
        self.record = skill_decision(status="decided")
        self.record.record["accepted_revision"] = None

    def test_a_support_answer_must_cover_exactly_the_requested_finding(self):
        runner = ExtraVerdictRunner([finding()])
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(self.record, None, planned, "intake-1")
        result = research_pass(self.record, "deep", "A", "claude-code", contract=contract, confirmation=confirmation,
                               worker_evidence=planned, run_id="intake-1", runner=runner, environ=PARENT,
                               fetch=fetch_page)
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["support"][0]["verdict"], "missing")

    def test_research_launches_nothing_for_an_unconfirmed_part(self):
        from harness_forge.contracts import confirm_contract, render_contract
        from tests.builders import part
        record = skill_decision(part("B", "Summarize the checklist", slug="checklist-summary"), status="decided")
        record.record["accepted_revision"] = None
        runner = ResearchRunner([finding()])
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract = render_contract(record, None, [], worker_evidence=planned)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "intake-1"}
        confirmation = confirm_contract(contract, relay, run_id="intake-1")
        for pass_name, part_id in (("deep", "B"), ("survey", None)):
            with self.subTest(pass_name=pass_name), self.assertRaisesRegex(ValueError, "not confirmed"):
                research_pass(record, pass_name, part_id, "claude-code", contract=contract, confirmation=confirmation,
                              worker_evidence=planned, run_id="intake-1", runner=runner, environ=PARENT,
                              fetch=fetch_page)
        self.assertEqual(runner.worker_calls, [])

    def test_research_launches_nothing_for_a_decision_changed_after_confirmation(self):
        import copy

        from harness_forge.contracts import confirm_contract, render_contract
        record = skill_decision(status="decided")
        record.record["accepted_revision"] = None
        runner = ResearchRunner([finding()])
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract = render_contract(record, None, [], worker_evidence=planned)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "intake-1"}
        confirmation = confirm_contract(contract, relay, run_id="intake-1")
        for field, change in (("goals", lambda r: r.record.update(goals=["Something else"])),
                              ("picture", lambda r: r.record.update(picture="Another picture")),
                              ("part goal", lambda r: r.part("A").update(goal="Another goal"))):
            changed = copy.deepcopy(record)
            change(changed)
            for pass_name, part_id in (("deep", "A"), ("survey", None)):
                with self.subTest(field=field, pass_name=pass_name), \
                        self.assertRaisesRegex(ValueError, "changed after the contract was confirmed"):
                    research_pass(changed, pass_name, part_id, "claude-code", contract=contract,
                                  confirmation=confirmation, worker_evidence=planned, run_id="intake-1",
                                  runner=runner, environ=PARENT, fetch=fetch_page)
        self.assertEqual(runner.worker_calls, [])

    def run_pass(self, findings, support="supported", existing=(), fetch=fetch_page):
        runner = ResearchRunner(findings, support)
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(self.record, None, planned, "intake-1")
        result = research_pass(self.record, "deep", "A", "claude-code", contract=contract, confirmation=confirmation,
                               worker_evidence=planned, run_id="intake-1", runner=runner, environ=PARENT,
                               fetch=fetch, existing_ids=list(existing))
        return result, runner

    def test_kept_findings_passed_both_checks_and_ids_continue_the_record(self):
        result, runner = self.run_pass([finding(), finding(quote="A sentence the page does not contain.")],
                                       existing=["F1", "F2"])
        self.assertEqual([f["id"] for f in result["kept"]], ["F3"])
        self.assertEqual([d["id"] for d in result["dropped"]], ["F4"])
        self.assertEqual(runner.support_ids, ["F3"], "no support worker for a quote the page lacks")
        self.assertEqual({entry["finding_id"]: entry["quote_check"] for entry in result["support"]},
                         {"F3": "pass", "F4": "fail"})
        self.assertEqual(len(result["worker_evidence"]), 2)

    def test_a_dropped_finding_never_counts_as_supported(self):
        """Its quote and verdict may pass while its context fails: the entry then binds no finding."""
        from harness_forge.research import supported_findings
        fabricated = finding(context=f"Invented surroundings. {QUOTE} More invention.")
        result, _ = self.run_pass([fabricated])
        self.assertEqual(result["kept"], [])
        [entry] = result["support"]
        self.assertEqual((entry["quote_check"], entry["verdict"]), ("pass", "supported"))
        self.assertNotIn("finding_digest", entry)
        from harness_forge.research import worker_finding, make_brief
        restored = worker_finding(fabricated, make_brief(self.record, "deep", "A"), "F1").to_dict()
        self.assertEqual(supported_findings({"findings": [restored], "support": result["support"]}), {})

    def test_unsupported_findings_are_dropped(self):
        result, _ = self.run_pass([finding()], support="unsupported")
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["support"][0]["verdict"], "unsupported")

    def test_rejected_sources_are_never_fetched(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return PAGE

        result, runner = self.run_pass([finding(source="https://x.com/someone/status/1")], fetch=fetch)
        self.assertEqual(fetched, [])
        self.assertEqual(runner.support_ids, [])
        self.assertIn("X platform", result["dropped"][0]["reason"])

    def test_forge_adds_the_pass_part_and_brief_digest(self):
        result, _ = self.run_pass([finding()])
        kept = result["kept"][0]
        self.assertEqual((kept["pass"], kept["part_id"], kept["brief_digest"]), ("deep", "A", result["brief"]["digest"]))

    def test_an_unfetchable_source_is_dropped(self):
        result, _ = self.run_pass([finding(source="https://docs.example-vendor.com/gone")])
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
        self.assertEqual(runner.roles, ["review", "baseline", "baseline", "runtime", "runtime", "runtime"])
        self.assertEqual(len(run.runtime_results), 3)
        self.assertTrue(any(e["role"] == "review" and e["observed_tools"]["status"] == "observed"
                            for e in run.worker_evidence))

    def test_a_build_without_an_observed_explicit_activation_is_not_verified(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()

        class ExplicitTimesOut(ScriptedRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "runtime" and stdin.startswith("Use the reproduction-checklist"):
                    return Completed(0, "", "", True)
                return answer

        vertical = load_cases(FORGE / "evals" / "smoke" / "vertical.yaml")
        for name, runner, cases in (("explicit case timed out", ExplicitTimesOut(), vertical),
                                    ("no cases", ScriptedRunner(), [])):
            with self.subTest(name):
                planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
                contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
                _, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                         confirmation=confirmation, worker_evidence=planned, cases=cases,
                                         runner=runner, environ=PARENT, kb_version="0.1.0")
                part = {result.part_id: result for result in results}["A"]
                self.assertEqual(part.harness_result, "not verified")
                self.assertIn({"rule_id": "runtime.explicit-activation", "check": "script", "result": "fail",
                               "reason": "the live build needs an observed explicit activation, and none passed"},
                              part.rule_results)

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
                                   environ=PARENT, kb_version="0.1.0",
                                   collect=lambda: (changed, symlinks, record, REVISION))
        part = {result.part_id: result for result in results}["A"]
        self.assertEqual(part.harness_result, "not verified")
        self.assertIn("inputs.bound", [r["rule_id"] for r in part.rule_results])
        self.assertEqual(runner.roles, ["review"], "the reviewer runs once")

    def test_a_decision_changed_while_workers_ran_invalidates_the_part(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()
        changed = copy.deepcopy(record)
        changed.part("A")["answers"]["every_time"] = "always"

        def unreadable():
            raise ValueError("the decision record is gone")

        for name, collect in (("changed answer", lambda: (files, symlinks, changed, REVISION)),
                              ("unreadable record", unreadable)):
            with self.subTest(name):
                runner = ScriptedRunner()
                planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
                contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
                _, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                         confirmation=confirmation, worker_evidence=planned, cases=[], runner=runner,
                                         environ=PARENT, kb_version="0.1.0", collect=collect)
                part = {result.part_id: result for result in results}["A"]
                self.assertEqual(part.harness_result, "not verified")
                self.assertIn("inputs.bound", [r["rule_id"] for r in part.rule_results])

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

    def test_a_reviewer_that_could_not_start_says_why(self):
        record = skill_decision()
        files, symlinks = build_files(record)
        files["README.md"] = update_outside_section("# Repro\n", record).encode()

        class Blocked(ScriptedRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin:
                    return Completed(1, "", "Error: the session's sandbox refused it\n", False)
                return answer

        runner = Blocked()
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(record, REVISION, planned, "verify-1")
        run, results = verify_live(record, files, symlinks, RULES, REVISION, "claude-code", contract=contract,
                                   confirmation=confirmation, worker_evidence=planned, cases=[], runner=runner,
                                   environ=PARENT, kb_version="0.1.0")
        self.assertEqual(run.worker_violations,
                         ["review worker: the worker did not complete (exit 1); Error: the session's sandbox refused it"])
        self.assertEqual({r.part_id: r for r in results}["A"].harness_result, "not verified")


if __name__ == "__main__":
    unittest.main()
