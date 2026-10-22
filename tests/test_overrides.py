"""Repository rule overrides: validation, warnings, scope and the rules they produce."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.documents import canonical_digest, load_document
from harness_forge.models import Confirmation, Rule
from harness_forge.overrides import (
    OverridesPending, apply_overrides, check_override, describe, effective_rules, load_overrides, write_override,
)
from harness_forge.research import _catalog
from tests.test_research import finding

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
PINNED = [Rule.from_dict(rule) for name in ("skills", "security", "outside-controls")
          for rule in load_document(SNAPSHOT / "rules" / f"{name}.yaml", "rules")]
PINNED += [Rule.from_dict(rule) for rule in load_document(SNAPSHOT / "rules" / "selection.yaml", "selection")["rules"]]
KB_VERSION = json.loads((SNAPSHOT / "kb-manifest.json").read_text(encoding="utf-8"))["kb_version"]
FINDING = finding(source="https://docs.example-vendor.com/skills/limits", quote="Descriptions may be 2048 characters.",
                  context="Limits. Descriptions may be 2048 characters. Names stay short.").to_dict()
RESEARCH = [{"slug": "repro", "search_mode": "live", "briefs": [], "findings": [FINDING],
             "support": [{"finding_id": "F1", "quote_check": "pass", "verdict": "supported", "worker_ref": "support-f1",
                          "finding_digest": canonical_digest(FINDING)}],
             "drift": []}]
LENGTH = next(rule for rule in PINNED if rule.id == "skills.description.length")
RAISED = {**LENGTH.to_dict(), "params": {"max_chars": 2048},
          "statement": "The description is present and at most 2,048 characters"}


def override(rule=RAISED, **changes):
    """An override of skills.description.length adopted from FINDING; ``rule=None`` retires the rule."""
    value = {"rule_id": "skills.description.length", "pinned_kb_version": KB_VERSION,
             "drift": {"finding": FINDING, "support_verdict": "supported"},
             "reason": "The vendor raised the limit.", "date": "2026-10-12", "link": "pending"}
    value.update({"rule": copy.deepcopy(rule)} if rule is not None else {"retired": True})
    value.update(changes)
    return value


def check(value, research=RESEARCH, pinned=PINNED, kb_version=KB_VERSION):
    return check_override(value, pinned, kb_version=kb_version, catalog=_catalog(), research=research)


class ValidationTest(unittest.TestCase):
    def test_a_supported_correction_is_valid_and_warns_when_it_loosens_an_error_rule(self):
        checked = check(override())
        self.assertEqual((checked["status"], checked["problems"]), ("valid", []))
        self.assertEqual([change["field"] for change in checked["changes"]], ["params", "statement"])
        self.assertEqual(checked["warnings"], [
            "changes its parameters for the error rule skills.description.length, which may loosen it",
            "changes what it requires for the error rule skills.description.length, which may loosen it"])
        lines = describe(override(), checked)
        self.assertIn("  Pinned: error: The description is present and at most 1,024 characters, the limit of Codex, "
                      "OpenCode and the specification; params {'max_chars': 1024}", lines)
        self.assertIn("  New: error: The description is present and at most 2,048 characters; "
                      "params {'max_chars': 2048}", lines)
        self.assertIn('  Source (fetched, untrusted): https://docs.example-vendor.com/skills/limits: '
                      '"Descriptions may be 2048 characters."',
                      lines)
        self.assertIn("  Warning: it changes its parameters for the error rule skills.description.length, which may "
                      "loosen it", lines)

    def test_retiring_or_downgrading_says_so_explicitly(self):
        self.assertEqual(check(override(rule=None))["warnings"],
                         ["retires the error rule skills.description.length: nothing checks it in this repository "
                          "any more"])
        self.assertEqual(check(override(rule={**LENGTH.to_dict(), "severity": "warning"}))["warnings"],
                         ["lowers the error rule skills.description.length from error to warning"])
        retire_exclusion = override(rule=None, rule_id="selection.eligibility.own-context-skills")
        self.assertIn("eligibility rule, which makes a chosen mechanism a goal conflict",
                      check(retire_exclusion)["warnings"][0])

    def test_retiring_a_security_warning_rule_warns_as_a_security_rule(self):
        """A security rule warns although its severity is only a warning, unlike an ordinary warning rule."""
        self.assertEqual(check(override(rule=None, rule_id="security.untrusted-input.as-data"))["warnings"],
                         ["retires the security rule security.untrusted-input.as-data: nothing checks it in this "
                          "repository any more"])

    def test_forged_or_overreaching_overrides_are_invalid(self):
        unsupported = copy.deepcopy(RESEARCH)
        unsupported[0]["support"][0]["verdict"] = "unsupported"
        failed_quote = copy.deepcopy(RESEARCH)
        failed_quote[0]["support"][0]["quote_check"] = "fail"
        forged = {**FINDING, "quote": "Descriptions may be any length."}
        cases = {
            "unsupported verdict": (override(), unsupported, "is not a finding this repository's research kept"),
            "failed quote check": (override(), failed_quote, "with a passed quote check"),
            "forged finding": (override(drift={"finding": forged, "support_verdict": "supported"}), RESEARCH,
                               "is not a finding this repository's research kept"),
            "finding edited after its checks": (override(drift={"finding": forged, "support_verdict": "supported"}),
                                                [{**RESEARCH[0], "findings": [forged]}],
                                                "is not a finding this repository's research kept"),
            "unknown rule": (override(rule_id="skills.made-up"), RESEARCH, "is not a pinned Harness KB rule"),
            "checker": (override(rule={**LENGTH.to_dict(), "checker": "skills.name-format"}), RESEARCH,
                        "may not change checker"),
            "concept": (override(rule={**LENGTH.to_dict(), "concept": "security"}), RESEARCH, "may not change concept"),
            "params": (override(rule={**LENGTH.to_dict(), "params": {"max_chars": "2048"}}), RESEARCH, "invalid params"),
            "sources": (override(rule={**LENGTH.to_dict(), "sources": ["https://example.com/other"]}), RESEARCH,
                        "sources may only gain"),
            "no change": (override(rule=LENGTH.to_dict()), RESEARCH, "must change it"),
        }
        for name, (value, research, message) in cases.items():
            with self.subTest(case=name):
                checked = check(value, research)
                self.assertEqual(checked["status"], "invalid")
                self.assertTrue(any(message in problem for problem in checked["problems"]), checked["problems"])

    def test_a_selection_rule_changes_its_conditions_in_params_not_applies_to(self):
        preference = next(rule for rule in PINNED if rule.id == "selection.mechanism.procedure")
        changed = {**preference.to_dict(), "applies_to": {"harnesses": ["claude-code"],
                                                          "when": [{"eq": ["script_checkable", True]}]}}
        checked = check(override(rule=changed, rule_id=preference.id))
        self.assertEqual(checked["status"], "invalid")
        self.assertIn("selection does not evaluate applies_to", " ".join(checked["problems"]))

    def test_a_malformed_selection_predicate_is_a_problem_not_a_crash(self):
        preference = next(rule for rule in PINNED if rule.id == "selection.mechanism.procedure")
        malformed = {**preference.to_dict(), "params": {**preference.params, "when": {"all": ["not a predicate"]}}}
        checked = check_override(override(rule=malformed, rule_id=preference.id), PINNED, kb_version=KB_VERSION,
                                 catalog=_catalog(), research=RESEARCH, answerable={"job": ["procedure"]})
        self.assertEqual(checked["status"], "invalid")
        self.assertTrue(any("invalid params for selection.match" in problem for problem in checked["problems"]))

    def test_a_selection_rules_control_lists_only_pinned_rules(self):
        protection = next(rule for rule in PINNED if rule.id == "selection.control.configuration-protection")
        control = protection.params["implies"]["control"]
        typo = {**protection.to_dict(), "params": {**protection.params, "implies": {"control": {
            **control, "prerequisites": [*control["prerequisites"], "outside.gate.typo"]}}}}
        checked = check(override(rule=typo, rule_id=protection.id))
        self.assertEqual(checked["status"], "invalid")
        self.assertIn("the new rule's control lists outside.gate.typo, which is not a pinned rule", checked["problems"])
        other = check(override(rule={**RAISED, "checker": "skills.unknown", "params": {"implies": "bad"}}))
        self.assertIn("skills.unknown is not a catalog checker", other["problems"], "another checker's params stay data")

    def test_after_a_kb_update_it_proposes_removal_or_asks(self):
        differs = check(override(), kb_version="0.2.0")
        self.assertEqual((differs["status"], differs["kb_comparison"]), ("kb-updated", "differs"))
        self.assertIn("ask whether to keep the override", differs["problems"][0])
        updated = [Rule.from_dict(RAISED) if rule.id == LENGTH.id else rule for rule in PINNED]
        incorporated = check(override(), pinned=updated, kb_version="0.2.0")
        self.assertEqual(incorporated["kb_comparison"], "incorporated")
        self.assertIn("propose removing the override", incorporated["problems"][0])
        gone = check(override(), pinned=[rule for rule in PINNED if rule.id != LENGTH.id], kb_version="0.2.0")
        self.assertEqual(gone["kb_comparison"], "removed")


class EffectiveRulesTest(unittest.TestCase):
    def test_pinned_rules_apply_without_overrides(self):
        self.assertEqual(effective_rules(PINNED, [], None), PINNED)

    def test_overrides_apply_only_under_a_confirmation(self):
        with self.assertRaises(OverridesPending):
            effective_rules(PINNED, [override()], None)
        confirmation = Confirmation(run_id="run-1", contract_digest="0" * 64, part_ids=["A"])
        rules = effective_rules(PINNED, [override()], confirmation)
        self.assertEqual(next(rule for rule in rules if rule.id == LENGTH.id).params, {"max_chars": 2048})
        self.assertEqual(len(rules), len(PINNED))
        self.assertNotIn(LENGTH.id, [rule.id for rule in apply_overrides(PINNED, [override(rule=None)])])


def snapshot_digest() -> str:
    return hashlib.sha256(b"".join(path.read_bytes() for path in sorted(SNAPSHOT.rglob("*")) if path.is_file())).hexdigest()


class StorageTest(unittest.TestCase):
    def test_overrides_live_in_the_repository_that_holds_them(self):
        before = snapshot_digest()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            path = write_override(Path(first), override())
            self.assertEqual(path, Path(first).resolve() / ".harness/overrides/skills.description.length.yaml")
            self.assertEqual(load_overrides(Path(first)), [override()])
            self.assertEqual(load_overrides(Path(second)), [], "another repository's overrides never apply")
        self.assertEqual(snapshot_digest(), before, "an override never edits the pinned snapshot")

    def test_escaping_ids_links_and_misnamed_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            with self.assertRaises(ValueError):
                write_override(root, override(rule_id="../escape"))
            (root / ".harness").mkdir()
            (root / ".harness/overrides").symlink_to(outside)
            with self.assertRaises(ValueError):
                write_override(root, override())
            with self.assertRaises(ValueError):
                load_overrides(root)
            (root / ".harness/overrides").unlink()
            (root / ".harness/overrides").mkdir()
            (root / ".harness/overrides/security.other.yaml").write_text(yaml.safe_dump(override()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "named for its rule"):
                load_overrides(root)


if __name__ == "__main__":
    unittest.main()
