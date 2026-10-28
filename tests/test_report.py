"""Report rendering and the bounded report path."""

import tempfile
import unittest
from pathlib import Path

from harness_forge.report import render_report, write_report
from harness_forge.verify import finish_run, validate_review
from tests.test_verify import RuntimeAndInputsTest, Scenario, passing_review, skill_decision


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        self.results = finish_run(self.scenario.run, review, [], self.scenario.current())

    def test_report_groups_by_part_and_rule_and_discloses_isolation(self):
        rendered = render_report(self.scenario.run, self.results)
        self.assertIn("## Part A", rendered)
        self.assertIn("Harness result: verified", rendered)
        self.assertIn("Goal result: met by the verified build", rendered)
        self.assertIn("skills.description.length", rendered)
        self.assertIn("best-effort-v1", rendered)
        self.assertIn("not-certified", rendered)
        self.assertIn("0.1.0", rendered)
        for path, digest in self.scenario.run.frozen_inputs["files"].items():
            self.assertIn(f"{digest}  {path}", rendered)

    def test_runtime_section_is_separate_and_says_none_run(self):
        rendered = render_report(self.scenario.run, self.results)
        self.assertIn("## Runtime tests", rendered)
        self.assertIn("none run", rendered)

    def test_runtime_results_render_with_reasons(self):
        helper = RuntimeAndInputsTest()
        helper.scenario = self.scenario
        result = helper.runtime(result="not-run", observed_activation=None, reason="No observable activation signal.")
        self.scenario.run.runtime_results.append(result)
        rendered = render_report(self.scenario.run, self.results)
        self.assertIn("not-run", rendered)
        self.assertIn("No observable activation signal.", rendered)
        self.assertNotIn("none run", rendered)

    def test_an_unobservable_inventory_is_stated_under_the_runtime_tests(self):
        helper = RuntimeAndInputsTest()
        helper.scenario = self.scenario
        self.scenario.run.runtime_results.append(helper.runtime(inventory="observed"))
        self.assertNotIn("does not report", render_report(self.scenario.run, self.results))
        self.scenario.run.runtime_results.append(helper.runtime(harness="codex", inventory="not observable"))
        rendered = render_report(self.scenario.run, self.results)
        self.assertIn("Inventory gap: codex does not report the skills, plugins and MCP servers it loaded", rendered)


class ContainmentTest(unittest.TestCase):
    """#129: text from workers, runs and the repository stays in its cell or span and renders as written."""

    HOSTILE = "a|b \\|c \\\\|d `x` **b** _e_ [l](x) <b>t</b> <http://x> &amp; &#124; ~~s~~ $m$ ![i](u) line\nnext"

    def setUp(self):
        from harness_forge.report import OUTCOMES
        self.scenario = Scenario(skill_decision())
        run, hostile = self.scenario.run, self.HOSTILE
        review = passing_review(run)
        for verdict in review["verdicts"]:
            verdict.update(reason=hostile, citation=hostile)
        self.results = finish_run(run, validate_review(run, review), [], self.scenario.current())
        # A source URL may hold a pipe and other Markdown characters (rule.schema.json allows any HTTPS URL).
        self.source = "https://example.com/a|b_c*d*`e`"
        self.source_rule = sorted(item["rule_id"] for item in self.results[0].rule_results)[0]
        run.rule_index[self.source_rule] = {**run.rule_index[self.source_rule], "sources": [self.source]}
        helper = RuntimeAndInputsTest()
        helper.scenario = self.scenario
        run.runtime_results += [
            helper.runtime(observed_activation=hostile, reason=hostile, harness_version=hostile),
            helper.runtime(case_id="behave", kind="behavior", reason=hostile, harness_version="2.1.292",
                           details={"changes": [hostile], "failed": [hostile], "requests": [], "denied": [],
                                    "gaps": [], "case_digest": "0" * 64})]
        counts = lambda declared: {"declared": declared, "sources": {}, **{name: [] for name in OUTCOMES}}
        run.runtime_evidence = {"A": {"codex": {"ran": False, "reason": hostile, "activation": counts(["k1"]),
                                                "behavior": counts(["b2"])}}}
        run.whole_idea["hosting"]["plan"] = "``x`` ends`"
        run.parts[0]["answers"] = {"q": "`a` ``b``"}
        self.rendered = render_report(run, self.results)
        self.lines = self.rendered.split("\n")

    def _tables(self):
        """Each table outside fenced blocks as its lines, header first."""
        tables, current, fenced = [], [], False
        for line in self.lines:
            if line.startswith("```"):
                fenced = not fenced
            if not fenced and line.startswith("|"):
                current.append(line)
            elif current:
                tables.append(current)
                current = []
        return tables + ([current] if current else [])

    @staticmethod
    def _gfm_cells(line: str) -> int:
        """Cells by GitHub's table rule (cmark-gfm): a cell runs over backslash pairs and anything but a pipe."""
        body, count, index = line.strip()[1:], 1, 0
        body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
        while index < len(body):
            if body[index] == "\\":
                index += 2
                continue
            count += body[index] == "|"
            index += 1
        return count

    def test_every_row_keeps_the_header_cell_count_under_github_rules(self):
        tables = self._tables()
        self.assertGreaterEqual(len(tables), 4, "rules, runtime evidence, runtime tests and behavior tests")
        for table in tables:
            for line in table:
                self.assertEqual(self._gfm_cells(line), self._gfm_cells(table[0]), line)

    def test_each_cell_renders_the_text_as_written(self):
        import html

        from markdown_it import MarkdownIt
        parser = MarkdownIt("commonmark").enable("table")
        original = self.HOSTILE.replace("\n", " ")
        cells = [parser.renderer.renderInline(token.children, parser.options, {})
                 for token in parser.parse(self.rendered) if token.type == "inline" and "line next" in token.content]
        rendered = [html.unescape(cell) for cell in cells]
        # Rule reasons and citations; observed activation, reason and version; behavior reason, changes and
        # failed assertions; the runtime-evidence note; the not-run activation and behavior reasons.
        self.assertGreaterEqual(len(rendered), 2 + 3 + 3 + 1 + 2)
        for cell in rendered:
            self.assertIn(cell, (original, f"claude-code {original}"))

    def test_a_rule_source_renders_as_written(self):
        import html

        from markdown_it import MarkdownIt
        parser = MarkdownIt("commonmark").enable("table")
        row = next(line for line in self.lines if line.startswith(f"| `{self.source_rule}` |"))
        tokens = [token for token in parser.parse(row.replace("\n", " ").join(
            ["| a | b | c | d | e | f | g |\n| - | - | - | - | - | - | - |\n", "\n"]))]
        cells = [html.unescape(parser.renderer.renderInline(token.children, parser.options, {}))
                 for token in tokens if token.type == "inline"]
        self.assertEqual(cells[-1], self.source)

    def test_answers_are_one_code_span_with_their_backticks(self):
        import html
        import json

        from markdown_it import MarkdownIt
        parser = MarkdownIt("commonmark")
        for prefix, value in (("- Whole-idea answers and targets: ", self.scenario.run.whole_idea),
                              ("- Confirmed answers: ", {"q": "`a` ``b``"})):
            line = next(item for item in self.lines if item.startswith(prefix))
            span = parser.renderInline(line[len(prefix):].split(" (digest")[0])
            self.assertTrue(span.startswith("<code>") and span.endswith("</code>"), span)
            self.assertEqual(html.unescape(span[len("<code>"):-len("</code>")]), json.dumps(value, sort_keys=True))

    def test_the_label_precedes_the_first_embedded_text(self):
        label = self.rendered.index("text from review workers, runs and the repository")
        self.assertLess(label, self.rendered.index("- Whole-idea answers and targets:"))


class WriteTest(unittest.TestCase):
    def test_exact_path_and_escapes(self):
        with tempfile.TemporaryDirectory(prefix="forge-report-") as directory:
            repository = Path(directory) / "repo"
            repository.mkdir()
            path = write_report(repository, "repro-checklist", "# Report\n")
            self.assertEqual(path, repository.resolve() / ".harness" / "reports" / "repro-checklist.md")
            self.assertEqual(path.read_text(encoding="utf-8"), "# Report\n")
            for slug in ("../escape", "a/b", "", "Upper"):
                with self.subTest(slug=slug):
                    with self.assertRaises(ValueError):
                        write_report(repository, slug, "x")
            outside = Path(directory) / "outside"
            outside.mkdir()
            other = Path(directory) / "repo2"
            other.mkdir()
            (other / ".harness").symlink_to(outside)
            with self.assertRaises(ValueError):
                write_report(other, "repro-checklist", "x")
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class RuntimeEvidenceTest(unittest.TestCase):
    """#103: every outcome of a declared case is counted once, with its source."""

    def test_each_outcome_has_its_count(self):
        from types import SimpleNamespace

        from harness_forge.report import runtime_evidence, unrun_harnesses
        part = {"id": "A", "chosen": "skills", "outcome": "harness-only", "status": "built", "reuse": None}
        result = lambda case_id, value, reason="r", kind="activation": SimpleNamespace(
            part_id="A", case_id=case_id, harness="codex", result=value, reason=reason, kind=kind)
        cases = [{"id": f"c{n}", "part_id": "A", "permitted_tools": {"codex": []}} for n in range(6)]
        cases.append({"id": "b1", "part_id": "A", "kind": "behavior", "harnesses": ["codex"]})
        run = SimpleNamespace(parts=[part], targets=["claude-code", "codex"], runtime_results=[
            result("c0", "pass"), result("c1", "fail"), result("c2", "error"),
            result("c3", "not-run", "unavailable: a plugin stayed loaded"), result("c4", "not-run", "skipped"),
            result("b1", "unavailable", "unavailable: no shell", kind="behavior"),
            result("c5", "pass", "r (kept from attempt a1 of run r1)")])
        evidence = runtime_evidence(run, cases, "codex")
        codex = evidence["A"]["codex"]["activation"]
        self.assertEqual({name: codex[name] for name in ("passed", "failed", "error", "unavailable", "not_run")},
                         {"passed": ["c0", "c5"], "failed": ["c1"], "error": ["c2"], "unavailable": ["c3"],
                          "not_run": ["c4"]})
        self.assertEqual((codex["sources"]["c0"], codex["sources"]["c5"]), ("this attempt", "kept"))
        self.assertEqual(evidence["A"]["codex"]["behavior"]["unavailable"], ["b1"])
        self.assertEqual(evidence["A"]["claude-code"]["activation"]["declared"], [], "no Claude Code tools declared")
        # c4 did not run in the harness that ran: a runtime result with its reason, not another harness to run.
        self.assertEqual(unrun_harnesses(evidence), {})
        run.targets = ["claude-code", "codex"]
        cases.append({"id": "k1", "part_id": "A", "permitted_tools": {"claude-code": []}})
        self.assertEqual(unrun_harnesses(runtime_evidence(run, cases, "codex")), {"A": ["claude-code"]})
