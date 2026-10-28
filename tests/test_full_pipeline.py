"""The whole offline workflow through the CLI with scripted workers, T12's flows included.

Split, survey, per-part choices, deep acceptance, build, a broken artifact,
its repair and a Review, then overrides that must change selection,
building and checking alike. No branch claims success after an unresolved
error or a changed contract. The live counterpart is tools/live_checks.py.
"""

import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.contracts import load_decision, split_part, write_decision
from tests.builders import ANSWERS, decision, part
from tests.test_build_skill import DESCRIPTION, NAME, PROCEDURE
from tests.test_cli_live import FORGE, Cli
from tests.test_workflow import ResearchRunner, finding


class FullPipelineTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="forge-full-")
        self.root = Path(self.directory.name)
        (self.root / "README.md").write_text("# Repro\n", encoding="utf-8")
        self.runner = ResearchRunner([finding()])
        self.cli = Cli(self.runner, self.root)
        self.cli.parts = ["A1", "A2"]

    def tearDown(self):
        self.directory.cleanup()

    def run_ok(self, *arguments, code=0):
        returned, output, stderr = self.cli(*arguments)
        self.assertEqual(returned, code, f"{arguments[0]}: {stderr}")
        return output

    def select(self, answers, root=False):
        request = self.cli.write("select.json", {"answers": answers, "whole_idea": self.facts})
        return self.run_ok("select", "--input", request, *(["--root", self.root] if root else []))

    def test_split_choose_build_break_fix_review_and_override(self):
        cli, root = self.cli, self.root
        # Intake: one idea, split into a checklist skill (A1) and a publishing step an outside control owns (A2).
        record = decision(part("A", "Turn approved bug reports into checklists and publish them", slug=NAME))
        self.facts = record.record["facts"]
        record = split_part(record, "A", ["Write a reproduction checklist when asked",
                                          "Publish the checklist to the tracker"])
        record.record.update(split_confirmed=True, survey_findings_used=[])
        decision_path = write_decision(root, record, slug="repro", date="2026-10-12")
        workers = cli.write("workers.json", self.run_ok("workers", "--harness", "claude-code"))

        relay = cli.confirm(decision_path, "intake-1")
        self.run_ok("research", "--decision", decision_path, "--pass", "survey", "--harness", "claude-code",
                    "--evidence", workers, "--relay", relay, "--root", root, "--slug", "repro", "--write")

        # Per-part choices from select: A1 is the recommended skill, A2 an outside control instead.
        checklist = self.select(dict(ANSWERS))
        self.assertEqual((checklist["outcome"], checklist["mechanism"]), ("harness-only", "skills"))
        publishing = self.select({**ANSWERS, "irreversible": True})
        self.assertEqual((publishing["outcome"], publishing["mechanism"]), ("outside-instead", None))
        record = load_decision(decision_path)
        def recorded(controls):
            return [{"control": control["id"], "prerequisites": control["prerequisites"],
                     "availability": control["availability"], "required_for_goal": control["required_for_goal"]}
                    for control in controls]

        # select's `record` holds the fields in the record's shape; it maps the controls as this test once did.
        for selected in (checklist, publishing):
            self.assertEqual(selected["record"]["outside_controls"], recorded(selected["controls"]))
        record.part("A1").update(slug=NAME, answers=dict(ANSWERS), outcome="harness-only", recommended="skills",
                                 chosen="skills", **checklist["record"])
        record.part("A2").update(slug="publish-checklist", answers={**ANSWERS, "irreversible": True},
                                 outcome="outside-instead", recommended=None, chosen=None, connects_to=[
                                     {"to": "A1", "how": "uses"}], **publishing["record"])
        write_decision(root, record, slug="repro", date="2026-10-12")
        self.run_ok("readme", "--decision", decision_path, "--root", root)

        # Deep research and acceptance for the skill.
        relay = cli.confirm(decision_path, "intake-2")
        self.run_ok("research", "--decision", decision_path, "--pass", "deep", "--part", "A1", "--harness",
                    "claude-code", "--evidence", workers, "--relay", relay, "--root", root, "--slug", "repro",
                    "--write")
        proposed = cli.write("proposed.json", {"requirements": [
            {"id": "idea.repro.a1.platform", "concept": "idea", "kind": "must", "severity": "error",
             "check": "judged", "statement": "The checklist names the platform the reproducer runs on",
             "applies_to": {"harnesses": ["claude-code", "codex"]}, "evidence": "Vendor",
             "sources": ["https://docs.example-vendor.com/repro/platforms"], "contested": False}]})
        choices = cli.write("choices.json", {"idea.repro.a1.platform": "accept"})
        self.run_ok("accept", "--decision", decision_path, "--part", "A1", "--proposed", proposed,
                    "--choices", choices, "--root", root, "--slug", "repro")
        revision = root / ".harness/research/repro.rules.yaml"

        # Build and the first verification.
        relay = cli.confirm(decision_path, "build-1", "--revision", revision)
        build = ["build", "--decision", decision_path, "--part", "A1", "--revision", revision, "--evidence", workers,
                 "--relay", relay, "--description", cli.write("description.txt", DESCRIPTION),
                 "--procedure", cli.write("procedure.md", PROCEDURE), "--root", root]
        self.run_ok(*build)
        cases = yaml.safe_load((FORGE / "evals/smoke/vertical.yaml").read_text(encoding="utf-8"))
        for case in cases["cases"]:
            case["part_id"] = "A1"
        cases_path = cli.write("cases.yaml", yaml.safe_dump(cases))
        relay = cli.confirm(decision_path, "verify-1", "--revision", revision)
        verify = ["verify", "--decision", decision_path, "--root", root, "--revision", revision, "--evidence",
                  workers, "--relay", relay, "--harness", "claude-code", "--cases", cases_path, "--slug", "repro",
                  "--write"]
        output = self.run_ok(*verify)
        self.assertEqual([(p["part_id"], p["harness_result"]) for p in output["parts"]],
                         [("A1", "verified"), ("A2", "not applicable")])

        # An error: the skill breaks, and nothing claims success.
        skill = root / f".agents/skills/{NAME}/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8").replace(f"name: {NAME}", "name: Broken_Name"),
                         encoding="utf-8")
        output = self.run_ok(*verify, code=1)
        self.assertEqual(output["parts"][0]["harness_result"], "not verified")
        report = (root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        # The reason sits in its cell escaped (#129): `Broken\_Name` renders as Broken_Name.
        self.assertIn("| `links.parts` | error | script | fail | part A1's skill names itself Broken\\_Name", report)

        # The fix: the builder repairs only the requested artifact, and a Review checks it.
        output = self.run_ok(*build, code=1)
        self.assertIn("-name: Broken_Name", output["diff"])
        self.run_ok(*build, "--replace", f".agents/skills/{NAME}/SKILL.md")
        shown = self.run_ok("scope", "--harness", "claude-code", "--decision", decision_path, "--root", root, "--revision", revision,
                            "--evidence", workers, "--relay", verify[verify.index("--relay") + 1], "--slug", "repro",
                            "--fixed", "A1", "--cases", cases_path)["scope"]
        self.assertEqual([item["part_id"] for item in shown["judged"]], ["A1", "A2"], "A2 links to the fixed A1")
        output = self.run_ok(*verify, "--fixed", "A1")
        self.assertEqual(output["parts"][0]["harness_result"], "verified")

        # A stricter description limit, adopted from drift: building and checking both apply it.
        self.run_ok("drift", "--root", root, "--slug", "repro", "--finding", "F2", "--rule",
                    "skills.description.length", "--category", "compatibility", "--summary", "A lower limit.")
        returned, _, _ = cli(*verify)
        self.assertEqual(returned, 2, "recorded drift changes the contract")
        verify[verify.index("--relay") + 1] = cli.confirm(decision_path, "verify-drift", "--revision", revision)
        output = self.run_ok(*verify, code=1)
        self.assertEqual(output["parts"][0]["status"], "blocked", "open compatibility drift blocks the skill")
        pinned = next(rule for rule in yaml.safe_load((FORGE / "snapshot/rules/skills.yaml").read_text())
                      if rule["id"] == "skills.description.length")
        request = cli.write("override.json", {"rule_id": "skills.description.length", "finding_id": "F2",
                                              "reason": "A lower limit.", "rule": {**pinned, "params": {"max_chars": 40}}})
        self.run_ok("override", "--root", root, "--input", request, "--write")
        self.run_ok("drift-decision", "--decision", decision_path, "--root", root, "--rule",
                    "skills.description.length", "--finding", "F2", "--choice", "adopt", "--reason", "A lower limit.")
        returned, _, stderr = cli(*verify)
        self.assertEqual(returned, 2, "the override and the decision changed the contract")
        relay = cli.confirm(decision_path, "verify-2", "--revision", revision)
        build[build.index("--relay") + 1] = relay
        returned, _, stderr = cli(*build, "--replace", f".agents/skills/{NAME}/SKILL.md")
        self.assertEqual(returned, 2)
        self.assertIn("description", stderr, "the builder applies the adopted limit")
        verify[verify.index("--relay") + 1] = relay
        output = self.run_ok(*verify, code=1)
        self.assertEqual(output["parts"][0]["harness_result"], "not verified", "checking applies the same limit")
        report = (root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        self.assertIn("| `skills.description.length` | error | script | fail |", report)

        # A selection override changes the recommendation, so the recorded one is stale until select runs again.
        rule_id = next(row["rules"][0] for row in checklist["assessment"] if row["state"] == "recommended")
        request = cli.write("selection.json", {"rule_id": rule_id, "finding_id": "F2",
                                               "reason": "The preference no longer holds.", "retired": True})
        self.run_ok("override", "--root", root, "--input", request, "--write")
        changed = self.select(dict(ANSWERS), root=True)
        self.assertEqual(changed["mechanism"], "skills", "unconfirmed, the pinned rules decide")
        self.assertNotEqual(changed["override_preview"]["mechanism"], "skills")
        relay = cli.confirm(decision_path, "build-3", "--revision", revision)
        build[build.index("--relay") + 1] = relay
        returned, _, stderr = cli(*build)
        self.assertEqual(returned, 2)
        self.assertIn("is not what its answers give now", stderr)


if __name__ == "__main__":
    unittest.main()
