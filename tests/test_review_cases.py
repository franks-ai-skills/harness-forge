"""The unexecuted reviewer-evaluation cases for procedure consistency (#57) and checklist lines (#49) are well formed."""

import difflib
import unittest
from pathlib import Path

import yaml

FORGE = Path(__file__).resolve().parents[1]
CASES = FORGE / "evals" / "harness-verify" / "review-consistency.yaml"
REVIEWER = FORGE / "workers" / "reviewer.md"
CHECKLIST_CASES = FORGE / "evals" / "harness-verify" / "review-checklist.yaml"


class ReviewConsistencyCasesTest(unittest.TestCase):
    def setUp(self):
        self.cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))

    def test_the_cases_are_labelled_as_not_executed(self):
        self.assertEqual((self.cases["skill"], self.cases["worker"], self.cases["executed"]),
                         ("harness-verify", "review", False))
        self.assertIn("NOT EXECUTED", CASES.read_text(encoding="utf-8"))

    def test_each_pair_has_an_unambiguous_expected_verdict(self):
        ids = [pair["id"] for pair in self.cases["pairs"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 3)
        for pair in self.cases["pairs"]:
            with self.subTest(pair["id"]):
                self.assertTrue(pair["goal"] and pair["source"].startswith("franks-ai-skills/prompt-me#"))
                if pair["rule"] is None:
                    expected = ({"goal": "not-met"}, {"goal": "met"})
                else:
                    self.assertEqual(set(pair["rule"]), {"id", "kind", "statement"})
                    self.assertIn(pair["rule"]["kind"], ("must", "must-not", "should", "should-not"))
                    self.assertTrue(pair["rule"]["id"].startswith("idea."))
                    expected = ({"rule": "fail"}, {"rule": "pass"})
                self.assertEqual((pair["inconsistent"]["expected"], pair["consistent"]["expected"]), expected)

    def test_each_corrected_passage_differs_only_in_the_passage_at_issue(self):
        for pair in self.cases["pairs"]:
            with self.subTest(pair["id"]):
                wrong, right = pair["inconsistent"]["passage"], pair["consistent"]["passage"]
                self.assertNotEqual(wrong, right)
                self.assertGreater(difflib.SequenceMatcher(None, wrong, right).ratio(), 0.5)
                steps = [[line.split(".")[0].strip() for line in text.splitlines() if line[:3].strip().rstrip(".").isdigit()]
                         for text in (wrong, right)]
                self.assertEqual(steps[0], steps[1], "the same procedure steps")

    def test_the_reviewer_is_told_to_judge_consistency(self):
        text = REVIEWER.read_text(encoding="utf-8")
        for phrase in ("every procedure step and exception", "A closed list of allowed actions",
                       "do not require every list to end", "static assessment of the supplied files"):
            self.assertIn(phrase, text)



class ReviewChecklistCasesTest(unittest.TestCase):
    def setUp(self):
        self.cases = yaml.safe_load(CHECKLIST_CASES.read_text(encoding="utf-8"))

    def test_the_cases_are_labelled_as_not_executed(self):
        self.assertEqual((self.cases["skill"], self.cases["worker"], self.cases["executed"]),
                         ("harness-verify", "review", False))
        self.assertIn("NOT EXECUTED", CHECKLIST_CASES.read_text(encoding="utf-8"))

    def test_each_pair_differs_only_in_its_requirements_line(self):
        for pair in self.cases["pairs"]:
            with self.subTest(pair["id"]):
                self.assertEqual((pair["inconsistent"]["expected"], pair["consistent"]["expected"]),
                                 ({"rule": "fail"}, {"rule": "pass"}))
                self.assertEqual(set(pair["rule"]), {"id", "kind", "statement"})
                wrong, right = (pair[side]["passage"].split("## Procedure") for side in ("inconsistent", "consistent"))
                self.assertEqual(wrong[1], right[1], "the same procedure")
                self.assertNotEqual(wrong[0], right[0])

    def test_the_reviewer_is_told_to_judge_checklist_lines(self):
        self.assertIn("instruction-style line", REVIEWER.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
