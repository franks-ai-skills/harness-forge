"""The input shapes the skills' references show are valid for the commands that read them."""

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.contracts import load_decision, render_contract
from harness_forge.documents import validate
from harness_forge.models import Rule
from harness_forge.research import accept_requirements

FORGE = Path(__file__).resolve().parents[1]
INTAKE = FORGE / "skills" / "harness-intake" / "references" / "inputs.md"
RESEARCH = FORGE / "skills" / "harness-research" / "references" / "inputs.md"


def blocks(path: Path) -> list[tuple[str, str]]:
    """Each fenced block of ``path`` as (language, text)."""
    return re.findall(r"^```(\w+)\n(.*?)^```$", path.read_text(encoding="utf-8"), flags=re.S | re.M)


def forge_cli():
    specification = importlib.util.spec_from_file_location("forge_cli", FORGE / "scripts" / "forge.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class IntakeInputsTest(unittest.TestCase):
    def setUp(self):
        self.blocks = blocks(INTAKE)
        self.assertEqual([language for language, _ in self.blocks], ["markdown", "yaml", "json"])
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def record(self):
        path = self.root / "2026-10-21-repro.md"
        path.write_text(self.blocks[0][1], encoding="utf-8")
        return load_decision(path)

    def test_the_survey_record_loads_and_renders(self):
        record = self.record()
        self.assertEqual(record.record["parts"], [])
        self.assertIn("Reuse candidates to research", render_contract(record, None, []).text)

    def test_the_part_entry_completes_a_valid_record(self):
        record = self.record().record
        record.update(yaml.safe_load(self.blocks[1][1]))
        validate(record, "decision")

    def test_the_select_input_is_accepted(self):
        path = self.root / "select.json"
        path.write_text(self.blocks[2][1], encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = forge_cli().main(["select", "--input", str(path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIsNotNone(json.loads(output.getvalue())["question"], "partial answers name the next question")


class ResearchInputsTest(unittest.TestCase):
    def test_proposed_requirements_and_choices_are_accepted(self):
        (_, proposed), (_, choices) = blocks(RESEARCH)
        rules = [Rule.from_dict(rule) for rule in json.loads(proposed)["requirements"]]
        accepted = accept_requirements(rules, json.loads(choices), "0" * 64, part_id="A")
        self.assertEqual(accepted.rule_ids, ["idea.repro.a.environment"])


if __name__ == "__main__":
    unittest.main()
