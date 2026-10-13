"""The plugin package: manifests, skills, runtime files, snapshot identity and the Forge environment."""

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

from harness_forge import environment

FORGE = Path(__file__).resolve().parents[1]
SKILLS = {"harness-intake", "harness-research", "build-skill", "harness-verify"}
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
# What the four skills need at run time; tests, tools, evals and packaging notes are development files.
RUNTIME = ("scripts/forge.py", "scripts/setup.py", "src/harness_forge/__init__.py", "snapshot/kb-manifest.json",
           "schemas", "templates/skill", "workers/researcher.md", "workers/source-support.md", "workers/reviewer.md",
           "requirements.lock", "LICENSE", "README.md")
# Components that run code when a harness installs or loads a plugin.
EXECUTABLE_COMPONENTS = ("hooks", "hooks.json", ".mcp.json", "mcp.json", ".lsp.json", "bin", "monitors",
                         "settings.json")
EXECUTABLE_KEYS = {"hooks", "mcpServers", "lspServers", "monitors", "channels", "experimental"}
# The owner's personal setup (profile names, home folder, email addresses, the local note) is listed only in this
# git-ignored file, so the check itself publishes none of it.
PERSONAL_MARKERS = FORGE / "personal-markers.txt"
# Historical evidence that names personal profiles; the owner decides before publication whether it is reworded,
# kept unpublished or published as it is. No other tracked file may name the owner's setup.
AWAITING_OWNER_DECISION = {
    "evals/canaries/results/diagnostic-2026-10-08-index.json",
    "evals/canaries/results/diagnostic-2026-10-08-resumed-index.json",
    "evals/canaries/results/diagnostic-2026-10-08.md",
    "evals/canaries/results/offline-init-2026-10-08-index.json",
    "evals/canaries/results/offline-init-2026-10-08.md",
}


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


def fake_environment(root: Path, environ: dict) -> Path:
    """A finished Forge environment whose python is the checkout's development interpreter, which can import
    Forge's locked packages; the real one comes from scripts/setup.py."""
    directory = environment.environment_dir(root, environ)
    python = environment.python_of(directory)
    python.parent.mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked interpreter would not find the development environment's packages.
    python.write_text(f'#!/bin/sh\nexec "{FORGE / ".venv" / "bin" / "python"}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    (directory / environment.MARKER).write_text(environment.lock_digest(root), encoding="utf-8")
    return directory


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

    def test_every_skill_says_how_forge_finds_its_environment(self):
        for name in SKILLS:
            text = (FORGE / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("python3", text, name)
            self.assertIn("scripts/setup.py", text, name)
            self.assertNotIn("environment's Python", text, name)


class InstalledCopyTest(unittest.TestCase):
    def test_runtime_files_are_tracked(self):
        files = tracked()
        for path in RUNTIME:
            self.assertTrue(path in files or any(f.startswith(path + "/") for f in files), path)

    def test_an_installed_copy_has_the_same_snapshot_and_validates(self):
        with tempfile.TemporaryDirectory(prefix="forge-install-") as directory:
            installed = install_copy(Path(directory) / "harness-forge")
            home = {environment.HOME_VARIABLE: str(Path(directory) / "home")}
            fake_environment(installed, home)
            installed_snapshot_digest = snapshot_digest(installed)
            source_snapshot_digest = snapshot_digest(FORGE)
            self.assertEqual(installed_snapshot_digest, source_snapshot_digest)
            manifest = read_json(installed / "snapshot" / "kb-manifest.json")
            for relative, digest in manifest["files"].items():
                data = (installed / "snapshot" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, relative)
            completed = subprocess.run([sys.executable, str(installed / "scripts" / "forge.py"), "validate-kb",
                                        "--kb", str(installed / "snapshot")],
                                       cwd=directory, capture_output=True, text=True, timeout=120,
                                       env={**os.environ, **home})
            self.assertEqual(completed.returncode, 0, completed.stderr)

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
        self.assertEqual(naming, AWAITING_OWNER_DECISION)


class EnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-home-")
        self.home = Path(self.temporary.name)
        self.environ = {environment.HOME_VARIABLE: str(self.home)}

    def tearDown(self):
        self.temporary.cleanup()

    def make_environment(self, *, complete=True) -> Path:
        directory = environment.environment_dir(FORGE, self.environ)
        python = environment.python_of(directory)
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        if complete:
            (directory / environment.MARKER).write_text(environment.lock_digest(FORGE), encoding="utf-8")
        return directory

    def test_the_environment_is_keyed_by_the_lock(self):
        directory = environment.environment_dir(FORGE, self.environ)
        self.assertEqual(directory.parent, self.home)
        self.assertEqual(directory.name, "env-" + environment.lock_digest(FORGE))
        self.assertEqual(environment.lock_digest(FORGE),
                         hashlib.sha256((FORGE / "requirements.lock").read_bytes()).hexdigest()[:16])

    def test_the_default_home_is_a_user_data_directory(self):
        self.assertEqual(environment.forge_home({"HOME": "/h"}, platform="darwin"),
                         Path("/h/.local/share/harness-forge"))
        self.assertEqual(environment.forge_home({"HOME": "/h", "XDG_DATA_HOME": "/x"}, platform="linux"),
                         Path("/x/harness-forge"))
        self.assertEqual(environment.forge_home({"LOCALAPPDATA": "C:/L"}, platform="win32"),
                         Path("C:/L/harness-forge"))

    def test_without_an_environment_forge_names_the_setup_command(self):
        decision, detail = environment.decide(FORGE, "/some/other/python", self.environ)
        self.assertEqual(decision, "missing")
        self.assertIn(str(FORGE / "scripts" / "setup.py"), detail)

    def test_an_unfinished_environment_counts_as_missing(self):
        self.make_environment(complete=False)
        self.assertEqual(environment.decide(FORGE, "/some/other/python", self.environ)[0], "missing")

    def test_a_finished_environment_runs_forge(self):
        directory = self.make_environment()
        self.assertEqual(environment.decide(FORGE, "/some/other/python", self.environ),
                         ("exec", str(environment.python_of(directory))))
        self.assertEqual(environment.decide(FORGE, str(directory), self.environ), ("run", None))
        switched = {**self.environ, environment.ACTIVE_VARIABLE: str(directory)}
        self.assertEqual(environment.decide(FORGE, "/some/other/python", switched), ("run", None))

    def test_the_checkout_development_environment_runs_forge(self):
        self.assertEqual(environment.decide(FORGE, str(FORGE / ".venv"), self.environ), ("run", None))

    def test_another_virtual_environment_does_not_count(self):
        self.assertEqual(environment.decide(FORGE, str(self.home / "venv"), self.environ)[0], "missing")

    def test_forge_started_outside_its_environment_reports_setup(self):
        base = Path(getattr(sys, "_base_executable", sys.executable))
        completed = subprocess.run([str(base), "-I", str(FORGE / "scripts" / "forge.py"), "validate-kb", "--kb",
                                    str(FORGE / "snapshot")], capture_output=True, text=True, timeout=60,
                                   env={**os.environ, **self.environ})
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("scripts/setup.py", completed.stderr)
        self.assertIn("ask the user", completed.stderr.lower())

    def test_forge_started_outside_its_environment_switches_to_it(self):
        base = Path(getattr(sys, "_base_executable", sys.executable))
        fake_environment(FORGE, self.environ)
        completed = subprocess.run([str(base), "-I", str(FORGE / "scripts" / "forge.py"), "validate-kb", "--kb",
                                    str(FORGE / "snapshot")], capture_output=True, text=True, timeout=120,
                                   env={**os.environ, **self.environ})
        self.assertEqual(completed.returncode, 0, completed.stderr)


def load_setup():
    sys.path.insert(0, str(FORGE / "scripts"))
    try:
        import setup
    finally:
        sys.path.remove(str(FORGE / "scripts"))
    return setup


def fake_venv(path):
    python = environment.python_of(path)
    python.parent.mkdir(parents=True)
    python.touch()


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.setup = load_setup()
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-setup-")
        self.environ = {environment.HOME_VARIABLE: self.temporary.name, "PIP_TARGET": "/elsewhere",
                        "PIP_CONFIG_FILE": "/elsewhere/pip.conf", "PATH": os.environ.get("PATH", "")}
        self.target = environment.environment_dir(FORGE, self.environ)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, *, install=0, imports=0):
        def run(command, **options):
            self.calls.append({"command": command, "env": options.get("env"), "cwd": options.get("cwd"),
                               "locked": self.setup.lock_path(self.target).exists(), "target": self.target.exists()})
            code = install if "install" in command else imports
            return subprocess.CompletedProcess(command, code, "", "" if code == 0 else "failed")
        return run

    def test_setup_installs_exactly_the_locked_wheels_with_pip_isolated(self):
        python = Path("/env/bin/python")
        self.assertEqual(self.setup.install_command(python, FORGE / "requirements.lock"),
                         [str(python), "-I", "-m", "pip", "--isolated", "--no-input", "install", "--require-hashes",
                          "--only-binary", ":all:", "--no-deps", "-r", str(FORGE / "requirements.lock")])
        offline = self.setup.install_command(python, FORGE / "requirements.lock", wheelhouse=Path("/w"))
        self.assertEqual(offline[offline.index("--find-links") - 1:offline.index("--find-links") + 2],
                         ["--no-index", "--find-links", "/w"])

    def test_pip_settings_from_the_environment_never_reach_the_install(self):
        result = self.setup.setup(FORGE, self.environ, run=self.runner(), create=fake_venv)
        self.assertTrue(result["ok"], result)
        for call in self.calls:
            self.assertEqual([name for name in call["env"] if name.startswith("PIP_")], ["PIP_CONFIG_FILE"])

    def test_no_pip_configuration_file_or_working_directory_module_takes_part(self):
        """--isolated skips user configuration only; a pip.py or ensurepip.py in the caller's directory would run."""
        seen = []
        result = self.setup.setup(FORGE, self.environ, run=self.runner(),
                                  create=lambda path: (seen.append(os.getcwd()), fake_venv(path)))
        self.assertTrue(result["ok"], result)
        home = os.path.realpath(self.target.parent)
        self.assertEqual([os.path.realpath(directory) for directory in seen], [home])
        for call in self.calls:
            self.assertEqual(call["env"]["PIP_CONFIG_FILE"], os.devnull)
            self.assertEqual(os.path.realpath(call["cwd"]), home)
            self.assertEqual(call["command"][1], "-I")
        self.assertNotEqual(os.path.realpath(os.getcwd()), home, "the caller's directory is restored")

    def test_relative_paths_mean_the_callers_directory(self):
        caller = os.getcwd()
        os.chdir(self.temporary.name)
        try:
            environ = {**self.environ, environment.HOME_VARIABLE: "relative-home"}
            result = self.setup.setup(FORGE, environ, run=self.runner(), create=fake_venv, wheelhouse=Path("wheels"))
        finally:
            os.chdir(caller)
        self.assertTrue(result["ok"], result)
        home = Path(self.temporary.name).resolve() / "relative-home"
        self.assertEqual(Path(result["environment"]).resolve().parent, home)
        install = self.calls[0]["command"]
        self.assertEqual(Path(install[install.index("--find-links") + 1]).resolve(),
                         Path(self.temporary.name).resolve() / "wheels")
        self.assertTrue((Path(result["environment"]) / environment.MARKER).exists())

    def test_a_home_through_a_link_and_dot_dot_gets_one_real_environment(self):
        """venv normalizes `link/..` lexically while the file system follows the link: setup resolves first."""
        base = Path(self.temporary.name).resolve()
        (base / "deep" / "inner").mkdir(parents=True)
        (base / "linked").symlink_to(base / "deep" / "inner")
        environ = {**self.environ, environment.HOME_VARIABLE: str(base / "linked" / ".." / "forge-home")}
        result = self.setup.setup(FORGE, environ, run=self.runner())
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["environment"]).parent, base / "deep" / "forge-home")
        self.assertTrue(environment.python_of(Path(result["environment"])).exists())
        self.assertFalse((base / "forge-home").exists(), "nothing is built where venv's spelling points")

    def test_an_install_whose_packages_cannot_be_imported_is_not_finished(self):
        result = self.setup.setup(FORGE, self.environ, run=self.runner(imports=1), create=fake_venv)
        self.assertFalse(result["ok"])
        self.assertEqual(environment.decide(FORGE, "/x", self.environ)[0], "missing")
        self.assertFalse(self.target.exists(), "a failed setup leaves nothing")

    def test_setup_builds_at_the_final_path_under_a_lock(self):
        created = []
        result = self.setup.setup(FORGE, self.environ, run=self.runner(),
                                  create=lambda path: (created.append(path), fake_venv(path)))
        self.assertTrue(result["ok"], result)
        self.assertEqual([path.resolve() for path in created], [self.target.resolve()],
                         "a virtual environment cannot be moved after creation")
        self.assertTrue(all(call["locked"] for call in self.calls))
        self.assertFalse(self.setup.lock_path(self.target).exists(), "the lock is released")
        self.assertEqual(environment.decide(FORGE, "/x", self.environ)[0], "exec")
        again = self.setup.setup(FORGE, self.environ, run=self.runner(install=1), create=None)
        self.assertTrue(again["ok"] and again["existing"], "an existing environment is reused")

    def test_a_running_setup_is_left_alone(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.setup.lock_path(self.target).mkdir()
        fake_venv(self.target)
        result = self.setup.setup(FORGE, self.environ, run=self.runner(), create=fake_venv)
        self.assertFalse(result["ok"])
        self.assertIn("another setup", result["error"])
        self.assertTrue(environment.python_of(self.target).exists(), "nothing of the other setup is deleted")
        self.assertEqual(self.calls, [])

    def test_an_environment_finished_while_waiting_for_the_lock_is_reused(self):
        """Another setup finishes between the first check and taking the lock: the second check sees it."""
        checks = []
        real_complete = environment.complete

        def complete(root, directory):
            checks.append(directory)
            if len(checks) == 1:  # the other setup finishes right after this first check
                fake_venv(directory)
                (directory / environment.MARKER).write_text(environment.lock_digest(FORGE), encoding="utf-8")
                return False
            return real_complete(root, directory)

        def never(path):
            raise AssertionError("must not create")

        original = self.setup.environment.complete
        self.setup.environment.complete = complete
        try:
            result = self.setup.setup(FORGE, self.environ, run=self.runner(install=1), create=never)
        finally:
            self.setup.environment.complete = original
        self.assertTrue(result["ok"] and result["existing"], result)
        self.assertEqual(len(checks), 2, "checked again after taking the lock")
        self.assertEqual(self.calls, [])
        self.assertTrue((self.target / environment.MARKER).exists(), "the other setup's environment survives")
        self.assertFalse(self.setup.lock_path(self.target).exists())

    def test_something_else_at_the_target_is_reported_not_deleted(self):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text("not an environment", encoding="utf-8")
        result = self.setup.setup(FORGE, self.environ, run=self.runner(), create=fake_venv)
        self.assertFalse(result["ok"])
        self.assertIn(str(self.target), result["error"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "not an environment")


if __name__ == "__main__":
    unittest.main()
