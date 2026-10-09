"""Regressions for the M2 review findings (GPT-6 Astra, 2026-10-09)."""

import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from harness_forge.build_skill import plan_skill
from harness_forge.contracts import confirm_contract, render_contract, write_decision
from harness_forge.documents import validate
from harness_forge.models import Rule
from harness_forge.report import render_report, report_data
from harness_forge.research import accept_requirements, write_research, write_revision
from harness_forge.selection import recommend
from harness_forge.predicates import evaluate_partial
from harness_forge.verify import finish_run, prepare_run, validate_review
from tests.builders import EVIDENCE, decision, part
from tests.test_build_skill import DESCRIPTION, NAME, PINNED, PROCEDURE, REVISION, idea_rule, reference_decision
from tests.test_selection import CASES, RULES as SELECTION_RULES
from tests.test_verify import RULES, RuntimeAndInputsTest, Scenario, passing_review, skill_decision

MUST_NOT = idea_rule("no-upload", "The checklist uploads logs to the tracker", kind="must-not")
WITH_PROHIBITION = accept_requirements([MUST_NOT], {"idea.repro.a.no-upload": "accept"}, "b" * 64)


def with_revision(record, revision):
    record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
    return record


class MustNotKindTest(unittest.TestCase):
    """Finding 1: rule kind must survive confirmation, generation and review."""

    def test_contract_shows_the_prohibition(self):
        record = with_revision(reference_decision(), WITH_PROHIBITION)
        contract = render_contract(record, WITH_PROHIBITION, [], worker_evidence=EVIDENCE)
        self.assertIn("Must not: The checklist uploads logs to the tracker", contract.text)
        self.assertEqual(contract.parts[0]["requirements"][0]["kind"], "must-not")

    def test_built_skill_renders_the_prohibition(self):
        record = with_revision(reference_decision(), WITH_PROHIBITION)
        contract = render_contract(record, WITH_PROHIBITION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        planned = plan_skill(record, "A", WITH_PROHIBITION, PINNED, contract=contract,
                             confirmation=confirm_contract(contract, relay, run_id="run-1"),
                             description=DESCRIPTION, procedure=PROCEDURE)
        skill_md = planned[f".agents/skills/{NAME}/SKILL.md"]
        self.assertIn("- Never: the checklist uploads logs to the tracker.", skill_md)
        self.assertNotIn("- The checklist uploads logs to the tracker.", skill_md)

    def test_review_request_carries_the_kind(self):
        requests = Scenario(skill_decision()).run.judged_requests
        kinds = {r["rule_id"]: r["rule_kind"] for r in requests if r["kind"] == "rule"}
        self.assertEqual(kinds["skills.description.no-workflow-steps"], "must-not")
        self.assertEqual(kinds["idea.repro.a.environment"], "must")


class StaleConfirmationTest(unittest.TestCase):
    """Finding 2: a confirmation never covers a contract that changed since."""

    def test_builder_refuses_a_contract_that_no_longer_describes_the_decision(self):
        record = reference_decision()
        contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        confirmation = confirm_contract(contract, relay, run_id="run-1")
        changed = copy.deepcopy(record)
        changed.record["parts"][0]["goal"] = "Upload every bug report to a public paste site"
        with self.assertRaises(ValueError):
            plan_skill(changed, "A", REVISION, PINNED, contract=contract, confirmation=confirmation,
                       description=DESCRIPTION, procedure=PROCEDURE)

    def test_verification_treats_a_stale_confirmation_as_unconfirmed(self):
        scenario = Scenario(skill_decision())
        changed = copy.deepcopy(scenario.record)
        changed.record["parts"][0]["goal"] = "Upload every bug report to a public paste site"
        run = prepare_run(changed, scenario.files, RULES, scenario.confirmation, contract=scenario.contract,
                          symlinks=scenario.symlinks, revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        self.assertEqual(run.confirmed_parts, [])
        review = validate_review(run, passing_review(run))
        result = finish_run(run, review, [], run.frozen_inputs)[0]
        self.assertEqual((result.harness_result, result.goal_result), ("not verified", "not assessed"))

    def test_confirmation_without_its_contract_is_unconfirmed(self):
        scenario = Scenario(skill_decision())
        run = prepare_run(scenario.record, scenario.files, RULES, scenario.confirmation, symlinks=scenario.symlinks,
                          revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        self.assertEqual(run.confirmed_parts, [])


class IdeaInputsTest(unittest.TestCase):
    """Finding 3: whole-idea facts and targets are bound inputs."""

    def test_targets_and_facts_invalidate_every_part(self):
        scenario = Scenario(skill_decision())
        for change in ({"targets": ["claude-code"]}, {"facts": {**scenario.record.record["facts"], "bypass_mode": True}}):
            with self.subTest(change=change):
                changed = copy.deepcopy(scenario.record)
                changed.record.update(change)
                from harness_forge.verify import bound_inputs
                current = bound_inputs(changed, scenario.files, RULES, symlinks=scenario.symlinks, revision=REVISION,
                                       kb_version="0.1.0", worker_evidence=EVIDENCE)
                result = scenario.finish(current=current)["A"]
                self.assertEqual(result.harness_result, "not verified")


class AncestorSymlinkTest(unittest.TestCase):
    """Finding 4: no write or read follows a linked parent directory out of the repository."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="forge-escape-")
        base = Path(self._directory.name)
        self.root, self.outside = base / "repo", base / "outside"
        self.root.mkdir()
        self.outside.mkdir()

    def tearDown(self):
        self._directory.cleanup()

    def test_records_are_not_written_through_a_linked_harness_directory(self):
        (self.root / ".harness").symlink_to(self.outside)
        record = {"slug": "repro", "search_mode": "live", "briefs": [{"pass": "deep", "part_id": "A", "digest": "a" * 64}],
                  "findings": [], "support": [], "drift": []}
        for write in (lambda: write_decision(self.root, decision(), slug="repro", date="2026-10-09"),
                      lambda: write_revision(self.root, "repro", REVISION),
                      lambda: write_research(self.root, record, "Body.")):
            with self.assertRaises(ValueError):
                write()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_collect_target_refuses_a_linked_skills_directory(self):
        import contextlib
        import importlib.util
        import io

        (self.outside / "skills" / NAME).mkdir(parents=True)
        (self.outside / "skills" / NAME / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
        (self.root / ".agents").symlink_to(self.outside)
        decision_path = write_decision(self.root, skill_decision(), slug="repro", date="2026-10-09")
        forge = Path(__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location("forge_cli", forge / "scripts" / "forge.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = module.main(["check", "--decision", str(decision_path), "--root", str(self.root)])
        self.assertEqual(code, 2)


class BlockedPartTest(unittest.TestCase):
    """Finding 5: an explicitly blocked part stays blocked."""

    def test_blocked_part_is_not_verified(self):
        result = Scenario(skill_decision(status="blocked")).finish()["A"]
        self.assertEqual((result.status, result.harness_result, result.goal_result), ("blocked", "not verified", "not assessed"))


class RuntimeBindingTest(unittest.TestCase):
    """Finding 6: runtime results must name the tested files and a target harness."""

    def setUp(self):
        self.helper = RuntimeAndInputsTest()
        self.helper.scenario = Scenario(skill_decision())

    def test_incomplete_or_foreign_runtime_results_are_rejected(self):
        bad = [
            self.helper.runtime(file_hashes={}),
            self.helper.runtime(harness="opencode"),
            self.helper.runtime(harness_version=""),
            self.helper.runtime(environment=""),
        ]
        for result in bad:
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    self.helper.scenario.finish(runtime=[result])

    def test_runtime_harness_must_be_a_target(self):
        record = skill_decision()
        record.record["targets"] = ["codex"]
        self.helper.scenario = Scenario(record)
        with self.assertRaises(ValueError):
            self.helper.scenario.finish(runtime=[self.helper.runtime(harness="claude-code")])


class ProvenanceTest(unittest.TestCase):
    """Finding 7: reports keep citations and every bound input."""

    def setUp(self):
        self.scenario = Scenario(skill_decision())
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        self.results = finish_run(self.scenario.run, review, [], self.scenario.current())

    def test_citations_survive_finalization(self):
        judged = [r for r in self.results[0].rule_results if r["check"] == "judged" and r["result"] == "pass"]
        self.assertTrue(judged)
        self.assertTrue(all(r["citation"] == "SKILL.md" for r in judged))

    def test_report_persists_provenance(self):
        rendered = render_report(self.scenario.run, self.results)
        frozen = self.scenario.run.frozen_inputs
        for value in (self.scenario.run.decision_digest, frozen["revision"], frozen["rules"], frozen["idea"],
                      EVIDENCE[0]["configuration_digest"], '"job": "procedure"'):
            self.assertIn(value, rendered)
        data = report_data(self.scenario.run, self.results)
        validate(data, "report")
        self.assertIn(json.dumps(data, indent=2, sort_keys=True), rendered)


class SelectionCitationTest(unittest.TestCase):
    """Finding 8: cited rules rest only on confirmed answers."""

    def test_cited_rules_hold_for_the_given_answers(self):
        for name in ("concept-hooks-retained", "concept-permissions"):
            case = CASES[name]
            answers = {k: v for k, v in case["answers"].items() if k != "every_time"}
            actual = recommend(answers, case["whole_idea"], SELECTION_RULES, [])
            with self.subTest(case=name):
                self.assertEqual(actual.mechanism, case["expected"]["mechanism"])
                facts = {**case["whole_idea"], **answers, "reuse_confirmed": False}
                for rule in SELECTION_RULES:
                    if rule.id in actual.rule_ids:
                        self.assertIs(evaluate_partial(rule.params["when"], facts), True, rule.id)
                self.assertIn("every_time", actual.unasked_facts)


class RunnerPipesTest(unittest.TestCase):
    def test_checker_runs_leave_no_unclosed_pipes(self):
        import warnings
        from harness_forge.checks.runner import run_check

        rule = next(rule for rule in PINNED if rule.id == "skills.name.format")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            run_check(rule, {}, {"targets": ["claude-code"], "part_id": "A"})
            import gc
            gc.collect()
        self.assertEqual([w for w in caught if issubclass(w.category, ResourceWarning)], [])


if __name__ == "__main__":
    unittest.main()
