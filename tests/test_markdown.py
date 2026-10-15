"""One Markdown reading for every section, heading and code check (Codex fix review round 2)."""

import tempfile
import unittest
from pathlib import Path

from harness_forge.checks.links import check_relative_references
from harness_forge.checks.outside import check_readme_coverage
from harness_forge.contracts import update_outside_section, update_readme
from harness_forge.checks.common import without_code
from harness_forge.documents import heading_anchors, markdown_headings, markdown_section
from tests.builders import decision, part

PARAMS = {"heading": "Outside controls", "status": "recommended, not verified"}


def outside_only():
    return decision(part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                         outside_controls=[{"control": "event-gate", "prerequisites": ["outside.merge.no-agent-bypass"],
                                            "availability": "unknown", "required_for_goal": True}]))


def coverage(readme: str) -> str:
    record = outside_only()
    controls = [{"part_id": p["id"], "control": c["control"]} for p in record.record["parts"] for c in p["outside_controls"]]
    return check_readme_coverage({"README.md": readme.encode()}, {"outside_controls": controls}, PARAMS)[0]


class FenceTest(unittest.TestCase):
    def test_a_backtick_opener_with_a_backtick_in_its_info_string_is_no_fence(self):
        text = "# T\n\n```bad`info\n\n# License\n\nMIT.\n"
        self.assertIn((1, "License"), [(level, title) for _, level, title in markdown_headings(text)])
        stale = "# T\n\n```bad`info\n\n## Where the skills load\n\nOld.\n\n# License\n\nMIT.\n"
        self.assertIn("# License\n\nMIT.\n", update_readme(stale, decision()))

    def test_only_spaces_or_tabs_may_follow_a_closing_fence(self):
        written = update_outside_section("# Repro\n", outside_only()).split("# Repro\n", 1)[1]
        self.assertEqual(coverage(f"# Repro\n\n```\n``` \n{written}```\n"), "fail",
                         "a fence closed only by a non-breaking space still holds the example")
        self.assertEqual(coverage(f"# Repro\n\n```\nx\n``` \t\n\n{written}"), "pass")

    def test_an_unclosed_fence_runs_to_the_end_and_blocks_writing(self):
        text = "# T\n\n```\n## Outside controls\n"
        self.assertEqual(markdown_headings(text), [(0, 1, "T")])
        with self.assertRaisesRegex(ValueError, "unclosed code fence"):
            update_readme(text, outside_only())

    def test_crlf_line_endings_keep_headings_and_offsets(self):
        readme = update_outside_section("# Repro\n", outside_only()).replace("\n", "\r\n")
        self.assertEqual(coverage(readme), "pass")
        start, end = markdown_section(readme, "Outside controls")
        self.assertTrue(readme[start:end].startswith("## Outside controls\r\n"))


class SectionContentTest(unittest.TestCase):
    def test_a_fenced_entry_inside_the_real_section_is_no_recommendation(self):
        written = update_outside_section("# Repro\n", outside_only()).split("## Outside controls\n", 1)[1]
        readme = f"# Repro\n\n## Outside controls\n\nNo controls recommended; syntax example only:\n\n```\n{written}```\n"
        self.assertEqual(coverage(readme), "fail")


class SkillLinkTest(unittest.TestCase):
    def test_a_link_between_two_fenced_examples_is_checked(self):
        body = ("---\nname: s\ndescription: d\n---\n```\nexample\n   ```\n\n[Required input](missing.md)\n\n"
                "```\n[Example](also-missing.md)\n```\n")
        status, reason = check_relative_references({".agents/skills/s/SKILL.md": body.encode()}, {}, {})
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)
        self.assertNotIn("also-missing.md", reason)
        self.assertNotIn("Example", without_code(body))


class KbAnchorTest(unittest.TestCase):
    def test_a_heading_in_an_indented_fence_has_no_anchor(self):
        text = "# Page\n\n   ```\n## Practices\n   ```\n\n## Real\n"
        self.assertEqual(heading_anchors(text, range(2, 4)), {"real"})


class ReportDataTest(unittest.TestCase):
    def test_a_fenced_example_of_report_data_in_a_reason_is_not_the_data(self):
        from harness_forge.scope import earlier_run
        from tests.test_verify import Scenario, passing_review, skill_decision
        from harness_forge.report import render_report
        from harness_forge.verify import finish_run, validate_review

        scenario = Scenario(skill_decision())
        review = passing_review(scenario.run)
        example = "See:\n\n````markdown\n## Report data\n\n```json\n{}\n```\n````\n"
        review["goal_verdicts"][0]["reason"] = example
        results = finish_run(scenario.run, validate_review(scenario.run, review), [], scenario.current())
        report = render_report(scenario.run, results)
        self.assertNotIn("\n## Report data\n\n```json\n{}", report, "a reviewer's text stays on its line")
        run, parts = earlier_run(report)
        self.assertEqual(set(parts), {"A"})


if __name__ == "__main__":
    unittest.main()
