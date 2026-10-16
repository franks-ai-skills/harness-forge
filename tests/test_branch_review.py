"""Regressions for the whole-branch M2 review findings (GPT-6.1 Sol, 2026-10-10)."""

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.checks.runner import run_check
from harness_forge.contracts import confirm_contract, confirmation_problem, render_contract, write_decision
from harness_forge.documents import canonical_digest, validate
from harness_forge.models import AcceptedRevision
from harness_forge.research import accept_requirements, load_revision, revision_digest, write_revision
from harness_forge import verify
from harness_forge.verify import bound_inputs, prepare_run
from tests.builders import EVIDENCE, decision, part
from tests.test_build_skill import NAME, REVISION, idea_rule, reference_decision
from tests.test_checks import BOTH, skill, snapshot_rule
from tests.test_verify import GATE, RULES, Scenario, skill_decision

FORGE = Path(__file__).resolve().parents[1]


def forge_cli():
    specification = importlib.util.spec_from_file_location("forge_cli", FORGE / "scripts" / "forge.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def run_cli(*arguments):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = forge_cli().main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


class DuplicateRequirementTest(unittest.TestCase):
    """Finding 1: two requirements with one id would let the later result hide the earlier."""

    def twins(self):
        return [idea_rule("environment", "The checklist names the environment"),
                idea_rule("environment", "The checklist names the operating system")]

    def test_acceptance_rejects_two_proposals_with_one_id(self):
        with self.assertRaises(ValueError):
            accept_requirements(self.twins(), {"idea.repro.a.environment": "accept"}, "b" * 64)

    def test_loading_rejects_a_revision_with_one_id_twice(self):
        rules = [{"part_id": "A", "rule": rule.to_dict()} for rule in self.twins()]
        value = {"briefs": {"A": "b" * 64}, "digest": revision_digest({"A": "b" * 64}, rules), "rules": rules}
        with tempfile.TemporaryDirectory(prefix="forge-revision-") as directory:
            path = Path(directory) / "repro.rules.yaml"
            path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_revision(path)

    def test_verification_rejects_a_revision_with_one_id_twice(self):
        rules = [{"part_id": "A", "rule": rule.to_dict()} for rule in self.twins()]
        twice = AcceptedRevision(rule_ids=[r["rule"]["id"] for r in rules], rules=rules, brief_digests={"A": "b" * 64},
                                 digest=revision_digest({"A": "b" * 64}, rules))
        with self.assertRaises(ValueError):
            prepare_run(skill_decision(), {}, RULES, None, revision=twice)


class PrepareContractRevisionTest(unittest.TestCase):
    """Finding 2: prepare-contract reads the revision research writes, with its digest check."""

    def write(self, directory):
        root = Path(directory)
        record = reference_decision()
        return record, write_decision(root, record, slug="repro", date="2026-10-10"), write_revision(root, "repro", REVISION)

    def test_the_written_revision_renders_the_same_contract(self):
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            record, decision_path, revision_path = self.write(directory)
            code, stdout, stderr = run_cli("prepare-contract", "--decision", str(decision_path),
                                           "--revision", str(revision_path))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["digest"], render_contract(record, REVISION, []).digest)

    def test_an_edited_revision_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            _, decision_path, revision_path = self.write(directory)
            value = yaml.safe_load(revision_path.read_text(encoding="utf-8"))
            value["rules"][0]["rule"]["statement"] = "The checklist may skip the environment"
            revision_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
            code, stdout, _ = run_cli("prepare-contract", "--decision", str(decision_path),
                                      "--revision", str(revision_path))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")


class ReuseBindingTest(unittest.TestCase):
    """Finding 3: the contract shows and binds the artifact a reuse part adopts."""

    REUSE = {"name": NAME, "source": "https://example.com/repro", "license": "MIT", "maintenance": "maintained",
             "path": f".agents/skills/{NAME}"}

    def record(self, **reuse):
        record = reference_decision(outcome="reuse", reuse={**self.REUSE, **reuse})
        return record

    def test_the_contract_text_names_the_reused_artifact(self):
        text = render_contract(self.record(), REVISION, [], worker_evidence=EVIDENCE).text
        self.assertIn(NAME, text)
        self.assertIn("https://example.com/repro", text)
        self.assertIn("MIT", text)

    def test_a_replaced_artifact_needs_a_new_confirmation(self):
        original = self.record()
        contract = render_contract(original, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        confirmation = confirm_contract(contract, relay, run_id="run-1")
        for change in ({"name": "other-skill"}, {"source": "https://example.com/other"}, {"license": "GPL-3.0"},
                       {"path": ".agents/skills/other-skill"}, {"maintenance": "unmaintained"}):
            with self.subTest(change=change):
                replaced = self.record(**change)
                self.assertIsNotNone(confirmation_problem(confirmation, replaced, REVISION, [], EVIDENCE))


class ConfirmedAnswersTest(unittest.TestCase):
    """Follow-up 1: the contract binds the confirmed answers and facts that decide applicability."""

    def confirmation(self, record):
        contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        return confirm_contract(contract, relay, run_id="run-1")

    def test_the_contract_text_shows_facts_and_answers(self):
        text = render_contract(skill_decision(), REVISION, [], worker_evidence=EVIDENCE).text
        self.assertIn("target_set: both", text)
        self.assertIn("reach: repository", text)
        self.assertIn("job: procedure", text)
        self.assertIn(f"Name: {NAME}", text)

    def test_changed_facts_answers_or_name_need_a_new_confirmation(self):
        original = skill_decision()
        confirmation = self.confirmation(original)
        edits = (lambda r: r.record["facts"].update(untrusted_repo=True),
                 lambda r: (r.record.update(reach="public"), r.record["facts"].update(reach="public")),
                 lambda r: r.record["hosting"].update(visibility="private"),
                 lambda r: r.record["parts"][0]["answers"].update(job="knowledge"),
                 lambda r: r.record["parts"][0].update(slug="other-skill"))
        for number, edit in enumerate(edits):
            with self.subTest(edit=number):
                changed = copy.deepcopy(original)
                edit(changed)
                self.assertIsNotNone(confirmation_problem(confirmation, changed, REVISION, [], EVIDENCE))

    def test_a_target_set_that_contradicts_the_targets_is_refused(self):
        changed = skill_decision()
        changed.record["facts"]["target_set"] = "codex"
        with self.assertRaises(ValueError):
            render_contract(changed, REVISION, [], worker_evidence=EVIDENCE)

    def test_the_reviewers_reproduction_stays_unverified(self):
        original = skill_decision()
        confirmation = self.confirmation(original)
        changed = copy.deepcopy(original)
        changed.record["facts"]["target_set"] = "codex"
        run = prepare_run(changed, {}, RULES, confirmation, revision=REVISION, worker_evidence=EVIDENCE)
        self.assertEqual(run.confirmed_parts, [])
        self.assertIn("contradicts", run.unconfirmed_reason)

    def test_the_cli_refuses_a_contradictory_record(self):
        changed = skill_decision()
        changed.record["facts"]["target_set"] = "codex"
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            path = write_decision(Path(directory), changed, slug="repro", date="2026-10-10")
            code, stdout, stderr = run_cli("prepare-contract", "--decision", str(path))
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("contradicts", stderr)


class OutsideOnlyInputsTest(unittest.TestCase):
    """Finding 4: a changed input leaves an outside-only part unassessed."""

    def scenario(self):
        outside = part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                       outside_controls=[GATE])
        return Scenario(skill_decision(outside), confirmed=("A", "B"))

    def test_a_changed_goal_is_not_assessed(self):
        scenario = self.scenario()
        changed = copy.deepcopy(scenario.record)
        changed.record["parts"][1]["goal"] = "Merge whenever the agent likes"
        current = bound_inputs(changed, scenario.files, RULES, symlinks=scenario.symlinks, revision=REVISION,
                               kb_version="0.1.0", worker_evidence=EVIDENCE)
        result = scenario.finish(current=current)["B"]
        self.assertEqual(result.goal_result, "not assessed")
        self.assertIn("inputs.bound", [r["rule_id"] for r in result.rule_results])

    def test_a_removed_readme_hand_off_is_not_assessed(self):
        scenario = self.scenario()
        files = {path: data for path, data in scenario.files.items() if path != "README.md"}
        result = scenario.finish(current=scenario.current(files=files))["B"]
        self.assertEqual(result.goal_result, "not assessed")

    def test_unchanged_inputs_still_depend_on_outside_controls(self):
        self.assertEqual(self.scenario().finish()["B"].goal_result, "depends on outside controls")


class ReferenceSyntaxTest(unittest.TestCase):
    """Finding 5: every Markdown link form to a missing file fails the reference check."""

    def status(self, body):
        files = skill("name: repro-checklist\ndescription: d\n", body)
        return run_check(snapshot_rule("skills.references.resolve"), files, BOTH).status

    def test_links_with_titles_and_reference_definitions_are_checked(self):
        for body in ('Read [it](references/missing.md "Details") when needed.\n',
                     "Read [it](<references/missing.md> 'Details') when needed.\n",
                     "Read [it][details] when needed.\n\n[details]: references/missing.md\n",
                     'Read [it][details].\n\n  [details]: <references/missing.md> "Details"\n'):
            with self.subTest(body=body):
                self.assertEqual(self.status(body), "fail")

    def test_footnotes_are_not_file_links(self):
        self.assertEqual(self.status("Use this checklist.[^1]\n\n[^1]: Background explanation only.\n"), "pass")

    def test_existing_targets_in_every_form_pass(self):
        files = skill("name: repro-checklist\ndescription: d\n",
                      'Read [it](references/template.md "Template") or [the same][t].\n\n[t]: references/template.md\n')
        files[".agents/skills/repro-checklist/references/template.md"] = b"# Template\n"
        self.assertEqual(run_check(snapshot_rule("skills.references.resolve"), files, BOTH).status, "pass")


class ReviewRequestTest(unittest.TestCase):
    """Finding 6: the review worker request carries what its instructions promise."""

    def test_the_request_for_a_run_is_schema_valid_and_complete(self):
        run = Scenario(skill_decision()).run
        request = verify.review_request(run, "review-1")
        validate(request, "worker-request")
        rules = {entry["rule_id"]: entry for entry in request["inputs"]["rules"]}
        self.assertEqual(rules["skills.description.no-workflow-steps"]["rule_kind"], "must-not")
        self.assertEqual({entry["applicability"] for entry in rules.values()} <= {"script", "judged"}, True)
        self.assertEqual(request["inputs"]["goals"][0]["requirement_ids"], REVISION.rule_ids)
        self.assertEqual(request["input_digest"], canonical_digest(request["inputs"]))
        self.assertTrue(all(path.startswith(f".agents/skills/{NAME}/") or path == "README.md"
                            for path in (entry["path"] for entry in request["inputs"]["files"])))


class OutputBoundTest(unittest.TestCase):
    """Finding 7: no command prints more than the documented bound."""

    def test_an_oversized_contract_is_refused_instead_of_printed(self):
        parts = [part(letter, letter + " goal " + "x" * 19000) for letter in "ABCDE"]
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            path = write_decision(Path(directory), decision(*parts), slug="large", date="2026-10-10")
            code, stdout, stderr = run_cli("prepare-contract", "--decision", str(path))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("65536", stderr)


if __name__ == "__main__":
    unittest.main()
