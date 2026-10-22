"""A skill's requirements reference names only its own part's requirements (issue #16)."""

import tempfile
import unittest
from pathlib import Path

from harness_forge.build_skill import part_requirements_digest, plan_skill, write_skill
from harness_forge.contracts import confirm_contract, render_contract
from harness_forge.documents import canonical_digest
from harness_forge.research import accept_requirements, merge_revision
from tests.builders import EVIDENCE, decision, part
from tests.test_build_skill import DESCRIPTION, NAME, PINNED, PROCEDURE, PROPOSED, REVISION, idea_rule

REFERENCE = f".agents/skills/{NAME}/references/requirements.md"
SKILL = f".agents/skills/{NAME}/SKILL.md"
CHOICES = {"idea.repro.a.environment": "accept", "idea.repro.a.numbered-steps": "accept",
           "idea.repro.a.expected-observed": "accept", "idea.repro.a.attach-logs": "reject"}
B_REVISION = accept_requirements(
    [idea_rule("platform", "The summary names the platform", id="idea.repro.b.platform")],
    {"idea.repro.b.platform": "accept"}, "c" * 64)


def two_parts():
    return decision(part("A", "Write a reproduction checklist when asked", slug=NAME),
                    part("B", "Summarize a reproduction checklist", slug="checklist-summary"))


def planned(revision):
    record = two_parts()
    record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
    contract = render_contract(record, revision, [], worker_evidence=EVIDENCE)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
    confirmation = confirm_contract(contract, relay, run_id="run-1")
    return plan_skill(record, "A", revision, PINNED, confirmation=confirmation, worker_evidence=EVIDENCE,
                      description=DESCRIPTION, procedure=PROCEDURE)


class PartDigestTest(unittest.TestCase):
    def test_the_reference_names_the_parts_own_requirements_digest(self):
        entries = [entry for entry in REVISION.rules if entry["part_id"] == "A"]
        expected = canonical_digest({"brief": REVISION.brief_digests["A"], "rules": entries})
        self.assertEqual(part_requirements_digest(REVISION, "A"), expected)
        reference = planned(REVISION)[REFERENCE]
        self.assertIn(expected, reference)
        self.assertNotIn(REVISION.digest, reference)

    def test_another_parts_acceptance_leaves_the_reference_unchanged(self):
        merged = merge_revision(REVISION, B_REVISION)
        self.assertNotEqual(merged.digest, REVISION.digest)
        self.assertEqual(planned(merged)[REFERENCE], planned(REVISION)[REFERENCE])

    def test_the_parts_own_requirements_or_brief_change_the_reference(self):
        more = accept_requirements(PROPOSED, {**CHOICES, "idea.repro.a.attach-logs": "accept"}, "b" * 64)
        rebriefed = accept_requirements(PROPOSED, CHOICES, "d" * 64)
        for changed in (more, rebriefed):
            with self.subTest(digest=changed.digest):
                self.assertNotEqual(part_requirements_digest(changed, "A"), part_requirements_digest(REVISION, "A"))
                self.assertNotEqual(planned(changed)[REFERENCE], planned(REVISION)[REFERENCE])

    def test_a_one_file_repair_after_another_parts_acceptance_needs_no_other_file(self):
        with tempfile.TemporaryDirectory(prefix="forge-part-digest-") as directory:
            root = Path(directory)
            write_skill(root, planned(REVISION))
            broken = root / SKILL
            broken.write_text(broken.read_text(encoding="utf-8").replace(f"name: {NAME}", "name: renamed"),
                              encoding="utf-8")
            write_skill(root, planned(merge_revision(REVISION, B_REVISION)), replace=[SKILL])
            self.assertIn(f"name: {NAME}", broken.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
