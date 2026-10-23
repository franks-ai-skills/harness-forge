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
# Playground removed its copy after publication; the copied research is read from its history.
SIBLINGS = (PLAYGROUND / ".git").exists() and (KB / "kb-manifest.json").is_file()


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
        (self.kb / "CHANGELOG.md").write_text("# Changelog\n\n## 0.0.1 (2026-10-23)\n\n- First.\n", encoding="utf-8")
        git(self.kb, "init", "-q")
        self.commit("kb")
        git(self.kb, "tag", "v0.0.1")

    def tearDown(self):
        self._directory.cleanup()

    def commit(self, message: str) -> None:
        git(self.kb, "add", ".")
        git(self.kb, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", message)

    def assert_refused(self, message: str) -> None:
        with self.assertRaisesRegex(ValueError, message):
            self.tool.pin_snapshot(self.kb, Path(self._directory.name) / "refused")
        self.assertFalse((Path(self._directory.name) / "refused").exists())

    def test_annotated_release_tag_pins(self):
        git(self.kb, "tag", "-d", "v0.0.1")
        git(self.kb, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "tag", "-a", "v0.0.1", "-m", "0.0.1")
        manifest = self.tool.pin_snapshot(self.kb, Path(self._directory.name) / "one")
        self.assertEqual((manifest["kb_version"], manifest["kb_commit"]), ("0.0.1", git(self.kb, "rev-parse", "HEAD").strip()))

    def test_untagged_checkout_is_refused(self):
        git(self.kb, "tag", "-d", "v0.0.1")
        self.assert_refused(r"kb_version 0\.0\.1 has no release tag v0\.0\.1")

    def test_tag_of_another_version_is_refused(self):
        git(self.kb, "tag", "-d", "v0.0.1")
        git(self.kb, "tag", "v0.0.2")
        self.assert_refused(r"no release tag v0\.0\.1")

    def test_commit_after_the_release_is_refused(self):
        (self.kb / "README.md").write_text("# KB\n", encoding="utf-8")
        self.commit("after the release")
        self.assert_refused(r"HEAD [0-9a-f]{7} is not v0\.0\.1 \([0-9a-f]{7}\)")

    def test_unfinished_changelog_section_is_refused(self):
        git(self.kb, "tag", "-d", "v0.0.1")
        (self.kb / "CHANGELOG.md").write_text("# Changelog\n\n## 0.0.1 (unreleased)\n\n- First.\n", encoding="utf-8")
        self.commit("unreleased")
        git(self.kb, "tag", "v0.0.1")
        self.assert_refused(r"CHANGELOG\.md has no dated section '## 0\.0\.1 \(YYYY-MM-DD\)'")

    def test_missing_changelog_is_refused(self):
        git(self.kb, "tag", "-d", "v0.0.1")
        git(self.kb, "rm", "-q", "CHANGELOG.md")
        self.commit("no changelog")
        git(self.kb, "tag", "v0.0.1")
        self.assert_refused(r"has no committed CHANGELOG\.md")

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
        self.assert_refused("uncommitted changes in the snapshot paths")


@unittest.skipUnless((KB / "kb-manifest.json").is_file(), "needs the sibling agent-harness-kb checkout")
class SnapshotProvenanceTest(unittest.TestCase):
    """The pinned snapshot is the KB release its manifest names, whatever the sibling has checked out."""

    def test_snapshot_is_the_release_its_manifest_names(self):
        tool = load_tool()
        pinned = json.loads((FORGE / "snapshot" / "kb-manifest.json").read_text(encoding="utf-8"))
        tag = f"v{pinned['kb_version']}"
        tags = git(KB, "tag", "--list", "v*").split()
        if not tags:
            self.skipTest("the sibling KB checkout has no release tags, so the pin cannot be compared with its release")
        self.assertIn(tag, tags, f"the sibling KB has release tags, but not {tag}")
        commit = git(KB, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}").strip()
        self.assertEqual(pinned["kb_commit"], commit)
        released = json.loads(git(KB, "show", f"{commit}:kb-manifest.json"))
        self.assertEqual(released["kb_version"], pinned["kb_version"])
        files = set(git(KB, "ls-tree", "-r", "--name-only", commit, "--", *tool.SNAPSHOT_PATHS).split()) - {"kb-manifest.json"}
        self.assertEqual(set(pinned["files"]), files)
        for relative, digest in pinned["files"].items():
            with self.subTest(file=relative):
                data = subprocess.run(["git", "-C", str(KB), "show", f"{commit}:{relative}"], check=True,
                                      capture_output=True).stdout
                self.assertEqual(tool.sha256(data), digest)
                self.assertEqual(tool.sha256((FORGE / "snapshot" / relative).read_bytes()), digest)


class CoverageCompletenessTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(FORGE / "src"))
        from harness_forge.documents import validate_kb

        self.validate_kb = validate_kb
        self._directory = tempfile.TemporaryDirectory(prefix="forge-coverage-")
        self.kb = Path(self._directory.name) / "kb"
        shutil.copytree(FORGE / "tests" / "fixtures" / "minimal-kb", self.kb)

    def tearDown(self):
        self._directory.cleanup()

    def test_minimal_kb_is_fully_covered(self):
        self.assertEqual(self.validate_kb(self.kb), [])

    def test_a_missing_schema_copy_fails(self):
        (self.kb / "schemas" / "coverage.schema.json").unlink()
        errors = self.validate_kb(self.kb)
        self.assertTrue(any("schemas/coverage.schema.json: missing" in error for error in errors), errors)

    def test_uncovered_section_fails(self):
        page = self.kb / "docs" / "guide" / "skills.md"
        page.write_text(page.read_text(encoding="utf-8") + "\n## Portability\n\nNew section.\n", encoding="utf-8")
        errors = self.validate_kb(self.kb)
        self.assertTrue(any("uncovered section docs/guide/skills.md#portability" in error for error in errors), errors)

    def test_rule_missing_from_coverage_fails(self):
        coverage = self.kb / "rules" / "coverage.yaml"
        text = coverage.read_text(encoding="utf-8").replace("rules: [skills.description.length]", "status: not-a-rule\n            reason: Synthetic.")
        coverage.write_text(text, encoding="utf-8")
        errors = self.validate_kb(self.kb)
        self.assertTrue(any("skills.description.length is not in rules/coverage.yaml" in error for error in errors), errors)


@unittest.skipUnless(SIBLINGS, "needs sibling playground and agent-harness-kb checkouts")
class KbCopyTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.manifest = json.loads((KB / "kb-manifest.json").read_text(encoding="utf-8"))

    def test_kb_copy_preserves_source_bytes_and_links(self):
        # The migration commit is the one that added the manifest. The KB's
        # docs may change after it (corrections go to the KB); the copy is
        # checked as it was migrated, and links as they are now.
        migration = self.manifest["migration"]
        self.assertRegex(migration["source_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(migration["repairs"]), set(self.tool.REPAIRS))
        migrated = git(KB, "log", "--diff-filter=A", "--format=%H", "--", "kb-manifest.json").split()[-1]
        for relative, digest in migration["files"].items():
            with self.subTest(file=relative):
                source = subprocess.run(["git", "-C", str(PLAYGROUND), "show", f"{migration['source_commit']}:{relative}"],
                                        check=True, capture_output=True).stdout
                self.assertEqual(self.tool.sha256(source), digest)
                copy = subprocess.run(["git", "-C", str(KB), "show", f"{migrated}:{relative}"], check=True,
                                      capture_output=True).stdout
                self.assertEqual(copy, self.tool.apply_repairs(relative, source))
        copied = set(git(KB, "ls-tree", "-r", "--name-only", migrated, "docs").split())
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
            self.assertIn("https://github.com/franks-ai-skills/agent-harness-kb", text)
            self.assertNotRegex(text, r"(?i)frozen copy")

    def test_real_kb_validates(self):
        sys.path.insert(0, str(FORGE / "src"))
        from harness_forge.documents import validate_kb

        self.assertEqual(validate_kb(KB.resolve()), [])


if __name__ == "__main__":
    unittest.main()
