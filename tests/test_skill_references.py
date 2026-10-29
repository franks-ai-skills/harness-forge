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

from harness_forge.acceptance import annotate
from harness_forge.contracts import load_decision, render_contract
from harness_forge.documents import validate
from harness_forge.models import Rule
from harness_forge.research import accept_requirements, compare_requirements

FORGE = Path(__file__).resolve().parents[1]
INTAKE = FORGE / "skills" / "harness-intake" / "references" / "inputs.md"
RESEARCH = FORGE / "skills" / "harness-research" / "references" / "inputs.md"
RESEARCH_SKILL = FORGE / "skills" / "harness-research" / "SKILL.md"


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
        decision = self.record()
        decision.record.update(yaml.safe_load(self.blocks[1][1]))
        validate(decision.record, "decision")
        # The survey candidate to investigate is the one research_candidates names.
        self.assertIn("repro-steps from https://github.com/example-org/skills/tree/main/skills/repro-steps: "
                      "investigate for reuse", render_contract(decision, None, []).text)

    def test_the_select_input_is_accepted(self):
        path = self.root / "select.json"
        path.write_text(self.blocks[2][1], encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = forge_cli().main(["select", "--input", str(path)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIsNotNone(json.loads(output.getvalue())["question"], "partial answers name the next question")


class DroppedFindingsTest(unittest.TestCase):
    def test_research_names_dropped_findings_without_their_content(self):
        """#150: a dropped finding is named by id and reason; its claim, quote and source text are never shown."""
        text = " ".join(RESEARCH_SKILL.read_text(encoding="utf-8").split())
        self.assertIn("Name each dropped finding by its id with Forge's reason only; never show its claim, quote or "
                      "source text.", text)
        self.assertNotIn("Show the dropped ones and why", text)
        self.assertIn("Show only the findings `scripts/forge.py research` keeps", text)


class ResearchInputsTest(unittest.TestCase):
    def test_proposed_requirements_and_choices_are_accepted(self):
        (_, proposed), (_, choices), _, _ = blocks(RESEARCH)
        rules = [Rule.from_dict(rule) for rule in json.loads(proposed)["requirements"]]
        accepted = accept_requirements(rules, json.loads(choices), "0" * 64, part_id="A")
        self.assertEqual(accepted.rule_ids, ["idea.repro.a.environment"])

    def test_the_comparison_output_shows_the_commands_groups_and_fields(self):
        (_, proposed), _, (_, shown), _ = blocks(RESEARCH)
        shown = json.loads(shown)
        rules = [Rule.from_dict(rule) for rule in json.loads(proposed)["requirements"]]
        previous = accept_requirements(rules, {rule.id: "accept" for rule in rules}, "0" * 64, part_id="A")
        statement = {**rules[1].to_dict(), "statement": "The checklist presents a guess as an observation"}
        source = rules[0].sources[0]
        groups = annotate(compare_requirements(previous, [rules[0], Rule.from_dict(statement)], "A",
                                               [{"id": "F3", "source": source, "claim": "c", "quote": "q"}]))
        self.assertEqual(set(shown) - {"command", "part_id", "brief_digest", "untrusted", "compact"}, set(groups))
        self.assertEqual(set(shown["group"]), set(groups["group"]))
        self.assertEqual(set(shown["unchanged"][0]), set(groups["unchanged"][0]))
        self.assertEqual(set(shown["changed"][0]), set(groups["changed"][0]))
        self.assertEqual(set(shown["changed"][0]["differences"]), set(groups["changed"][0]["differences"]))


    def test_the_acceptance_relay_has_the_fields_accept_checks(self):
        from harness_forge.acceptance import _KEYS
        *_, (_, relay) = blocks(RESEARCH)
        relay = json.loads(relay)
        self.assertEqual(set(relay), {"run_id", "part_id", "decisions"})
        for decision in relay["decisions"]:
            self.assertEqual(set(decision), _KEYS[decision["choice"]])
        self.assertEqual({decision["choice"] for decision in relay["decisions"]}, set(_KEYS))


if __name__ == "__main__":
    unittest.main()
