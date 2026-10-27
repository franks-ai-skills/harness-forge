"""A run's files in <repository>/.harness-forge/runs/<run id>/ (#83): allocation, .gitignore and protected I/O."""

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_forge import runfiles

FORGE = Path(__file__).resolve().parents[1]


def load_forge():
    specification = importlib.util.spec_from_file_location("forge_run_files", FORGE / "scripts" / "forge.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class RunFilesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-run-files-")))
        self.outside = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-outside-")))
        self.forge = load_forge()

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.forge.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def allocate(self):
        code, output, stderr = self.cli("run-dir", "--root", self.root)
        self.assertEqual(code, 0, stderr)
        return output

    def tree(self, directory):
        return sorted(str(path.relative_to(directory)) for path in directory.rglob("*"))

    def test_allocation_creates_the_ignore_file_once_and_a_fresh_directory_per_run(self):
        (self.root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        first = self.allocate()
        self.assertRegex(first["run_id"], r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
        self.assertEqual(first["path"], os.path.join(os.path.abspath(self.root), ".harness-forge", "runs",
                                                     first["run_id"]))
        self.assertTrue(Path(first["path"]).is_dir())
        self.assertEqual(first["gitignore"], "created")
        self.assertEqual((self.root / ".harness-forge/.gitignore").read_bytes(), b"*\n")

        (self.root / ".harness-forge/.gitignore").write_bytes(b"*\n!keep\n")
        second = self.allocate()
        self.assertEqual(second["gitignore"], "kept")
        self.assertNotEqual(second["path"], first["path"])
        self.assertEqual((self.root / ".harness-forge/.gitignore").read_bytes(), b"*\n!keep\n")
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), "node_modules/\n")

    def test_an_existing_run_directory_is_refused_at_allocation(self):
        with mock.patch.object(runfiles, "RUN_ID", lambda: "20261026T000000Z-00000000"):
            first = self.allocate()
            (Path(first["path"]) / "relay.json").write_text("{}", encoding="utf-8")
            code, output, stderr = self.cli("run-dir", "--root", self.root)
        self.assertEqual((code, output), (2, None))
        self.assertIn("already exists; a run directory is allocated once", stderr)
        self.assertEqual((Path(first["path"]) / "relay.json").read_text(encoding="utf-8"), "{}")

    def test_links_and_other_files_on_the_way_are_refused(self):
        target = self.outside / "target"
        target.mkdir()
        cases = {
            "link at .harness-forge": lambda: (self.root / ".harness-forge").symlink_to(target),
            "file at .harness-forge": lambda: (self.root / ".harness-forge").write_text("x", encoding="utf-8"),
            "link at runs": lambda: ((self.root / ".harness-forge").mkdir(),
                                     (self.root / ".harness-forge/runs").symlink_to(target)),
            "file at runs": lambda: ((self.root / ".harness-forge").mkdir(),
                                     (self.root / ".harness-forge/runs").write_text("x", encoding="utf-8")),
        }
        for name, arrange in cases.items():
            with self.subTest(name):
                for path in (self.root / ".harness-forge/runs", self.root / ".harness-forge"):
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        for child in path.iterdir():
                            child.unlink()
                        path.rmdir()
                arrange()
                code, _, stderr = self.cli("run-dir", "--root", self.root)
                self.assertEqual(code, 2, stderr)
                self.assertRegex(stderr, "a symbolic link|not a directory")
                self.assertIn("only a real directory is used", stderr)
                self.assertEqual(self.tree(target), [], "nothing is created through a link")

    def test_the_ignore_file_must_be_a_regular_file_and_is_never_written_through(self):
        kept = self.outside / "kept"
        kept.write_text("original\n", encoding="utf-8")
        forge = self.root / ".harness-forge"
        cases = {
            "link": lambda: (forge / ".gitignore").symlink_to(kept),
            "dangling link": lambda: (forge / ".gitignore").symlink_to(self.outside / "missing"),
            "directory": lambda: (forge / ".gitignore").mkdir(),
        }
        for name, arrange in cases.items():
            with self.subTest(name):
                forge.mkdir(exist_ok=True)
                ignore = forge / ".gitignore"
                if ignore.is_symlink() or ignore.is_file():
                    ignore.unlink()
                elif ignore.is_dir():
                    ignore.rmdir()
                arrange()
                code, _, stderr = self.cli("run-dir", "--root", self.root)
                self.assertEqual(code, 2, stderr)
                self.assertIn("a symbolic link" if "link" in name else "not a regular file", stderr)
                self.assertEqual(kept.read_text(encoding="utf-8"), "original\n")
                self.assertFalse((self.outside / "missing").exists())
                self.assertFalse((forge / "runs").exists(), "no run directory without a usable ignore file")

    def test_tracked_files_are_counted_and_missing_git_is_no_failure(self):
        self.assertIsNone(self.allocate()["tracked_files"], "outside a work tree")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.assertEqual((self.allocate()["tracked_files"], self.allocate()["note"]), (0, None))
        tracked = self.root / ".harness-forge/notes.txt"
        tracked.write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", ".harness-forge/notes.txt"], check=True)
        output = self.allocate()
        self.assertEqual(output["tracked_files"], 1)
        self.assertIn("does not untrack them", output["note"])
        with mock.patch.object(runfiles, "GIT", str(self.outside / "no-git")):
            output = self.allocate()
        self.assertEqual((output["tracked_files"], output["note"]), (None, None))

    def test_inputs_below_the_directory_are_read_without_following_links(self):
        run = Path(self.allocate()["path"])
        secret = self.outside / "elsewhere.json"
        secret.write_text('{"answers": {}}', encoding="utf-8")
        (run / "answers.json").write_text('{"parts": []}', encoding="utf-8")
        self.assertEqual(self.forge._read_json(str(run / "answers.json")), {"parts": []})
        (run / "linked.json").symlink_to(secret)
        (self.root / ".harness-forge/runs/linked-run").symlink_to(self.outside)
        os.mkfifo(run / "pipe.json")
        (run / "large.json").write_text("[" + " " * (1024 * 1024) + "]", encoding="utf-8")
        for path, reason in ((run / "linked.json", "a symbolic link"),
                             (self.root / ".harness-forge/runs/linked-run/elsewhere.json", "a symbolic link"),
                             (run / "pipe.json", "not a regular file"),
                             (run / "large.json", "larger than 1 MiB")):
            with self.subTest(path.name):
                code, _, stderr = self.cli("select", "--input", path)
                self.assertEqual(code, 2, stderr)
                self.assertIn(reason, stderr)
                with self.assertRaisesRegex(self.forge._InvalidInput, reason):
                    self.forge._read_text(str(path))

    def test_a_link_at_the_top_directory_is_refused_for_reads(self):
        (self.outside / "runs/r").mkdir(parents=True)
        (self.outside / "runs/r/input.json").write_text("{}", encoding="utf-8")
        (self.root / ".harness-forge").symlink_to(self.outside)
        with self.assertRaisesRegex(self.forge._InvalidInput, r"\.harness-forge: a symbolic link"):
            self.forge._read_json(str(self.root / ".harness-forge/runs/r/input.json"))

    def test_a_run_file_is_created_once_and_never_overwritten(self):
        run = Path(self.allocate()["path"])
        self.assertTrue(runfiles.create(run / "details.md", "first\n"))
        with self.assertRaisesRegex(runfiles.RunFilesError, "already exists; a run file is never overwritten"):
            runfiles.create(run / "details.md", "second\n")
        self.assertEqual((run / "details.md").read_text(encoding="utf-8"), "first\n")
        kept = self.outside / "kept.md"
        kept.write_text("original\n", encoding="utf-8")
        (run / "linked.md").symlink_to(kept)
        (run / "dangling.md").symlink_to(self.outside / "missing.md")
        for name in ("linked.md", "dangling.md"):
            with self.assertRaises(runfiles.RunFilesError):
                runfiles.create(run / name, "x")
        self.assertEqual(kept.read_text(encoding="utf-8"), "original\n")
        self.assertFalse((self.outside / "missing.md").exists())
        self.assertFalse(runfiles.create(self.outside / "elsewhere.md", "x"), "paths elsewhere are not handled here")
        self.assertFalse((self.outside / "elsewhere.md").exists())

    def test_the_guidance_names_the_directory_the_ignore_rule_and_the_files(self):
        for skill in ("harness-intake", "harness-research", "build-skill", "harness-verify"):
            with self.subTest(skill):
                text = " ".join((FORGE / "skills" / skill / "SKILL.md").read_text(encoding="utf-8").split())
                self.assertIn("scripts/forge.py run-dir --root <repository>", text)
                self.assertIn(".harness-forge/", text)
                self.assertIn("symbolic link", text)
        intake = (FORGE / "skills/harness-intake/references/contract.md").read_text(encoding="utf-8")
        for name in ("workers.json", "relay.json"):
            self.assertIn(name, intake)
        self.assertIn("select-input.json", (FORGE / "skills/harness-intake/SKILL.md").read_text(encoding="utf-8"))
        self.assertNotIn("scope-relay", (FORGE / "skills/harness-verify/SKILL.md").read_text(encoding="utf-8"),
                         "Forge chooses the scope; there is no scope answer to relay (#102)")
        self.assertTrue(re.search(r"exactly the single line `\*`",
                                  " ".join((FORGE / "skills/harness-intake/SKILL.md").read_text(encoding="utf-8").split())))


if __name__ == "__main__":
    unittest.main()
