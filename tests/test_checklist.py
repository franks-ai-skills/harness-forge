"""An instruction-style Requirements section from the build model's checklist (issue #49)."""

import os
import re
import tempfile
import unittest
from pathlib import Path

from harness_forge.build_skill import CHECKLIST_MAX_CHARS, plan_skill, write_skill
from harness_forge.contracts import confirm_contract, render_contract
from harness_forge.research import accept_requirements
from tests.builders import EVIDENCE
from tests.test_build_skill import (DESCRIPTION, NAME, PINNED, PROCEDURE, REVISION, idea_rule, plan,
                                    reference_decision)

SKILL = f".agents/skills/{NAME}/SKILL.md"
REFERENCE = f".agents/skills/{NAME}/references/requirements.md"
CHECKLIST = {
    "idea.repro.a.environment": "Name the environment the bug was observed in",
    "idea.repro.a.numbered-steps": "Number each reproduction step.",
    "idea.repro.a.expected-observed": "Do not merge expected and observed behavior into one line",
}

# One requirement per kind, each with a condition the checklist line has to keep.
KINDS = [
    idea_rule("environment", "When the report names a platform, the checklist names its version"),
    idea_rule("no-tokens", "The checklist asks for access tokens, unless the user pasted a redacted example",
              kind="must-not"),
    idea_rule("short", "The checklist fits on one screen when the bug has at most five steps", kind="should"),
    idea_rule("screenshots", "The checklist asks for screenshots for a command-line bug", kind="should-not"),
]
KINDS_CHECKLIST = {
    "idea.repro.a.environment": "When the report names a platform, name its version",
    "idea.repro.a.no-tokens": "ask for access tokens, unless the user pasted a redacted example",
    "idea.repro.a.short": "fit the checklist on one screen when the bug has at most five steps",
    "idea.repro.a.screenshots": "asking for screenshots for a command-line bug",
}


def kinds_plan(checklist):
    revision = accept_requirements(KINDS, {rule.id: "accept" for rule in KINDS}, "b" * 64)
    record = reference_decision()
    record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
    contract = render_contract(record, revision, [], worker_evidence=EVIDENCE)
    relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
    return plan_skill(record, "A", revision, PINNED, worker_evidence=EVIDENCE, description=DESCRIPTION,
                      procedure=PROCEDURE, confirmation=confirm_contract(contract, relay, run_id="run-1"),
                      checklist=checklist)


class ChecklistTest(unittest.TestCase):
    def test_the_checklist_replaces_the_statements_and_each_requirement_is_stated_once(self):
        """#146: the reference keeps id, kind and sources by number; the accepted wording stays in the revision."""
        planned = plan(checklist=CHECKLIST)
        skill_md, reference = planned[SKILL], planned[REFERENCE]
        self.assertIn("## Requirements\n\n1. Name the environment the bug was observed in.\n"
                      "2. Number each reproduction step.\n"
                      "3. Do not merge expected and observed behavior into one line.\n", skill_md)
        self.assertIn("1. `idea.repro.a.environment` (must): https://docs.example-vendor.com/bug-reports", reference)
        self.assertIn(f"accepted revision with\nsha256 {REVISION.digest}", reference)
        self.assertIn("need the rule id or the source behind one of the requirements above", skill_md)
        self.assertNotIn("exact accepted wording", skill_md)
        both = "".join(planned[path] for path in (SKILL, REFERENCE))
        self.assertEqual(both.count("The checklist names the environment"), 0)
        self.assertEqual(both.count("Name the environment"), 1)

    def test_a_build_leads_to_its_exact_wording_after_a_later_acceptance(self):
        """#146: the reference names the revision's sha256; after a later acceptance replaces the rules file, the
        history holds that revision only if it was committed, as here, and its digest identifies it."""
        import subprocess

        from harness_forge.research import load_revision, write_revision
        reference = plan(checklist=CHECKLIST)[REFERENCE]
        named = re.search(r"accepted revision with\nsha256 ([0-9a-f]{64})", reference).group(1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            git = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": directory,
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            run = lambda *argv: subprocess.run(["git", *argv], cwd=root, env=git, check=True, capture_output=True,
                                               text=True).stdout
            run("init", "-q")
            path = write_revision(root, "repro", REVISION)
            run("add", "-A")
            run("commit", "-q", "-m", "accept")
            later = accept_requirements([idea_rule("other", "The checklist names the other thing")],
                                        {"idea.repro.a.other": "accept"}, "c" * 64, part_id="A")
            write_revision(root, "repro", later)
            run("commit", "-qam", "accept again")
            self.assertNotEqual(load_revision(path).digest, named, "the current file no longer holds it")
            relative = path.relative_to(root).as_posix()
            found = None
            for commit in run("log", "--format=%H", "--", relative).split():
                (root / "earlier.yaml").write_text(run("show", f"{commit}:{relative}"), encoding="utf-8")
                if load_revision(root / "earlier.yaml").digest == named:
                    found = load_revision(root / "earlier.yaml")
            self.assertIsNotNone(found)
            self.assertIn("The checklist names the environment.",
                          [entry["rule"]["statement"] for entry in found.rules])

    def test_each_kind_keeps_its_label_and_the_line_keeps_its_condition(self):
        skill_md = kinds_plan(KINDS_CHECKLIST)[SKILL]
        self.assertIn("1. When the report names a platform, name its version.\n"
                      "2. Never: ask for access tokens, unless the user pasted a redacted example.\n"
                      "3. Prefer to: fit the checklist on one screen when the bug has at most five steps.\n"
                      "4. Avoid: asking for screenshots for a command-line bug.\n", skill_md)

    def test_the_exact_wording_stays_in_the_revision_not_in_the_reference(self):
        """#146: with a checklist the reference names ids, kinds and sources; the revision keeps the wording."""
        exact = [idea_rule("command", "The checklist quotes `a  b` as written; it ends with a colon:"),
                 idea_rule("lines", "The checklist lists\n  - the platform\n  - the version")]
        revision = accept_requirements(exact, {rule.id: "accept" for rule in exact}, "b" * 64)
        record = reference_decision()
        record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": revision.rule_ids}
        contract = render_contract(record, revision, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        reference = plan_skill(record, "A", revision, PINNED, worker_evidence=EVIDENCE, description=DESCRIPTION,
                               procedure=PROCEDURE, confirmation=confirm_contract(contract, relay, run_id="run-1"),
                               checklist={"idea.repro.a.command": "Quote `a  b` as written",
                                          "idea.repro.a.lines": "List the platform and the version"})[REFERENCE]
        self.assertNotIn("The checklist quotes", reference)
        self.assertNotIn("the platform\n", reference)
        self.assertRegex(reference, r"1\. `idea\.repro\.a\.command` \(must\): \S+")
        self.assertEqual([entry["rule"]["statement"] for entry in revision.rules],
                         ["The checklist quotes `a  b` as written; it ends with a colon:",
                          "The checklist lists\n  - the platform\n  - the version"])

    def test_without_a_checklist_the_statements_stay_in_skill_md(self):
        planned = plan()
        self.assertIn("1. The checklist names the environment.", planned[SKILL])
        self.assertIn("1. `idea.repro.a.environment` (must): https://docs.example-vendor.com/bug-reports",
                      planned[REFERENCE])

    def test_a_part_without_accepted_requirements_takes_only_an_empty_checklist(self):
        revision = accept_requirements([], {}, "b" * 64, part_id="A")
        record = reference_decision()
        record.record["accepted_revision"] = {"digest": revision.digest, "rule_ids": []}
        contract = render_contract(record, revision, [], worker_evidence=EVIDENCE)
        relay = {"choice": "confirm", "displayed_digest": contract.digest, "part_ids": ["A"], "run_id": "run-1"}
        arguments = {"worker_evidence": EVIDENCE, "description": DESCRIPTION, "procedure": PROCEDURE,
                     "confirmation": confirm_contract(contract, relay, run_id="run-1")}
        planned = plan_skill(record, "A", revision, PINNED, checklist={}, **arguments)
        self.assertNotIn("## Requirements", planned[SKILL])
        with self.assertRaisesRegex(ValueError, "not accepted idea.repro.a.environment"):
            plan_skill(record, "A", revision, PINNED, checklist={"idea.repro.a.environment": "Name it"}, **arguments)

    def test_a_malformed_checklist_is_refused(self):
        long = "Name the environment " + "x" * CHECKLIST_MAX_CHARS
        cases = {
            "must map each accepted": ["Name the environment"],
            "missing idea.repro.a.numbered-steps": {k: v for k, v in CHECKLIST.items() if "numbered" not in k},
            "not accepted idea.repro.a.attach-logs": {**CHECKLIST, "idea.repro.a.attach-logs": "Upload logs"},
            "non-empty text": {**CHECKLIST, "idea.repro.a.environment": "  "},
            "must be non-empty text": {**CHECKLIST, "idea.repro.a.environment": 7},
            "one line": {**CHECKLIST, "idea.repro.a.environment": "Name the\nenvironment"},
            f"longer than {CHECKLIST_MAX_CHARS}": {**CHECKLIST, "idea.repro.a.environment": long},
            "placeholder": {**CHECKLIST, "idea.repro.a.environment": "Name $ARGUMENTS"},
            "own obligation word": {**CHECKLIST, "idea.repro.a.environment": "Always: name the environment"},
        }
        for message, checklist in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                plan(checklist=checklist)

    def test_a_label_or_obligation_word_cannot_repeat_or_reverse_the_kinds_label(self):
        for rule_id, line in (("idea.repro.a.no-tokens", "Never ask for access tokens"),
                              ("idea.repro.a.no-tokens", "Do not ask for access tokens"),
                              ("idea.repro.a.short", "Should fit on one screen"),
                              ("idea.repro.a.screenshots", "Avoid: screenshots"),
                              ("idea.repro.a.environment", "Never: skip the version")):
            with self.subTest(line), self.assertRaisesRegex(ValueError, "own obligation word"):
                kinds_plan({**KINDS_CHECKLIST, rule_id: line})

    def test_a_must_line_may_be_a_negative_instruction(self):
        skill_md = plan(checklist=CHECKLIST)[SKILL]
        self.assertIn("3. Do not merge expected and observed behavior into one line.", skill_md)

    def test_a_rebuild_with_the_same_checklist_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, plan(checklist=CHECKLIST))
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            write_skill(root, plan(checklist=CHECKLIST))
            self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)

    def test_the_contract_binding_is_unchanged(self):
        self.assertEqual(REVISION.rule_ids, ["idea.repro.a.environment", "idea.repro.a.numbered-steps",
                                             "idea.repro.a.expected-observed"])
        # The checklist is presentation only: the same revision builds with or without it.
        self.assertEqual(set(plan(checklist=CHECKLIST)), set(plan()))


if __name__ == "__main__":
    unittest.main()
