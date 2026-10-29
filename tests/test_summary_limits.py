"""#99: every confirmation summary within 27 lines, 265 words and 1,575 characters, its question included."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.comparison import REVISION_CHANGED, compare
from harness_forge.contracts import render_contract, write_decision
from harness_forge.summary import (
    LIMITS, OPTIONS, QUESTION_LIMITS, SUMMARY_LIMITS, TITLES, SummaryTooLong, fits, render_question, render_summary,
    summary_size,
)
from tests.builders import EVIDENCE, decision, part
from tests.test_summary import CHECKED, DRIFT, accepted

FORGE = Path(__file__).resolve().parents[1]
LONG = "word " * 2000
KINDS = ("must", "must-not", "should", "should-not")


def large():
    """Many parts, requirements, overrides, warnings, goal conflicts, refused parts and open research."""
    parts = [part(chr(65 + index), f"Goal {index} " + LONG, slug=f"part-{index}-" + "x" * 40, goal_conflict={
        "choice": "keep", "reason": LONG, "date": "2026-10-10", "chosen": "skills",
        "rule_ids": ["selection.skills.side-effects"]}) for index in range(8)]
    record = decision(*parts, idea="An idea " + LONG, goals=[f"Goal {n} " + LONG for n in range(6)])
    revision = accepted(*[(chr(65 + index), f"idea.repro.{chr(97 + index)}.r{number}", KINDS[number % 4],
                           f"Statement {number} " + LONG) for index in range(8) for number in range(12)])
    checked = [{**CHECKED[0], "rule_id": f"skills.rule-{n}", "warnings": [LONG, LONG]} for n in range(3)]
    refused = [{"part_id": f"Z{n}", "problem": LONG} for n in range(4)]
    research = [{"part_id": chr(65 + n), "accepted": "stale", "deep_research": "none", "next": LONG}
                for n in range(8)]
    return record, revision, checked, refused, research


def rendered(record, revision, checked=(), refused=(), research=(), title="Research proposal", details=None,
             comparison=None, reason=None):
    contract = render_contract(record, revision, [], worker_evidence=EVIDENCE, drift=list(DRIFT))
    return contract, render_summary(
        contract, record.record, title=title, checked_overrides=list(checked), refused=list(refused),
        research=list(research), drift=contract.content["drift"], drift_states=["kept", "open"], conditions={},
        details_path=details or "/repo/.harness-forge/runs/20261027T000000Z-0123abcd/research-proposal-"
                                "0123456789abcdef.md", comparison=comparison, comparison_reason=reason)


class DecidesTest(unittest.TestCase):
    """#134: the first line says what confirming decides and what follows, by purpose and research stage."""

    def first(self, record, revision, title, research=()):
        return rendered(record, revision, title=title, research=list(research))[1].split("\n")[2]

    def test_each_purpose_and_research_stage(self):
        record, revision = decision(), accepted()
        stale = [{"part_id": "A", "accepted": "stale", "deep_research": "none", "next": "run the deep pass"}]
        current = [{"part_id": "A", "accepted": "current", "deep_research": "current", "next": "nothing"}]
        self.assertEqual(self.first(record, revision, "Research proposal", stale),
                         "Confirming fixes the goals, parts and answers research uses. Next: the deep pass where it "
                         "is due and your acceptance of the requirements, for the parts under Research to do.")
        researched = [{"part_id": "A", "accepted": "stale", "deep_research": "current", "next": "accept again"}]
        self.assertEqual(self.first(record, revision, "Research proposal", researched),
                         "Confirming fixes the goals, parts and answers research uses. Next: your acceptance of the "
                         "requirements, for the parts under Research to do; their deep research is current.")
        self.assertIn("the deep pass where it is due",
                      self.first(record, revision, "Research proposal", researched + [{**stale[0], "part_id": "B"}]))
        self.assertIn("Next: intake continues", self.first(record, revision, "Research proposal", current))
        self.assertIn("Next: the build.", self.first(record, revision, "Configuration proposal", current))
        self.assertIn("Next: harness-verify assesses the configuration",
                      self.first(record, revision, "Configuration summary", current))
        empty = decision()
        empty.record["parts"] = []
        self.assertEqual(self.first(empty, None, "Research proposal"),
                         "Confirming fixes the idea and its goals. Next: the survey of existing solutions.")

    def test_the_question_names_no_location(self):
        for title in ("Research proposal", "Configuration proposal", "Configuration summary"):
            for parts in (["A"], [f"A{n}" for n in range(40)]):
                question = render_question(title, parts, has_parts=True)
                self.assertNotIn("above", question)
                self.assertTrue(question.startswith(f"Confirm the {title[0].lower() + title[1:]} and its linked "
                                                    "details for "), question)


class CountingTest(unittest.TestCase):
    def test_lines_words_and_characters(self):
        self.assertEqual(summary_size("## Title\n\nTwo words\n"), {"lines": 3, "words": 4, "chars": 20})
        self.assertEqual(summary_size("没有空格的中文\n")["words"], 1, "words are split on whitespace only")
        self.assertEqual(summary_size("é 🙂\n")["chars"], 5, "code points: a combining mark counts")
        self.assertEqual(summary_size("a b\n")["words"], 2, "Unicode whitespace separates words")
        self.assertTrue(fits("x\n" * 27, LIMITS))
        self.assertFalse(fits("x\n" * 28, LIMITS))


class HeadroomTest(unittest.TestCase):
    def test_the_fullest_summary_question_and_options_stay_within_the_owners_limits(self):
        # A summary at every limit, the longest question allowed and the two options, one per line.
        body = " ".join(["w"] * SUMMARY_LIMITS["words"])
        body += "x" * (SUMMARY_LIMITS["chars"] - SUMMARY_LIMITS["lines"] - len(body))
        shown = body + "\n" * SUMMARY_LIMITS["lines"]
        self.assertEqual(summary_size(shown), SUMMARY_LIMITS)
        question = " ".join(["q"] * (QUESTION_LIMITS["words"] - 1)) + " "
        question += "x" * (QUESTION_LIMITS["chars"] - 1 - len(question))
        self.assertEqual(summary_size(question + "\n"), {**QUESTION_LIMITS})
        visible = shown + question + "\n" + "".join(option + "\n" for option in OPTIONS)
        self.assertEqual(summary_size(visible), LIMITS)


class LargeDecisionTest(unittest.TestCase):
    def test_every_purpose_fits_with_its_question_and_keeps_the_priority(self):
        record, revision, checked, refused, research = large()
        for title in TITLES.values():
            with self.subTest(title=title):
                contract, shown = rendered(record, revision, checked, refused, research, title=title)
                self.assertTrue(fits(shown, SUMMARY_LIMITS), summary_size(shown))
                question = render_question(title, [p["id"] for p in contract.parts])
                self.assertTrue(fits(question + "\n", QUESTION_LIMITS), question)
                visible = shown + question + "\n" + "\n".join(OPTIONS) + "\n"
                self.assertTrue(fits(visible, LIMITS), summary_size(visible))
                lines = shown.rstrip("\n").split("\n")
                self.assertTrue(lines[-1].startswith("**Details:** ["))
                self.assertIn("Goal conflict, part A", shown)
                self.assertTrue(lines[-3].startswith("Not shown here, see the details: "), lines[-3])
                self.assertIn("parts", lines[-3], "part lines go first")
                self.assertNotIn(" all ", shown.lower())

    def test_a_long_part_list_falls_back_in_the_question(self):
        question = render_question("Configuration proposal", [f"A{n}" for n in range(40)])
        self.assertEqual(question, "Confirm the configuration proposal and its linked details for the 40 parts "
                                   "offered there?")
        self.assertEqual(render_question("Research proposal", []),
                         "Confirm the research proposal and its linked details for the idea?")


class ImpossibleFitTest(unittest.TestCase):
    def test_a_long_details_path_is_refused_with_its_cause(self):
        record, revision = decision(), accepted()
        with self.assertRaises(SummaryTooLong) as raised:
            rendered(record, revision, details="/" + "d" * 1500 + "/research-proposal-0123456789abcdef.md")
        self.assertIn("the details link line is", str(raised.exception))
        self.assertIn("shorter path", str(raised.exception))


def _content(*parts, goals=("G",), overrides=()):
    return {"parts": [{"id": part_id, "goal": goal, "outcome": "harness-only", "mechanism": "skills",
                       "requirements": [{"rule_id": rule_id, "kind": kind, "statement": statement}
                                        for rule_id, kind, statement in rules]} for part_id, goal, rules in parts],
            "goals": list(goals), "overrides": [{"rule_id": rule_id} for rule_id in overrides]}


class CompareTest(unittest.TestCase):
    def test_requirements_match_by_id_and_weakening_is_named(self):
        before = _content(("A", "g", [("r1", "must", "x"), ("r2", "must-not", "y"), ("r3", "should", "z"),
                                      ("r4", "must", "gone"), ("r5", "should", "w")]))
        after = _content(("A", "g", [("r1", "should", "x"), ("r2", "must-not", "y2"), ("r3", "must", "z"),
                                     ("r5", "should", "w"), ("r6", "must", "new")]))
        part = compare(before, ["A"], after)["parts"][0]
        self.assertEqual(part["weakened"], ["r1", "r4"], "must to should, and a removed must")
        self.assertEqual([(c["rule_id"], c["wording"]) for c in part["changed"]], [("r2", True), ("r3", False)])
        self.assertEqual((part["new"], [r["rule_id"] for r in part["removed"]]), (["r6"], ["r4"]))

    def test_only_confirmed_parts_are_compared(self):
        before = _content(("A", "g", []), ("B", "g", []), ("D", "g", []))
        after = _content(("A", "g2", []), ("B", "g", []), ("C", "g", []))
        parts = {p["id"]: p for p in compare(before, ["A", "D"], after)["parts"]}
        self.assertEqual(parts["A"]["fields"], ["goal"])
        self.assertEqual(parts["B"]["status"], "unconfirmed", "B was in the content but not confirmed")
        self.assertEqual((parts["C"]["status"], parts["D"]["status"]), ("unconfirmed", "removed"))


class CompareCoverageTest(unittest.TestCase):
    """Nothing bound changes unreported (review of PR #116)."""

    def test_other_part_fields_idea_content_and_override_content_are_reported(self):
        before = {**_content(("A", "g", [])), "targets": ["claude-code"],
                  "overrides": [{"rule_id": "o1", "digest": "1" * 64}]}
        before["parts"][0]["outside_controls"] = []
        after = {**_content(("A", "g", [])), "targets": ["claude-code", "codex"],
                 "overrides": [{"rule_id": "o1", "digest": "2" * 64}]}
        after["parts"][0]["outside_controls"] = [{"control": "x"}]
        changes = compare(before, ["A"], after)
        self.assertEqual(changes["parts"][0]["fields"], ["outside_controls"])
        self.assertEqual((changes["other"], changes["overrides_changed"]), (["targets"], ["o1"]))

    def test_a_changed_accepted_revision_is_reported(self):
        before = {**_content(("A", "g", [])), "revision_digest": "1" * 64}
        after = {**_content(("A", "g", [])), "revision_digest": "2" * 64}
        self.assertEqual(compare(before, ["A"], after)["other"], [REVISION_CHANGED])

    def test_any_remaining_difference_is_never_called_nothing(self):
        before = _content(("A", "g", [("r1", "must", "x"), ("r2", "must", "y")]))
        after = _content(("A", "g", [("r2", "must", "y"), ("r1", "must", "x")]))
        self.assertEqual(compare(before, ["A"], after)["other"], ["other bound content"])

    def test_weakened_requirements_of_every_part_come_first(self):
        from harness_forge.summary import _change_items
        before = _content(("A", "g", [(f"a{n}", "should", "x") for n in range(5)]), ("B", "g", [("b1", "must", "x")]))
        after = _content(("A", "g2", [(f"a{n}", "should", "changed") for n in range(5)]), ("B", "g", [("b1", "should", "x")]))
        items = _change_items({"changes": compare(before, ["A", "B"], after)})
        self.assertEqual([item.kind for item in items], ["weakened requirements", "other changes",
                                                          "other requirement changes"])
        self.assertIn("Part B: 1 weakened (`b1`)", items[0].variants[0][0])


class CompareCliTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-compare-")))
        specification = importlib.util.spec_from_file_location("forge_compare", FORGE / "scripts" / "forge.py")
        self.forge = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.forge)
        self.record = decision(part("A", "Write a checklist"), part("B", "Summarize"))
        self.decision = write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        self.evidence = self.root / "workers.json"
        self.evidence.write_text(json.dumps({"worker_evidence": EVIDENCE}), encoding="utf-8")

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.forge.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def run_dir(self):
        return Path(self.cli("run-dir", "--root", self.root)[1]["path"])

    def prepare(self, run, decision_path=None, *extra):
        code, output, stderr = self.cli("prepare-contract", "--decision", decision_path or self.decision,
                                        "--evidence", self.evidence, "--run-dir", run, "--purpose", "research", *extra)
        self.assertEqual(code, 0, stderr)
        return output

    def confirm(self, run, output, part_ids, name="relay.json", **changes):
        relay = {"choice": "confirm", "displayed_digest": output["digest"], "part_ids": part_ids, "run_id": run.name,
                 **changes}
        path = run / name
        path.write_text(json.dumps(relay), encoding="utf-8")
        return path

    def test_the_details_link_shows_the_repository_relative_path(self):
        """#132: the link text is the path to copy in one piece, the destination the absolute file."""
        import urllib.parse

        from markdown_it import MarkdownIt
        output = self.prepare(self.run_dir())
        path = Path(output["details"]["path"])
        last = output["summary"].rstrip("\n").split("\n")[-1]
        tokens = MarkdownIt("commonmark").parseInline(last)[0].children
        link = next(token for token in tokens if token.type == "link_open")
        shown = "".join(token.content for token in tokens[tokens.index(link) + 1:] if token.type == "text")
        self.assertEqual(shown, path.resolve().relative_to(self.root.resolve()).as_posix())
        self.assertTrue(shown.startswith(".harness-forge/runs/"))
        self.assertEqual(urllib.parse.unquote(link.attrs["href"]), str(path))

    def test_no_relay_named_says_so(self):
        output = self.prepare(self.run_dir())
        self.assertIsNone(output["compared_with"])
        self.assertIn("No earlier confirmation was named, so nothing here is compared.", output["summary"])
        self.assertEqual(output["question"],
                         "Confirm the research proposal and its linked details for parts A, B?")

    def test_a_partial_confirmation_compares_only_its_parts(self):
        run = self.run_dir()
        relay = self.confirm(run, self.prepare(run), ["A"])
        self.record.record["parts"][0]["goal"] = "Write a better checklist"
        write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        output = self.prepare(run, None, "--compare-with", relay)
        self.assertEqual(output["compared_with"]["part_ids"], ["A"])
        self.assertIn("Changes since the confirmation of parts A (run", output["summary"])
        self.assertIn("- Part A: goal changed.", output["summary"])
        self.assertIn("- Part B: not confirmed before.", output["summary"])

    def test_unchanged_inputs_say_nothing_changed(self):
        run = self.run_dir()
        relay = self.confirm(run, self.prepare(run), ["A", "B"])
        self.assertIn("Nothing changed since the confirmation of parts A, B", self.prepare(run, None, "--compare-with",
                                                                                     relay)["summary"])

    def test_two_relays_each_compare_with_their_own_content(self):
        run = self.run_dir()
        first = self.confirm(run, self.prepare(run), ["A", "B"])
        self.record.record["goals"] = ["Changed goals"]
        write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        second = self.confirm(run, self.prepare(run), ["A", "B"], name="relay-2.json")
        self.assertIn("The idea's goals changed.", self.prepare(run, None, "--compare-with", first)["summary"])
        self.assertIn("Nothing changed", self.prepare(run, None, "--compare-with", second)["summary"])

    def test_an_unusable_relay_gives_its_reason(self):
        run = self.run_dir()
        output = self.prepare(run)
        other = write_decision(self.root, decision(part("A", "Another idea's part")), slug="other", date="2026-10-11")
        cases = {
            "the details file does not name this decision record": (other, self.confirm(run, output, ["A"])),
            "the relay's run id is not its run directory's": (None, self.confirm(run, output, ["A"], name="r1.json",
                                                                                 run_id="elsewhere")),
            "the relay names parts the confirmed content does not hold": (None, self.confirm(
                run, output, ["A", "C"], name="r2.json")),
            "the relay's part ids are malformed": (None, self.confirm(run, output, ["A", 3], name="r3.json")),
            "no details file in its run directory holds the confirmed content": (None, self.confirm(
                run, output, ["A"], name="r4.json", displayed_digest="0" * 64)),
        }
        for reason, (record, relay) in cases.items():
            with self.subTest(reason=reason):
                shown = self.prepare(run, record, "--compare-with", relay)
                self.assertIsNone(shown["compared_with"])
                self.assertIn(f"Comparison unavailable: {reason}.", shown["summary"].replace("\\", ""))

    def test_an_edited_details_file_no_longer_matches(self):
        run = self.run_dir()
        output = self.prepare(run)
        relay = self.confirm(run, output, ["A"])
        details = Path(output["details"]["path"])
        details.write_text(details.read_text(encoding="utf-8").replace('"Write a checklist"', '"Edited"'),
                           encoding="utf-8")
        self.assertIsNone(self.prepare(run, None, "--compare-with", relay)["compared_with"])

    def test_every_part_refused_offers_no_question(self):
        self.record.record["parts"][0]["outcome"] = self.record.record["parts"][1]["outcome"] = "harness-only"
        for part in self.record.record["parts"]:
            part["recommended"] = "hooks"  # select gives skills, so build would refuse the stale selection
        write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        output = self.prepare(self.run_dir())
        self.assertEqual(sorted(item["part_id"] for item in output["refused_parts"]), ["A", "B"])
        self.assertEqual((output["question"], output["options"]), (None, []))
        self.assertIn("- Offered for confirmation: nothing, every part is refused",
                      Path(output["details"]["path"]).read_text(encoding="utf-8"))

    def test_an_idea_without_parts_is_confirmed_as_the_idea(self):
        self.record.record["parts"] = []
        write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        output = self.prepare(self.run_dir())
        self.assertEqual(output["question"], "Confirm the research proposal and its linked details for the idea?")
        self.assertEqual(output["options"], ["Confirm", "Leave pending"])
        self.assertIn("- Offered for confirmation: the idea, which has no parts yet",
                      Path(output["details"]["path"]).read_text(encoding="utf-8"))

    def test_many_confirmed_parts_keep_the_status_line_bounded(self):
        self.record = decision(*[part(f"A{n}", f"Goal {n}") for n in range(40)])
        self.decision = write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        run = self.run_dir()
        relay = self.confirm(run, self.prepare(run), [f"A{n}" for n in range(40)])
        output = self.prepare(run, None, "--compare-with", relay)
        status = output["summary"].split("\n")[4]
        self.assertIn("and 34 more parts", status)
        self.assertRegex(output["summary"], r"Not shown here, see the details: .*\b(3[4-9]|[4-9][0-9]) parts")
        self.assertLessEqual(len(status), 200)
        self.assertTrue(fits(output["summary"], SUMMARY_LIMITS))
        self.assertEqual(output["question"], "Confirm the research proposal and its linked details for the 40 "
                                             "parts offered there?")
        details = Path(output["details"]["path"]).read_text(encoding="utf-8")
        self.assertIn("- Offered for confirmation: parts " + ", ".join(f"A{n}" for n in range(40)) + "\n", details)

    def test_every_omitted_kind_is_in_the_details_file(self):
        record, revision, checked, refused, research = large()
        _, shown = rendered(record, revision, checked, refused, research)
        self.assertIn("Not shown here", shown)
        from harness_forge.summary import render_listing
        contract = render_contract(record, revision, [], worker_evidence=EVIDENCE, drift=list(DRIFT))
        full = render_listing(contract, record.record, checked_overrides=checked, refused=refused, research=research,
                              drift=contract.content["drift"], drift_states=["kept", "open"], conditions={})
        for heading in ("### Part H:", "### Override warnings", "### Parts not offered for confirmation",
                        "### Research to do", "### Drift", "- **Goal conflict:**"):
            self.assertIn(heading, full)


if __name__ == "__main__":
    unittest.main()
