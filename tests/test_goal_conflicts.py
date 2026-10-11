"""Goal conflicts: a chosen mechanism the confirmed answers rule out, and the three ways forward."""

import unittest
from pathlib import Path

import yaml

from harness_forge.contracts import render_contract, resolve_goal_conflict
from harness_forge.models import Rule
from harness_forge.selection import goal_conflict, selection_state
from tests.builders import ANSWERS, EVIDENCE, decision, part

SELECTION = yaml.safe_load((Path(__file__).resolve().parents[1] / "snapshot/rules/selection.yaml").read_text(encoding="utf-8"))
RULES = [Rule.from_dict(rule) for rule in SELECTION["rules"]]
DOMAINS = {fact["id"]: list(fact["values"]) for fact in SELECTION["facts"]}
OWN_CONTEXT = {**ANSWERS, "own_context": True}  # the Claude Code trial's answers: subagents, skills ruled out
IRREVERSIBLE_BY_USER = {**ANSWERS, "irreversible": True, "trigger": "user"}  # commands; skills ruled out
IRREVERSIBLE = {**ANSWERS, "irreversible": True}  # an outside control instead; every mechanism ruled out


def conflicted(answers=OWN_CONTEXT, recommended="subagents", chosen="skills", outcome="harness-only", **extra):
    linked = part("B", "Report the checklist's gaps", connects_to=[{"to": "A", "how": "uses"}])
    return decision(part("A", "Write a reproduction checklist in its own context", answers=answers, outcome=outcome,
                         recommended=recommended, chosen=chosen, override_reason="I prefer a skill",
                         status="built", **extra), linked)


def detect(record, part_id="A"):
    return goal_conflict(record.part(part_id), record.record["facts"], RULES, domains=DOMAINS)


class DetectionTest(unittest.TestCase):
    def test_a_ruled_out_choice_is_a_goal_conflict(self):
        conflict = detect(conflicted())
        self.assertEqual(conflict["severity"], "error")
        self.assertEqual(conflict["choices"], ["recommended", "adjust-goal", "keep"])
        self.assertEqual((conflict["goal"], conflict["chosen"], conflict["recommended"]),
                         ("Write a reproduction checklist in its own context", "skills", "subagents"))
        self.assertEqual([item["rule_id"] for item in conflict["failing"]], ["selection.eligibility.own-context-skills"])
        self.assertTrue(all(item["sources"] and item["statement"] for item in conflict["failing"]))
        self.assertTrue(conflict["pros"] and conflict["cons"], "the recommended mechanism's pros and cons are shown")
        self.assertIn({"fact": "own_context", "current": True, "values": [False]}, conflict["adjust"])

    def test_a_recommendation_only_mismatch_warns(self):
        record = conflicted(answers=ANSWERS, recommended="skills", chosen="instructions")
        conflict = detect(record)
        self.assertEqual(conflict["severity"], "warning")
        self.assertEqual((conflict["choices"], conflict["failing"]), ([], []))
        self.assertEqual(conflict["chosen_state"], "viable alternative")

    def test_the_recommended_choice_has_no_conflict(self):
        self.assertIsNone(detect(conflicted(answers=ANSWERS, recommended="skills", chosen="skills")))

    def test_a_stale_or_open_selection_is_reported(self):
        state = selection_state(conflicted(answers=ANSWERS, recommended="subagents").part("A"),
                                conflicted().record["facts"], RULES)
        self.assertFalse(state["current"])
        self.assertIn("recorded harness-only/subagents", state["problem"])
        open_part = conflicted(answers={"job": "procedure"}).part("A")
        state = selection_state(open_part, conflicted().record["facts"], RULES)
        self.assertIsNotNone(state["pending_question"])
        self.assertIn("still has an open question", state["problem"])


class ResolutionTest(unittest.TestCase):
    def resolve(self, record, choice, adjusted=None, reason="Decided after seeing the conflict."):
        return resolve_goal_conflict(record, "A", choice, adjusted, reason, rules=RULES, date="2026-10-12")

    def test_keep_retains_the_error(self):
        record = conflicted()
        result = self.resolve(record, "keep")
        failing_rule_ids = ["selection.eligibility.own-context-skills"]
        self.assertEqual(result["next_stage"], "report")
        self.assertEqual(result["remaining_error_ids"], failing_rule_ids)
        self.assertEqual(result["invalidated_parts"], [])
        changed = result["decision"].part("A")
        self.assertEqual(changed["goal_conflict"], {"choice": "keep", "reason": "Decided after seeing the conflict.",
                                                    "date": "2026-10-12", "chosen": "skills", "rule_ids": failing_rule_ids})
        self.assertEqual({k: v for k, v in changed.items() if k != "goal_conflict"}, record.part("A"))
        self.assertNotIn("goal_conflict", record.part("A"), "the input decision is not changed")

    def test_recommended_reroutes_by_mechanism(self):
        for answers, recommended, outcome, stage in (
            (IRREVERSIBLE_BY_USER, "commands", "outside-alongside", "build"),
            (OWN_CONTEXT, "subagents", "harness-only", "intake"),
            (IRREVERSIBLE, None, "outside-instead", "readme"),
        ):
            with self.subTest(stage=stage):
                record = conflicted(answers=answers, recommended=recommended, outcome=outcome)
                result = self.resolve(record, "recommended")
                changed = result["decision"].part("A")
                self.assertEqual(result["next_stage"], stage)
                self.assertEqual((changed["chosen"], changed["override_reason"], changed["status"]),
                                 (recommended, None, "decided"))
                self.assertEqual(result["invalidated_parts"], ["A", "B"], "the part and its linked parts")
                self.assertEqual(result["remaining_error_ids"], [])

    def test_adjust_goal_keeps_history_and_returns_to_the_questions(self):
        record = conflicted()
        record.record["research"]["deep_digests"]["A"] = "d" * 64
        result = self.resolve(record, "adjust-goal", "Write a reproduction checklist in the session",
                              reason="It does not need a context of its own.")
        changed = result["decision"].part("A")
        self.assertEqual(changed["goal"], "Write a reproduction checklist in the session")
        self.assertEqual(changed["goal_history"], [{"goal": "Write a reproduction checklist in its own context",
                                                    "replaced": "2026-10-12",
                                                    "reason": "It does not need a context of its own."}])
        self.assertNotIn("own_context", changed["answers"], "the answer the conflict rests on is asked again")
        self.assertEqual(changed["status"], "decided")
        self.assertEqual(result["next_stage"], "questions")
        self.assertEqual(result["invalidated_parts"], ["A", "B"])
        self.assertNotIn("A", result["decision"].record["research"]["deep_digests"])
        state = selection_state(changed, record.record["facts"], RULES)
        self.assertIsNotNone(state["pending_question"], "it cannot go straight to a new build")

    def test_invalid_resolutions_are_refused(self):
        with self.assertRaisesRegex(ValueError, "no goal conflict"):
            self.resolve(conflicted(answers=ANSWERS, recommended="skills", chosen="instructions"), "keep")
        with self.assertRaisesRegex(ValueError, "adjusted goal"):
            self.resolve(conflicted(), "adjust-goal")
        with self.assertRaisesRegex(ValueError, "adjusted goal"):
            self.resolve(conflicted(), "adjust-goal", "Write a reproduction checklist in its own context")
        with self.assertRaisesRegex(ValueError, "choice"):
            self.resolve(conflicted(), "ignore")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.resolve(conflicted(), "keep", reason=" ")

    def test_the_contract_shows_a_kept_conflict(self):
        record = self.resolve(conflicted(), "keep")["decision"]
        contract = render_contract(record, None, [], worker_evidence=EVIDENCE)
        self.assertIn("Goal conflict: skills is ruled out by selection.eligibility.own-context-skills; kept "
                      "(Decided after seeing the conflict.)", contract.text)
        self.assertNotEqual(contract.digest, render_contract(conflicted(), None, [], worker_evidence=EVIDENCE).digest)


if __name__ == "__main__":
    unittest.main()
