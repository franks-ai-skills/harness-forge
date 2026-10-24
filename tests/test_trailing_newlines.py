"""A token with a trailing line break is refused on every path that checks a whole token (#51).

Python's ``$`` also matches before a final newline. Schema patterns are
compiled with fastjsonschema, which anchors them at the real end; Forge's
own whole-token checks use ``fullmatch``. No schema uses
``patternProperties``, whose keys fastjsonschema compiles as written.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.access import command_problems
from harness_forge.checks.runner import run_check
from harness_forge.documents import schema_named, validate
from harness_forge.runtime import _skills_read
from tests.builders import decision, part
from tests.test_build_skill import NAME, plan, reference_decision
from tests.test_checks import portable_facts, skill, snapshot_rule

ENDINGS = ("\n", "\r\n")


def record() -> dict:
    value = decision(part("A", "Goal A")).record
    value["research"]["deep_digests"] = {"A": "a" * 64}
    return value


class SchemaPathTest(unittest.TestCase):
    def test_the_valid_record_passes(self):
        validate(record(), "decision")

    def test_tokens_with_a_trailing_line_break_are_refused(self):
        def mutations(ending):
            yield "part id", lambda r: r["parts"][0].update(id="A" + ending)
            yield "part slug", lambda r: r["parts"][0].update(slug=r["parts"][0]["slug"] + ending)
            yield "fact name (propertyNames)", lambda r: r["facts"].update({"reach" + ending: r["reach"]})
            yield "deep digest part (propertyNames)", lambda r: r["research"]["deep_digests"].update(
                {"A" + ending: "b" * 64})
            yield "digest", lambda r: r["research"].update(survey_digest="c" * 64 + ending)
        for ending in ENDINGS:
            for label, mutate in mutations(ending):
                with self.subTest(label, ending=repr(ending)):
                    value = copy.deepcopy(record())
                    mutate(value)
                    with self.assertRaises(ValueError):
                        validate(value, "decision")

    def test_a_rule_id_with_a_trailing_line_break_is_refused(self):
        rule = json.loads(json.dumps({"id": "skills.example-rule", "concept": "skills", "kind": "must",
                                      "severity": "warning", "check": "judged", "statement": "Example.",
                                      "applies_to": {"harnesses": ["claude-code"]}, "evidence": "Vendor",
                                      "sources": ["docs/guide/skills.md"], "contested": False}))
        validate(rule, "rule")
        for ending in ENDINGS:
            with self.subTest(ending=repr(ending)), self.assertRaises(ValueError):
                validate({**rule, "id": "skills.example-rule" + ending}, "rule")

    def test_no_schema_uses_pattern_properties(self):
        for name in ("decision", "report", "research", "revision", "rule", "rules", "selection", "catalog"):
            self.assertNotIn("patternProperties", json.dumps(schema_named(name)), name)


class PythonPathTest(unittest.TestCase):
    def test_the_name_checker_refuses_a_trailing_line_break(self):
        rule = snapshot_rule("skills.name.format")
        good = run_check(rule, skill("name: repro-checklist\ndescription: Does a thing.\n"), portable_facts())
        self.assertEqual(good.status, "pass", good.reason)
        for ending in ENDINGS:
            escaped = ending.replace("\r", "\\r").replace("\n", "\\n")
            files = skill(f'name: "repro-checklist{escaped}"\ndescription: Does a thing.\n')
            with self.subTest(ending=repr(ending)):
                self.assertEqual(run_check(rule, files, portable_facts()).status, "fail")

    def test_the_builder_refuses_a_slug_with_a_trailing_line_break(self):
        plan(reference_decision())
        for ending in ENDINGS:
            record = reference_decision()
            record.record["parts"][0]["slug"] = NAME + ending
            # The decision schema refuses it before the builder's own name check would.
            with self.subTest(ending=repr(ending)), \
                    self.assertRaisesRegex(ValueError, "slug: must match pattern|not a valid skill name"):
                plan(record)

    def test_reviewer_commands_refuse_a_number_or_sed_script_with_a_line_break(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "f.txt").write_text("x\n", encoding="utf-8")
            self.assertEqual(command_problems("head -n 5 f.txt", workspace), [])
            self.assertEqual(command_problems("sed -n '1,5p' f.txt", workspace), [])
            for ending in ENDINGS:
                with self.subTest(ending=repr(ending)):
                    self.assertTrue(command_problems(f"head -n '5{ending}' f.txt", workspace))
                    self.assertTrue(command_problems(f"sed -n '1,5p{ending}' f.txt", workspace))

    def test_a_skill_path_with_a_line_break_is_no_skill_read(self):
        item = {"status": "completed", "exit_code": 0, "aggregated_output": "name: probe\n"}
        self.assertEqual(_skills_read({**item, "command": "cat .agents/skills/probe/SKILL.md"}),
                         [("probe", ".agents/skills/probe/SKILL.md")])
        for ending in ENDINGS:
            with self.subTest(ending=repr(ending)):
                self.assertEqual(_skills_read({**item, "command": f"cat '.agents/skills/probe/SKILL.md{ending}'"}), [])


if __name__ == "__main__":
    unittest.main()
