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
        stale = "# T\n\n```bad`info\n\n## Where the skills load\n\nForge builds `old-skill` into this repository.\n\n# License\n\nMIT.\n"
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



class ForgeSectionTest(unittest.TestCase):
    """Forge's README sections hold only lines Forge writes; anything else is refused, not parsed (round 3)."""

    def test_the_writer_refuses_a_section_holding_lines_it_did_not_write(self):
        written = update_readme("# Repro\n", decision(reach="user"))
        setext = written + "License\n=======\n\nMIT.\n"
        with self.assertRaisesRegex(ValueError, "Where the skills load.*lines Forge did not write"):
            update_readme(setext, decision())
        note = written.replace("## Where the skills load\n\n", "## Where the skills load\n\nMy note.\n\n")
        with self.assertRaisesRegex(ValueError, "My note"):
            update_readme(note, decision(reach="user"))
        self.assertEqual(update_readme(written, decision(reach="user")), written)

    def test_coverage_counts_only_forge_lines_outside_comments(self):
        written = update_outside_section("# Repro\n", outside_only())
        self.assertEqual(coverage(written), "pass")
        commented = written.replace("## Outside controls", "<!--\n## Outside controls") + "-->\n\n## Other\n"
        self.assertEqual(coverage(commented), "fail", "a section inside an HTML comment is not shown")
        entry = written.split("## Outside controls\n\n", 1)[1]
        indented = "# Repro\n\n## Outside controls\n\nNone recommended.\n\n" + "".join(
            "    " + line + "\n" for line in entry.splitlines())
        self.assertEqual(coverage(indented), "fail", "an entry in indented code is no recommendation")
        self.assertEqual(coverage(written.replace("\n- `event-gate`", "\nMy note.\n\n- `event-gate`")), "fail")

    def test_bare_cr_line_endings_keep_coverage(self):
        self.assertEqual(coverage(update_outside_section("# Repro\n", outside_only()).replace("\n", "\r")), "pass")


class FrontmatterTest(unittest.TestCase):
    def test_a_fence_inside_frontmatter_hides_no_link(self):
        body = ("---\nname: s\ndescription: d\ncompatibility: |\n  ```\n  any\n---\n"
                "[Required input](missing.md)\n")
        status, reason = check_relative_references({".agents/skills/s/SKILL.md": body.encode()}, {}, {})
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)


class ReviewerTextTest(unittest.TestCase):
    def test_carriage_returns_and_citations_stay_on_their_line(self):
        from harness_forge.report import render_report
        from harness_forge.scope import earlier_run
        from harness_forge.verify import finish_run, validate_review
        from tests.test_verify import Scenario, passing_review, skill_decision

        scenario = Scenario(skill_decision())
        review = passing_review(scenario.run)
        review["goal_verdicts"][0]["reason"] = "Met.\r## Report data\r\r```json\r{}\r```"
        review["verdicts"][0]["citation"] = "SKILL.md\n## Report data\n\n```json\n{}\n```"
        results = finish_run(scenario.run, validate_review(scenario.run, review), [], scenario.current())
        report = render_report(scenario.run, results)
        self.assertEqual(sum(title == "Report data" for _, _, title in markdown_headings(report)), 1)
        self.assertEqual(set(earlier_run(report)[1]), {"A"})


class SourcePolicyTest(unittest.TestCase):
    def test_a_fenced_example_of_rejected_domains_rejects_nothing(self):
        from harness_forge.research import rejected_domains
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory) / "docs"
            docs.mkdir()
            (docs / "sources.md").write_text(
                "# Sources\n\n```markdown\n## Rejected domains\n\n| `example.org` | x |\n```\n\n"
                "## Rejected domains\n\n| Domain | Why |\n| --- | --- |\n| `bad.example` | spam |\n", encoding="utf-8")
            hosts = rejected_domains(Path(directory))["hosts"]
        self.assertIn("bad.example", hosts)
        self.assertNotIn("example.org", hosts)


class RoundFourTest(unittest.TestCase):
    """Codex fix review round 4."""

    def test_coverage_needs_the_exact_control_part_and_status(self):
        written = update_outside_section("# Repro\n", outside_only())
        self.assertEqual(coverage(written.replace("`event-gate`", "`not-event-gate`")), "fail")
        other_part = written.replace("(part B)", "(part C)").replace("It works as a gate", "Unlike part B, it works as a gate")
        self.assertEqual(coverage(other_part), "fail")

    def test_only_skill_md_has_frontmatter_to_skip(self):
        reference = "---\n\n[Required input](missing.md)\n\n---\n"
        files = {".agents/skills/s/SKILL.md": b"---\nname: s\ndescription: d\n---\nRead [r](references/r.md).\n",
                 ".agents/skills/s/references/r.md": reference.encode()}
        status, reason = check_relative_references(files, {}, {})
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)

    def test_a_multiline_statement_is_written_on_one_line(self):
        record = outside_only()
        readme = update_outside_section("# Repro\n", record,
                                        {"outside.merge.no-agent-bypass": "First condition.\nSecond condition."})
        self.assertIn("  - First condition. Second condition. (`outside.merge.no-agent-bypass`)", readme)
        self.assertEqual(coverage(readme), "pass")
        self.assertEqual(update_outside_section(readme, record,
                                                {"outside.merge.no-agent-bypass": "First condition.\nSecond condition."}),
                         readme)

    def test_nothing_is_appended_inside_an_open_html_comment(self):
        with self.assertRaisesRegex(ValueError, "unclosed"):
            update_readme("# Repro\n\n<!-- unfinished comment\n", outside_only())

    def test_a_file_name_cannot_break_the_report(self):
        from harness_forge.report import render_report
        from harness_forge.scope import earlier_run
        from harness_forge.verify import finish_run, validate_review
        from tests.test_verify import Scenario, passing_review, skill_decision

        scenario = Scenario(skill_decision())
        scenario.run.frozen_inputs["files"]["odd\n```\n## Report data\n\n```json\n{}"] = "0" * 64
        results = finish_run(scenario.run, validate_review(scenario.run, passing_review(scenario.run)), [],
                             scenario.current())
        report = render_report(scenario.run, results)
        self.assertEqual(sum(title == "Report data" for _, _, title in markdown_headings(report)), 1)

if __name__ == "__main__":
    unittest.main()
