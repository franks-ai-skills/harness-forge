"""Verification: applicability, verdicts, runtime results and result precedence."""

import copy
import unittest
from pathlib import Path

from harness_forge.build_skill import plan_skill
from harness_forge.contracts import confirm_contract, render_contract
from harness_forge.documents import canonical_digest
from harness_forge.documents import load_document
from harness_forge.models import Confirmation, Decision, RuntimeResult, Rule
from harness_forge.verify import bound_inputs, exclusion_token, finish_run, prepare_run, validate_review
from tests.builders import EVIDENCE, decision, part
from tests.test_build_skill import DESCRIPTION, NAME, PROCEDURE, REVISION

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
RULES = []
for name in ("skills", "security", "outside-controls"):
    RULES += [Rule.from_dict(r) for r in load_document(SNAPSHOT / "rules" / f"{name}.yaml", "rules")]
FACTS = {"target_set": "both", "reach": "repository", "plan_controls": "available", "opens_prs": False,
         "bypass_mode": False, "untrusted_repo": False, "lethal_trifecta": False}
README_CONTROL = {"control": "codeowners-agent-config", "prerequisites": ["outside.codeowners.agent-config"],
                  "availability": "available", "required_for_goal": False}
GATE = {"control": "event-gate", "prerequisites": ["outside.merge.no-agent-bypass"], "availability": "unknown",
        "required_for_goal": True}


def build_files(record, part_id="A"):
    """The files the builder wrote; a part blocked afterwards was built before its block."""
    record = Decision(record=copy.deepcopy(record.record), body=record.body)
    for part in record.record["parts"]:
        if part["status"] == "blocked":
            part["status"] = "built"
    contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": [part_id], "run_id": "run-1"}
    planned = plan_skill(record, part_id, REVISION, RULES, worker_evidence=EVIDENCE,
                         confirmation=confirm_contract(contract, relay, run_id="run-1"),
                         description=DESCRIPTION, procedure=PROCEDURE)
    files = {key: value.encode() for key, value in planned.items() if not key.startswith(".claude/")}
    symlinks = {key: str(value) for key, value in planned.items() if key.startswith(".claude/")}
    return files, symlinks


def readme(record):
    from harness_forge.contracts import update_outside_section
    return update_outside_section("# Repo\n", record).encode()


def skill_decision(*extra_parts, **part_changes):
    record = decision(part("A", "Write a reproduction checklist when asked", slug=NAME, **{"status": "built", **part_changes}),
                      *extra_parts, facts=FACTS)
    record.record["accepted_revision"] = {"digest": REVISION.digest, "rule_ids": REVISION.rule_ids}
    return record


def passing_review(run):
    return {"run_id": run.run_id,
            "verdicts": [{"part_id": r["part_id"], "rule_id": r["rule_id"], "verdict": "pass", "reason": "Meets it.",
                          "citation": "SKILL.md"} for r in run.judged_requests if r["kind"] == "rule"],
            "goal_verdicts": [{"part_id": r["part_id"], "verdict": "met", "reason": "The procedure produces the checklist."}
                              for r in run.judged_requests if r["kind"] == "goal"]}


_PREPARED: dict = {}


class Scenario:
    def __init__(self, record, confirmed=("A",), files=None, symlinks=None):
        self.record = record
        built_files, built_links = build_files(record) if record.part("A")["chosen"] in ("skills", "commands") else ({}, {})
        self.files = {**built_files, "README.md": readme(record), **(files or {})}
        self.symlinks = symlinks if symlinks is not None else built_links
        self.contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": self.contract.digest, "part_ids": list(confirmed), "run_id": "run-1"}
        self.confirmation = confirm_contract(self.contract, relay, run_id="run-1") if confirmed else None
        key = canonical_digest({"record": record.record, "files": {k: v.decode("latin-1") for k, v in self.files.items()},
                                "symlinks": self.symlinks, "confirmed": list(confirmed)})
        if key not in _PREPARED:
            _PREPARED[key] = prepare_run(record, self.files, RULES, self.confirmation,
                                         symlinks=self.symlinks, revision=REVISION, kb_version="0.1.0",
                                         worker_evidence=EVIDENCE)
        self.run = copy.deepcopy(_PREPARED[key])

    def current(self, **changes):
        arguments = dict(files=self.files, symlinks=self.symlinks, revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        arguments.update(changes)
        return bound_inputs(self.record, arguments.pop("files"), RULES, **arguments)

    def finish(self, review=None, runtime=(), current=None, drift=()):
        review = validate_review(self.run, review if review is not None else passing_review(self.run))
        return {r.part_id: r for r in finish_run(self.run, review, list(runtime), current or self.current(), drift=drift)}


def assert_result(test, result, harness_expected, goal_expected):
    test.assertEqual(result.harness_result, harness_expected, result.rule_results)
    test.assertEqual(result.goal_result, goal_expected)


class PrecedenceTest(unittest.TestCase):
    def test_verified_build_without_goal_dependencies_is_met(self):
        result = Scenario(skill_decision()).finish()["A"]
        assert_result(self, result, "verified", "met by the verified build")
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.outside_dependencies, [])

    def test_precautionary_control_does_not_become_a_goal_dependency(self):
        result = Scenario(skill_decision(outside_controls=[README_CONTROL])).finish()["A"]
        assert_result(self, result, "verified", "met by the verified build")

    def test_unconfirmed_part_is_not_assessed(self):
        result = Scenario(skill_decision(), confirmed=()).finish()["A"]
        assert_result(self, result, "not verified", "not assessed")
        self.assertEqual(result.status, "blocked")

    def test_failed_build_is_not_met_even_with_outside_dependencies(self):
        scenario = Scenario(skill_decision(outside_controls=[README_CONTROL, GATE]),
                            files={f".agents/skills/{NAME}/SKILL.md": b"---\nname: Bad_Name\ndescription: d\n---\nx\n"})
        result = scenario.finish()["A"]
        assert_result(self, result, "not verified", "not met")
        self.assertEqual(result.outside_dependencies, ["event-gate"])

    def test_outside_only_part(self):
        outside = part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                       outside_controls=[GATE])
        result = Scenario(skill_decision(outside), confirmed=("A", "B")).finish()["B"]
        assert_result(self, result, "not applicable", "depends on outside controls")

    def test_blocking_drift_stops_the_parts_its_rule_applies_to(self):
        outside = part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                       outside_controls=[GATE])
        scenario = Scenario(skill_decision(outside), confirmed=("A", "B"))
        ordinary = {"rule_id": "skills.description.length", "finding_id": "F1", "summary": "The limit grew.",
                    "category": "ordinary"}
        self.assertEqual(scenario.finish(drift=[ordinary])["A"].status, "verified", "ordinary drift keeps the pinned rule")
        results = scenario.finish(drift=[{**ordinary, "category": "compatibility"}])
        self.assertEqual((results["A"].status, results["A"].harness_result), ("blocked", "not verified"))
        self.assertIn("unresolved compatibility drift on skills.description.length", str(results["A"].rule_results))
        self.assertEqual(results["B"].status, "decided", "the rule does not apply to B")

        rule_id = next(r for r in scenario.run.applicable_rules["B"] if r.startswith("outside."))
        results = scenario.finish(drift=[{**ordinary, "rule_id": rule_id}])
        self.assertEqual((results["B"].status, results["B"].goal_result), ("blocked", "not assessed"))
        self.assertIn(f"unresolved security drift on {rule_id}", str(results["B"].rule_results))

    def test_several_blocking_drifts_make_one_result_per_part(self):
        from harness_forge.report import render_report
        from harness_forge.scope import earlier_run
        scenario = Scenario(skill_decision())
        drift = [{"rule_id": "skills.description.length", "finding_id": "F1", "summary": "Limit.",
                  "category": "compatibility"},
                 {"rule_id": "security.secrets.none-in-files", "finding_id": "F2", "summary": "Secrets.",
                  "category": "security"}]
        results = scenario.finish(drift=drift)
        entries = [r for r in results["A"].rule_results if r["rule_id"] == "research.drift"]
        self.assertEqual(len(entries), 1)
        self.assertIn("skills.description.length (finding F1)", entries[0]["reason"])
        self.assertIn("security.secrets.none-in-files (finding F2)", entries[0]["reason"])
        earlier_run(render_report(scenario.run, list(results.values())))  # Forge reads its own report back

    def test_confirmed_nothing_fits(self):
        nothing = part("B", "One-step task", outcome="nothing-fits", recommended=None, chosen=None, outside_controls=[])
        result = Scenario(skill_decision(nothing), confirmed=("A", "B")).finish()["B"]
        assert_result(self, result, "not applicable", "not met")

    def test_remote_reuse_is_not_checked(self):
        reuse = part("B", "Reuse a checklist skill", outcome="reuse", recommended="skills", chosen="skills",
                     reuse={"name": "repro-steps", "source": "https://example.com/repro", "license": "MIT", "maintenance": "maintained"})
        result = Scenario(skill_decision(reuse), confirmed=("A", "B")).finish()["B"]
        assert_result(self, result, "not checked", "not assessed")

    def test_local_reuse_is_checked_read_only_like_a_build(self):
        record = skill_decision(outcome="reuse", reuse={"name": NAME, "source": "https://example.com/repro", "license": "MIT",
                                                        "maintenance": "maintained", "path": f".agents/skills/{NAME}"})
        files, symlinks = build_files(skill_decision())
        scenario = Scenario.__new__(Scenario)
        scenario.record, scenario.files, scenario.symlinks = record, {**files, "README.md": readme(record)}, symlinks
        contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        scenario.confirmation = confirm_contract(contract, relay, run_id="run-1")
        scenario.run = prepare_run(record, scenario.files, RULES, scenario.confirmation, symlinks=symlinks,
                                   revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        assert_result(self, scenario.finish()["A"], "verified", "met by the verified build")

    def test_builder_unavailable_part_is_blocked(self):
        hook = part("B", "Block force pushes", recommended="hooks", chosen="hooks", outside_controls=[])
        result = Scenario(skill_decision(hook), confirmed=("A", "B")).finish()["B"]
        assert_result(self, result, "not applicable", "not assessed")
        self.assertEqual(result.status, "blocked")


class EveryTimeAnswerTest(unittest.TestCase):
    """The must-hold rule follows the user's every-time answer, not a reviewer's reading of the goal."""

    RULE = "skills.goal.no-must-hold-rule-in-skill"

    def judged(self, every_time):
        run = Scenario(skill_decision(answers={"job": "procedure", "every_time": every_time})).run
        return [r["rule_id"] for r in run.judged_requests if r["kind"] == "rule"], run.not_applicable["A"]

    def test_a_part_that_may_be_skipped_does_not_ask_the_reviewer(self):
        judged, not_applicable = self.judged("usually")
        self.assertNotIn(self.RULE, judged)
        self.assertEqual(not_applicable[self.RULE], "a condition in applies_to.when does not hold")

    def test_a_part_that_must_hold_every_time_is_judged(self):
        judged, _ = self.judged("always")
        self.assertIn(self.RULE, judged)


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())
        self.review = passing_review(self.scenario.run)

    def test_judged_requests_cover_exactly_the_applicable_judged_rules_and_the_goal(self):
        requests = self.scenario.run.judged_requests
        rule_ids = [r["rule_id"] for r in requests if r["kind"] == "rule"]
        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        self.assertIn("skills.description.no-workflow-steps", rule_ids)
        self.assertNotIn("skills.description.length", rule_ids)
        self.assertNotIn("skills.reuse.reviewed-and-pinned", rule_ids)
        self.assertIn("skills.reuse.reviewed-and-pinned", self.scenario.run.not_applicable["A"])
        goals = [r for r in requests if r["kind"] == "goal"]
        self.assertEqual(goals[0]["requirement_ids"], REVISION.rule_ids)
        self.assertIn("idea.repro.a.environment", rule_ids)

    def test_missing_verdict_prevents_verified(self):
        self.review["verdicts"].pop()
        assert_result(self, self.scenario.finish(self.review)["A"], "not verified", "not met")

    def test_duplicate_and_unknown_verdicts_are_rejected(self):
        duplicate = copy.deepcopy(self.review)
        duplicate["verdicts"].append(duplicate["verdicts"][0])
        unknown = copy.deepcopy(self.review)
        unknown["verdicts"].append({**unknown["verdicts"][0], "rule_id": "skills.not-a-rule"})
        other_run = {**copy.deepcopy(self.review), "run_id": "run-2"}
        for response in (duplicate, unknown, other_run, {"run_id": "run-1"}):
            with self.assertRaises(ValueError):
                validate_review(self.scenario.run, response)

    def test_a_not_applicable_confirmation_binds_to_the_parts_inputs(self):
        rule = "skills.invocation.side-effects-user-invoked"
        scenario = Scenario(skill_decision())
        changed = Scenario(skill_decision(), files={f".agents/skills/{NAME}/references/extra.md": b"More.\n"})
        self.assertRegex(exclusion_token(scenario.run, "A", rule), r"^[0-9a-f]{16}$")
        self.assertEqual(exclusion_token(scenario.run, "A", rule), exclusion_token(Scenario(skill_decision()).run, "A", rule))
        self.assertNotEqual(exclusion_token(scenario.run, "A", rule), exclusion_token(changed.run, "A", rule))
        self.assertNotEqual(exclusion_token(scenario.run, "A", rule),
                            exclusion_token(scenario.run, "A", "skills.description.length"))
        resourced = [Rule.from_dict({**r.to_dict(), "sources": ["https://code.claude.com/docs/en/skills"]})
                     if r.id == rule else r for r in RULES]
        rerun = prepare_run(scenario.record, scenario.files, resourced, scenario.confirmation, symlinks=scenario.symlinks,
                            revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        self.assertNotEqual(exclusion_token(scenario.run, "A", rule), exclusion_token(rerun, "A", rule),
                            "the token binds the whole rule, its sources included")

    def test_not_applicable_only_for_judged_applicability(self):
        review = copy.deepcopy(self.review)
        for verdict in review["verdicts"]:
            if verdict["rule_id"] == "skills.description.no-workflow-steps":
                verdict["verdict"] = "not-applicable"
        assert_result(self, self.scenario.finish(review)["A"], "not verified", "not met")

    def test_error_rule_excluded_by_judgment_stays_unresolved(self):
        review = copy.deepcopy(self.review)
        for verdict in review["verdicts"]:
            if verdict["rule_id"] == "skills.invocation.side-effects-user-invoked":
                verdict["verdict"] = "not-applicable"
        result = self.scenario.finish(review)["A"]
        assert_result(self, result, "not verified", "not met")
        self.assertIn("unresolved", [r["result"] for r in result.rule_results])

    def test_failed_judged_error_rule_and_false_goal_verdict_prevent_verified(self):
        failed = copy.deepcopy(self.review)
        for verdict in failed["verdicts"]:
            if verdict["rule_id"] == "skills.security.no-surprising-content":
                verdict["verdict"] = "fail"
        assert_result(self, self.scenario.finish(failed)["A"], "not verified", "not met")
        not_met = copy.deepcopy(self.review)
        not_met["goal_verdicts"][0]["verdict"] = "not-met"
        assert_result(self, self.scenario.finish(not_met)["A"], "not verified", "not met")

    def test_failed_warning_rule_does_not_block(self):
        review = copy.deepcopy(self.review)
        for verdict in review["verdicts"]:
            if verdict["rule_id"] == "skills.description.boundary":
                verdict["verdict"] = "fail"
        assert_result(self, self.scenario.finish(review)["A"], "verified", "met by the verified build")

    def test_model_cannot_reverse_a_script_failure(self):
        scenario = Scenario(skill_decision(), files={f".agents/skills/{NAME}/SKILL.md": b"no frontmatter\n"})
        review = passing_review(scenario.run)
        self.assertNotIn("skills.frontmatter.valid", [v["rule_id"] for v in review["verdicts"]])
        assert_result(self, scenario.finish(review)["A"], "not verified", "not met")


class RuntimeAndInputsTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())

    def runtime(self, **changes):
        directory = self.scenario.run.part_dirs["A"] + "/"
        part_files = {path: digest for path, digest in self.scenario.run.frozen_inputs["files"].items() if path.startswith(directory)}
        value = dict(run_id="run-1", part_id="A", case_id="explicit", prompt="/reproduction-checklist bug 12",
                     expected="The skill activates", observed_activation="Skill tool use", result="pass",
                     reason="Activated once.", file_hashes=part_files, harness="claude-code", harness_version="2.1.292",
                     environment="throwaway workspace")
        value.update(changes)
        return RuntimeResult(**value)

    def test_failed_or_errored_runtime_test_prevents_verified(self):
        for outcome in ("fail", "error"):
            with self.subTest(outcome=outcome):
                result = self.scenario.finish(runtime=[self.runtime(result=outcome)])["A"]
                assert_result(self, result, "not verified", "not met")

    def test_not_run_never_counts_as_a_pass_but_does_not_block(self):
        result = self.scenario.finish(runtime=[self.runtime(result="not-run", observed_activation=None,
                                                            reason="No observable activation signal.")])["A"]
        assert_result(self, result, "verified", "met by the verified build")

    def test_runtime_results_for_another_run_or_changed_files_are_rejected(self):
        for bad in (self.runtime(run_id="run-2"), self.runtime(file_hashes={f".agents/skills/{NAME}/SKILL.md": "0" * 64})):
            with self.subTest(result=bad):
                with self.assertRaises(ValueError):
                    self.scenario.finish(runtime=[bad])

    def test_changed_bound_inputs_return_the_part_to_built(self):
        changes = [
            {"kb_version": "0.2.0"},
            {"revision": None},
            {"worker_evidence": [{**EVIDENCE[0], "configuration_digest": "1" * 64}]},
            {"files": {**self.scenario.files, f".agents/skills/{NAME}/references/requirements.md": b"edited"}},
        ]
        for change in changes:
            with self.subTest(change=list(change)):
                result = self.scenario.finish(current=self.scenario.current(**change))["A"]
                assert_result(self, result, "not verified", "not met")
                self.assertEqual(result.status, "built")

    def test_unproven_containment_alone_does_not_block(self):
        self.assertEqual({e["isolation_status"] for e in self.scenario.run.worker_evidence}, {"not-certified"})
        assert_result(self, self.scenario.finish()["A"], "verified", "met by the verified build")

    def test_observed_worker_scope_violation_cannot_become_success(self):
        self.scenario.run.worker_violations.append("review worker wrote a file outside its input")
        assert_result(self, self.scenario.finish()["A"], "not verified", "not met")


class WorkerObservationTest(unittest.TestCase):
    """Observations made while the run executes enrich the report; changed controls invalidate it."""

    OBSERVED = {**EVIDENCE[0], "observed_tools": {"status": "observed", "tools": ["Read", "Grep"]},
                "observed_behavior": ["read the two skill files"], "evidence_refs": ["review-transcript.jsonl"]}

    def setUp(self):
        self.scenario = Scenario(skill_decision())

    def finish(self, observations):
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        current = self.scenario.current(worker_evidence=EVIDENCE + observations)
        return finish_run(self.scenario.run, review, [], current, worker_observations=observations)[0]

    def test_an_observation_with_the_confirmed_configuration_enriches_the_report(self):
        result = self.finish([self.OBSERVED])
        assert_result(self, result, "verified", "met by the verified build")
        self.assertIn(self.OBSERVED, self.scenario.run.worker_evidence)

    def test_an_observation_with_changed_controls_invalidates_the_run(self):
        changed = {**self.OBSERVED, "configured_controls": ["tools: Read, Grep, Glob, Bash"]}
        result = self.finish([changed])
        assert_result(self, result, "not verified", "not met")
        self.assertIn("inputs.bound", [r["rule_id"] for r in result.rule_results if r["result"] == "fail"])

    def test_changed_controls_invalidate_the_run_even_when_current_inputs_omit_them(self):
        changed = {**self.OBSERVED, "configured_controls": ["tools: Read, Grep, Glob, Bash"]}
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        result = finish_run(self.scenario.run, review, [], self.scenario.current(), worker_observations=[changed])[0]
        assert_result(self, result, "not verified", "not met")

    def test_a_rejected_configuration_stays_rejected_when_finalized_again(self):
        changed = {**self.OBSERVED, "configured_controls": ["tools: Read, Grep, Glob, Bash"]}
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        for observations in ([changed], [changed], []):
            with self.subTest(observations=len(observations)):
                result = finish_run(self.scenario.run, review, [], self.scenario.current(),
                                    worker_observations=observations)[0]
                assert_result(self, result, "not verified", "not met")

    def test_changing_the_callers_evidence_after_prepare_changes_nothing_approved(self):
        evidence = [copy.deepcopy(EVIDENCE[0])]
        run = prepare_run(self.scenario.record, self.scenario.files, RULES, self.scenario.confirmation,
                          symlinks=self.scenario.symlinks, revision=REVISION, kb_version="0.1.0",
                          worker_evidence=evidence)
        evidence[0]["configured_controls"].append("tools: Bash")
        review = validate_review(run, passing_review(run))
        result = finish_run(run, review, [], run.frozen_inputs, worker_observations=evidence)[0]
        assert_result(self, result, "not verified", "not met")
        self.assertNotIn("tools: Bash", run.worker_evidence[0]["configured_controls"])

    def test_an_observed_violation_still_blocks_success(self):
        self.scenario.run.worker_violations.append("review worker used Bash")
        assert_result(self, self.finish([self.OBSERVED]), "not verified", "not met")


if __name__ == "__main__":
    unittest.main()
