"""The routing evaluation's cases: about 20 per skill, with near misses that must not trigger it (plan task 14)."""

import re
import unittest
from pathlib import Path

import yaml

EVALS = Path(__file__).resolve().parents[1] / "evals"
SKILLS = ("harness-intake", "harness-research", "build-skill", "harness-verify")


class RoutingCasesTest(unittest.TestCase):
    def test_each_skill_has_twenty_cases_with_one_explicit_and_several_near_misses(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                document = yaml.safe_load((EVALS / skill / "routing.yaml").read_text(encoding="utf-8"))
                cases = document["cases"]
                self.assertEqual(document["skill"], skill)
                self.assertGreaterEqual(len(cases), 20)
                self.assertEqual(len({case["id"] for case in cases}), len(cases))
                self.assertEqual(len({case["prompt"] for case in cases}), len(cases))
                self.assertEqual([case["mode"] for case in cases].count("explicit"), 1)
                self.assertTrue(all(case["expect"] in ("trigger", "no-trigger") for case in cases))
                self.assertGreaterEqual(sum(case["expect"] == "no-trigger" for case in cases), 6)

    def test_a_near_miss_of_one_skill_is_a_trigger_of_another_or_of_none(self):
        """Adjacent Forge tasks must route to their own skill, not to two at once."""
        triggers = {skill: {case["prompt"] for case in yaml.safe_load(
            (EVALS / skill / "routing.yaml").read_text(encoding="utf-8"))["cases"] if case["expect"] == "trigger"}
            for skill in SKILLS}
        for skill, prompts in triggers.items():
            for other, other_prompts in triggers.items():
                if other != skill:
                    self.assertFalse(prompts & other_prompts, f"{skill} and {other} both expect {prompts & other_prompts}")


    def test_a_trigger_names_the_forge_record_or_the_idea_it_concerns(self):
        """Each case runs in a fresh session: a trigger cannot lean on an earlier turn for what "it" or "the skill" is.

        A build or verify trigger names Harness Forge, whose records it acts on;
        a research trigger names Harness Forge or the tooling idea it researches
        before building.
        """
        for skill in ("harness-research", "build-skill", "harness-verify"):
            cases = yaml.safe_load((EVALS / skill / "routing.yaml").read_text(encoding="utf-8"))["cases"]
            for case in cases:
                if case["expect"] != "trigger" or case["mode"] != "implicit":
                    continue
                prompt = case["prompt"]
                with self.subTest(skill=skill, case=case["id"]):
                    self.assertTrue("Harness Forge" in prompt
                                    or (skill == "harness-research" and re.search(r"\bbuild", prompt)), prompt)
                    self.assertIsNone(re.search(r"\b(this|we built|we decided|we agreed)\b", prompt), prompt)


if __name__ == "__main__":
    unittest.main()
