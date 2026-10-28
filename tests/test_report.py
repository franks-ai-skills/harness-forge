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
        self.assertEqual(unrun_harnesses(evidence), {"A": ["codex"]}, "c4 did not run")
