"""Re-verification scope and invalidation of bound inputs."""

import unittest

from harness_forge.scope import affected_parts, extensive_scope, review_scope, scope_options
from tests.builders import decision, part
from tests.test_verify import FACTS, NAME, REVISION, RULES, Scenario, skill_decision


def chain():
    return decision(
        part("A", "Goal A", connects_to=[{"to": "B", "how": "starts"}]),
        part("B", "Goal B", connects_to=[{"to": "C", "how": "uses"}]),
        part("C", "Goal C"),
        part("D", "Goal D"),
        facts=FACTS,
    )


class ReviewScopeTest(unittest.TestCase):
    def test_review_includes_transitive_links_in_both_directions(self):
        self.assertEqual(review_scope(chain(), fixed={"C"}, changed=set()), {"A", "B", "C"})
        self.assertEqual(review_scope(chain(), fixed={"D"}, changed=set()), {"D"})

    def test_review_includes_parts_with_changed_inputs(self):
        self.assertEqual(review_scope(chain(), fixed={"D"}, changed={"A"}), {"A", "B", "C", "D"})

    def test_extensive_review_covers_every_part(self):
        self.assertEqual(extensive_scope(chain()), {"A", "B", "C", "D"})

    def test_options_show_exact_sets_and_judged_counts(self):
        counts = {"A": 5, "B": 3, "C": 2, "D": 7}
        options = scope_options(chain(), fixed={"C"}, changed=set(), judged_counts=counts)
        self.assertEqual([o["label"] for o in options], ["Review (recommended)", "Extensive review (rarely needed)"])
        self.assertEqual(options[0]["parts"], ["A", "B", "C"])
        self.assertEqual(options[0]["judged_rules"], 10)
        self.assertTrue(options[0]["link_checks"])
        self.assertEqual(options[1]["parts"], ["A", "B", "C", "D"])
        self.assertEqual(options[1]["judged_rules"], 17)


class InvalidationTest(unittest.TestCase):
    def setUp(self):
        second = part("B", "Never merge without review", outcome="outside-instead", recommended=None, chosen=None,
                      outside_controls=[{"control": "event-gate", "prerequisites": ["outside.merge.no-agent-bypass"],
                                         "availability": "unknown", "required_for_goal": True}])
        self.scenario = Scenario(skill_decision(second), confirmed=("A", "B"))

    def affected(self, **change):
        return affected_parts(self.scenario.run, self.scenario.current(**change))

    def test_unchanged_inputs_affect_nothing(self):
        self.assertEqual(self.affected(), set())

    def test_shared_file_invalidates_every_owner(self):
        files = {**self.scenario.files, "README.md": b"# Repo\n\nEdited.\n"}
        affected_parts_ = self.affected(files=files)
        self.assertEqual(affected_parts_, {"A", "B"})

    def test_part_file_and_deleted_file_affect_their_owner(self):
        skill = f".agents/skills/{NAME}/SKILL.md"
        self.assertEqual(self.affected(files={**self.scenario.files, skill: b"changed"}), {"A"})
        remaining = {k: v for k, v in self.scenario.files.items() if k != f".agents/skills/{NAME}/references/requirements.md"}
        self.assertEqual(self.affected(files=remaining), {"A"})

    def test_each_bound_input_changes_independently(self):
        self.assertEqual(self.affected(kb_version="0.2.0"), {"A", "B"})
        self.assertEqual(self.affected(revision=None), {"A"})
        self.assertEqual(self.affected(symlinks={}), {"A"})
        changed_answers = skill_decision(part("B", "Never merge without review", outcome="outside-instead", recommended=None,
                                              chosen=None, answers={"job": "check"}, outside_controls=[]))
        from harness_forge.verify import bound_inputs
        current = bound_inputs(changed_answers, self.scenario.files, RULES, symlinks=self.scenario.symlinks,
                               revision=REVISION, kb_version="0.1.0", worker_evidence=())
        self.assertIn("B", affected_parts(self.scenario.run, current))


if __name__ == "__main__":
    unittest.main()


class PreservedResultsTest(unittest.TestCase):
    def test_history_is_kept_only_with_matching_inputs_and_current_confirmation(self):
        from harness_forge.models import Confirmation
        from harness_forge.scope import preserved_parts

        scenario = Scenario(skill_decision())
        current = scenario.current()
        same = Confirmation(run_id="run-2", contract_digest=scenario.run.contract_digest, part_ids=["A"])
        self.assertEqual(preserved_parts(scenario.run, current, same), {"A"})
        self.assertEqual(preserved_parts(scenario.run, current, None), set())
        other_contract = Confirmation(run_id="run-2", contract_digest="e" * 64, part_ids=["A"])
        self.assertEqual(preserved_parts(scenario.run, current, other_contract), set())
        self.assertEqual(preserved_parts(scenario.run, scenario.current(kb_version="0.2.0"), same), set())


def two_skills():
    """Parts A and C, both built skills, confirmed together; C summarizes and is not linked to A."""
    from harness_forge.contracts import confirm_contract, render_contract
    from harness_forge.verify import prepare_run
    from tests.builders import EVIDENCE
    from tests.test_verify import build_files, readme

    record = skill_decision(part("C", "Summarize a reproduction checklist", slug="checklist-summary"))
    files_a, links_a = build_files(record, "A")
    files_c, links_c = build_files(record, "C")
    files = {**files_a, **files_c, "README.md": readme(record)}
    contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A", "C"], "run_id": "run-1"}
    confirmation = confirm_contract(contract, relay, run_id="run-1")

    def prepare(scope=None, changed_files=None):
        return prepare_run(record, {**files, **(changed_files or {})}, RULES, confirmation, symlinks={**links_a, **links_c},
                           revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE, scope=scope,
                           scope_label="Review (recommended)" if scope else "Extensive review (rarely needed)")
    return record, prepare


class ScopedRunTest(unittest.TestCase):
    """A Review run judges only its scope; every part's scripts and links run, and kept results need them to pass."""

    def finish(self, run, kept=None):
        from harness_forge.verify import finish_run, validate_review
        from tests.test_verify import passing_review
        return {r.part_id: r for r in finish_run(run, validate_review(run, passing_review(run)), [], run.frozen_inputs,
                                                 kept=kept)}

    def test_out_of_scope_parts_run_scripts_but_are_not_judged(self):
        _, prepare = two_skills()
        run = prepare(scope={"A"})
        self.assertEqual({r["part_id"] for r in run.judged_requests}, {"A"})
        self.assertTrue(any(result.part_id == "C" for result in run.static_results), "C's scripts still run")
        self.assertEqual(run.scope_parts, ["A"])

    def test_an_unchanged_part_keeps_its_earlier_judged_results(self):
        _, prepare = two_skills()
        first = self.finish(prepare())
        self.assertEqual({part_id: r.harness_result for part_id, r in first.items()}, {"A": "verified", "C": "verified"})
        kept = {"C": {"run_id": "run-0", "result": first["C"].to_dict()}}
        second = self.finish(prepare(scope={"A"}), kept=kept)
        self.assertEqual(second["C"].harness_result, "verified")
        judged = [r for r in second["C"].rule_results if r["check"] == "judged" and r["result"] != "not-applicable"]
        self.assertTrue(judged and all(r["reason"].endswith("(kept from run run-0)") for r in judged))
        self.assertEqual(second["C"].goal_verdict, {**first["C"].goal_verdict,
                                                    "reason": first["C"].goal_verdict["reason"] + " (kept from run run-0)"})

        failed = {"C": {"run_id": "run-0", "result": {**first["C"].to_dict(), "harness_result": "not verified"}}}
        self.assertEqual(self.finish(prepare(scope={"A"}), kept=failed)["C"].harness_result, "not verified")
        self.assertEqual(self.finish(prepare(scope={"A"}))["C"].harness_result, "not verified",
                         "a part outside the scope without earlier results is not verified")
        broken = {".agents/skills/checklist-summary/SKILL.md": b"---\nname: Bad_Name\ndescription: d\n---\nx\n"}
        self.assertEqual(self.finish(prepare(scope={"A"}, changed_files=broken), kept=kept)["C"].harness_result,
                         "not verified", "a kept result never outweighs a script failure now")


class LinkCheckTest(unittest.TestCase):
    """Each link names a part that exists, and a part with an artifact has it under its recorded name."""

    def results(self, record, files):
        from harness_forge.verify import prepare_run
        run = prepare_run(record, files, RULES, None, kb_version="0.1.0")
        return {r.part_id: r for r in run.static_results if r.rule_id == "links.parts"}

    def test_links_to_built_parts_and_unknown_parts(self):
        from tests.test_verify import build_files, readme
        record = skill_decision(part("B", "Never merge without review", outcome="outside-instead", recommended=None,
                                     chosen=None, connects_to=[{"to": "A", "how": "uses"}]))
        files, _ = build_files(record)
        files["README.md"] = readme(record)
        self.assertEqual(self.results(record, files)["B"].status, "pass")
        self.assertNotIn("A", self.results(record, files), "a part without links has no link check")
        missing = {path: data for path, data in files.items() if not path.endswith("/SKILL.md")}
        result = self.results(record, missing)["B"]
        self.assertEqual(result.status, "fail")
        self.assertIn(f"part A has no .agents/skills/{NAME}/SKILL.md", result.reason)
        renamed = {**files, f".agents/skills/{NAME}/SKILL.md": files[f".agents/skills/{NAME}/SKILL.md"].replace(
            f"name: {NAME}".encode(), b"name: other-name")}
        self.assertIn("names itself other-name", self.results(record, renamed)["B"].reason)
        dangling = skill_decision(part("B", "Never merge without review", outcome="outside-instead", recommended=None,
                                       chosen=None, connects_to=[{"to": "Z", "how": "uses"}]))
        self.assertIn("part Z does not exist", self.results(dangling, files)["B"].reason)


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        import contextlib
        import importlib.util
        import io
        import json
        from pathlib import Path

        forge = Path(__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location("forge_cli", forge / "scripts" / "forge.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = module.main(list(argv))
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else {}

    def test_check_report_and_scope_commands(self):
        import os
        import tempfile
        from pathlib import Path

        from harness_forge.contracts import update_outside_section, write_decision
        from harness_forge.research import write_revision
        from tests.test_verify import build_files

        record = skill_decision()
        files, symlinks = build_files(record)
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as directory:
            root = Path(directory)
            for path, data in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
            for link, target in symlinks.items():
                (root / link).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, root / link)
            (root / "README.md").write_text(update_outside_section("# Repo\n", record), encoding="utf-8")
            decision_path = write_decision(root, record, slug="repro", date="2026-10-09")
            revision_path = write_revision(root, "repro", REVISION)

            code, output = self.run_cli("check", "--decision", str(decision_path), "--root", str(root), "--revision", str(revision_path))
            self.assertEqual(code, 0, output)
            part_a = output["parts"][0]
            self.assertEqual((part_a["harness_result"], part_a["goal_result"]), ("not verified", "not assessed"))
            self.assertEqual(part_a["failed_script_rules"], [])

            code, output = self.run_cli("report", "--decision", str(decision_path), "--root", str(root),
                                        "--revision", str(revision_path), "--slug", "repro", "--write")
            self.assertEqual(code, 0, output)
            report = root / ".harness" / "reports" / "repro.md"
            self.assertTrue(report.is_file())
            self.assertIn("none run", report.read_text(encoding="utf-8"))

            code, output = self.run_cli("scope", "--decision", str(decision_path), "--fixed", "A")
            self.assertEqual(code, 0, output)
            self.assertEqual(output["options"][0]["parts"], ["A"])
