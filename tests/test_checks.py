"""Deterministic checks: fixtures for every script rule, limits and isolation."""

import tempfile
import unittest
from pathlib import Path

from harness_forge.checks import REGISTRY
from harness_forge.checks.runner import run_check
from harness_forge.documents import load_document, parse_yaml
from harness_forge.models import Rule

FORGE = Path(__file__).resolve().parents[1]
SNAPSHOT = FORGE / "snapshot"
SKILL = ".agents/skills/repro-checklist"
BOTH = {"targets": ["claude-code", "codex"], "target_set": "both", "part_id": "A", "skill_home": "repository"}


def snapshot_rules() -> list[Rule]:
    rules = []
    for path in sorted((SNAPSHOT / "rules").glob("*.yaml")):
        if path.name in ("coverage.yaml", "selection.yaml"):
            continue
        rules += [Rule.from_dict(rule) for rule in load_document(path, "rules")]
    return rules


def snapshot_rule(rule_id: str) -> Rule:
    return next(rule for rule in snapshot_rules() if rule.id == rule_id)


def load_case(path: Path) -> tuple[dict[str, bytes], dict]:
    case = parse_yaml(path.read_text(encoding="utf-8"))
    files = {name: text.encode("utf-8") for name, text in case["files"].items()}
    return files, case.get("facts", {})


def skill(frontmatter: str, body: str = "Body.\n", name: str = "repro-checklist") -> dict[str, bytes]:
    return {f".agents/skills/{name}/SKILL.md": f"---\n{frontmatter}---\n{body}".encode("utf-8")}


def portable_facts(name: str = "repro-checklist") -> dict:
    return {**BOTH, "symlinks": {f".claude/skills/{name}": f"../../.agents/skills/{name}"}}


class FixtureTest(unittest.TestCase):
    def test_every_script_rule_has_passing_good_and_failing_bad_fixtures(self):
        script_rules = [rule for rule in snapshot_rules() if rule.check == "script"]
        self.assertTrue(script_rules)
        for rule in script_rules:
            directory = SNAPSHOT / "fixtures" / rule.concept / rule.id
            with self.subTest(rule=rule.id):
                good_files, good_facts = load_case(directory / "good.yaml")
                bad_files, bad_facts = load_case(directory / "bad.yaml")
                good = run_check(rule, good_files, good_facts)
                bad = run_check(rule, bad_files, bad_facts)
                self.assertEqual(good.status, "pass", good.reason)
                self.assertEqual(bad.status, "fail", bad.reason)

    def test_catalog_checkers_are_implemented_at_their_versions(self):
        catalog = load_document(SNAPSHOT / "checks" / "catalog.yaml", "catalog")
        catalog_versions = {checker["id"]: checker["version"] for checker in catalog["checkers"]}
        implemented = {checker_id: version for checker_id, (version, _) in REGISTRY.items()}
        self.assertEqual(catalog_versions, implemented)


class BoundaryTest(unittest.TestCase):
    def test_description_length_boundary(self):
        rule = snapshot_rule("skills.description.length")
        at_limit = run_check(rule, skill(f"name: repro-checklist\ndescription: {'d' * 1024}\n"), BOTH)
        over = run_check(rule, skill(f"name: repro-checklist\ndescription: {'d' * 1025}\n"), BOTH)
        self.assertEqual(at_limit.status, "pass", at_limit.reason)
        self.assertEqual(over.status, "fail")

    def test_name_length_boundary(self):
        rule = snapshot_rule("skills.name.format")
        good_name, bad_name = "n" * 64, "n" * 65
        good = run_check(rule, skill(f"name: {good_name}\ndescription: d\n", name=good_name), BOTH)
        bad = run_check(rule, skill(f"name: {bad_name}\ndescription: d\n", name=bad_name), BOTH)
        self.assertEqual(good.status, "pass", good.reason)
        self.assertEqual(bad.status, "fail")

    def test_malformed_frontmatter_fails(self):
        rule = snapshot_rule("skills.frontmatter.valid")
        for files in (
            {f"{SKILL}/SKILL.md": b"no frontmatter\n"},
            {f"{SKILL}/SKILL.md": b"---\nname: [unclosed\n---\nBody\n"},
            skill("name: repro-checklist\ndescription: yes\n"),
            skill("name: repro-checklist\n"),
            {},
        ):
            with self.subTest(files=files):
                self.assertEqual(run_check(rule, files, BOTH).status, "fail")

    def test_mismatched_name_fails(self):
        rule = snapshot_rule("skills.name.format")
        result = run_check(rule, skill("name: other-name\ndescription: d\n"), BOTH)
        self.assertEqual(result.status, "fail")
        self.assertIn("other-name", result.reason)

    def test_broken_portable_symlink_fails(self):
        rule = snapshot_rule("skills.portable.layout")
        files = skill("name: repro-checklist\ndescription: d\n")
        for symlinks in ({}, {".claude/skills/repro-checklist": "/abs/.agents/skills/repro-checklist"},
                         {".claude/skills/repro-checklist": "../../.agents/skills/other"}):
            with self.subTest(symlinks=symlinks):
                self.assertEqual(run_check(rule, files, {**BOTH, "symlinks": symlinks}).status, "fail")
        self.assertEqual(run_check(rule, files, portable_facts()).status, "pass")

    def test_a_plugin_skill_needs_skills_name_under_a_root_with_the_codex_manifest(self):
        """Both harnesses load a plugin's skills/<name>/SKILL.md; Codex reads the plugin through plugin.json."""
        rule = snapshot_rule("skills.portable.plugin-layout")
        facts = {**BOTH, "skill_home": "plugin"}
        body = b"---\nname: repro-checklist\ndescription: d\n---\nBody.\n"
        manifest = b'{"name": "repro-tools"}\n'
        for label, files in (("at the root", {"skills/repro-checklist/SKILL.md": body, "plugin.json": manifest}),
                             ("in a marketplace", {"plugins/repro/skills/repro-checklist/SKILL.md": body,
                                                   "plugins/repro/.codex-plugin/plugin.json": manifest})):
            with self.subTest(label):
                self.assertEqual(run_check(rule, files, facts).status, "pass")
        for label, files in (("no Codex manifest", {"skills/repro-checklist/SKILL.md": body,
                                                    ".claude-plugin/plugin.json": manifest}),
                             ("a manifest that is not JSON", {"skills/repro-checklist/SKILL.md": body,
                                                              "plugin.json": b"name: repro-tools\n"}),
                             ("outside skills/", {"tools/repro-checklist/SKILL.md": body, "plugin.json": manifest})):
            with self.subTest(label):
                self.assertEqual(run_check(rule, files, facts).status, "fail")
        repository = {"skills/repro-checklist/SKILL.md": body, "plugin.json": manifest}
        self.assertEqual(run_check(rule, repository, BOTH).status, "not-applicable")
        self.assertEqual(run_check(snapshot_rule("skills.portable.layout"), repository, facts).status,
                         "not-applicable")
        unknown = {key: value for key, value in BOTH.items() if key != "skill_home"}
        self.assertEqual(run_check(rule, repository, unknown).status, "error", "an unknown home decides nothing")

    def test_dangling_and_escaping_references_fail(self):
        rule = snapshot_rule("skills.references.resolve")
        for link in ("references/missing.md", "../other-skill/SKILL.md", "/etc/passwd"):
            files = skill("name: repro-checklist\ndescription: d\n", f"Read [it]({link}) when needed.\n")
            with self.subTest(link=link):
                self.assertEqual(run_check(rule, files, BOTH).status, "fail")

    def test_rule_outside_its_targets_is_not_applicable(self):
        rule = snapshot_rule("skills.portable.layout")
        facts = {"targets": ["claude-code"], "target_set": "claude-code", "part_id": "A"}
        self.assertEqual(run_check(rule, {}, facts).status, "not-applicable")

    def test_unconfirmed_applicability_fact_is_an_error(self):
        rule = snapshot_rule("skills.portable.layout")
        result = run_check(rule, {}, {"targets": ["claude-code", "codex"], "part_id": "A"})
        self.assertEqual(result.status, "error")


class RunnerLimitsTest(unittest.TestCase):
    def double(self, checker: str) -> Rule:
        return Rule(
            id="test.double", concept="test", kind="must", severity="error", check="script",
            statement="test double", applies_to={"harnesses": ["claude-code"]}, evidence="Vendor",
            sources=["docs/x.md"], contested=False, checker=checker, params={},
        )

    def run_double(self, checker: str, **kwargs):
        return run_check(self.double(checker), {"a.txt": b"original"}, {"targets": ["claude-code"], "part_id": "A"},
                         registry="tests.doubles.checkers", extra_path=[str(FORGE)], **kwargs)

    def test_timeout_is_an_explicit_error(self):
        result = self.run_double("test.sleep", timeout=1.0)
        self.assertEqual(result.status, "error")
        self.assertIn("timed out", result.reason)

    def test_output_over_the_limit_is_an_explicit_error(self):
        result = self.run_double("test.flood")
        self.assertEqual(result.status, "error")
        self.assertIn("output", result.reason)

    def test_crash_and_invalid_status_are_errors_never_passes(self):
        for checker in ("test.crash", "test.bad-status"):
            with self.subTest(checker=checker):
                self.assertEqual(self.run_double(checker).status, "error")

    def test_network_is_disabled(self):
        self.assertEqual(self.run_double("test.network").status, "error")

    def test_input_bytes_stay_immutable(self):
        files = {"a.txt": b"original"}
        run_check(self.double("test.write-back"), files, {"targets": ["claude-code"], "part_id": "A"},
                  registry="tests.doubles.checkers", extra_path=[str(FORGE)])
        self.assertEqual(files, {"a.txt": b"original"})

    def test_unknown_checker_is_an_error(self):
        self.assertEqual(run_check(self.double("test.absent"), {}, {"targets": ["claude-code"], "part_id": "A"}).status, "error")

    def test_candidate_helper_is_never_executed(self):
        with tempfile.TemporaryDirectory(prefix="forge-marker-") as directory:
            marker = Path(directory) / "executed"
            helper = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n"
            files = {
                **skill("name: repro-checklist\ndescription: d\n", "Run `scripts/helper.py` first.\n"),
                f"{SKILL}/scripts/helper.py": helper.encode("utf-8"),
                f"{SKILL}/scripts/__init__.py": helper.encode("utf-8"),
                "conftest.py": helper.encode("utf-8"),
                "sitecustomize.py": helper.encode("utf-8"),
            }
            for rule in snapshot_rules():
                if rule.check == "script":
                    run_check(rule, files, portable_facts())
            self.assertFalse(marker.exists())




class VersionApplicabilityTest(unittest.TestCase):
    """A version range applies only to a harness version Forge knows."""

    def rule(self):
        from harness_forge.checks.runner import evaluate_applicability  # noqa: F401
        pinned = next(rule for rule in load_document(Path(__file__).resolve().parents[1] / "snapshot/rules/skills.yaml",
                                                     "rules") if rule["id"] == "skills.description.length")
        return Rule.from_dict({**pinned, "applies_to": {"harnesses": ["claude-code"],
                                                        "versions": {"claude-code": ">=999.0.0"}}})

    def test_an_unknown_version_is_unresolved_and_a_known_one_decides(self):
        from harness_forge.checks.runner import evaluate_applicability
        from harness_forge.predicates import UnknownFact
        facts = {"targets": ["claude-code"], "part_id": "A"}
        with self.assertRaisesRegex(UnknownFact, "harness_versions.claude-code"):
            evaluate_applicability(self.rule(), facts)
        applies, reason = evaluate_applicability(self.rule(), {**facts, "harness_versions": {"claude-code": "2.1.296"}})
        self.assertEqual((applies, reason), (False, "no target harness version is in the rule's range"))
        self.assertTrue(evaluate_applicability(self.rule(), {**facts, "harness_versions": {"claude-code": "999.1"}})[0])
        with self.assertRaisesRegex(UnknownFact, "harness_versions.claude-code"):
            evaluate_applicability(self.rule(), {**facts, "harness_versions": {"claude-code": "unknown"}})


if __name__ == "__main__":
    unittest.main()
