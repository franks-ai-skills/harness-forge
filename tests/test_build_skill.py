"""Portable skill builds from confirmed, accepted inputs."""

import os
import tempfile
import unittest
from pathlib import Path

from harness_forge.build_skill import (
    BuilderUnavailable,
    ConflictError,
    mark_built,
    plan_skill,
    unavailable_result,
    write_skill,
)
from harness_forge.checks.runner import run_check
from harness_forge.contracts import confirm_contract, render_contract
from harness_forge.documents import load_document
from harness_forge.models import Confirmation, Rule
from harness_forge.research import accept_requirements
from tests.builders import EVIDENCE, decision, part

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
NAME = "reproduction-checklist"
DESCRIPTION = ("Turns an approved bug report into a reproduction checklist with environment, numbered steps, "
               "expected and observed behavior. Use when asked for reproduction steps or a repro checklist. "
               "Not for running the reproducer or contacting external systems.")
PROCEDURE = ("1. Read the approved bug report the user names; if none is named, ask for it.\n"
             "2. Write the checklist: environment, numbered reproduction steps, expected behavior, observed behavior.\n"
             "3. Do not run the reproducer and do not contact external systems.\n")


def idea_rule(name, statement, **extra):
    value = {"id": f"idea.repro.a.{name}", "concept": "idea", "kind": "must", "severity": "error", "check": "judged",
             "statement": statement, "applies_to": {"harnesses": ["claude-code", "codex"]}, "evidence": "Vendor",
             "sources": ["https://docs.example-vendor.com/bug-reports"], "contested": False}
    value.update(extra)
    return Rule.from_dict(value)


PROPOSED = [
    idea_rule("environment", "The checklist names the environment"),
    idea_rule("numbered-steps", "The checklist numbers each reproduction step"),
    idea_rule("expected-observed", "The checklist states expected and observed behavior"),
    idea_rule("attach-logs", "The checklist uploads logs to the tracker"),
]
REVISION = accept_requirements(PROPOSED, {
    "idea.repro.a.environment": "accept", "idea.repro.a.numbered-steps": "accept",
    "idea.repro.a.expected-observed": "accept", "idea.repro.a.attach-logs": "reject",
}, "b" * 64)
PINNED = [Rule.from_dict(r) for r in load_document(SNAPSHOT / "rules" / "skills.yaml", "rules")]


def reference_decision(**part_changes):
    record = decision(part("A", "Write a reproduction checklist when asked", slug=NAME, **part_changes))
    record.record["accepted_revision"] = {"digest": REVISION.digest, "rule_ids": REVISION.rule_ids}
    return record


def confirmed(record, part_ids=("A",)):
    contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": list(part_ids), "run_id": "run-1"}
    return contract, confirm_contract(contract, relay, run_id="run-1")


def plan(record=None, **kwargs):
    record = record or reference_decision()
    contract, confirmation = confirmed(record)
    arguments = {"contract": contract, "confirmation": confirmation, "description": DESCRIPTION, "procedure": PROCEDURE}
    arguments.update(kwargs)
    return plan_skill(record, "A", REVISION, PINNED, **arguments)


class PlanTest(unittest.TestCase):
    def test_plan_contains_only_accepted_requirements(self):
        planned = plan()
        skill_md = planned[f".agents/skills/{NAME}/SKILL.md"]
        references = planned[f".agents/skills/{NAME}/references/requirements.md"]
        self.assertIn(f"name: {NAME}", skill_md)
        self.assertIn("Do not run the reproducer", skill_md)
        self.assertLess(skill_md.index("Always: the checklist numbers each reproduction step"), skill_md.index("## Procedure"))
        self.assertIn("The checklist numbers each reproduction step", references)
        self.assertIn("https://docs.example-vendor.com/bug-reports", references)
        self.assertNotIn("uploads logs", "".join(planned.values()))
        self.assertEqual(planned[f".claude/skills/{NAME}"], f"../../.agents/skills/{NAME}")

    def test_command_choice_sets_both_user_only_flags(self):
        planned = plan(reference_decision(recommended="commands", chosen="commands"))
        self.assertIn("disable-model-invocation: true", planned[f".agents/skills/{NAME}/SKILL.md"])
        self.assertIn("allow_implicit_invocation: false", planned[f".agents/skills/{NAME}/agents/openai.yaml"])

    def test_current_confirmation_and_matching_revision_are_required(self):
        record = reference_decision()
        contract, _ = confirmed(record)
        with self.assertRaises(ValueError):
            plan(confirmation=None)
        with self.assertRaises(ValueError):
            plan(confirmation=Confirmation(run_id="run-1", contract_digest=contract.digest, part_ids=["B"]))
        with self.assertRaises(ValueError):
            plan(confirmation=Confirmation(run_id="run-1", contract_digest="f" * 64, part_ids=["A"]))
        stale = reference_decision()
        stale.record["accepted_revision"]["digest"] = "c" * 64
        with self.assertRaises(ValueError):
            plan(stale)

    def test_non_skill_choice_is_builder_unavailable(self):
        record = reference_decision(recommended="hooks", chosen="hooks")
        with self.assertRaises(BuilderUnavailable):
            plan(record)
        result = unavailable_result(record, "A")
        self.assertEqual((result.status, result.harness_result, result.goal_result), ("blocked", "not applicable", "not assessed"))
        self.assertEqual(record.part("A")["chosen"], "hooks")
        self.assertEqual(record.part("A")["status"], "decided")

    def test_remote_reuse_stays_read_only_and_unbuilt(self):
        record = reference_decision(outcome="reuse", reuse={"name": "repro-steps", "source": "https://example.com/repro",
                                                            "license": "MIT", "maintenance": "maintained"})
        with self.assertRaises(BuilderUnavailable):
            plan(record)

    def test_placeholders_and_unsafe_text_are_rejected(self):
        with self.assertRaises(ValueError):
            plan(procedure="Use $ARGUMENTS as the issue number.\n")
        with self.assertRaises(ValueError):
            plan(description="")


class WriteTest(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory(prefix="forge-build-")
        self.root = Path(self._directory.name) / "repo"
        self.root.mkdir()

    def tearDown(self):
        self._directory.cleanup()

    def test_portable_skill_is_written_with_a_relative_symlink(self):
        write_skill(self.root, plan())
        canonical_path = self.root / ".agents" / "skills" / NAME
        claude_path = self.root / ".claude" / "skills" / NAME
        self.assertTrue((canonical_path / "SKILL.md").is_file())
        self.assertTrue(claude_path.is_symlink())
        self.assertEqual(os.readlink(claude_path), f"../../.agents/skills/{NAME}")
        self.assertEqual(claude_path.resolve(), canonical_path.resolve())

    def test_built_files_pass_the_static_skill_checks(self):
        write_skill(self.root, plan())
        files = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in (self.root / ".agents").rglob("*") if p.is_file()}
        facts = {"targets": ["claude-code", "codex"], "target_set": "both", "part_id": "A",
                 "symlinks": {f".claude/skills/{NAME}": os.readlink(self.root / ".claude" / "skills" / NAME)}}
        for rule in PINNED:
            if rule.check == "script":
                with self.subTest(rule=rule.id):
                    self.assertEqual(run_check(rule, files, facts).status, "pass")

    def test_matching_existing_skill_is_preserved(self):
        planned = plan()
        write_skill(self.root, planned)
        skill = self.root / ".agents" / "skills" / NAME / "SKILL.md"
        before = skill.stat().st_mtime_ns
        write_skill(self.root, planned)
        self.assertEqual(skill.stat().st_mtime_ns, before)

    def test_conflicting_content_is_not_overwritten(self):
        skill = self.root / ".agents" / "skills" / NAME / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: reproduction-checklist\ndescription: mine\n---\nHand-written.\n", encoding="utf-8")
        with self.assertRaises(ConflictError) as raised:
            write_skill(self.root, plan())
        self.assertIn("Hand-written.", raised.exception.diff)
        self.assertIn("Hand-written.", skill.read_text(encoding="utf-8"))
        write_skill(self.root, plan(), replace=[f".agents/skills/{NAME}/SKILL.md"])
        self.assertNotIn("Hand-written.", skill.read_text(encoding="utf-8"))

    def test_traversal_and_symlink_escapes_fail(self):
        for key in ("../outside/SKILL.md", "/tmp/SKILL.md", ".agents/skills/other/SKILL.md", "README.md"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    write_skill(self.root, {**plan(), key: "x"})
        outside = Path(self._directory.name) / "outside"
        outside.mkdir()
        (self.root / ".agents").symlink_to(outside)
        with self.assertRaises(ValueError):
            write_skill(self.root, plan())
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_claude_directory_is_a_conflict(self):
        (self.root / ".claude" / "skills" / NAME).mkdir(parents=True)
        with self.assertRaises(ConflictError):
            write_skill(self.root, plan())


class StatusTest(unittest.TestCase):
    def test_build_sets_built_never_verified(self):
        built = mark_built(reference_decision(), "A")
        self.assertEqual(built.part("A")["status"], "built")


if __name__ == "__main__":
    unittest.main()
