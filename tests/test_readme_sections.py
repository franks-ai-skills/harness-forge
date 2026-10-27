"""Readable README sections that name Harness Forge, and the reader that keeps the old ones (#50, #53)."""

import copy
import unittest

from harness_forge.checks.outside import FORGE_URL, KB_URL, foreign_lines
from harness_forge.checks.runner import run_check
from harness_forge.contracts import pinned_controls, pinned_kb_version, update_readme
from tests.builders import decision, part
from tests.test_build_skill import plan
from tests.test_checks import snapshot_rule

CODEOWNERS = {"control": "codeowners-agent-config", "prerequisites": ["outside.codeowners.agent-config"],
              "availability": "available", "required_for_goal": False}
GATE = {"control": "event-gate", "prerequisites": ["outside.merge.no-agent-bypass"], "availability": "unknown",
        "required_for_goal": True}
LEGACY = ("# Repro\n\nIntro by the user.\n\n## Outside controls\n\n"
          "- `codeowners-agent-config` (part A): recommended, not verified. Precaution; availability available. "
          "It works as a gate only when:\n  - Something (`outside.codeowners.agent-config`)\n\n"
          "## Where the skills load\n\n"
          "Forge builds `part-a` into this repository (`.agents/skills/<name>/`), and a skill there loads only in "
          "this repository, although the idea's reach is public. Publish it. Forge verifies the skills in this "
          "repository, not a copy.\n\n## Notes\n\nThe user's own notes.\n")


def one_part(*controls, reach="repository"):
    return decision(part("A", "Write a reproduction checklist", outside_controls=list(controls)), reach=reach,
                    facts={**decision().record["facts"], "reach": reach})


def two_parts():
    return decision(part("A", "Write a reproduction checklist", outside_controls=[CODEOWNERS]),
                    part("B", "Summarize the report", outside_controls=[]))


def coverage(readme, record):
    facts = {"outside_controls": [{"part_id": p["id"], "control": c["control"]} for p in record.record["parts"]
                                  for c in p["outside_controls"]],
             "readme_path": "README.md", "decision_part_ids": [p["id"] for p in record.record["parts"]],
             "targets": record.record["targets"]}
    return run_check(snapshot_rule("outside.readme.lists-controls"), {"README.md": readme.encode("utf-8")}, facts)


class OutsideSectionTest(unittest.TestCase):
    def test_controls_have_readable_names_and_reasons_from_the_pin_and_keep_their_ids(self):
        readme = update_readme("# Repro\n", one_part(CODEOWNERS, GATE))
        names = pinned_controls()
        for control in ("codeowners-agent-config", "event-gate"):
            self.assertIn(f"- **{names[control]['name']}** (`{control}`): recommended, not verified.", readme)
            self.assertIn(f"Why: {names[control]['reason'].rstrip('.')}.", readme)
        self.assertIn("Not recorded as required for the goal.", readme)
        self.assertIn("Required for the goal: the goal does not hold without it.", readme)
        self.assertIn("Recorded as available during intake; Harness Forge has not verified its setup or enforcement.",
                      readme)
        self.assertIn("Its availability was unknown during intake;", readme)
        self.assertNotIn("availability available", readme)
        self.assertNotIn("Precaution;", readme)

    def test_the_section_explains_outside_controls_and_links_the_pinned_guide(self):
        readme = update_readme("# Repro\n", one_part(CODEOWNERS))
        self.assertIn(f"[Harness Forge]({FORGE_URL}) recommends them from the intake answers; it does not set them "
                      "up or check them.", readme)
        self.assertIn(f"({KB_URL}/blob/v{pinned_kb_version()}/docs/guide/outside-gates.md)", readme)
        self.assertNotIn("](docs/", readme)

    def test_each_availability_reads_as_recorded_information(self):
        for availability, text in (("available", "Recorded as available during intake"),
                                   ("unavailable", "Recorded as unavailable during intake"),
                                   ("unknown", "Its availability was unknown during intake")):
            with self.subTest(availability):
                readme = update_readme("# Repro\n", one_part({**CODEOWNERS, "availability": availability}))
                self.assertIn(f"{text}; Harness Forge has not verified its setup or enforcement.", readme)

    def test_several_parts_name_the_part_and_one_part_does_not(self):
        self.assertIn("(`codeowners-agent-config`, part A): recommended, not verified. Not recorded as required for "
                      "this part's goal.", update_readme("# Repro\n", two_parts()))
        self.assertNotIn("part A", update_readme("# Repro\n", one_part(CODEOWNERS)))

    def test_a_control_the_pin_does_not_name_keeps_its_id(self):
        readme = update_readme("# Repro\n", one_part({**CODEOWNERS, "control": "custom-gate"}))
        self.assertIn("- **custom-gate** (`custom-gate`): recommended, not verified.", readme)
        self.assertNotIn("Why:", readme)

    def test_no_controls_is_still_none_recommended(self):
        readme = update_readme("# Repro\n", one_part())
        self.assertIn("## Outside controls\n\nNone recommended.\n", readme)
        self.assertNotIn("Outside controls are settings", readme)


class ReachSectionTest(unittest.TestCase):
    def test_the_reach_section_names_and_links_harness_forge_in_plain_words(self):
        readme = update_readme("# Repro\n", one_part(reach="public"))
        self.assertIn(f"[Harness Forge]({FORGE_URL}) builds `part-a` into this repository", readme)
        self.assertNotIn("reach is", readme)

    def test_one_skill_is_spoken_of_in_the_singular(self):
        # #109: "them" and "each skill's folder" had no plural to refer to.
        public = update_readme("# Repro\n", one_part(reach="public"))
        self.assertIn("into this repository (`.agents/skills/part-a/`), and a skill there loads only in this "
                      "repository, although you plan to share it publicly. This release packages no plugin: publish "
                      "this repository, and each user copies the skill's folder to their user skills location: "
                      "`~/.claude/skills/part-a/` for Claude Code and `~/.agents/skills/part-a/` for Codex. Harness "
                      "Forge verifies the skill in this repository, not a copy.", public)
        user = update_readme("# Repro\n", one_part(reach="user"))
        self.assertIn("although you want to use it in all of your repositories. To use it everywhere, copy the "
                      "skill's folder to your user skills location: `~/.claude/skills/part-a/`", user)
        for readme in (public, user):
            self.assertNotIn(" them", readme.split("## Where the skills load", 1)[1])
            self.assertNotIn("each skill", readme)

    def test_several_skills_keep_the_plural(self):
        for reach, wanted, use in (
                ("public", "although you plan to share them publicly.",
                 "and each user copies each skill's folder to their user skills location: `~/.claude/skills/<name>/`"),
                ("user", "although you want to use them in all of your repositories.",
                 "To use them everywhere, copy each skill's folder to your user skills location")):
            with self.subTest(reach=reach):
                record = decision(part("A", "Write a reproduction checklist", outside_controls=[]),
                                  part("B", "Summarize the report", outside_controls=[]), reach=reach,
                                  facts={**decision().record["facts"], "reach": reach})
                readme = update_readme("# Repro\n", record)
                self.assertIn("builds `part-a`, `part-b` into this repository (`.agents/skills/<name>/`)", readme)
                self.assertIn(wanted, readme)
                self.assertIn(use, readme)
                self.assertIn("Harness Forge verifies the skills in this repository, not a copy.", readme)

    def test_the_earlier_plural_wording_for_one_skill_is_read_and_replaced(self):
        record = one_part(reach="public")
        earlier = ("# Repro\n\n## Where the skills load\n\n"
                   f"[Harness Forge]({FORGE_URL}) builds `part-a` into this repository (`.agents/skills/<name>/`), and "
                   "a skill there loads only in this repository, although you plan to share them publicly. This "
                   "release packages no plugin: publish this repository, and each user copies each skill's folder to "
                   "their user skills location: `~/.claude/skills/<name>/` for Claude Code and "
                   "`~/.agents/skills/<name>/` for Codex. Harness Forge verifies the skills in this repository, not "
                   "a copy.\n")
        current = update_readme("# Repro\n", record)
        for readme in (earlier, current):
            section = readme[readme.index("## Where the skills load"):]
            self.assertEqual(foreign_lines(section, "Where the skills load"), [])
        upgraded = update_readme(earlier, record)
        reach = current[current.index("## Where the skills load"):]
        self.assertIn(reach, upgraded, "replaced in place by the singular wording")
        self.assertNotIn("share them publicly", upgraded)
        self.assertEqual(update_readme(upgraded, record), upgraded)


class LegacyUpgradeTest(unittest.TestCase):
    def test_an_old_section_upgrades_in_place_and_a_second_update_changes_nothing(self):
        record = one_part(CODEOWNERS, reach="public")
        upgraded = update_readme(LEGACY, record)
        self.assertNotIn("`codeowners-agent-config` (part A)", upgraded)
        self.assertNotIn("Forge builds `part-a`", upgraded)
        self.assertIn("**Code-owner review of agent configuration**", upgraded)
        for kept in ("# Repro\n\nIntro by the user.\n", "## Notes\n\nThe user's own notes.\n"):
            self.assertIn(kept, upgraded)
        self.assertEqual(update_readme(upgraded, record), upgraded)

    def test_the_checker_reads_old_and_new_sections(self):
        record = one_part(CODEOWNERS, reach="public")
        self.assertEqual(coverage(LEGACY, record).status, "pass")
        self.assertEqual(coverage(update_readme(LEGACY, record), record).status, "pass")

    def test_user_text_inside_a_section_still_stops_the_writer_and_fails_the_check(self):
        record = one_part(CODEOWNERS)
        written = update_readme("# Repro\n", record)
        edited = written.replace("None recommended.", "x").replace("- **Code-owner", "My note.\n\n- **Code-owner")
        self.assertEqual(coverage(edited, record).status, "fail")
        with self.assertRaisesRegex(ValueError, "lines Forge did not write"):
            update_readme(edited, record)


class ReaderTest(unittest.TestCase):
    def test_a_part_less_entry_counts_only_for_a_decision_with_one_part(self):
        single = update_readme("# Repro\n", one_part(CODEOWNERS))
        self.assertEqual(coverage(single, one_part(CODEOWNERS)).status, "pass")
        # A decision with two parts needs the part even when only one part has controls.
        result = coverage(single, two_parts())
        self.assertEqual(result.status, "fail")
        self.assertIn("codeowners-agent-config (part A)", result.reason)
        self.assertEqual(coverage(update_readme("# Repro\n", two_parts()), two_parts()).status, "pass")

    def test_a_name_never_stands_in_for_the_id_or_the_status(self):
        record = one_part(CODEOWNERS)
        readme = update_readme("# Repro\n", record)
        for broken in (readme.replace("(`codeowners-agent-config`)", "(`other-control`)"),
                       readme.replace("): recommended, not verified.", "): recommended.")):
            with self.subTest(broken.count("other-control")):
                self.assertEqual(coverage(broken, record).status, "fail")


class NameTest(unittest.TestCase):
    def test_the_requirements_reference_links_harness_forge(self):
        reference = next(text for path, text in plan().items() if path.endswith("references/requirements.md"))
        self.assertIn(f"[Harness Forge]({FORGE_URL})'s\nverification checks the skill against each one.", reference)
        self.assertNotIn("Forge's\nverification", reference)


if __name__ == "__main__":
    unittest.main()
