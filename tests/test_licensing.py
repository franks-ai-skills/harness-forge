"""The licensing-information note for a publicly shared result (#58)."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_forge.contracts import load_decision, update_readme, write_decision
from harness_forge.documents import validate
from harness_forge.licensing import GUIDANCE, applies, check
from tests.test_verify import skill_decision

FORGE = Path(__file__).resolve().parents[1]


def record(reach="repository", visibility="private", **extra):
    value = copy.deepcopy(skill_decision().record)
    value["reach"] = reach
    value["facts"]["reach"] = reach
    value["hosting"]["visibility"] = visibility
    value.update(extra)
    return value


class LicensingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="forge-licensing-")
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, text="x\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_it_applies_to_a_public_reach_or_a_public_repository_only(self):
        self.assertTrue(applies(record(reach="public")))
        self.assertTrue(applies(record(visibility="public")))
        self.assertFalse(applies(record(visibility="unknown")))
        self.assertFalse(applies(record()))
        result = check(record(), self.root)
        self.assertEqual((result["status"], result["ask"], result["checked"]), ("not applicable", False, []))

    def test_missing_licensing_information_asks_with_the_checked_locations(self):
        self.write("README.md", "# Repro\n\nUse it.\n")
        self.write("LICENSED_FEATURES.md")
        result = check(record(reach="public"), self.root)
        self.assertEqual((result["status"], result["ask"], result["found"]), ("missing", True, []))
        self.assertEqual(len(result["checked"]), 4)
        self.assertEqual(result["guidance"], GUIDANCE)

    def test_conventional_files_and_directories_are_identified(self):
        for name in ("LICENSE", "license.md", "LICENSE-MIT", "Licence.txt", "COPYING", "UNLICENSE", "LICENSE_APACHE"):
            with self.subTest(name):
                self.write(name)
                result = check(record(visibility="public"), self.root)
                self.assertEqual((result["status"], result["ask"]), ("identified", False))
                self.assertEqual(result["found"], [{"path": name, "kind": "license file"}])
                (self.root / name).unlink()
        (self.root / "LICENSES").mkdir()
        self.assertEqual(check(record(reach="public"), self.root)["found"],
                         [{"path": "LICENSES/", "kind": "licenses directory"}])

    def test_readme_headings_and_spdx_lines_count_outside_code_only(self):
        self.write("README.md", "# Repro\n\n```\n## License\nSPDX-License-Identifier: MIT\n```\n\n"
                                "<!--\nSPDX-License-Identifier: MIT\n-->\n\n`SPDX-License-Identifier: MIT`\n")
        self.assertEqual(check(record(reach="public"), self.root)["status"], "missing")
        self.write("README.md", "# Repro\n\n## Licence\n\nSee below.\n\nSPDX-License-Identifier: Apache-2.0\n")
        result = check(record(reach="public"), self.root)
        self.assertEqual(result["found"], [{"path": "README.md", "kind": "heading 'Licence'"},
                                           {"path": "README.md", "kind": "SPDX-License-Identifier: Apache-2.0"}])
        self.assertEqual(result["status"], "identified")

    def test_a_link_is_reported_but_not_followed_and_does_not_settle_it(self):
        target = self.write("elsewhere/terms.txt", "MIT\n")
        os.symlink(target, self.root / "LICENSE")
        result = check(record(reach="public"), self.root)
        self.assertEqual(result["found"], [{"path": "LICENSE", "kind": "link, not inspected"}])
        self.assertEqual((result["status"], result["ask"]), ("missing", True))

    def test_an_unreadable_readme_makes_the_check_incomplete(self):
        self.write("README.md").write_bytes(b"\xff\xfe not utf-8")
        result = check(record(reach="public"), self.root)
        self.assertEqual((result["status"], result["ask"]), ("incomplete", True))
        self.assertIn("README.md could not be read", result["problems"][0])

    def test_a_recorded_answer_holds_until_the_licensing_observations_change(self):
        self.write("README.md", "# Repro\n")
        first = check(record(reach="public"), self.root)
        answered = record(reach="public", licensing={"basis": first["basis"], "answer": "continue"})
        validate(answered, "decision")
        self.assertFalse(check(answered, self.root)["ask"])
        # The intake's own README sections and other edits do not ask again.
        decision = load_decision(write_decision(self.root, skill_decision(), slug="repro", date="2026-10-10"))
        self.write("README.md", update_readme("# Repro\n", decision))
        self.write("src/main.py", "print(1)\n")
        self.assertFalse(check(answered, self.root)["ask"])
        self.write("LICENSE", "MIT\n")
        (self.root / "LICENSE").unlink()
        os.symlink(self.root / "README.md", self.root / "COPYING")
        self.assertTrue(check(answered, self.root)["ask"], "a new candidate is a new observation")

    def test_a_user_supplied_location_is_recorded_as_given(self):
        basis = check(record(reach="public"), self.root)["basis"]
        validate(record(reach="public", licensing={"basis": basis, "answer": "location",
                                                   "location": "docs/TERMS.md (covers everything)"}), "decision")
        for bad in ({"basis": basis, "answer": "location"}, {"basis": basis, "answer": "location", "location": " "},
                    {"basis": basis, "answer": "continue", "location": "x"}, {"basis": "x", "answer": "continue"}):
            with self.subTest(bad), self.assertRaises(ValueError):
                validate(record(reach="public", licensing=bad), "decision")

    def test_the_command_reports_without_changing_anything(self):
        decision = skill_decision()
        decision.record["reach"] = decision.record["facts"]["reach"] = "public"
        path = write_decision(self.root, decision, slug="repro", date="2026-10-10")
        before = sorted((p, p.read_bytes()) for p in self.root.rglob("*") if p.is_file())
        completed = subprocess.run([sys.executable, "-I", "-S", str(FORGE / "scripts" / "forge.py"), "licensing",
                                    "--decision", str(path), "--root", str(self.root)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual((output["command"], output["status"], output["ask"]), ("licensing", "missing", True))
        self.assertEqual(sorted((p, p.read_bytes()) for p in self.root.rglob("*") if p.is_file()), before)


if __name__ == "__main__":
    unittest.main()
