"""Rules, catalog parameters and KB-level consistency checks."""

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness_forge.documents import load_document, validate_kb

MINIMAL_KB = Path(__file__).parent / "fixtures" / "minimal-kb"
FORGE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "forge.py"


def run_cli(*argv: str) -> tuple[int, dict]:
    specification = importlib.util.spec_from_file_location("forge_cli", FORGE_SCRIPT)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = module.main(list(argv))
    output = stdout.getvalue()
    return code, (json.loads(output) if output else {})


class KbCopy:
    """A writable copy of the minimal KB for one mutation."""

    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory(prefix="forge-kb-")
        root = Path(self._directory.name) / "kb"
        shutil.copytree(MINIMAL_KB, root)
        return root

    def __exit__(self, *exc):
        self._directory.cleanup()


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


class ClosedRulesTest(unittest.TestCase):
    def test_closed_rules_and_catalog_parameters(self):
        self.assertEqual(validate_kb(MINIMAL_KB), [])
        rules = load_document(MINIMAL_KB / "rules" / "skills.yaml", "rules")
        self.assertEqual(
            [rule["id"] for rule in rules],
            ["skills.description.no-workflow-steps", "skills.description.length"],
        )

    def test_unknown_rule_field_is_rejected(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "  contested: false\n", "  contested: false\n  run: rm -rf /\n")
            with self.assertRaises(ValueError):
                load_document(root / "rules/skills.yaml", "rules")
            self.assertTrue(validate_kb(root))

    def test_script_path_as_checker_is_rejected(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "checker: skills.description-length", "checker: ./checks/length.sh")
            with self.assertRaises(ValueError):
                load_document(root / "rules/skills.yaml", "rules")

    def test_unknown_checker_is_reported(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "checker: skills.description-length", "checker: skills.run-anything")
            errors = validate_kb(root)
            self.assertTrue(any("skills.run-anything" in error for error in errors), errors)

    def test_invalid_checker_params_are_reported(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "params: {max_chars: 1024}", "params: {max_chars: \"1024\"}")
            errors = validate_kb(root)
            self.assertTrue(any("skills.description.length" in error and "params" in error for error in errors), errors)

    def test_duplicate_rule_ids_are_reported(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "id: skills.description.length", "id: skills.description.no-workflow-steps")
            errors = validate_kb(root)
            self.assertTrue(any("duplicate rule id" in error for error in errors), errors)

    def test_executable_yaml_tags_are_rejected(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "statement: The description is present", "statement: !!python/object/apply:os.getcwd []\n  note: The description is present")
            with self.assertRaises(ValueError):
                load_document(root / "rules/skills.yaml", "rules")

    def test_contested_rule_cannot_be_an_error(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "  evidence: Vendor\n  sources: [docs/vendors.md]\n  contested: false",
                       "  evidence: Vendor\n  sources: [docs/vendors.md]\n  contested: true\n  positions:\n"
                       "    - {position: Keep it short, sources: [docs/vendors.md]}\n"
                       "    - {position: Length does not matter, sources: [docs/vendors.md]}")
            with self.assertRaises(ValueError):
                load_document(root / "rules/skills.yaml", "rules")

    def test_practitioner_error_needs_a_recorded_rationale(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "  severity: warning\n  check: judged", "  severity: error\n  check: judged")
            errors = validate_kb(root)
            self.assertTrue(any("severity" in error and "no-workflow-steps" in error for error in errors), errors)
            replace_in(root / "rules/skills.yaml", "  check: judged",
                       "  check: judged\n  severity_rationale: Listed steps make the agent skip the body in two vendor reports.")
            self.assertEqual(validate_kb(root), [])

    def test_judged_rule_cannot_name_a_checker(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "  check: judged\n", "  check: judged\n  checker: skills.description-length\n")
            with self.assertRaises(ValueError):
                load_document(root / "rules/skills.yaml", "rules")

    def test_citations_must_exist_in_the_snapshot(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "docs/guide/skills.md#practices", "docs/guide/skills.md#missing-section")
            self.assertTrue(any("missing-section" in error for error in validate_kb(root)))
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "sources: [docs/vendors.md]", "sources: [docs/absent.md]")
            self.assertTrue(any("docs/absent.md" in error for error in validate_kb(root)))
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "sources: [docs/vendors.md]", "sources: [../outside.md]")
            self.assertTrue(validate_kb(root))

    def test_a_params_schema_with_a_non_string_key_is_an_error_not_a_crash(self):
        with KbCopy() as root:
            replace_in(root / "checks/catalog.yaml", "max_chars: {type: integer, minimum: 1}", "7: {type: integer}")
            errors = validate_kb(root)
        self.assertTrue(any("invalid params schema: schema object keys must be strings" in e for e in errors), errors)

    def test_selection_predicates_use_declared_facts(self):
        with KbCopy() as root:
            replace_in(root / "rules/selection.yaml", "{eq: [trigger, user]}", "{eq: [triggered_by, user]}")
            self.assertTrue(any("triggered_by" in error for error in validate_kb(root)))
        with KbCopy() as root:
            replace_in(root / "rules/selection.yaml", "{eq: [trigger, user]}", "{eq: [trigger, robot]}")
            self.assertTrue(any("robot" in error for error in validate_kb(root)))
        with KbCopy() as root:
            replace_in(root / "rules/selection.yaml", "{eq: [trigger, user]}", "{matches: [trigger, \"u.*\"]}")
            self.assertTrue(validate_kb(root))

    def test_control_prerequisites_must_be_existing_rules(self):
        with KbCopy() as root:
            catalog = root / "checks" / "catalog.yaml"
            replace_in(catalog, "            concept: {type: string, pattern: \"^[a-z][a-z0-9-]*$\"}\n",
                       "            concept: {type: string, pattern: \"^[a-z][a-z0-9-]*$\"}\n"
                       "            control:\n              type: object\n              additionalProperties: true\n")
            replace_in(root / "rules/selection.yaml", "implies: {outcome: harness-only, concept: skills}",
                       "implies: {control: {id: gate, family: merge rules, prerequisites: [outside.absent.rule], required_for_goal: true}}")
            errors = validate_kb(root)
            self.assertTrue(any("outside.absent.rule" in error for error in errors), errors)

    def test_preference_rules_must_be_in_the_selection_policy(self):
        with KbCopy() as root:
            (root / "policy" / "selection.md").write_text("# Selection policy\n", encoding="utf-8")
            self.assertTrue(any("selection.user-trigger.skill" in e and "policy/selection.md" in e for e in validate_kb(root)))
        with KbCopy() as root:
            (root / "policy" / "selection.md").unlink()
            self.assertTrue(any("policy/selection.md" in e for e in validate_kb(root)))

    def test_coverage_must_reference_existing_rules(self):
        with KbCopy() as root:
            replace_in(root / "rules/coverage.yaml", "rules: [skills.description.length]", "rules: [skills.description.gone]")
            self.assertTrue(any("skills.description.gone" in error for error in validate_kb(root)))


class ValidateKbCliTest(unittest.TestCase):
    def test_valid_kb_exits_zero_with_bounded_json(self):
        code, output = run_cli("validate-kb", "--kb", str(MINIMAL_KB))
        self.assertEqual(code, 0)
        self.assertEqual(output, {"command": "validate-kb", "ok": True, "error_count": 0, "errors": []})

    def test_kb_root_given_with_parent_segments_is_accepted(self):
        relative = MINIMAL_KB / ".." / "minimal-kb"
        code, output = run_cli("validate-kb", "--kb", str(relative))
        self.assertEqual(code, 0, output)

    def test_invalid_kb_exits_one(self):
        with KbCopy() as root:
            replace_in(root / "rules/skills.yaml", "checker: skills.description-length", "checker: skills.run-anything")
            code, output = run_cli("validate-kb", "--kb", str(root))
        self.assertEqual(code, 1)
        self.assertFalse(output["ok"])
        self.assertGreaterEqual(output["error_count"], 1)

    def test_missing_kb_is_invalid_input(self):
        code, _ = run_cli("validate-kb", "--kb", str(MINIMAL_KB / "absent"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
