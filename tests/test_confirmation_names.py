"""What the user confirms is named by its purpose, never "contract" (#85); identifiers and preserved text stay."""

import ast
import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from harness_forge.contracts import render_contract, write_decision
from harness_forge.research import write_revision
from tests.builders import EVIDENCE, decision, part

FORGE = Path(__file__).resolve().parents[1]
# Identifiers that keep the word: the command, stored fields and the reference files' names.
IDENTIFIERS = re.compile(r"prepare-contract|contract_digest|references/contract\.md|contract\.md")
TITLES = {"research": "Research proposal", "configuration": "Configuration proposal",
          "verification": "Configuration summary"}


def mentions(value: str) -> bool:
    return bool(re.search(r"contract", IDENTIFIERS.sub("", value), re.I))


def load_forge():
    specification = importlib.util.spec_from_file_location("forge_names", FORGE / "scripts" / "forge.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class PurposeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-names-")))
        self.forge = load_forge()
        self.record = decision(part("A", "Write a reproduction checklist when asked"),
                               idea="Keep the contract between bug reports and checklists")
        self.decision = write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        self.evidence = self.root / "workers.json"
        self.evidence.write_text(json.dumps({"worker_evidence": EVIDENCE}), encoding="utf-8")

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.forge.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def prepare(self, purpose, *extra):
        code, allocated, stderr = self.cli("run-dir", "--root", self.root)
        self.assertEqual(code, 0, stderr)
        code, output, stderr = self.cli("prepare-contract", "--decision", self.decision, "--evidence", self.evidence,
                                        "--run-dir", allocated["path"], "--purpose", purpose, *extra)
        self.assertEqual(code, 0, stderr)
        return output

    def test_each_purpose_names_the_summary_the_details_and_the_file(self):
        digests, paths = set(), set()
        for purpose, title in TITLES.items():
            with self.subTest(purpose=purpose):
                output = self.prepare(purpose)
                self.assertEqual((output["purpose"], output["title"]), (purpose, title))
                self.assertTrue(output["summary"].startswith(f"## {title}\n"), "the title names only the purpose")
                path = Path(output["details"]["path"])
                self.assertTrue(path.name.startswith(title.lower().replace(" ", "-") + "-"))
                details = path.read_text(encoding="utf-8")
                self.assertTrue(details.startswith(f"# {title} details: "))
                self.assertIn("Confirmation digest: " + output["digest"], details)
                self.assertFalse(mentions(output["summary"].replace("Keep the contract", "")),
                                 "Forge's own summary text says no contract")
                digests.add(output["digest"])
                paths.add(path.name)
        self.assertEqual(len(digests), 1, "the purpose is presentation only")
        self.assertEqual(len(paths), 3)
        self.assertEqual(digests.pop(), render_contract(self.record, None, [], worker_evidence=EVIDENCE).digest)

    def test_a_record_with_accepted_requirements_prepared_for_research_is_a_research_proposal(self):
        from tests.test_build_skill import REVISION, reference_decision
        self.decision = write_decision(self.root, reference_decision(), slug="repro", date="2026-10-10")
        rules = write_revision(self.root, "repro", REVISION)
        output = self.prepare("research", "--revision", rules)
        self.assertTrue(output["summary"].startswith("## Research proposal\n"))
        self.assertIn("- **Accepted requirements:**\n  - ", Path(output["details"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(self.prepare("configuration", "--revision", rules)["digest"], output["digest"])

    def test_a_purpose_is_required(self):
        code, _, stderr = self.cli("prepare-contract", "--decision", self.decision, "--run-dir", self.root)
        self.assertEqual(code, 2)
        self.assertIn("--purpose", stderr)


class ForgeTextTest(unittest.TestCase):
    """Forge-authored text: every string literal (docstrings aside), CLI help, skills and README."""

    def test_no_string_literal_says_contract(self):
        found = []
        for path in [FORGE / "scripts/forge.py", *sorted((FORGE / "src/harness_forge").rglob("*.py"))]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}
            found += [f"{path.relative_to(FORGE)}:{node.lineno}: {node.value[:80]!r}" for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and id(node) not in docstrings and node.value != "contract" and mentions(node.value)]
        self.assertEqual(found, [])

    def test_no_command_help_says_contract(self):
        forge = load_forge()
        parser = forge.build_parser()
        commands = next(action for action in parser._actions if action.dest == "command").choices
        for name, command in commands.items():
            with self.subTest(command=name):
                self.assertFalse(mentions(command.format_help()), command.format_help())

    def test_no_guidance_or_readme_prose_says_contract(self):
        paths = [*(FORGE / "skills").glob("*/SKILL.md"), *(FORGE / "skills").glob("*/references/*.md"),
                 FORGE / "README.md"]
        for path in paths:
            with self.subTest(path=str(path.relative_to(FORGE))):
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if mentions(line)]
                self.assertEqual(lines, [])
        intake = " ".join((FORGE / "skills/harness-intake/references/contract.md").read_text(encoding="utf-8")
                          .split())
        self.assertIn("`prepare-contract` is an internal command name: it prepares the research proposal, "
                      "configuration proposal or configuration summary for the current step.", intake)


class HistoryTest(unittest.TestCase):
    def test_a_report_with_the_historical_label_still_reads(self):
        from harness_forge.scope import earlier_run
        stored = (FORGE / ".harness/reports/forge-self-verify.md").read_text(encoding="utf-8")
        self.assertEqual(stored.count("- Confirmed configuration digest: "), 1)
        # A report recorded before #85, such as Forge 0.3.0's, carries the historical label.
        historical = stored.replace("- Confirmed configuration digest: ", "- Contract digest: ")
        self.assertEqual(earlier_run(historical), earlier_run(stored))
        self.assertIsNotNone(earlier_run(historical))


if __name__ == "__main__":
    unittest.main()
