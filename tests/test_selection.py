"""Reproducible recommendations from confirmed answers."""

import itertools
import unittest
from pathlib import Path

from harness_forge.documents import load_document, parse_yaml
from harness_forge.models import Rule
from harness_forge.selection import next_question, recommend

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
SELECTION = load_document(SNAPSHOT / "rules" / "selection.yaml", "selection")
RULES = [Rule.from_dict(rule) for rule in SELECTION["rules"]]
CASES = {path.stem: parse_yaml(path.read_text(encoding="utf-8")) for path in sorted((SNAPSHOT / "fixtures" / "selection").glob("*.yaml"))}


def reversed_dict(value: dict) -> dict:
    return dict(reversed(list(value.items())))


class FixedAnswerSetsTest(unittest.TestCase):
    def test_every_concept_and_outcome_has_a_case(self):
        mechanisms = {case["expected"]["mechanism"] for case in CASES.values()}
        outcomes = {case["expected"]["outcome"] for case in CASES.values()}
        self.assertTrue({"instructions", "configuration", "permissions-and-sandbox", "mcp", "skills", "commands",
                         "subagents", "hooks", "plugins", "automation"} <= mechanisms)
        self.assertEqual(outcomes, {"harness-only", "outside-alongside", "outside-instead", "reuse", "nothing-fits"})

    def test_fixed_answer_sets(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                actual = recommend(case["answers"], case["whole_idea"], RULES, case["candidates"])
                expected_outcome = case["expected"]["outcome"]
                expected_mechanism = case["expected"]["mechanism"]
                self.assertIsNone(actual.pending_question)
                self.assertEqual(actual.outcome, expected_outcome)
                self.assertEqual(actual.mechanism, expected_mechanism)
                self.assertEqual(sorted(control["id"] for control in actual.controls), case["expected"]["controls"])

    def test_reordered_input_keys_give_the_same_result(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                first = recommend(case["answers"], case["whole_idea"], RULES, case["candidates"])
                second = recommend(reversed_dict(case["answers"]), reversed_dict(case["whole_idea"]), RULES, case["candidates"])
                self.assertEqual(first, second)

    def test_recommendations_cite_existing_rules_and_pages(self):
        rule_ids = {rule.id for rule in RULES}
        for name, case in CASES.items():
            actual = recommend(case["answers"], case["whole_idea"], RULES, case["candidates"])
            with self.subTest(case=name):
                self.assertTrue(actual.rule_ids)
                self.assertLessEqual(set(actual.rule_ids), rule_ids)
                for page in actual.pages:
                    self.assertTrue((SNAPSHOT / page.split("#")[0]).is_file(), page)


class SpecialCasesTest(unittest.TestCase):
    def case(self, name):
        return CASES[name]

    def test_fresh_context_review_selects_a_subagent(self):
        case = self.case("concept-subagents-fresh-review")
        self.assertEqual(recommend(case["answers"], case["whole_idea"], RULES, []).mechanism, "subagents")

    def test_user_triggered_recurring_procedure_selects_a_command(self):
        case = self.case("concept-commands")
        self.assertEqual(recommend(case["answers"], case["whole_idea"], RULES, []).mechanism, "commands")

    def test_merge_enforcement_selects_an_outside_control(self):
        case = self.case("outcome-outside-instead-merge")
        actual = recommend(case["answers"], case["whole_idea"], RULES, [])
        self.assertEqual(actual.outcome, "outside-instead")
        gate = next(control for control in actual.controls if control["id"] == "event-gate")
        self.assertTrue(gate["required_for_goal"])
        self.assertEqual(gate["availability"], "available")

    def test_configuration_protection_is_a_precaution_not_a_goal_dependency(self):
        case = self.case("concept-skills")
        controls = recommend(case["answers"], case["whole_idea"], RULES, []).controls
        self.assertEqual([(c["id"], c["required_for_goal"]) for c in controls], [("codeowners-agent-config", False)])

    def test_reuse_needs_confirmed_fit_license_and_maintenance(self):
        case = self.case("outcome-reuse")
        candidate = case["candidates"][0]
        self.assertEqual(recommend(case["answers"], case["whole_idea"], RULES, [candidate]).outcome, "reuse")
        for missing in ({"fit": "unconfirmed"}, {"license": "unknown"}, {"maintenance": "unmaintained"}, {"license": ""}):
            with self.subTest(missing=missing):
                actual = recommend(case["answers"], case["whole_idea"], RULES, [{**candidate, **missing}])
                self.assertNotEqual(actual.outcome, "reuse")

    def test_unavailable_control_gives_nothing_fits_for_a_guarantee(self):
        case = self.case("outcome-nothing-fits-unavailable")
        actual = recommend(case["answers"], case["whole_idea"], RULES, [])
        self.assertEqual(actual.outcome, "nothing-fits")
        self.assertEqual({c["availability"] for c in actual.controls}, {"unavailable"})

    def test_non_skill_choice_is_retained(self):
        case = self.case("concept-hooks-retained")
        actual = recommend(case["answers"], case["whole_idea"], RULES, [])
        self.assertEqual((actual.outcome, actual.mechanism), ("harness-only", "hooks"))

    def test_isolation_is_added_for_forced_cases(self):
        case = self.case("concept-skills")
        for fact in ("bypass_mode", "untrusted_repo", "lethal_trifecta"):
            with self.subTest(fact=fact):
                actual = recommend(case["answers"], {**case["whole_idea"], fact: True}, RULES, [])
                self.assertIn("outer-isolation", [control["id"] for control in actual.controls])


class QuestionsTest(unittest.TestCase):
    def test_missing_decisive_answer_produces_a_question_not_a_default(self):
        case = CASES["concept-skills"]
        answers = {"job": "procedure"}
        actual = recommend(answers, case["whole_idea"], RULES, [])
        self.assertIsNone(actual.outcome)
        self.assertIsNone(actual.mechanism)
        self.assertIsNotNone(actual.pending_question)
        question = next_question({"answers": answers}, case["whole_idea"], SELECTION)
        self.assertIsNotNone(question)
        self.assertIn(question["id"], {fact["id"] for fact in SELECTION["facts"]})
        self.assertNotIn(question["id"], answers)

    def test_resolved_part_asks_nothing(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                resolved_part = {"answers": case["answers"]}
                self.assertEqual(next_question(resolved_part, case["whole_idea"], SELECTION), None)

    def test_answers_that_all_lead_to_one_outcome_stop_questions(self):
        whole_idea = CASES["concept-skills"]["whole_idea"]
        resolved_part = {"answers": {"job": "one-step"}}
        self.assertEqual(next_question(resolved_part, whole_idea, SELECTION), None)
        actual = recommend(resolved_part["answers"], whole_idea, RULES, [])
        self.assertEqual((actual.outcome, actual.mechanism), ("nothing-fits", None))

    def test_stop_logic_agrees_with_enumerated_remaining_answers(self):
        whole_idea = CASES["concept-skills"]["whole_idea"]
        declared = {fact["id"]: fact["values"] for fact in SELECTION["facts"]}
        partial = {"job": "procedure", "trigger": "model", "own_context": False, "event_without_agent": False,
                   "must_survive_compromise": "not-required", "who_disables": "contributor", "irreversible": False}
        remaining = ["every_time", "early_feedback", "external_system", "script_checkable"]
        results = set()
        for values in itertools.product(*(declared[fact] for fact in remaining)):
            answers = {**partial, **dict(zip(remaining, values))}
            actual = recommend(answers, whole_idea, RULES, [])
            results.add((actual.outcome, actual.mechanism, tuple(c["id"] for c in actual.controls)))
        stops = next_question({"answers": partial}, whole_idea, SELECTION) is None
        self.assertEqual(stops, len(results) == 1)

    def test_unknown_or_undeclared_answers_are_rejected(self):
        case = CASES["concept-skills"]
        with self.assertRaises(ValueError):
            recommend({**case["answers"], "job": ["procedure"]}, case["whole_idea"], RULES, [])


if __name__ == "__main__":
    unittest.main()


class SelectCliTest(unittest.TestCase):
    def run_select(self, payload: dict) -> tuple[int, dict]:
        import contextlib
        import importlib.util
        import io
        import json
        import tempfile

        specification = importlib.util.spec_from_file_location("forge_cli", SNAPSHOT.parent / "scripts" / "forge.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = module.main(["select", "--input", handle.name])
        Path(handle.name).unlink()
        return code, (json.loads(stdout.getvalue()) if stdout.getvalue() else {})

    def test_select_returns_the_recommendation(self):
        case = CASES["reference-reproduction-checklist"]
        code, output = self.run_select({"answers": case["answers"], "whole_idea": case["whole_idea"], "candidates": []})
        self.assertEqual(code, 0)
        self.assertEqual((output["outcome"], output["mechanism"]), ("harness-only", "skills"))
        self.assertIn("selection.mechanism.procedure", output["rule_ids"])

    def test_select_reports_the_deciding_question(self):
        case = CASES["concept-skills"]
        code, output = self.run_select({"answers": {"job": "procedure"}, "whole_idea": case["whole_idea"], "candidates": []})
        self.assertEqual(code, 0)
        self.assertIsNone(output["outcome"])
        self.assertIn("id", output["question"])

    def test_select_rejects_malformed_reuse_candidates(self):
        case = CASES["reference-reproduction-checklist"]
        good = {"name": "repro", "source": "https://example.com/repro", "license": "MIT", "maintenance": "maintained",
                "fit": "confirmed", "concept": "skills"}
        for candidate in (None, "repro", {key: value for key, value in good.items() if key != "name"},
                          {**good, "stars": 5}, {**good, "concept": "magic"}, {**good, "name": 7},
                          {**good, "source": "https://"}, {**good, "source": "https://example.com/a b"},
                          {**good, "path": "../outside"}, {**good, "path": "/tmp/external"}):
            with self.subTest(candidate=candidate):
                code, output = self.run_select({"answers": case["answers"], "whole_idea": case["whole_idea"],
                                                "candidates": [candidate]})
                self.assertEqual((code, output), (2, {}))
        code, output = self.run_select({"answers": case["answers"], "whole_idea": case["whole_idea"],
                                        "candidates": [good]})
        self.assertEqual(code, 0)

    def test_select_rejects_undeclared_facts_and_values(self):
        case = CASES["concept-skills"]
        for answers in ({**case["answers"], "mood": "happy"}, {**case["answers"], "job": "anything"}, {**case["answers"], "own_context": "yes"}):
            with self.subTest(answers=answers):
                code, _ = self.run_select({"answers": answers, "whole_idea": case["whole_idea"], "candidates": []})
                self.assertEqual(code, 2)
