"""Grouping a part's proposed requirements against the ones accepted for it before (issue #47)."""

import copy
import unittest

import yaml

from harness_forge.contracts import load_decision, write_decision
from harness_forge.documents import canonical_digest
from harness_forge.models import Rule
from harness_forge.research import accept_requirements, compare_requirements
from tests.test_pipeline import SOURCE
from tests.test_recheck import (NOTES, PLATFORM, SECRETS, VERSION, VERSIONS, RecheckCase, notes_finding,
                                requirement, versions_finding)
from tests.test_workflow import finding

BRIEF = "1" * 64


def rules(*items):
    return [Rule.from_dict(copy.deepcopy(item)) for item in items]


def kept(*sources):
    return [{"id": f"F{number}", "source": source, "claim": f"claim {number}", "quote": f"quote {number}"}
            for number, source in enumerate(sources, start=1)]


def ids(group):
    return [item["requirement"]["id"] for item in group]


class CompareTest(unittest.TestCase):
    def setUp(self):
        choices = {item["id"]: "accept" for item in (PLATFORM, VERSION, SECRETS)}
        self.previous = accept_requirements(rules(PLATFORM, VERSION, SECRETS), choices, BRIEF, part_id="A")

    def test_without_earlier_acceptance_every_proposal_is_new(self):
        groups = compare_requirements(None, rules(PLATFORM, VERSION), "A", kept(SOURCE, VERSIONS))
        self.assertEqual(ids(groups["new"]), [PLATFORM["id"], VERSION["id"]])
        self.assertEqual([groups[name] for name in ("unchanged", "unsupported", "changed", "removed")], [[]] * 4)

    def test_equal_content_with_current_provenance_is_unchanged_whatever_the_key_order(self):
        reordered = dict(reversed(list(PLATFORM.items())))
        groups = compare_requirements(self.previous, rules(reordered, VERSION, SECRETS), "A",
                                      kept(SOURCE, VERSIONS, NOTES))
        self.assertEqual(ids(groups["unchanged"]), [PLATFORM["id"], VERSION["id"], SECRETS["id"]])
        platform = groups["unchanged"][0]
        self.assertEqual(platform["missing_sources"], [])
        self.assertEqual([item["source"] for item in platform["findings"]], [SOURCE, VERSIONS])

    def test_a_source_without_provenance_makes_equal_content_unsupported(self):
        groups = compare_requirements(self.previous, rules(PLATFORM, VERSION, SECRETS), "A", kept(SOURCE, VERSIONS))
        self.assertEqual(ids(groups["unsupported"]), [SECRETS["id"]])
        self.assertEqual(groups["unsupported"][0]["missing_sources"], [NOTES])
        self.assertEqual(groups["unsupported"][0]["recheckable_sources"], [NOTES])
        self.assertEqual(groups["unsupported"][0]["findings"], [])

    def test_only_sources_an_earlier_version_cited_can_be_re_checked(self):
        added = {**PLATFORM, "sources": [SOURCE, VERSIONS, NOTES]}
        new = requirement("crash-log", [NOTES], statement="The checklist asks for the full crash log")
        groups = compare_requirements(self.previous, rules(added, new, SECRETS), "A", kept(SOURCE))
        changed, = groups["changed"]
        self.assertEqual((changed["missing_sources"], changed["recheckable_sources"]), ([VERSIONS, NOTES], [VERSIONS]))
        self.assertEqual((groups["new"][0]["missing_sources"], groups["new"][0]["recheckable_sources"]), ([NOTES], []))
        self.assertEqual(groups["unsupported"][0]["recheckable_sources"], [NOTES])

    def test_any_changed_field_or_source_order_is_changed_with_its_differences(self):
        statement = {**VERSION, "statement": "The checklist names the reproducer's version"}
        kind = {**SECRETS, "kind": "should-not"}
        order = {**PLATFORM, "sources": [VERSIONS, SOURCE]}
        groups = compare_requirements(self.previous, rules(order, statement, kind), "A", kept(SOURCE, VERSIONS))
        self.assertEqual(ids(groups["changed"]), [PLATFORM["id"], VERSION["id"], SECRETS["id"]])
        differences = [item["differences"] for item in groups["changed"]]
        self.assertEqual(differences[0], {"sources": {"before": [SOURCE, VERSIONS], "after": [VERSIONS, SOURCE]}})
        self.assertEqual(differences[1], {"statement": {"before": VERSION["statement"],
                                                        "after": statement["statement"]}})
        self.assertEqual(differences[2], {"kind": {"before": "must-not", "after": "should-not"}})
        # Provenance is reported for changed requirements too.
        self.assertEqual(groups["changed"][2]["missing_sources"], [NOTES])

    def test_a_changed_param_is_a_difference(self):
        before = {**PLATFORM, "check": "script", "checker": "skills.body-lines", "params": {"max_lines": 50}}
        after = {**before, "params": {"max_lines": 40}}
        previous = accept_requirements(rules(before), {before["id"]: "accept"}, BRIEF, part_id="A")
        groups = compare_requirements(previous, rules(after), "A", kept(SOURCE, VERSIONS))
        self.assertEqual(groups["changed"][0]["differences"],
                         {"params": {"before": {"max_lines": 50}, "after": {"max_lines": 40}}})

    def test_requirements_no_longer_proposed_are_removed_with_their_content(self):
        groups = compare_requirements(self.previous, rules(PLATFORM), "A", kept(SOURCE, VERSIONS))
        self.assertEqual(ids(groups["removed"]), [VERSION["id"], SECRETS["id"]])
        self.assertEqual(groups["removed"][1]["requirement"], SECRETS)

    def test_other_parts_requirements_are_not_compared(self):
        other = requirement("platform", [SOURCE])
        other["id"] = "idea.repro.b.platform"
        previous = accept_requirements(rules(other), {other["id"]: "accept"}, BRIEF, part_id="B")
        groups = compare_requirements(previous, rules(PLATFORM), "A", kept(SOURCE, VERSIONS))
        self.assertEqual((ids(groups["new"]), groups["removed"]), ([PLATFORM["id"]], []))

    def test_duplicate_or_foreign_proposals_are_refused(self):
        with self.assertRaisesRegex(ValueError, "use an id more than once"):
            compare_requirements(self.previous, rules(PLATFORM, PLATFORM), "A", [])
        foreign = {**PLATFORM, "id": "idea.repro.b.platform"}
        with self.assertRaisesRegex(ValueError, "belongs to part B, not A"):
            compare_requirements(self.previous, rules(foreign), "A", [])


class CompareCommandTest(RecheckCase):
    """The command reads current research and the recorded revision, and changes nothing."""

    NEW = requirement("crash-log", [NOTES], statement="The checklist asks for the full crash log")

    def compare(self, *requirements):
        proposed = self.cli.write(f"compare-{self.runs}-{len(requirements)}.json",
                                  {"requirements": list(requirements)})
        return self.cli("compare-requirements", "--decision", self.decision, "--part", "A", "--proposed", proposed,
                        "--root", self.root, "--slug", "repro")

    def accepted_then_goal_changed(self):
        """Three requirements accepted; the goal changes, and a new deep pass no longer finds the notes page."""
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        record = load_decision(self.decision)
        record.part("A")["goal"] = "Write a reproduction checklist that names versions"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        self.runner.findings = [finding(), versions_finding()]
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)

    def snapshot(self):
        return {path: path.read_bytes() for path in (self.root / ".harness").rglob("*") if path.is_file()}

    def test_proposals_after_a_changed_goal_are_grouped(self):
        self.accepted_then_goal_changed()
        statement = {**VERSION, "statement": "The checklist names the reproducer's version"}
        before = self.snapshot()
        code, output, stderr = self.compare(PLATFORM, statement, SECRETS, self.NEW)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((ids(output["unchanged"]), ids(output["changed"]), ids(output["unsupported"]),
                          ids(output["new"]), output["removed"]),
                         ([PLATFORM["id"]], [VERSION["id"]], [SECRETS["id"]], [self.NEW["id"]], []))
        self.assertEqual([item["source"] for item in output["unchanged"][0]["findings"]], [SOURCE, VERSIONS])
        self.assertEqual(output["new"][0]["missing_sources"], [NOTES])
        self.assertEqual(output["new"][0]["recheckable_sources"], [])
        self.assertIn("untrusted", output["untrusted"])
        self.assertEqual(output["brief_digest"], self.current_brief())

    def current_brief(self):
        from harness_forge.research import make_brief
        return make_brief(load_decision(self.decision), "deep", "A").digest

    def test_a_re_check_makes_restored_requirements_unchanged(self):
        self.accepted_then_goal_changed()
        code, output, _ = self.compare(PLATFORM, VERSION, SECRETS)
        self.assertEqual(ids(output["unsupported"]), [SECRETS["id"]])
        self.runner.findings = [{**notes_finding(), "requirement_id": SECRETS["id"]}]
        code, _, stderr = self.research("--write", "--recheck")
        self.assertEqual(code, 0, stderr)
        code, output, stderr = self.compare(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((ids(output["unchanged"]), output["unsupported"]),
                         ([PLATFORM["id"], VERSION["id"], SECRETS["id"]], []))
        # The normal accept takes the group's per-requirement choices.
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)

    def test_comparison_needs_deep_research_under_the_current_brief(self):
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.accept(PLATFORM, VERSION, SECRETS)
        self.assertEqual(code, 0, stderr)
        record = load_decision(self.decision)
        record.part("A")["goal"] = "Write a security checklist"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, _, stderr = self.compare(PLATFORM)
        self.assertEqual(code, 2)
        self.assertIn("no deep research for part A under its current brief", stderr)

    def test_the_requirements_file_must_be_the_recorded_one(self):
        self.accepted_then_goal_changed()
        record = load_decision(self.decision)
        record.record["accepted_revision"]["digest"] = "2" * 64
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, _, stderr = self.compare(PLATFORM)
        self.assertEqual(code, 2)
        self.assertIn("not the one the decision records", stderr)

    def test_an_older_revision_without_a_part_lists_its_rules_as_no_longer_proposed(self):
        code, _, stderr = self.research("--write")
        self.assertEqual(code, 0, stderr)
        other = copy.deepcopy({**PLATFORM, "id": "idea.repro.b.platform"})
        legacy = {"brief_digest": "0" * 64, "rules": [{"part_id": "A", "rule": copy.deepcopy(PLATFORM)},
                                                      {"part_id": "B", "rule": other}]}
        legacy["digest"] = canonical_digest(legacy)
        self.revision.write_text(yaml.safe_dump(legacy), encoding="utf-8")
        record = load_decision(self.decision)
        record.record["accepted_revision"] = {"digest": legacy["digest"], "rule_ids": [PLATFORM["id"], other["id"]]}
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, output, stderr = self.compare(PLATFORM)
        self.assertEqual(code, 0, stderr)
        # The same rule id is a new proposal and a legacy rule; each is its own item (#100).
        self.assertEqual([item["item_id"] for item in output["new"]], [f"new:{PLATFORM['id']}"])
        self.assertEqual([item["item_id"] for item in output["removed"]],
                         [f"removed:{PLATFORM['id']}", "removed:idea.repro.b.platform"])

    def test_a_foreign_proposal_is_refused(self):
        self.accepted_then_goal_changed()
        code, _, stderr = self.compare({**PLATFORM, "id": "idea.repro.b.platform"})
        self.assertEqual(code, 2)
        self.assertIn("belongs to part", stderr)


if __name__ == "__main__":
    unittest.main()
