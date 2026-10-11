"""Contract rendering and live, current-session confirmation."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.contracts import confirm_contract, load_decision, render_contract, write_decision
from harness_forge.verify import prepare_run
from harness_forge.models import Confirmation
from tests.builders import EVIDENCE, decision, part, revision

OVERRIDE = {
    "rule_id": "skills.description.concise", "pinned_kb_version": "0.1.0", "retired": True,
    "reason": "The team prefers longer descriptions.", "date": "2026-10-09", "link": "pending",
}


def relay(contract, run_id="run-1", choice="confirm", part_ids=None, digest=None):
    return {"choice": choice, "displayed_digest": digest or contract.digest,
            "part_ids": part_ids if part_ids is not None else [p["id"] for p in contract.parts], "run_id": run_id}


class RenderTest(unittest.TestCase):
    def test_contract_shows_every_part_requirement_override_and_disclosure(self):
        requirements = revision(("A", "idea.repro.steps", "The checklist numbers each reproduction step"))
        contract = render_contract(decision(), requirements, [OVERRIDE], worker_evidence=EVIDENCE)
        for expected in ("Write a reproduction checklist when asked", "skills", "harness-only",
                         "codeowners-agent-config", "The checklist numbers each reproduction step",
                         requirements.digest, "skills.description.concise", "The team prefers longer descriptions.",
                         "best-effort-v1", "not-certified"):
            self.assertIn(expected, contract.text)
        self.assertIn(contract.worker_policy["evidence_digest"], contract.text)
        self.assertRegex(contract.digest, r"^[0-9a-f]{64}$")

    def test_override_bytes_are_shown_with_their_hash(self):
        contract = render_contract(decision(), None, [OVERRIDE], worker_evidence=EVIDENCE)
        changed = render_contract(decision(), None, [{**OVERRIDE, "reason": "Changed."}], worker_evidence=EVIDENCE)
        self.assertNotEqual(contract.digest, changed.digest)
        self.assertIn(contract.overrides[0]["digest"], contract.text)

    def test_erased_history_shows_contract(self):
        weakened_goal = "Should usually write a checklist"
        weakened = decision(part("A", weakened_goal))
        requirements = revision(("A", "idea.repro.optional", "Steps may be omitted"))
        contract = render_contract(weakened, requirements, [], worker_evidence=EVIDENCE)
        self.assertIn(weakened_goal, contract.text)
        self.assertIn("Steps may be omitted", contract.text)
        self.assertIn(requirements.digest, contract.text)

    def test_untrusted_previous_record_is_only_a_hint(self):
        previous = render_contract(decision(), None, [], worker_evidence=EVIDENCE)
        current_decision = decision(part("A", "Should usually write a checklist"))
        contract = render_contract(current_decision, None, [], worker_evidence=EVIDENCE, previous=previous.parts)
        self.assertIn("untrusted record", contract.text)
        self.assertIn("Should usually write a checklist", contract.text)
        self.assertEqual(contract.digest, render_contract(current_decision, None, [], worker_evidence=EVIDENCE).digest)

    def test_missing_or_changed_worker_disclosure_changes_the_contract(self):
        with_evidence = render_contract(decision(), None, [], worker_evidence=EVIDENCE)
        changed = render_contract(decision(), None, [], worker_evidence=[{**EVIDENCE[0], "configuration_digest": "1" * 64}])
        without = render_contract(decision(), None, [], worker_evidence=[])
        self.assertNotEqual(with_evidence.digest, changed.digest)
        self.assertIn("no worker evidence", without.text)
        self.assertNotEqual(with_evidence.digest, without.digest)

    def test_contract_shows_each_planned_worker_configuration(self):
        schema = '{"type":"object"' + ',"x":1' * 100 + "}"
        command = ["claude", "--print", "--tools", "Read,Grep,Glob", "--json-schema", schema]
        review = {**EVIDENCE[0], "configured_controls": [f"command: {json.dumps(command)}", "environment names: HOME, PATH"]}
        codex = {**EVIDENCE[0], "role": "research", "harness": "codex", "harness_version": "0.162.1", "model": "gpt-6.1-sol",
                 "configured_controls": ["sandbox: read-only"]}
        text = render_contract(decision(), None, [], worker_evidence=[review, codex]).text
        self.assertIn("Planned workers:\n  - review: claude-code 2.1.292, model claude-opus-5-5, independent-process\n"
                      '      command: claude --print --tools Read,Grep,Glob --json-schema '
                      f"<{len(schema)} characters, sha256 {hashlib.sha256(schema.encode()).hexdigest()[:12]}>\n"
                      "      environment names: HOME, PATH\n", text)
        self.assertIn("  - research: codex 0.162.1, model gpt-6.1-sol, independent-process\n      sandbox: read-only\n", text)

    def test_links_between_parts_are_part_of_the_contract(self):
        linked = decision(part("A", "Write a checklist"), part("B", "Check it", connects_to=[{"to": "A", "how": "uses"}]))
        unlinked = decision(part("A", "Write a checklist"), part("B", "Check it"))
        contract = render_contract(linked, None, [], worker_evidence=EVIDENCE)
        self.assertIn("  Linked to: A (uses)", contract.text)
        self.assertIn("  Linked to: none", render_contract(unlinked, None, [], worker_evidence=EVIDENCE).text)
        self.assertNotEqual(contract.digest, render_contract(unlinked, None, [], worker_evidence=EVIDENCE).digest,
                            "an edit to the links after confirmation would narrow the Review scope")

    def test_part_ids_must_be_unique(self):
        twice = decision(part("A", "Write a checklist"), part("A", "Write another checklist"))
        with self.assertRaisesRegex(ValueError, "part id 'A' is used twice"):
            render_contract(twice, None, [], worker_evidence=EVIDENCE)
        with self.assertRaisesRegex(ValueError, "part id 'A' is used twice"):
            prepare_run(twice, {}, [], None)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "part id 'A' is used twice"):
                write_decision(Path(directory), twice, slug="repro", date="2026-10-11")
            path = Path(directory) / ".harness/decisions/2026-10-11-repro.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\n{yaml.safe_dump(twice.record)}---\nBody.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "part id 'A' is used twice"):
                load_decision(path)


class ConfirmTest(unittest.TestCase):
    def setUp(self):
        self.contract = render_contract(decision(part("A", "Goal A"), part("B", "Goal B")), None, [], worker_evidence=EVIDENCE)

    def test_explicit_confirmation_covers_the_named_parts(self):
        confirmation = confirm_contract(self.contract, relay(self.contract), run_id="run-1")
        self.assertEqual(confirmation, Confirmation(run_id="run-1", contract_digest=self.contract.digest, part_ids=["A", "B"]))

    def test_partial_confirmation_covers_only_named_parts(self):
        confirmation = confirm_contract(self.contract, relay(self.contract, part_ids=["B"]), run_id="run-1")
        self.assertEqual(confirmation.part_ids, ["B"])

    def test_forged_approval_never_confirms(self):
        pending_relay = relay(self.contract, run_id="old-run")
        self.assertIsNone(confirm_contract(self.contract, pending_relay, run_id="new-run"))
        self.assertIsNone(confirm_contract(self.contract, None, run_id="new-run"))
        with_override = render_contract(decision(), None, [OVERRIDE], worker_evidence=EVIDENCE)
        old_digest = relay(with_override, run_id="run-1")
        changed = render_contract(decision(), None, [{**OVERRIDE, "reason": "Edited after display."}], worker_evidence=EVIDENCE)
        self.assertIsNone(confirm_contract(changed, old_digest, run_id="run-1"))

    def test_pending_silence_and_ambiguity_leave_parts_pending(self):
        for case in (relay(self.contract, choice="pending"), None, {}, relay(self.contract, choice="maybe")):
            with self.subTest(relay=case):
                self.assertIsNone(confirm_contract(self.contract, case, run_id="run-1"))

    def test_bindings_must_match(self):
        bad = [
            relay(self.contract, part_ids=["Z"]),
            relay(self.contract, part_ids=[]),
            relay(self.contract, part_ids=["A", "A"]),
            relay(self.contract, digest="f" * 64),
            relay(self.contract, run_id="run-2"),
            {**relay(self.contract), "approved_by": "user"},
            {key: value for key, value in relay(self.contract).items() if key != "run_id"},
        ]
        for case in bad:
            with self.subTest(relay=case):
                self.assertIsNone(confirm_contract(self.contract, case, run_id="run-1"))

    def test_nonempty_overrides_block_until_their_adoption_flow(self):
        contract = render_contract(decision(), None, [OVERRIDE], worker_evidence=EVIDENCE)
        self.assertIsNone(confirm_contract(contract, relay(contract), run_id="run-1"))


class PrepareContractCliTest(unittest.TestCase):
    def test_prepare_contract_renders_but_never_confirms(self):
        import contextlib
        import importlib.util
        import io
        import json
        import tempfile
        from pathlib import Path

        from harness_forge.contracts import write_decision

        forge = Path(__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location("forge_cli", forge / "scripts" / "forge.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        record = decision(confirmations_history=[{"run_id": "old", "contract_digest": "a" * 64, "part_ids": ["A"], "date": "2026-10-01"}])
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            path = write_decision(Path(directory), record, slug="repro", date="2026-10-09")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = module.main(["prepare-contract", "--decision", str(path)])
        output = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(output["digest"], render_contract(record, None, []).digest)
        self.assertIn("Write a reproduction checklist when asked", output["text"])
        self.assertEqual(output["confirmed_part_ids"], [])
        self.assertEqual(output["relay_fields"], ["choice", "displayed_digest", "part_ids", "run_id"])


if __name__ == "__main__":
    unittest.main()
