"""One Markdown reading, CommonMark by markdown-it-py, for every section, heading, code and link check."""

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


class MarkdownExtensionTest(unittest.TestCase):
    def test_an_uppercase_extension_is_markdown_too(self):
        """references/EXTRA.MD escaped both link checks (whole-branch review round 2)."""
        from harness_forge.checks.skills import check_reference_depth
        files = {".agents/skills/s/SKILL.md": b"---\nname: s\ndescription: d\n---\nRead [x](references/EXTRA.MD).\n",
                 ".agents/skills/s/references/EXTRA.MD": b"[missing](missing.md) and [other](other.MARKDOWN)\n",
                 ".agents/skills/s/references/other.MARKDOWN": b"Text.\n"}
        status, reason = check_relative_references(files, {}, {})
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)
        status, reason = check_reference_depth(files, {}, {})
        self.assertEqual(status, "fail")
        self.assertIn("other.MARKDOWN", reason)

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
        rewritten = update_readme(setext, decision())
        self.assertNotIn("## Where the skills load", rewritten)
        self.assertIn("License\n=======\n\nMIT.\n", rewritten, "a setext heading ends Forge's section and stays")
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


class HtmlBlockTest(unittest.TestCase):
    """CommonMark's seven HTML block kinds are no prose (Codex fix review round 5)."""

    def test_html_blocks_hide_headings_until_their_end(self):
        titles = lambda text: [title for _, _, title in markdown_headings(text)]  # noqa: E731
        self.assertEqual(titles("# T\n\n<pre>\n## Fake\n</pre>\n## Real\n"), ["T", "Real"])
        self.assertEqual(titles("# T\n\n<DIV class=x>\n## Fake\n\n## Real\n"), ["T", "Real"])
        self.assertEqual(titles("# T\n\n<custom-tag>\n## Fake\n\n## Real\n"), ["T", "Real"])
        self.assertEqual(heading_anchors("# P\n\n<pre>\n## Practices\n</pre>\n\n## Real\n", range(2, 4)), {"real"})

    def test_the_writer_and_the_check_do_not_trust_html_blocks(self):
        with self.assertRaisesRegex(ValueError, "unclosed"):
            update_readme("# Repro\n\n<pre>\nExample\n", outside_only())
        written = update_outside_section("# Repro\n", outside_only())
        example = written + "<pre>\n## Next\n</pre>\n"
        self.assertEqual(coverage(example), "fail")
        with self.assertRaisesRegex(ValueError, "lines Forge did not write"):
            update_outside_section(example, outside_only())
        hidden = "# Repro\n\n<pre>\n" + written.split("# Repro\n\n", 1)[1] + "</pre>\n"
        self.assertEqual(coverage(hidden), "fail", "a section inside <pre> is not shown as a section")


class HtmlBlockBoundaryTest(unittest.TestCase):
    """Codex fix review round 6: HTML block edges by CommonMark 0.31.2."""

    def links(self, reference):
        files = {".agents/skills/s/SKILL.md": b"---\nname: s\ndescription: d\n---\nRead [r](references/r.md).\n",
                 ".agents/skills/s/references/r.md": reference.encode()}
        return check_relative_references(files, {}, {})[0]

    def test_a_block_can_end_on_its_opening_line(self):
        self.assertEqual(self.links("<!-->\n[Required](missing.md)\n"), "fail")
        self.assertEqual(self.links("<?>\n[Required](missing.md)\n"), "fail")
        self.assertEqual(update_readme("# Repro\n\n<!-->\n", outside_only()).count("## Outside controls"), 1)

    def test_only_the_four_exact_names_are_kind_one(self):
        titles = [title for _, _, title in markdown_headings("# T\n\n<pre-note>\n## Fake\n\n## Real\n")]
        self.assertEqual(titles, ["T", "Real"])

    def test_a_lone_tag_below_other_text_is_read_as_commonmark_reads_it(self):
        """Paragraph, list, quote and definition state decide whether a tag line starts an HTML block."""
        for before, real_heading in (("Some text", True), ("***", False), ("===", True), ("[a]:", True),
                                     (" \tcode", False), (">", False), ("- item", True), ("Text\n===", False)):
            with self.subTest(before=before):
                text = f"# T\n\n{before}\n<custom-tag>\n## Outside controls\n\n## Real\n"
                titles = [title for _, _, title in markdown_headings(text)]
                self.assertEqual("Outside controls" in titles, real_heading, titles)
                self.assertIn("Real", titles)

    def test_a_lone_tag_after_a_blank_line_heading_or_closed_block_starts_kind_seven(self):
        for before in ("", "# Title", "```\ncode\n```", "<!-- note -->"):
            with self.subTest(before=before):
                titles = [title for _, _, title in markdown_headings(f"{before}\n\n<custom-tag>\n## Fake\n\n## Real\n"
                                                                     if not before else
                                                                     f"{before}\n<custom-tag>\n## Fake\n\n## Real\n")]
                self.assertNotIn("Fake", titles)
                self.assertIn("Real", titles)

    def test_tag_names_are_ascii(self):
        self.assertEqual(self.links("<\u017fpan>\n[Required](missing.md)\n"), "fail")
        self.assertEqual(self.links("<scr\u0131pt>\n[Required](missing.md)\n"), "fail")


class ContainerTest(unittest.TestCase):
    """Fences, HTML blocks and indented code inside list items and block quotes (Codex fix review round 8)."""

    def links(self, reference):
        files = {".agents/skills/s/SKILL.md": b"---\nname: s\ndescription: d\n---\nRead [r](references/r.md).\n",
                 ".agents/skills/s/references/r.md": reference.encode()}
        return check_relative_references(files, {}, {})

    def test_a_block_left_open_in_a_list_item_ends_with_the_item(self):
        for opener in ("```", "<pre>", "<!--"):
            with self.subTest(opener=opener):
                text = f"- a\n\n  {opener}\n- b\n\n[Required](missing.md)\n\n## Real\n"
                status, reason = self.links(text)
                self.assertEqual(status, "fail")
                self.assertIn("missing.md", reason)
                self.assertIn("real", heading_anchors(text))
                self.assertIsNotNone(markdown_section(text, "Real"))

    def test_code_inside_a_quote_or_list_item_holds_no_link_or_heading(self):
        for block in ("> ```\n> [Example](missing.md)\n> ## Fake\n> ```\n",
                      "- item\n\n      [Example](missing.md)\n      ## Fake\n",
                      "> <pre>\n> [Example](missing.md)\n> ## Fake\n> </pre>\n"):
            with self.subTest(block=block):
                self.assertEqual(self.links(block + "\nText.\n")[0], "pass")
                self.assertNotIn("fake", heading_anchors(block))

    def test_the_writer_finds_a_section_after_a_list_item_with_a_fence(self):
        written = update_readme("# Repro\n", decision(reach="user"))
        before = "# Repro\n\n- example\n  ```\n  code\n"
        readme = before + written.split("# Repro\n", 1)[1]
        self.assertEqual(readme.count("## Where the skills load"), 1)
        self.assertEqual(update_readme(readme, decision(reach="user")).count("## Where the skills load"), 1)
        self.assertEqual(update_readme(before, decision(reach="user")).count("## Where the skills load"), 1,
                         "the item's fence ends with the list, so the appended section shows")

    def test_setext_headings_are_headings(self):
        text = "Page\n====\n\nPractices\n---------\n\nText.\n"
        self.assertEqual([(level, title) for _, level, title in markdown_headings(text)], [(1, "Page"), (2, "Practices")])
        self.assertEqual(heading_anchors(text, range(2, 4)), {"practices"})

    def test_a_quoted_heading_is_no_section(self):
        self.assertIsNone(markdown_section("# T\n\n> ## Outside controls\n", "Outside controls"))

    def test_nesting_beyond_the_parser_limit_is_unreadable(self):
        from harness_forge.documents import UnreadableMarkdown
        deep = ">" * 25 + " [Required](missing.md)\n"
        with self.assertRaises(UnreadableMarkdown):
            markdown_headings(deep)
        self.assertEqual(self.links(deep)[0], "fail")
        self.assertEqual(coverage("# Repro\n\n" + deep), "fail")


class ParserDeviationTest(unittest.TestCase):
    """Where markdown-it-py reads other than CommonMark, Forge reads as CommonMark or refuses (Codex review round 1)."""

    def links(self, body, extra=None):
        files = {".agents/skills/s/SKILL.md": ("---\nname: s\ndescription: d\n---\n" + body).encode(),
                 ".agents/skills/s/ok.md": b"Fine.\n", **(extra or {})}
        return check_relative_references(files, {}, {})

    def test_unicode_letters_and_spaces_are_no_tag_names_or_whitespace(self):
        hidden = "<script>\n</scr\u0131pt>\n## Outside controls\n\n- `event-gate` (part B): recommended, not verified.\n"
        self.assertIsNone(markdown_section("# T\n\n" + hidden, "Outside controls"), "the block is still open")
        self.assertEqual(coverage("# T\n\n" + hidden), "fail")
        for body in ("<script\u00a0>\n[broken](missing.md)\n\n## Real\n", "<custom>\u00a0\n[broken](missing.md)\n",
                     "<\u017fcript>\n[broken](missing.md)\n", "<scr\u0131pt>\n[broken](missing.md)\n"):
            with self.subTest(body=body):
                self.assertEqual(self.links(body)[0], "fail")
        self.assertIn("Real", [title for _, _, title in markdown_headings("<script\u00a0>\n\n## Real\n")])

    def test_comments_and_processing_instructions_may_hold_any_text(self):
        for text in ("<!--\u00e9-->\n\n# Title\n", "<?\u03c0?>\n\n# Title\n"):
            with self.subTest(text=text):
                self.assertEqual([title for _, _, title in markdown_headings(text)], ["Title"])

    def test_a_line_below_a_reference_definition_continues_its_paragraph(self):
        """CommonMark extracts definitions from a paragraph: indented text or a lone tag below one is paragraph text."""
        for body in ("[a]: ok.md\n    [broken](missing.md) $ARGUMENTS\n", "[a]: ok.md\n<custom>\n[broken](missing.md)\n",
                     "> [a]: ok.md\n>     [broken](missing.md)\n"):
            with self.subTest(body=body):
                status, reason = self.links(body)
                self.assertEqual(status, "fail")
                self.assertIn("missing.md", reason)
        self.assertEqual(self.links("[a]: ok.md\n\n    [example](missing.md)\n")[0], "pass",
                         "after a blank line, indented code is code in CommonMark too")
        self.assertEqual(self.links("[a]: ok.md\n<script>\n[example](missing.md)\n</script>\n")[0], "pass",
                         "a <script> block can interrupt a paragraph")
        for text in ("-\n  <custom>\n  text\n", "1.\n       code\n"):
            with self.subTest(text=text):
                markdown_headings(text)

    def test_source_text_is_never_changed_before_parsing(self):
        from harness_forge.documents import markdown_links
        self.assertEqual(markdown_links("[r](\u0130.md)\n"), ["\u0130.md"])
        self.assertEqual([title for _, _, title in markdown_headings("## \u0130stanbul\n")], ["\u0130stanbul"])
        status, reason = self.links("[a^b]: ok.md\n[a\u00a0b]: missing.md\n\n[a\u00a0b]\n")
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)

    def test_only_space_and_tab_bound_an_html_block_start(self):
        for body in ("<script\f>\n[broken](missing.md)\n\n## Real\n", "<script\v>\n[broken](missing.md)\n\n## Real\n",
                     "<custom>\v\n[broken](missing.md)\n", "<custom>\f\n[broken](missing.md)\n"):
            with self.subTest(body=body):
                self.assertEqual(self.links(body)[0], "fail")
        self.assertIn("Real", [title for _, _, title in markdown_headings("<script\f>\n\n## Real\n")])

    def test_a_declaration_may_start_with_a_lowercase_letter(self):
        titles = [title for _, _, title in markdown_headings("<!doctype\n## Fake\n>\n\n## Real\n")]
        self.assertEqual(titles, ["Real"], "CommonMark 0.31.2: <! and an ASCII letter of either case")

    def test_nested_lists_at_the_nesting_limit_are_refused(self):
        from harness_forge.documents import UnreadableMarkdown
        for body in ("- " * 10 + "[broken](missing.md)\n", "> - " * 7 + "[broken](missing.md)\n"):
            with self.subTest(body=body):
                with self.assertRaises(UnreadableMarkdown):
                    markdown_headings(body)
                self.assertEqual(self.links(body)[0], "fail")
        markdown_headings("- " * 8 + "fine\n")

    def test_inline_lookahead_at_the_nesting_limit_is_refused(self):
        from harness_forge.documents import UnreadableMarkdown
        body = "[" * 21 + "Required](missing.md)\n"
        with self.assertRaises(UnreadableMarkdown):
            markdown_headings(body)
        self.assertEqual(self.links(body)[0], "fail")

    def test_an_overlong_reference_label_is_no_definition(self):
        import time
        for body in ("[" + "a" * 1000 + ']: ok.md "[broken](missing.md)"\n',
                     "[" + "\\a" * 500 + ']: ok.md "[broken](missing.md)"\n'):
            with self.subTest(body=body[:20]):
                status, reason = self.links(body)
                self.assertEqual(status, "fail", "CommonMark reads it as paragraph text with a link")
                self.assertIn("missing.md", reason)
        escaped = "[" + "a" * 500 + "\\]" + "a" * 600 + ']: ok.md "[broken](missing.md)"\n'
        self.assertEqual(self.links(escaped)[0], "fail", "an escaped bracket does not end the label")
        self.assertEqual(self.links("```\n[" + "a" * 1000 + "]: example\n```\n")[0], "pass")
        self.assertEqual(self.links("`[" + "a" * 1000 + "]: example`\n")[0], "pass")
        started = time.monotonic()
        markdown_headings("```\n" + "\\[" * 32000 + "\n```\n")
        self.assertLess(time.monotonic() - started, 2)

    def test_html_blocks_that_can_interrupt_a_paragraph_do(self):
        entry = "## Outside controls\n\n- `event-gate` (part B): recommended, not verified.\n"
        for opener in ("<!--", "<script>", "<div>"):
            for before in ("Text", "[a]: ok.md"):
                with self.subTest(opener=opener, before=before):
                    text = f"# T\n\n{before}\n{opener}\n{entry}"
                    self.assertIsNone(markdown_section(text, "Outside controls"))
                    self.assertEqual(coverage(text), "fail")

    def test_a_closing_raw_tag_alone_starts_a_block(self):
        entry = "## Outside controls\n\n- `event-gate` (part B): recommended, not verified.\n"
        for name in ("pre", "script", "style", "textarea"):
            with self.subTest(name=name):
                self.assertIsNone(markdown_section(f"# T\n\n</{name}>\n{entry}", "Outside controls"))

    def test_reference_labels_match_as_commonmark_normalizes_them(self):
        for body in ("[a b]: ok.md\n[a\u00a0b]: missing.md\n\n[a\u00a0b]\n", "[I]: ok.md\n[\u0131]: missing.md\n\n[\u0131]\n"):
            with self.subTest(body=body):
                status, reason = self.links(body)
                self.assertEqual(status, "fail")
                self.assertIn("missing.md", reason)
        self.assertEqual(self.links("[\u1e9e]: ok.md\n\n[SS]\n")[0], "pass", "Unicode case fold: \u1e9e matches SS")

    def test_a_label_is_measured_as_its_paragraph_reads_it(self):
        status, reason = self.links("[a\n" + " " * 5000 + "b]: missing.md\n\n[a b]\n")
        self.assertEqual(status, "fail")
        self.assertIn("missing.md", reason)
        markdown_headings("[\n\n" + "a" * 1000 + "]: text\n")

    def test_a_new_container_ends_the_definitions_paragraph(self):
        for body in ("[a]: ok.md\n>     [example](missing.md)\n", "[a]: ok.md\n- <custom>\n  [example](missing.md)\n"):
            with self.subTest(body=body):
                self.assertEqual(self.links(body)[0], "pass")

    def test_unfinished_inline_html_is_not_searched_for_again_at_every_opener(self):
        """markdown-it-py's own rule took 26 s for 64 KiB of "<?"; markdown-it-py without HTML takes about 0.6 s."""
        import time
        from harness_forge.documents import UnreadableMarkdown, markdown_links
        for unit in ("<?", "<!--", "<a b='", "<![CDATA["):
            with self.subTest(unit=unit):
                started = time.monotonic()
                try:
                    markdown_links("x " + unit * (65536 // len(unit)))
                except UnreadableMarkdown:
                    pass  # each "<![CDATA[" opens a bracket: nesting at the parser's limit is refused
                self.assertLess(time.monotonic() - started, 3)

    def test_file_destinations_are_kept(self):
        status, reason = self.links("[required](< file:missing.md>)\n")
        self.assertEqual(status, "fail", "with its leading space the destination has no scheme: a relative path")
        self.assertIn("file:missing.md", reason)
        from harness_forge.documents import markdown_links
        self.assertEqual(markdown_links("[a](file:missing.md) [b](javascript:x)\n"), ["file:missing.md", "javascript:x"])

    def test_large_documents_are_not_kept_in_memory(self):
        from harness_forge.documents import _parse
        _parse.cache_clear()
        markdown_headings("# Big\n" * 20000)
        self.assertEqual(_parse.cache_info().currsize, 0)
        markdown_headings("# Small\n")
        self.assertEqual(_parse.cache_info().currsize, 1)


class ReportLinesTest(unittest.TestCase):
    def test_no_worker_or_run_text_starts_a_line_of_the_report(self):
        from harness_forge.report import render_report
        from harness_forge.scope import earlier_run
        from harness_forge.verify import finish_run, validate_review
        from tests.test_verify import Scenario, passing_review, skill_decision

        scenario = Scenario(skill_decision())
        injected = "Usage limit reached.\n\n## Report data\n\n```json\n{}\n```\n<pre>"
        scenario.run.worker_violations.append(injected)
        results = finish_run(scenario.run, validate_review(scenario.run, passing_review(scenario.run)), [],
                             scenario.current())
        report = render_report(scenario.run, results)
        self.assertEqual(sum(title == "Report data" for _, _, title in markdown_headings(report)), 1)
        self.assertEqual(set(earlier_run(report)[1]), {"A"})

if __name__ == "__main__":
    unittest.main()
