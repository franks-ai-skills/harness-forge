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

NAMED = [{"name": "repro-steps", "source": "https://example.com/repro-steps", "part_id": "A"}]

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

    def test_goals_picture_and_drift_are_part_of_the_contract(self):
        base = render_contract(decision(), None, [], worker_evidence=EVIDENCE)
        self.assertIn("Goals: Reproduction checklists from approved bug reports", base.text)
        self.assertIn("Picture: I ask for a checklist", base.text)
        for change in ({"goals": ["Another goal"]}, {"picture": "Another picture."}):
            with self.subTest(change=list(change)):
                self.assertNotEqual(render_contract(decision(**change), None, [], worker_evidence=EVIDENCE).digest,
                                    base.digest)
        drift = [{"rule_id": "skills.description.length", "finding_id": "F1", "slug": "repro",
                  "category": "compatibility", "summary": "The limit grew.",
                  "finding": {"claim": "Descriptions may be longer.", "source": "https://docs.example.com/limits",
                              "fetched": "2026-10-09", "quote": "Up to 2048.",
                              "context": "Limits. Up to 2048. Names stay short."}}]
        with_drift = render_contract(decision(), None, [], worker_evidence=EVIDENCE, drift=drift)
        self.assertIn("Recorded drift:\n  - skills.description.length (finding F1 of research repro, compatibility): "
                      "The limit grew.\n    Claim: Descriptions may be longer.\n"
                      '    Source: https://docs.example.com/limits (fetched 2026-10-09): "Up to 2048."\n'
                      '    Context: "Limits. Up to 2048. Names stay short."', with_drift.text)
        changed = [{**drift[0], "finding": {**drift[0]["finding"], "context": "Another context."}}]
        self.assertNotEqual(render_contract(decision(), None, [], worker_evidence=EVIDENCE, drift=changed).digest,
                            with_drift.digest, "the whole drift entry is bound, as drift_state compares it")
        self.assertNotEqual(with_drift.digest, base.digest, "adding or deleting drift changes the contract")
        self.assertIn("Recorded drift: none", base.text)

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

    def test_a_confirmation_binds_the_overrides_it_showed(self):
        contract = render_contract(decision(), None, [OVERRIDE], worker_evidence=EVIDENCE)
        self.assertIsNotNone(confirm_contract(contract, relay(contract), run_id="run-1"))
        changed = render_contract(decision(), None, [{**OVERRIDE, "reason": "Changed."}], worker_evidence=EVIDENCE)
        self.assertIsNone(confirm_contract(changed, relay(contract), run_id="run-1"))
        shown = render_contract(decision(), None, [OVERRIDE], worker_evidence=EVIDENCE,
                                described={OVERRIDE["rule_id"]: ["Pinned: error: old", "New: retired"]})
        self.assertIn("      New: retired", shown.text)
        self.assertEqual(shown.digest, contract.digest, "the description is shown; the record is what binds")


class ReadmeTest(unittest.TestCase):
    """The result README says where built skills load when the reach is wider than the repository."""

    def test_user_reach_says_the_skills_load_only_here_and_how_to_use_them_everywhere(self):
        from harness_forge.contracts import update_readme
        record = decision(part("A", "Run the review", slug="codex-review-run"),
                          part("B", "Only outside controls", outcome="outside-instead", recommended=None, chosen=None),
                          targets=["claude-code"], reach="user")
        readme = update_readme("# Codex review\n", record)
        self.assertIn("## Outside controls", readme)
        section = readme[readme.index("## Where the skills load"):]
        self.assertIn("`codex-review-run`", section)
        self.assertIn("loads only in this repository", section)
        self.assertIn("all of your repositories", section)
        self.assertIn("`~/.claude/skills/<name>/`", section)
        self.assertNotIn("~/.agents/skills", section, "Codex is no target of this idea")
        self.assertNotIn("part-b", section)
        self.assertEqual(update_readme(readme, record), readme, "a second run replaces the section in place")

    def test_both_targets_get_their_user_locations_and_public_reach_says_how_others_use_them(self):
        from harness_forge.contracts import update_readme
        record = decision(reach="public")
        section = update_readme("# Repro\n", record).split("## Where the skills load", 1)[1]
        self.assertIn("`~/.claude/skills/<name>/`", section)
        self.assertIn("`~/.agents/skills/<name>/`", section)
        self.assertIn("publish this repository", section)

    def test_repository_reach_or_no_built_skill_needs_no_section(self):
        from harness_forge.contracts import update_readme
        self.assertNotIn("## Where the skills load", update_readme("# Repro\n", decision()))
        outside_only = decision(part("A", "Gate merges", outcome="outside-instead", recommended=None, chosen=None),
                                reach="user")
        self.assertNotIn("## Where the skills load", update_readme("# Repro\n", outside_only))
        stale = "# Repro\n\n## Where the skills load\n\nForge builds `old-skill` into this repository.\n"
        self.assertNotIn("old-skill", update_readme(stale, decision()), "a narrowed reach removes the section")


class ReadmeSectionBoundsTest(unittest.TestCase):
    """Managed sections are found and ended only by real headings (Codex fix review round 1)."""

    def test_a_heading_inside_a_code_fence_is_the_users_text(self):
        from harness_forge.contracts import update_readme
        example = ("# Repro\n\nHow a section looks:\n\n```markdown\n## Where the skills load\n\nText.\n```\n\n"
                   "More prose.\n\n## Usage\n\nRun it.\n")
        updated = update_readme(example, decision())
        self.assertIn("```markdown\n## Where the skills load\n\nText.\n```\n\nMore prose.", updated)
        self.assertIn("## Usage\n\nRun it.\n", updated)

    def test_closing_hashes_and_empty_headings_follow_commonmark(self):
        from harness_forge.documents import markdown_headings, markdown_section
        text = "# T\n\n## Outside controls ##\n\nOld.\n\n##\n\nNext.\n"
        self.assertEqual([(level, title) for _, level, title in markdown_headings(text)],
                         [(1, "T"), (2, "Outside controls"), (2, "")])
        start, end = markdown_section(text, "Outside controls")
        self.assertEqual(text[start:end], "## Outside controls ##\n\nOld.\n\n")
        self.assertEqual(markdown_headings("```\n## a\n``` x\n## b\n```\n## c\n"), [(len("```\n## a\n``` x\n## b\n```\n"), 2, "c")],
                         "a closing fence holds nothing after its characters")

    def test_a_section_ends_at_a_heading_of_the_same_or_a_higher_level(self):
        from harness_forge.contracts import update_readme
        stale = "# Repro\n\n## Where the skills load\n\nForge builds `old-skill` into this repository.\n\n# License\n\nMIT.\n"
        updated = update_readme(stale, decision())
        self.assertNotIn("old-skill", updated)
        self.assertIn("# License\n\nMIT.\n", updated)
        nested = "# Repro\n\n## Outside controls\n\n- `old-control` (part A): recommended, not verified. Precaution; availability available.\n\n### Notes\n\nMine.\n\n# License\n\nMIT.\n"
        with self.assertRaisesRegex(ValueError, "Notes"):
            update_readme(nested, decision())  # a subsection is inside the section, and Forge did not write it
        replaced = update_readme(nested.replace("### Notes\n\nMine.\n\n", ""), decision())
        self.assertNotIn("old-control", replaced)
        self.assertIn("# License\n\nMIT.\n", replaced)


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


class ResearchCandidateTest(unittest.TestCase):
    def test_named_candidates_are_shown_and_bound(self):
        plain = render_contract(decision(), None, [], worker_evidence=EVIDENCE)
        named = render_contract(decision(research_candidates=NAMED), None, [], worker_evidence=EVIDENCE)
        self.assertNotIn("research_candidates", plain.content, "a record without candidates keeps its digest")
        self.assertNotEqual(plain.digest, named.digest)
        self.assertIn("Reuse candidates to research: repro-steps from https://example.com/repro-steps (part A)",
                      named.text)

    def test_a_candidate_for_an_unknown_part_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown part 'Z'"):
            render_contract(decision(research_candidates=[{**NAMED[0], "part_id": "Z"}]), None, [])

    def test_dependencies_need_a_reused_skill_in_the_repository(self):
        reuse = {"name": "x", "source": "https://example.com/x", "license": "MIT", "maintenance": "maintained",
                 "dependencies": ["scripts"]}
        with self.assertRaisesRegex(ValueError, "not in the repository"):
            render_contract(decision(part("A", "Reuse x", outcome="reuse", reuse=reuse)), None, [])
