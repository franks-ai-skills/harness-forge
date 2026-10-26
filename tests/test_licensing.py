"""The licensing-information note (#58) and the offer of Forge's own license (#91)."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness_forge.contracts import load_decision, update_readme, write_decision
from harness_forge.documents import validate
from harness_forge import licensing
from harness_forge.licensing import GUIDANCE, LICENSE_SECTION, LICENSE_SHA256, OFFER, applies, check
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
        # Every repository is inspected; only the public-reuse question needs public sharing.
        result = check(record(), self.root)
        self.assertEqual((result["status"], result["ask"], result["applies"]), ("missing", False, False))
        self.assertEqual(len(result["checked"]), 4)
        self.assertEqual(result["offer"], OFFER)

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
        self.assertEqual((result["status"], result["ask"], result["offer"]), ("incomplete", True, None),
                         "a location behind a link was not read: nothing is offered over it")

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



class LicenseOfferTest(unittest.TestCase):
    """Forge offers its own license, AGPL-3.0-only, and adds it exactly, only after "Yes, please" (#91)."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-license-")))
        self.decision = skill_decision()
        self.path = write_decision(self.root, self.decision, slug="repro", date="2026-10-10")
        self.write("README.md", "# Repro\n\nUse it.\n")

    def write(self, name, text="x\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def add(self, *, python=(sys.executable,), path=None):
        completed = subprocess.run([*python, str(FORGE / "scripts" / "forge.py"), "license", "--decision",
                                    str(path or self.path), "--root", str(self.root), "--add", "agpl-3.0-only"],
                                   capture_output=True, text=True, timeout=60)
        return completed.returncode, json.loads(completed.stdout) if completed.stdout else None, completed.stderr

    def snapshot(self):
        return sorted((str(p.relative_to(self.root)), p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                      for p in self.root.rglob("*"))

    def test_the_offer_text_is_fixed_and_names_two_unmarked_options_in_order(self):
        self.assertEqual(OFFER["options"], ["Yes, please", "No, thanks"])
        self.assertEqual(OFFER["question"], "Would you like Harness Forge to add this license and its README "
                                            "declaration to this repository?")
        self.assertIn("GNU Affero General Public License v3.0 only (`AGPL-3.0-only`), the license Harness Forge "
                      "itself uses.", OFFER["explanation"])
        self.assertIn("This is not legal advice.", OFFER["explanation"])
        self.assertTrue(any("Generated Output Exception will not be copied" in line for line in OFFER["explanation"]))
        for visibility, reach in (("private", "repository"), ("public", "repository"), ("unknown", "local")):
            with self.subTest(visibility=visibility, reach=reach):
                result = check(record(reach=reach, visibility=visibility), self.root)
                self.assertEqual(result["offer"], OFFER)

    def test_yes_adds_forges_license_byte_for_byte_with_the_readme_declaration(self):
        body = load_decision(self.path).body
        code, output, stderr = self.add(python=(sys.executable, "-I", "-S"))
        self.assertEqual(code, 0, stderr)
        written = (self.root / "LICENSE").read_bytes()
        self.assertEqual(written, (FORGE / "LICENSE").read_bytes())
        self.assertEqual(hashlib.sha256(written).hexdigest(), LICENSE_SHA256)
        self.assertEqual(output["written"], ["LICENSE", "README.md"])
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertEqual(readme, "# Repro\n\nUse it.\n\n" + LICENSE_SECTION)
        self.assertIn("this repository is licensed under the GNU Affero General Public License v3.0 only "
                      "([AGPL-3.0-only](LICENSE)). Third-party material retains its applicable terms.", readme)
        recorded = load_decision(self.path)
        self.assertEqual(recorded.body, body)
        self.assertEqual(recorded.record["licensing"], {"answer": "added", "license": "AGPL-3.0-only",
                                                        "sha256": LICENSE_SHA256, "basis": output["recorded"]["basis"]})
        self.assertEqual({key: value for key, value in recorded.record.items() if key != "licensing"},
                         self.decision.record, "every other field stays")
        after = check(recorded.record, self.root)
        self.assertEqual((after["status"], after["offer"], after["basis"]), ("identified", None,
                                                                             output["recorded"]["basis"]))
        self.assertFalse((self.root / "LICENSE-OUTPUT-EXCEPTION").exists())
        code, _, stderr = self.add()
        self.assertEqual(code, 2)
        self.assertIn("not eligible", stderr)

    def test_a_missing_readme_is_created_with_the_section(self):
        (self.root / "README.md").unlink()
        code, _, stderr = self.add()
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"),
                         f"# {self.decision.record['idea']}\n\n" + LICENSE_SECTION)

    def test_existing_licensing_or_an_answer_for_this_basis_prevents_the_offer(self):
        target = self.write("elsewhere/terms.txt", "MIT\n")
        cases = {
            "license file": lambda: self.write("license.txt"),
            "licence variant": lambda: self.write("LICENCE-MIT"),
            "copying": lambda: self.write("COPYING.md"),
            "licenses dir": lambda: (self.root / "LICENSES").mkdir(),
            "dangling link": lambda: os.symlink(self.root / "missing", self.root / "LICENSE"),
            "link": lambda: os.symlink(target, self.root / "COPYING"),
            "readme heading": lambda: self.write("README.md", "# Repro\n\n## Licensing\n\nAsk us.\n"),
            "readme link": lambda: ((self.root / "README.md").unlink(), os.symlink(target, self.root / "README.md")),
        }
        for name, arrange in cases.items():
            with self.subTest(name):
                arrange()
                before = self.snapshot()
                self.assertIsNone(check(self.decision.record, self.root)["offer"])
                code, _, stderr = self.add()
                self.assertEqual(code, 2, stderr)
                self.assertEqual(self.snapshot(), before, "a refusal changes nothing")
                self.assertEqual(target.read_text(encoding="utf-8"), "MIT\n")
                for path in self.root.iterdir():
                    if path.name not in (".harness", "elsewhere"):
                        if path.is_dir() and not path.is_symlink():
                            path.rmdir()
                        else:
                            path.unlink()
                self.write("README.md", "# Repro\n\nUse it.\n")
        basis = check(self.decision.record, self.root)["basis"]
        for answer in ({"answer": "declined", "basis": basis}, {"answer": "continue", "basis": basis}):
            with self.subTest(answer=answer["answer"]):
                answered = {**self.decision.record, "licensing": answer}
                validate(answered, "decision")
                self.assertIsNone(check(answered, self.root)["offer"])

    def test_a_directory_holding_a_licensing_name_is_still_refused(self):
        for name in ("LICENSE", "license.d", "Copying"):
            with self.subTest(name):
                (self.root / name).mkdir()
                before = self.snapshot()
                code, _, stderr = self.add()
                self.assertEqual(code, 2, stderr)
                self.assertIn("already exists; nothing added", stderr)
                self.assertEqual(self.snapshot(), before)
                (self.root / name).rmdir()

    def test_an_altered_bundled_license_is_refused_before_anything_is_written(self):
        original = licensing.FORGE_LICENSE
        altered = self.write("forge-copy/LICENSE", "not the AGPL\n")
        before = self.snapshot()
        licensing.FORGE_LICENSE = altered
        try:
            with self.assertRaisesRegex(licensing.LicenseNotAdded, "sha256 differs"):
                licensing.add_license(self.decision.record, self.root)
        finally:
            licensing.FORGE_LICENSE = original
        self.assertEqual(self.snapshot(), before)

    def test_an_unclosed_fence_or_a_linked_record_refuses_before_writing(self):
        self.write("README.md", "# Repro\n\n```\nopen fence\n")
        before = self.snapshot()
        code, _, stderr = self.add()
        self.assertEqual(code, 2)
        self.assertIn("unclosed code fence", stderr)
        self.assertEqual(self.snapshot(), before)
        self.write("README.md", "# Repro\n")
        other = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-license-other-")))
        elsewhere = write_decision(other, self.decision, slug="repro", date="2026-10-10")
        before = self.snapshot()
        code, _, stderr = self.add(path=elsewhere)
        self.assertEqual(code, 2)
        self.assertIn("is not a decision record of", stderr)
        self.assertEqual(self.snapshot(), before)

    def test_a_failed_write_names_what_was_written_and_records_nothing(self):
        original = licensing._rewrite

        def failing(path, data):
            raise OSError(28, "No space left on device")

        licensing._rewrite = failing
        try:
            with self.assertRaises(licensing.LicenseNotAdded) as caught:
                licensing.add_license(self.decision.record, self.root)
        finally:
            licensing._rewrite = original
        self.assertEqual(caught.exception.written, ["LICENSE"])
        self.assertIn("already written: LICENSE", str(caught.exception))
        self.assertNotIn("licensing", load_decision(self.path).record)

    def test_a_record_that_cannot_be_written_reports_the_completed_writes(self):
        import importlib.util
        import contextlib
        import io
        specification = importlib.util.spec_from_file_location("forge_license", FORGE / "scripts" / "forge.py")
        forge = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(forge)

        def failing(*arguments, **keywords):
            raise OSError(13, "Permission denied")

        forge.write_decision = failing
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = forge.main(["license", "--decision", str(self.path), "--root", str(self.root), "--add",
                               "agpl-3.0-only"])
        self.assertEqual(code, 2)
        self.assertIn("the answer could not be recorded", stderr.getvalue())
        self.assertIn("already written: LICENSE, README.md", stderr.getvalue())
        self.assertNotIn("licensing", load_decision(self.path).record)

    def test_the_readme_command_keeps_the_license_section(self):
        self.assertEqual(self.add()[0], 0)
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        regenerated = update_readme(readme, load_decision(self.path))
        self.assertIn(LICENSE_SECTION, regenerated)

    def test_the_guidance_offers_exactly_this(self):
        text = " ".join((FORGE / "skills/harness-intake/SKILL.md").read_text(encoding="utf-8").split())
        for expected in ("ask its `question` word for word", "`Yes, please` and `No, thanks`, in that order, neither "
                         "marked recommended nor preselected", "--add agpl-3.0-only", "`answer: declined`",
                         "Never choose, replace or change a license yourself"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
