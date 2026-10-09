"""KB migration, snapshot pinning and rule coverage."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FORGE = Path(__file__).resolve().parents[1]
WORKSPACE = FORGE.parent
PLAYGROUND = WORKSPACE / "playground"
KB = WORKSPACE / "agent-harness-kb"
SIBLINGS = (PLAYGROUND / "docs").is_dir() and (KB / "kb-manifest.json").is_file()


def load_tool():
    specification = importlib.util.spec_from_file_location("pin_snapshot", FORGE / "tools" / "pin_snapshot.py")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def relative_links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    links = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if not re.match(r"^[a-z]+:", target) and not target.startswith("#"):
            links.append(target.split("#")[0])
    return [link for link in links if link]


class MigrationSourceGuardTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self._directory = tempfile.TemporaryDirectory(prefix="forge-migration-")
        self.play = Path(self._directory.name) / "playground"
        (self.play / "docs" / "guide").mkdir(parents=True)
        (self.play / "docs" / "guide" / "skills.md").write_text("# Skills\n", encoding="utf-8")
        (self.play / "docs" / "sources.md").write_text("# Sources\n", encoding="utf-8")
        self.manifest = {"migration": {"files": self.tool.research_inventory(self.play)}}

    def tearDown(self):
        self._directory.cleanup()

    def test_unchanged_research_passes(self):
        self.assertEqual(self.tool.check_migration_source(self.play, self.manifest), [])

    def test_changed_added_and_deleted_files_fail(self):
        (self.play / "docs" / "guide" / "skills.md").write_text("# Skills, edited\n", encoding="utf-8")
        (self.play / "docs" / "guide" / "hooks.md").write_text("# Hooks\n", encoding="utf-8")
        (self.play / "docs" / "sources.md").unlink()
        problems = self.tool.check_migration_source(self.play, self.manifest)
        self.assertEqual(
            sorted(problems),
            [
                "added after migration: docs/guide/hooks.md",
                "changed after migration: docs/guide/skills.md",
                "deleted after migration: docs/sources.md",
            ],
        )


class SnapshotPinTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self._directory = tempfile.TemporaryDirectory(prefix="forge-pin-")
        self.kb = Path(self._directory.name) / "kb"
        shutil.copytree(FORGE / "tests" / "fixtures" / "minimal-kb", self.kb)
        (self.kb / "kb-manifest.json").write_text(json.dumps({"kb_version": "0.0.1", "migration": None}) + "\n")
        git(self.kb, "init", "-q")
        git(self.kb, "add", ".")
        git(self.kb, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "kb")

    def tearDown(self):
        self._directory.cleanup()

    def test_pin_is_reproducible_from_the_same_commit(self):
        first = self.tool.pin_snapshot(self.kb, Path(self._directory.name) / "one")
        second = self.tool.pin_snapshot(self.kb, Path(self._directory.name) / "two")
        self.assertEqual(first, second)
        self.assertEqual(first["kb_commit"], git(self.kb, "rev-parse", "HEAD").strip())
        self.assertIn("rules/skills.yaml", first["files"])
        pinned = Path(self._directory.name) / "one" / "rules" / "skills.yaml"
        self.assertEqual(pinned.read_bytes(), (self.kb / "rules" / "skills.yaml").read_bytes())

    def test_uncommitted_kb_changes_are_refused(self):
        (self.kb / "rules" / "skills.yaml").write_text("[]\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.tool.pin_snapshot(self.kb, Path(self._directory.name) / "dirty")


@unittest.skipUnless(SIBLINGS, "needs sibling playground and agent-harness-kb checkouts")
class KbCopyTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.manifest = json.loads((KB / "kb-manifest.json").read_text(encoding="utf-8"))

    def test_kb_copy_preserves_source_bytes_and_links(self):
        migration = self.manifest["migration"]
        self.assertRegex(migration["source_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(migration["repairs"]), set(self.tool.REPAIRS))
        for relative, digest in migration["files"].items():
            with self.subTest(file=relative):
                source = (PLAYGROUND / relative).read_bytes()
                self.assertEqual(self.tool.sha256(source), digest)
                self.assertEqual((KB / relative).read_bytes(), self.tool.apply_repairs(relative, source))
        copied = {p.relative_to(KB).as_posix() for p in (KB / "docs").rglob("*") if p.is_file()}
        self.assertEqual(copied, {f for f in migration["files"] if f.startswith("docs/")})
        for page in (KB / "docs").rglob("*.md"):
            for link in relative_links(page):
                with self.subTest(page=str(page.relative_to(KB)), link=link):
                    self.assertTrue((page.parent / link).exists())
        symlink = KB / ".claude" / "skills" / "agent-harness"
        self.assertTrue(symlink.is_symlink())
        self.assertEqual(os.readlink(symlink), "../../.agents/skills/agent-harness")
        self.assertTrue((symlink / "SKILL.md").is_file())

    def test_playground_routes_research_and_corrections_to_kb(self):
        instructions = (PLAYGROUND / "AGENTS.md").read_text(encoding="utf-8")
        skill = (PLAYGROUND / ".agents" / "skills" / "agent-harness" / "SKILL.md").read_text(encoding="utf-8")
        for text in (instructions, skill):
            self.assertIn("../agent-harness-kb", text)
            self.assertRegex(text, r"(?i)frozen")

    def test_retained_research_is_unchanged(self):
        self.assertEqual(self.tool.check_migration_source(PLAYGROUND, self.manifest), [])


if __name__ == "__main__":
    unittest.main()
