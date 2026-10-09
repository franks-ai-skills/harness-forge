"""Effective rules before the override adoption flow exists (T12)."""

import unittest
from pathlib import Path

from harness_forge.documents import load_document
from harness_forge.models import Confirmation, Rule
from harness_forge.overrides import OverridesPending, effective_rules

SNAPSHOT = Path(__file__).resolve().parents[1] / "snapshot"
PINNED = [Rule.from_dict(rule) for rule in load_document(SNAPSHOT / "rules" / "skills.yaml", "rules")]
OVERRIDE = {"rule_id": "skills.description.concise", "pinned_kb_version": "0.1.0", "retired": True,
            "reason": "Longer descriptions.", "date": "2026-10-09", "link": "pending"}


class EffectiveRulesTest(unittest.TestCase):
    def test_pinned_rules_apply_without_overrides(self):
        self.assertEqual(effective_rules(PINNED, [], None), PINNED)

    def test_nonempty_overrides_block_instead_of_being_ignored(self):
        confirmation = Confirmation(run_id="run-1", contract_digest="0" * 64, part_ids=["A"])
        for current in (None, confirmation):
            with self.subTest(confirmation=current):
                with self.assertRaises(OverridesPending):
                    effective_rules(PINNED, [OVERRIDE], current)


if __name__ == "__main__":
    unittest.main()
