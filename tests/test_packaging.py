"""The plugin package: manifests, skills, runtime files, snapshot identity and the bundled dependencies."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from harness_forge import bundle, environment

FORGE = Path(__file__).resolve().parents[1]
SKILLS = {"harness-intake", "harness-research", "build-skill", "harness-verify"}
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
# What the four skills need at run time; tests, tools, evals and packaging notes are development files.
RUNTIME = ("scripts/forge.py", "src/harness_forge/__init__.py", "src/harness_forge/bundle.py",
           "snapshot/kb-manifest.json", "schemas", "templates/skill", "workers/researcher.md",
           "workers/source-support.md", "workers/reviewer.md", "vendor/manifest.json", "vendor/draft-07.schema.json",
           "vendor/yaml", "vendor/markdown_it", "vendor/mdurl", "vendor/fastjsonschema", "vendor/licenses", "LICENSE",
           "LICENSE-OUTPUT-EXCEPTION", "README.md")
# Run with no site-packages, so only the standard library and the bundle can be imported.
BARE_PYTHON = (sys.executable, "-I", "-S")
# Components that run code when a harness installs or loads a plugin.
EXECUTABLE_COMPONENTS = ("hooks", "hooks.json", ".mcp.json", "mcp.json", ".lsp.json", "bin", "monitors",
                         "settings.json")
EXECUTABLE_KEYS = {"hooks", "mcpServers", "lspServers", "monitors", "channels", "experimental"}
# The owner's personal setup (profile names, home folder, email addresses, the local note) is listed only in this
# git-ignored file, so the check itself publishes none of it.
PERSONAL_MARKERS = FORGE / "personal-markers.txt"


def tracked() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=FORGE, capture_output=True, check=True).stdout
    return sorted(path for path in listed.decode().split("\0") if path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def frontmatter_name(skill: Path) -> str:
    lines = (skill / "SKILL.md").read_text(encoding="utf-8").splitlines()
    return next(line.split(":", 1)[1].strip() for line in lines[1:lines.index("---", 1)] if line.startswith("name:"))


def snapshot_digest(root: Path) -> str:
    """One digest over every snapshot file, by relative path and content."""
    digest = hashlib.sha256()
    for path in sorted(p for p in (root / "snapshot").rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def install_copy(destination: Path) -> Path:
    """What a marketplace install with source "./" copies: the repository's tracked files."""
    for relative in tracked():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FORGE / relative, target)
    return destination


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.version = tomllib.loads((FORGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.codex = read_json(FORGE / "plugin.json")
        self.claude = read_json(FORGE / ".claude-plugin" / "plugin.json")
        self.marketplace = read_json(FORGE / ".claude-plugin" / "marketplace.json")

    def test_both_manifests_name_the_same_plugin_version(self):
        self.assertEqual(self.codex["$schema"], AGENT_PLUGINS_SCHEMA)
        for manifest in (self.codex, self.claude):
            self.assertEqual(manifest["name"], "harness-forge")
            self.assertEqual(manifest["version"], self.version)
            self.assertEqual(manifest["license"], "AGPL-3.0-only")
        self.assertNotEqual(self.version, "0.0.0", "a published plugin needs a real version")
        for key in ("description", "repository", "homepage"):
            self.assertEqual(self.codex.get(key), self.claude.get(key), key)

    def test_the_marketplace_lists_the_plugin_at_the_repository_root(self):
        self.assertEqual(self.marketplace["name"], "harness-forge")
        self.assertIn("name", self.marketplace["owner"])
        (entry,) = self.marketplace["plugins"]
        self.assertEqual((entry["name"], entry["source"], entry["version"]), ("harness-forge", "./", self.version))
        # Codex requires these per entry; Claude Code tolerates them.
        self.assertEqual(entry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertTrue(entry["category"])

    def test_no_component_runs_code_on_install_or_load(self):
        for name in EXECUTABLE_COMPONENTS:
            self.assertFalse((FORGE / name).exists(), name)
            self.assertFalse((FORGE / ".claude-plugin" / name).exists(), name)
        for manifest in (self.codex, self.claude, *self.marketplace["plugins"]):
            self.assertFalse(EXECUTABLE_KEYS & set(manifest), manifest["name"])


class SkillInventoryTest(unittest.TestCase):
    def test_exactly_the_four_forge_skills(self):
        skill_names = {path.name for path in (FORGE / "skills").iterdir() if path.is_dir()}
        self.assertEqual(skill_names, {"harness-intake", "harness-research", "build-skill", "harness-verify"})
        for name in skill_names:
            self.assertEqual(frontmatter_name(FORGE / "skills" / name), name)

    def test_every_skill_checks_the_bundle_first_and_downloads_nothing(self):
        for name in SKILLS:
            text = (FORGE / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("python3 scripts/forge.py environment", text, name)
            self.assertLess(text.index("scripts/forge.py environment"), text.index("## Procedure"), name)
            for gone in ("scripts/setup.py", "PyPI", "requirements.lock"):
                self.assertNotIn(gone, text, name)


class InstalledCopyTest(unittest.TestCase):
    def test_runtime_files_are_tracked(self):
        files = tracked()
        for path in RUNTIME:
            self.assertTrue(path in files or any(f.startswith(path + "/") for f in files), path)

    def test_an_installed_copy_has_the_same_snapshot_and_validates(self):
        with tempfile.TemporaryDirectory(prefix="forge-install-") as directory:
            installed = install_copy(Path(directory) / "harness-forge")
            home = {environment.HOME_VARIABLE: str(Path(directory) / "home")}
            installed_snapshot_digest = snapshot_digest(installed)
            source_snapshot_digest = snapshot_digest(FORGE)
            self.assertEqual(installed_snapshot_digest, source_snapshot_digest)
            manifest = read_json(installed / "snapshot" / "kb-manifest.json")
            for relative, digest in manifest["files"].items():
                data = (installed / "snapshot" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, relative)
            completed = subprocess.run([*BARE_PYTHON, str(installed / "scripts" / "forge.py"), "validate-kb",
                                        "--kb", str(installed / "snapshot")],
                                       cwd=directory, capture_output=True, text=True, timeout=120,
                                       env={**os.environ, **home})
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_hash_checked_files_survive_a_checkout_that_converts_line_endings(self):
        # vendor/, snapshot/ and schemas/ are compared byte for byte, so git must not convert them (.gitattributes).
        with tempfile.TemporaryDirectory(prefix="forge-clone-") as directory:
            clone = Path(directory) / "clone"
            subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "--quiet", "--local", "--no-checkout",
                            str(FORGE), str(clone)], check=True, timeout=120)
            subprocess.run(["git", "-c", "core.autocrlf=true", "-C", str(clone), "checkout", "--quiet",
                            subprocess.run(["git", "rev-parse", "HEAD"], cwd=FORGE, capture_output=True, text=True,
                                           check=True).stdout.strip()], check=True, timeout=120)
            for tree in ("vendor", "snapshot", "schemas"):
                for path in sorted((clone / tree).rglob("*")):
                    if path.is_file():
                        relative = path.relative_to(clone).as_posix()
                        committed = subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=FORGE, capture_output=True,
                                                   check=True).stdout
                        self.assertEqual(path.read_bytes(), committed, relative)

    def test_published_files_name_no_owner_setup(self):
        if not PERSONAL_MARKERS.exists():
            self.skipTest("the owner's publication check needs the local personal-markers.txt")
        markers = [line.strip().lower() for line in PERSONAL_MARKERS.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        self.assertTrue(markers)
        naming = set()
        for relative in tracked():
            text = (FORGE / relative).read_bytes().decode("utf-8", "replace").lower()
            if any(marker in text for marker in markers):
                naming.add(relative)
        self.assertEqual(naming, set())


NOTICE = "Licensed under AGPLv3 together with the Harness Forge Generated Output"
# Files whose material Forge copies into a user's repository; the snapshot's rule files are the knowledge base's.
# Every module whose text can reach Output carries the notice: the templates, the rule files and all of
# Forge's code with content, whose reasons and labels the report copies.
NOTICE_FILES = ("templates/skill/body.md.tmpl", "templates/skill/requirements.md.tmpl",
                "templates/skill/openai.yaml.tmpl", "snapshot/rules/skills.yaml", "snapshot/rules/security.yaml",
                "snapshot/rules/outside-controls.yaml", "snapshot/rules/selection.yaml", "scripts/forge.py",
                *sorted(path.relative_to(FORGE).as_posix() for path in (FORGE / "src/harness_forge").rglob("*.py")
                        if path.read_text(encoding="utf-8").strip()))


class OutputExceptionTest(unittest.TestCase):
    def test_the_approved_exception_is_its_own_file(self):
        approved = (FORGE / "legal" / "generated-output-permission.md").read_text(encoding="utf-8")
        start = approved.index("```text\nHARNESS FORGE GENERATED OUTPUT EXCEPTION") + len("```text\n")
        text = approved[start:approved.index("```", start)]
        exception = (FORGE / "LICENSE-OUTPUT-EXCEPTION").read_text(encoding="utf-8")
        self.assertEqual(exception, text)
        self.assertIn(hashlib.sha256(exception.encode()).hexdigest(), approved, "the record names the text's sha256")
        self.assertTrue((FORGE / "LICENSE").read_text(encoding="utf-8").startswith(
            "                    GNU AFFERO GENERAL PUBLIC LICENSE"), "LICENSE stays the AGPL text")
        self.assertNotIn("GENERATED OUTPUT EXCEPTION", (FORGE / "LICENSE").read_text(encoding="utf-8"))

    def test_files_copied_into_output_carry_the_notice(self):
        for relative in NOTICE_FILES:
            text = (FORGE / relative).read_text(encoding="utf-8")
            self.assertIn("SPDX-License-Identifier: AGPL-3.0-only", text, relative)
            self.assertIn(NOTICE, " ".join(text.split()), relative)

    def test_a_built_skill_carries_no_notice(self):
        from tests.test_build_skill import NAME
        from tests.test_verify import build_files, skill_decision

        files, _ = build_files(skill_decision())
        self.assertTrue(any(path.startswith(f".agents/skills/{NAME}/") for path in files))
        for path, data in files.items():
            self.assertNotIn(b"SPDX-License-Identifier", data, path)
            self.assertNotIn(b"Generated Output Exception", data, path)


class DataDirectoryTest(unittest.TestCase):
    def test_the_default_home_is_a_user_data_directory(self):
        self.assertEqual(environment.forge_home({"HOME": "/home/u"}, platform="linux"),
                         Path("/home/u/.local/share/harness-forge"))
        self.assertEqual(environment.forge_home({"HOME": "/home/u", "XDG_DATA_HOME": "/data"}, platform="linux"),
                         Path("/data/harness-forge"))
        self.assertEqual(environment.forge_home({environment.HOME_VARIABLE: "/forge"}), Path("/forge"))


def vendor_copy(destination: Path) -> Path:
    shutil.copytree(bundle.VENDOR, destination, ignore=shutil.ignore_patterns("__pycache__"))
    return destination


class BundleTest(unittest.TestCase):
    def test_the_bundle_matches_its_manifest(self):
        self.assertEqual(bundle.problems(), [])
        completed = subprocess.run([*BARE_PYTHON, str(FORGE / "tools" / "vendor.py"), "--check"], cwd=FORGE,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["ok"])

    def test_missing_changed_and_extra_files_are_named(self):
        with tempfile.TemporaryDirectory(prefix="forge-vendor-") as directory:
            vendor = vendor_copy(Path(directory) / "vendor")
            (vendor / "yaml" / "reader.py").unlink()
            (vendor / "fastjsonschema" / "draft07.py").write_text("raise SystemExit", encoding="utf-8")
            (vendor / "mdurl" / "extra.py").write_text("", encoding="utf-8")
            (vendor / "yaml" / "__pycache__").mkdir(exist_ok=True)
            (vendor / "yaml" / "__pycache__" / "x.pyc").write_bytes(b"")
            self.assertEqual(bundle.problems(vendor=vendor), [
                "vendor/fastjsonschema/draft07.py differs from the manifest",
                "vendor/yaml/reader.py is missing",
                "vendor/mdurl/extra.py is not part of the bundle"])
            (vendor / "manifest.json").unlink()
            self.assertIn("vendor/manifest.json is missing", bundle.problems(vendor=vendor)[0])

    def test_stray_compiled_files_links_and_broken_manifests_are_named(self):
        with tempfile.TemporaryDirectory(prefix="forge-vendor-") as directory:
            vendor = vendor_copy(Path(directory) / "vendor")
            (vendor / "yaml" / "evil.pyc").write_bytes(b"importable without source")
            (vendor / "mdurl" / "linked").symlink_to(vendor / "yaml")
            found = bundle.problems(vendor=vendor)
            self.assertIn("vendor/yaml/evil.pyc is not part of the bundle", found)
            self.assertIn("vendor/mdurl/linked is a link, which the bundle never contains", found)
            for broken in ("[]", '{"files": []}', '{"files": {"a.py": 5}}', "{not json"):
                (vendor / "manifest.json").write_text(broken, encoding="utf-8")
                (problem,) = bundle.problems(vendor=vendor, check_python=False)
                self.assertTrue(problem.startswith("vendor/manifest.json is missing or unreadable"), broken)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_file_is_named_not_raised(self):
        with tempfile.TemporaryDirectory(prefix="forge-vendor-") as directory:
            vendor = vendor_copy(Path(directory) / "vendor")
            target = vendor / "yaml" / "reader.py"
            target.chmod(0)
            try:
                self.assertEqual(bundle.problems(vendor=vendor, check_python=False)[0][:42],
                                 "vendor/yaml/reader.py cannot be read (Perm")
            finally:
                target.chmod(0o644)

    def test_activation_moves_vendor_to_the_front(self):
        script = ("import sys; sys.path.append(sys.argv[2]); sys.path.insert(0, sys.argv[1]); import harness_forge; "
                  "print(sys.path.index(sys.argv[2]), sys.path.count(sys.argv[2]))")
        completed = subprocess.run([*BARE_PYTHON, "-c", script, str(FORGE / "src"), str(bundle.VENDOR)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.stdout.split(), ["0", "1"], completed.stderr)

    def test_an_old_python_is_named(self):
        found = bundle.problems(version=(3, 10, 14))
        self.assertEqual(found, ["Python 3.10.14 is too old; Forge needs Python 3.11 or newer"])
        self.assertIn("Python 3.11 or newer", bundle.repair(found))
        self.assertIn("Reinstall or update", bundle.repair(["vendor/yaml/reader.py is missing"]))

    def test_forge_stops_on_a_damaged_bundle_without_downloading(self):
        with tempfile.TemporaryDirectory(prefix="forge-install-") as directory:
            installed = install_copy(Path(directory) / "harness-forge")
            (installed / "vendor" / "yaml" / "reader.py").unlink()
            completed = subprocess.run([*BARE_PYTHON, str(installed / "scripts" / "forge.py"), "environment"],
                                       cwd=directory, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual((report["command"], report["ok"]), ("environment", False))
        self.assertEqual(report["problems"], ["vendor/yaml/reader.py is missing"])
        self.assertIn("Reinstall or update the Harness Forge plugin", report["repair"])

    def test_the_environment_command_reports_the_bundled_dependencies(self):
        completed = subprocess.run([*BARE_PYTHON, str(FORGE / "scripts" / "forge.py"), "environment"],
                                   cwd=FORGE, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual([d["name"] for d in report["dependencies"]],
                         ["PyYAML", "markdown-it-py", "mdurl", "fastjsonschema", "JSON Schema Draft-07 metaschema"])
        self.assertEqual(report["bundled_files"], len(bundle.expected_files()))

    def test_bundled_packages_come_from_vendor(self):
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import harness_forge, json; "
                  "from harness_forge import bundle, documents; print(json.dumps(bundle.origins()))")
        completed = subprocess.run([*BARE_PYTHON, "-c", script, str(FORGE / "src")], capture_output=True, text=True,
                                   timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for name, origin in json.loads(completed.stdout).items():
            self.assertTrue(Path(origin).resolve().is_relative_to(bundle.VENDOR), (name, origin))

    def test_a_package_imported_before_forge_from_elsewhere_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="forge-shadow-") as directory:
            (Path(directory) / "yaml").mkdir()
            (Path(directory) / "yaml" / "__init__.py").write_text("", encoding="utf-8")
            script = ("import sys; sys.path.insert(0, sys.argv[1]); import yaml; sys.path.insert(0, sys.argv[2]); "
                      "import harness_forge")
            completed = subprocess.run([*BARE_PYTHON, "-c", script, directory, str(FORGE / "src")],
                                       capture_output=True, text=True, timeout=60)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("yaml was imported from", completed.stderr)
        self.assertIn("Forge uses only its bundled copy in vendor/", completed.stderr)

    def test_pyproject_declares_no_downloaded_dependency(self):
        project = tomllib.loads((FORGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["dependencies"], [])
        for gone in ("scripts/setup.py", "requirements.lock", "packaging/requirements.in", "tools/lock_requirements.py"):
            self.assertFalse((FORGE / gone).exists(), gone)


if __name__ == "__main__":
    unittest.main()
