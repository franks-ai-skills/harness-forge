"""A part that owns the README only for its hand-off is bound to the sections Forge writes (#155)."""

import copy
import unittest

from harness_forge.contracts import confirm_contract, render_contract
from harness_forge.scope import affected_parts, earlier_run, preserved_parts
from harness_forge.verify import (
    bound_inputs, exclusion_token, finish_run, prepare_run, readme_binding, validate_review,
)
from tests.builders import EVIDENCE
from tests.test_scope import two_skills
from tests.test_verify import NAME, REVISION, RULES, Scenario, build_files, passing_review, readme, skill_decision

RULE = "skills.invocation.side-effects-user-invoked"  # judged applicability, so it has an exclusion token


def intro_edited(data: bytes, text: str = "Edited introduction.\nA second line.\n\n") -> bytes:
    """The README with text inserted above every section, which shifts the lines below it."""
    head, rest = data.decode().split("\n", 1)
    return f"{head}\n\n{text}{rest}".encode()


def section_edited(data: bytes) -> bytes:
    return data.decode().replace("## Outside controls\n", "## Outside controls\n\nAn edited line.\n").encode()


class BindingTest(unittest.TestCase):
    README = (b"# Repo\n\nIntro.\n\n## Outside controls\n\n- a control\n- another control\n\n"
              b"## Where the skills load\n\n- here\n")

    def test_text_outside_the_sections_does_not_change_the_binding(self):
        binding = readme_binding(self.README)
        self.assertEqual(binding["mode"], "forge-sections")
        self.assertEqual(readme_binding(intro_edited(self.README)), binding)
        self.assertEqual(readme_binding(self.README + b"\n## Notes\n\nMine.\n"), binding)

    def test_a_change_in_or_around_the_sections_changes_it(self):
        binding = readme_binding(self.README)
        self.assertNotEqual(readme_binding(section_edited(self.README)), binding)
        self.assertNotEqual(readme_binding(self.README.replace(b"- here", b"- there")), binding)
        swallowed = self.README.replace(b"Intro.\n", b"Intro.\n\n```\n")  # an unclosed fence hides both headings
        self.assertNotEqual(readme_binding(swallowed), binding)
        inside = self.README.replace(b"- a control\n", b"- a control\n\n## Inserted\n\n")  # splits the section
        self.assertNotEqual(readme_binding(inside), binding)
        self.assertNotEqual(readme_binding(b"# Repo\n\nIntro.\n"), binding, "missing sections bind as missing")

    def test_a_readme_forge_cannot_read_by_section_binds_as_a_whole_file(self):
        twice = self.README + b"\n## Outside controls\n\n- another\n"
        self.assertEqual(readme_binding(twice)["mode"], "whole-file")
        self.assertNotEqual(readme_binding(intro_edited(twice)), readme_binding(twice))
        self.assertEqual(readme_binding(b"\xff\xfe# Repo\n")["mode"], "whole-file")


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())
        self.readme = self.scenario.files["README.md"]

    def current(self, data):
        return self.scenario.current(files={**self.scenario.files, "README.md": data})

    def test_an_edit_outside_the_sections_leaves_a_hand_off_part_current(self):
        run = self.scenario.run
        self.assertEqual(run.readme_handoff_only, ["A"])
        self.assertEqual(affected_parts(run, self.current(intro_edited(self.readme))), set())
        self.assertEqual(affected_parts(run, self.current(section_edited(self.readme))), {"A"})

    def test_a_run_without_the_binding_compares_the_whole_readme(self):
        old = copy.deepcopy(self.scenario.run)  # as a report from before #155 reads back
        old.frozen_inputs.pop("readme")
        old.readme_handoff_only = []
        self.assertEqual(affected_parts(old, self.current(intro_edited(self.readme))), {"A"})
        self.assertEqual(affected_parts(old, self.current(self.readme)), set())

    def test_a_part_that_also_declares_the_readme_compares_the_whole_file(self):
        reuse = {"name": NAME, "source": "https://example.com/repro", "license": "MIT", "maintenance": "maintained",
                 "path": f"skills/{NAME}", "skill_home": "plugin", "dependencies": ["README.md"]}
        from tests.builders import part
        record = skill_decision(part("B", "Summarize a reproduction checklist", slug="checklist-summary"),
                                outcome="reuse", reuse=reuse)
        built, _ = build_files(skill_decision())
        files = {path.replace(f".agents/skills/{NAME}/", f"skills/{NAME}/"): data for path, data in built.items()}
        files_b, links_b = build_files(record, "B")
        files.update({**files_b, "README.md": readme(record), "plugin.json": b'{"name": "repro-tools"}\n'})
        contract = render_contract(record, REVISION, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A", "B"], "run_id": "run-1"}
        run = prepare_run(record, files, RULES, confirm_contract(contract, relay, run_id="run-1"), symlinks=links_b,
                          revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE)
        self.assertEqual(run.readme_handoff_only, ["B"])
        edited = {**files, "README.md": intro_edited(files["README.md"])}
        current = bound_inputs(record, edited, RULES, symlinks=links_b, revision=REVISION, kb_version="0.1.0",
                               worker_evidence=EVIDENCE)
        self.assertEqual(affected_parts(run, current), {"A"}, "A declares the README; B owns it for the hand-off")

    def test_a_confirmed_exclusion_survives_an_edit_outside_the_sections(self):
        token = exclusion_token(self.scenario.run, "A", RULE)
        for data, same in ((intro_edited(self.readme), True), (section_edited(self.readme), False)):
            scenario = Scenario(skill_decision(), files={"README.md": data})
            self.assertEqual(exclusion_token(scenario.run, "A", RULE) == token, same)


class KeptOriginTest(unittest.TestCase):
    """A kept part's citations name the attempt and the README bytes they were judged on, through repeated Reviews."""

    def setUp(self):
        self.record, self.prepare = two_skills()
        self.readme = readme(self.record)

    def finish(self, run, kept=None):
        review = validate_review(run, passing_review(run))
        return {r.part_id: r for r in finish_run(run, review, [], run.frozen_inputs, kept=kept)}

    def kept_entry(self, run, result, run_id, attempt_id):
        return {"C": {"run_id": run_id, "attempt_id": attempt_id, "result": result.to_dict(),
                      "files": run.frozen_inputs["files"]}}

    def test_a_chain_of_reviews_names_the_judging_attempt_and_its_readme(self):
        first_run = self.prepare()
        first = self.finish(first_run)
        self.assertNotIn("kept_from", first["C"].to_dict(), "a judged part's result keeps its earlier form")
        r1 = first_run.frozen_inputs["files"]["README.md"]

        second_readme = intro_edited(self.readme)
        second_run = self.prepare(scope={"A"}, changed_files={"README.md": second_readme})
        self.assertEqual(preserved_parts(first_run, second_run.frozen_inputs, _confirmation(first_run, ["A", "C"])),
                         {"A", "C"}, "an edit outside the sections leaves both parts preservable")
        second = self.finish(second_run, self.kept_entry(first_run, first["C"], "run-0", "attempt-a"))
        self.assertEqual(second["C"].kept_from, {"run": "run-0", "attempt": "attempt-a", "files": {"README.md": r1}})

        same_run = self.prepare(scope={"A"}, changed_files={"README.md": second_readme})
        third = self.finish(same_run, self.kept_entry(second_run, second["C"], "run-1", "attempt-b"))
        self.assertEqual(third["C"].kept_from, {"run": "run-0", "attempt": "attempt-a", "files": {"README.md": r1}},
                         "kept again without a further edit, it still names the first attempt's README")

        third_readme = intro_edited(second_readme, "Edited once more.\n\n")
        edited_run = self.prepare(scope={"A"}, changed_files={"README.md": third_readme})
        again = self.finish(edited_run, self.kept_entry(second_run, second["C"], "run-1", "attempt-b"))
        self.assertEqual(again["C"].kept_from, {"run": "run-0", "attempt": "attempt-a", "files": {"README.md": r1}})

        judged = self.finish(self.prepare(changed_files={"README.md": third_readme}))
        self.assertIsNone(judged["C"].kept_from, "a part judged again has no origin to name")

    def test_a_chain_without_readme_edits_names_its_origin_and_no_file(self):
        first_run = self.prepare()
        first = self.finish(first_run)
        second_run = self.prepare(scope={"A"})
        second = self.finish(second_run, self.kept_entry(first_run, first["C"], "run-0", "attempt-a"))
        self.assertEqual(second["C"].kept_from, {"run": "run-0", "attempt": "attempt-a", "files": {}})
        third = self.finish(self.prepare(scope={"A"}), self.kept_entry(second_run, second["C"], "run-1", "attempt-b"))
        self.assertEqual(third["C"].kept_from, {"run": "run-0", "attempt": "attempt-a", "files": {}})

    def test_the_report_names_the_origin_and_reads_back_the_binding(self):
        from harness_forge.report import render_report
        first_run = self.prepare()
        first = self.finish(first_run)
        second_run = self.prepare(scope={"A"}, changed_files={"README.md": intro_edited(self.readme)})
        results = list(self.finish(second_run, self.kept_entry(first_run, first["C"], "run-0", "attempt-a")).values())
        text = render_report(second_run, results)
        self.assertIn("- Kept results cite these files as attempt attempt-a of run run-0 checked them: `README.md` "
                      f"sha256 {first_run.frozen_inputs['files']['README.md'][:12]}", text)
        run, parsed = earlier_run(text)
        self.assertEqual(run.readme_handoff_only, ["A", "C"])
        self.assertEqual(run.frozen_inputs["readme"], second_run.frozen_inputs["readme"])
        self.assertEqual(parsed["C"]["kept_from"]["attempt"], "attempt-a")


def _confirmation(run, part_ids):
    from harness_forge.models import Confirmation
    return Confirmation(run_id="run-2", contract_digest=run.contract_digest, part_ids=part_ids)


if __name__ == "__main__":
    unittest.main()
