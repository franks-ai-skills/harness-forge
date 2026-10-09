"""Intake state: part ids, links, questions, decision records and the README section."""

import tempfile
import unittest
from pathlib import Path

from harness_forge import contracts
from harness_forge.contracts import (
    combine_parts,
    load_decision,
    new_part_id,
    next_intake_question,
    split_part,
    update_outside_section,
    write_decision,
)
from harness_forge.documents import load_document
from harness_forge.models import Confirmation
from tests.builders import decision, part

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
SELECTION = load_document(SNAPSHOT / "rules" / "selection.yaml", "selection")


def linked_decision():
    return decision(
        part("A", "Goal A", connects_to=[{"to": "C", "how": "uses"}]),
        part("B", "Goal B", connects_to=[{"to": "A", "how": "starts"}]),
        part("C", "Goal C", connects_to=[{"to": "B", "how": "enforces"}]),
    )


class PartIdsTest(unittest.TestCase):
    def test_combine_keeps_the_first_and_retires_the_second(self):
        combined = combine_parts(linked_decision(), keep="A", retire="C")
        ids = [p["id"] for p in combined.record["parts"]]
        self.assertEqual(ids, ["A", "B"])
        self.assertEqual(combined.record["retired_ids"], ["C"])
        a = combined.part("A")
        self.assertEqual(a["connects_to"], [{"to": "B", "how": "enforces"}])
        self.assertEqual(combined.part("B")["connects_to"], [{"to": "A", "how": "starts"}])

    def test_split_produces_numbered_parts_and_repairs_links(self):
        split = split_part(linked_decision(), "B", goals=["Goal B1", "Goal B2"])
        ids = [p["id"] for p in split.record["parts"]]
        self.assertEqual(ids, ["A", "B1", "B2", "C"])
        self.assertEqual(split.record["retired_ids"], ["B"])
        self.assertEqual(split.part("C")["connects_to"], [{"to": "B1", "how": "enforces"}, {"to": "B2", "how": "enforces"}])
        self.assertEqual(split.part("B1")["connects_to"], [{"to": "A", "how": "starts"}])

    def test_retired_ids_are_never_reused(self):
        combined = combine_parts(linked_decision(), keep="A", retire="C")
        self.assertEqual(new_part_id(combined), "D")
        self.assertEqual(new_part_id(split_part(linked_decision(), "B", goals=["x", "y"])), "D")

    def test_unknown_or_self_combination_is_rejected(self):
        with self.assertRaises(ValueError):
            combine_parts(linked_decision(), keep="A", retire="A")
        with self.assertRaises(ValueError):
            split_part(linked_decision(), "Z", goals=["x", "y"])


FACTS = {"target_set": "both", "reach": "repository", "plan_controls": "available", "opens_prs": False,
         "bypass_mode": False, "untrusted_repo": False, "lethal_trifecta": False}


class QuestionsTest(unittest.TestCase):
    def test_one_question_at_a_time(self):
        record = decision(part("A", "Goal A", answers={"job": "procedure"}), facts=FACTS,
                          survey_findings_used=[], split_confirmed=True)
        question = next_intake_question(record, SELECTION)
        self.assertIsInstance(question, dict)
        self.assertEqual(set(question), {"part_id", "id", "text", "choices"})
        self.assertEqual(question["part_id"], "A")

    def test_whole_idea_questions_come_first(self):
        record = decision(part("A", "Goal A", answers={"job": "procedure"}), facts={})
        question = next_intake_question(record, SELECTION)
        self.assertIsNone(question["part_id"])
        self.assertEqual(question["id"], "target_set")

    def test_survey_use_and_split_are_confirmed_before_selection(self):
        record = decision(part("A", "Goal A", answers={"job": "procedure"}), facts=FACTS)
        self.assertEqual(next_intake_question(record, SELECTION)["id"], "survey_findings_used")
        record.record["survey_findings_used"] = ["F1"]
        self.assertEqual(next_intake_question(record, SELECTION)["id"], "split_confirmed")

    def test_decided_parts_ask_nothing(self):
        record = decision(part("A", "Goal A", answers={"job": "one-step"}), facts=FACTS,
                          survey_findings_used=[], split_confirmed=True)
        self.assertIsNone(next_intake_question(record, SELECTION))


class DecisionRecordTest(unittest.TestCase):
    def test_round_trip_and_history_never_confirms(self):
        record = decision(confirmations_history=[{"run_id": "old-run", "contract_digest": "a" * 64, "part_ids": ["A"], "date": "2026-10-01"}],
                          survey_findings_used=["F1"], split_confirmed=True)
        with tempfile.TemporaryDirectory(prefix="forge-intake-") as directory:
            path = write_decision(Path(directory), record, slug="repro-checklist", date="2026-10-09")
            self.assertEqual(path, Path(directory) / ".harness" / "decisions" / "2026-10-09-repro-checklist.md")
            loaded = load_decision(path)
        self.assertEqual(loaded.record, record.record)
        self.assertEqual(loaded.body.strip(), record.body.strip())
        for value in vars(contracts).values():
            self.assertFalse(isinstance(value, Confirmation))

    def test_invalid_record_is_rejected(self):
        broken = decision()
        broken.record["parts"][0]["answers"] = {"job": ["procedure"]}
        with tempfile.TemporaryDirectory(prefix="forge-intake-") as directory:
            with self.assertRaises(ValueError):
                write_decision(Path(directory), broken, slug="x", date="2026-10-09")


class ReadmeTest(unittest.TestCase):
    def outside_only(self):
        return decision(
            part("A", "Goal A"),
            part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                 outside_controls=[{"control": "event-gate", "prerequisites": ["outside.merge.no-agent-bypass"],
                                    "availability": "unknown", "required_for_goal": True}]),
        )

    def test_section_lists_every_control_including_no_build_parts(self):
        readme = update_outside_section("# Repro\n\nIntro.\n", self.outside_only())
        self.assertIn("## Outside controls", readme)
        self.assertIn("`codeowners-agent-config` (part A): recommended, not verified", readme)
        self.assertIn("`event-gate` (part B): recommended, not verified", readme)
        self.assertIn("outside.merge.no-agent-bypass", readme)
        self.assertIn("availability unknown", readme)

    def test_unrelated_content_is_preserved_and_section_replaced(self):
        original = "# Repro\n\nIntro.\n\n## Outside controls\n\n- stale entry\n\n## Usage\n\nRun it.\n"
        updated = update_outside_section(original, self.outside_only())
        self.assertNotIn("stale entry", updated)
        self.assertIn("## Usage\n\nRun it.\n", updated)
        self.assertTrue(updated.startswith("# Repro\n\nIntro.\n"))
        self.assertEqual(update_outside_section(updated, self.outside_only()), updated)

    def test_checker_accepts_the_written_section(self):
        from harness_forge.checks.outside import check_readme_coverage

        record = self.outside_only()
        readme = update_outside_section("# Repro\n", record)
        controls = [{"part_id": p["id"], "control": c["control"]} for p in record.record["parts"] for c in p["outside_controls"]]
        status, reason = check_readme_coverage({"README.md": readme.encode()}, {"outside_controls": controls},
                                               {"heading": "Outside controls", "status": "recommended, not verified"})
        self.assertEqual(status, "pass", reason)
        status, _ = check_readme_coverage({"README.md": b"# Repro\n"}, {"outside_controls": controls},
                                          {"heading": "Outside controls", "status": "recommended, not verified"})
        self.assertEqual(status, "fail")


if __name__ == "__main__":
    unittest.main()
