"""Selection model B: eligibility, ordered preference and dependencies.

Every mechanism gets one state: recommended, viable alternative, not
preferred, ruled out (a confirmed requirement conflicts with a sourced
"When not" condition) or unresolved (a relevant fact is missing).
"""

import unittest

from tests.test_selection import CASES, RULES, SELECTION
from harness_forge.selection import CONCEPTS, recommend

STATES = {"recommended", "viable alternative", "not preferred", "ruled out", "unresolved"}


def assessed(recommendation) -> dict[str, dict]:
    return {entry["concept"]: entry for entry in recommendation.assessment}


def answers(**changes):
    case = CASES["concept-skills"]
    return {**case["answers"], "hook_event": "PreToolUse", "hook_effect": "prevent", **changes}, case["whole_idea"]


class AssessmentTest(unittest.TestCase):
    def test_every_concept_gets_exactly_one_state(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                actual = recommend(case["answers"], case["whole_idea"], RULES, case["candidates"])
                table = assessed(actual)
                self.assertEqual(set(table), set(CONCEPTS))
                self.assertLessEqual({entry["state"] for entry in table.values()}, STATES)
                recommended = [c for c, e in table.items() if e["state"] == "recommended"]
                self.assertEqual(recommended, [actual.mechanism] if actual.mechanism else [])

    def test_ruled_out_only_by_a_sourced_conflict(self):
        actual = recommend(*answers(every_time="always", script_checkable=True, job="check"), RULES, [])
        table = assessed(actual)
        self.assertEqual(table["skills"]["state"], "ruled out")
        self.assertTrue(table["skills"]["rules"])
        for rule_id in table["skills"]["rules"]:
            rule = next(rule for rule in RULES if rule.id == rule_id)
            self.assertEqual(rule.params["implies"], {"excludes": "skills"})
            self.assertTrue(any(source.startswith("docs/overview.md#") for source in rule.sources))

    def test_skippable_procedure_does_not_rule_out_hooks(self):
        table = assessed(recommend(*answers(script_checkable=True), RULES, []))
        self.assertNotEqual(table["hooks"]["state"], "ruled out")
        self.assertEqual(table["hooks"]["state"], "not preferred")

    def test_judgment_rules_out_hooks(self):
        table = assessed(recommend(*answers(script_checkable=False), RULES, []))
        self.assertEqual(table["hooks"]["state"], "ruled out")


class AlternativesTest(unittest.TestCase):
    def test_overlapping_mechanisms_give_a_viable_alternative_with_a_reason(self):
        actual = recommend(*answers(), RULES, [])
        self.assertEqual(actual.mechanism, "skills")
        alternatives = {a["concept"]: a for a in actual.alternatives}
        self.assertIn("instructions", alternatives)
        for alternative in actual.alternatives:
            with self.subTest(alternative=alternative["concept"]):
                self.assertEqual(assessed(actual)[alternative["concept"]]["state"], "viable alternative")
                self.assertIn("policy/selection.md", alternative["why_not_chosen"])
                # The decision record holds it as YAML: a plain URL, no Markdown link (#50).
                self.assertIn("Harness Forge's preference order (policy/selection.md in "
                              "https://github.com/franks-ai-skills/harness-forge)", alternative["why_not_chosen"])
                self.assertTrue(alternative["rules"])

    def test_an_alternative_that_violates_a_requirement_is_never_offered(self):
        actual = recommend(*answers(job="procedure", every_time="always", script_checkable=True), RULES, [])
        self.assertEqual(actual.mechanism, "hooks")
        self.assertNotIn("skills", [a["concept"] for a in actual.alternatives])
        self.assertEqual(assessed(actual)["skills"]["state"], "ruled out")

    def test_conflicting_mandatory_requirements_leave_nothing(self):
        actual = recommend(*answers(every_time="always", script_checkable=False), RULES, [])
        self.assertEqual((actual.outcome, actual.mechanism), ("nothing-fits", None))
        table = assessed(actual)
        for concept in ("skills", "hooks", "instructions"):
            self.assertEqual(table[concept]["state"], "ruled out", concept)


class UnresolvedTest(unittest.TestCase):
    def test_missing_fact_is_unresolved_not_a_match_or_exclusion(self):
        decided, whole_idea = answers()
        partial = {k: v for k, v in decided.items() if k != "script_checkable"}
        actual = recommend(partial, whole_idea, RULES, [])
        self.assertEqual(actual.mechanism, "skills")
        table = assessed(actual)
        self.assertEqual(table["hooks"]["state"], "unresolved")
        self.assertIn("script_checkable", table["hooks"]["missing_facts"])
        self.assertIsNone(actual.pending_question)


class PolicyAndBuildersTest(unittest.TestCase):
    def test_preference_order_is_documented_policy(self):
        from pathlib import Path

        policy = (Path(__file__).resolve().parents[1] / "snapshot" / "policy" / "selection.md").read_text(encoding="utf-8")
        for rule in RULES:
            if "concept" in rule.params["implies"]:
                self.assertIn(rule.id, policy)

    def test_unbuildable_recommendation_stays_recommended(self):
        actual = recommend(*answers(job="check", every_time="always", script_checkable=True), RULES, [])
        self.assertEqual(actual.mechanism, "hooks")
        self.assertFalse(actual.builder_available)
        self.assertEqual(assessed(actual)["hooks"]["state"], "recommended")
        self.assertTrue(recommend(*answers(), RULES, []).builder_available)


if __name__ == "__main__":
    unittest.main()
