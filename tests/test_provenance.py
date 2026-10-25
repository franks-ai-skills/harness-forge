"""The Harness Forge copy that builds a part and the one that verifies it (#52, #55)."""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness_forge.build_skill import build_outputs, mark_built, write_skill
from harness_forge.checks.outside import FORGE_URL
from harness_forge.contracts import render_contract
from harness_forge.documents import validate
from harness_forge.provenance import UNKNOWN, collect, describe, forge_provenance, verifier
from harness_forge.report import recorded_builds, render_report, report_data
from harness_forge.verify import bound_inputs, finish_run, prepare_run, validate_review
from tests.builders import EVIDENCE
from tests.test_build_skill import plan
from tests.test_verify import REVISION, RULES, Scenario, build_files, passing_review, skill_decision

FORGE = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
BUILDER = {"version": "0.2.0", "source": "checkout", "commit": "a" * 40, "tree": "clean"}
VERIFIER = {"version": "0.3.0", "source": UNKNOWN, "commit": UNKNOWN, "tree": UNKNOWN}


def git(directory, *arguments):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                    "-C", str(directory), *arguments], check=True, capture_output=True,
                   env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")})


def copy_with_manifest(directory: Path, version="0.2.0") -> Path:
    (directory / ".claude-plugin").mkdir(parents=True)
    (directory / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return directory


@unittest.skipUnless(GIT, "git is not installed")
class CollectTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="forge-provenance-")
        self.root = Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()

    def checkout(self):
        root = copy_with_manifest(self.root / "forge")
        git(root, "init", "-q")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "x")
        return root

    def test_a_clean_checkout(self):
        root = self.checkout()
        found = collect(root)
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        self.assertEqual(found, {"version": "0.2.0", "source": "checkout", "commit": head.strip(), "tree": "clean"})
        self.assertEqual(describe(found), f"0.2.0 (checkout {head[:7]}, clean)")

    def test_changed_and_untracked_files_are_uncommitted_changes(self):
        root = self.checkout()
        (root / "new.txt").write_text("x", encoding="utf-8")
        self.assertEqual(collect(root)["tree"], "uncommitted changes")
        (root / "new.txt").unlink()
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "0.2.1"}), encoding="utf-8")
        self.assertEqual((collect(root)["version"], collect(root)["tree"]), ("0.2.1", "uncommitted changes"))

    def test_a_copy_without_git_metadata_has_an_unknown_source(self):
        root = copy_with_manifest(self.root / "installed")
        self.assertEqual(collect(root), {"version": "0.2.0", "source": UNKNOWN, "commit": UNKNOWN, "tree": UNKNOWN})
        self.assertEqual(describe(collect(root)), "0.2.0 (source unknown)")

    def test_a_copy_inside_another_repository_is_not_its_checkout(self):
        git(self.root, "init", "-q")
        root = copy_with_manifest(self.root / "cache" / "harness-forge")
        self.assertEqual(collect(root)["source"], UNKNOWN)

    def test_each_value_is_unknown_on_its_own(self):
        root = self.checkout()
        answers = {"rev-parse --show-toplevel": str(root) + "\n", "rev-parse HEAD": None, "status": "?? x\n"}

        def fake(where, *arguments):
            return next((value for key, value in answers.items() if " ".join(arguments).startswith(key)), None)
        self.assertEqual(collect(root, git=fake), {"version": "0.2.0", "source": "checkout", "commit": UNKNOWN,
                                                   "tree": "uncommitted changes"})
        answers.update({"rev-parse HEAD": "b" * 40 + "\n", "status": None})
        found = collect(root, git=fake)
        self.assertEqual((found["commit"], found["tree"]), ("b" * 40, UNKNOWN))
        self.assertEqual(describe(found), "0.2.0 (checkout bbbbbbb, changes unknown)")

    def test_git_missing_or_failing_and_no_manifest(self):
        root = self.root / "bare"
        root.mkdir()
        self.assertEqual(collect(root, git=lambda *a: None), {key: UNKNOWN for key in ("version", "source", "commit",
                                                                                         "tree")})
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text("[]", encoding="utf-8")
        self.assertEqual(collect(root, git=lambda *a: None)["version"], UNKNOWN)

    def test_git_does_not_follow_the_callers_git_variables(self):
        root = self.checkout()
        other = self.root / "other"
        other.mkdir()
        git(other, "init", "-q")
        os.environ["GIT_DIR"] = str(other / ".git")
        try:
            self.assertEqual(collect(root)["source"], "checkout")
        finally:
            del os.environ["GIT_DIR"]

    def test_this_copy_reports_its_version(self):
        version = json.loads((FORGE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(forge_provenance()["version"], version)


class BuildRecordTest(unittest.TestCase):
    def test_the_build_records_the_bytes_and_links_it_left(self):
        planned = plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, planned)
            outputs = build_outputs(root, planned)
        for key, value in planned.items():
            expected = (f"link:{value}" if key.startswith(".claude/")
                        else f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}")
            self.assertEqual(outputs[key], expected)

    def test_a_rebuild_replaces_the_record_and_a_build_without_one_keeps_it(self):
        record = skill_decision(status="decided")
        first = mark_built(record, "A", {"forge": BUILDER, "outputs": {"x/SKILL.md": "sha256:" + "0" * 64}})
        validate(first.record, "decision")
        self.assertEqual(first.part("A")["build"]["forge"], BUILDER)
        again = mark_built(first, "A", {"forge": VERIFIER, "outputs": {"x/SKILL.md": "sha256:" + "1" * 64}})
        self.assertEqual(again.part("A")["build"]["forge"], VERIFIER)
        self.assertEqual(mark_built(first, "A").part("A")["build"], first.part("A")["build"])

    def test_an_older_record_without_a_build_stays_valid(self):
        validate(skill_decision().record, "decision")

    def test_the_contract_does_not_bind_the_build_record_but_verification_does(self):
        record = skill_decision()
        built = mark_built(record, "A", {"forge": BUILDER, "outputs": {"x/SKILL.md": "sha256:" + "0" * 64}})
        built.part("A")["status"] = record.part("A")["status"]
        self.assertEqual(render_contract(record, REVISION, [], worker_evidence=EVIDENCE).digest,
                         render_contract(built, REVISION, [], worker_evidence=EVIDENCE).digest)
        self.assertNotEqual(bound_inputs(record, {}, RULES, revision=REVISION)["parts"]["A"],
                            bound_inputs(built, {}, RULES, revision=REVISION)["parts"]["A"])


def built_record(**changes):
    record = skill_decision()
    files, links = build_files(record)
    outputs = {**{path: "sha256:" + hashlib.sha256(data).hexdigest() for path, data in files.items()},
               **{path: f"link:{target}" for path, target in links.items()}}
    outputs.update(changes)
    record.part("A")["build"] = {"forge": BUILDER, "outputs": outputs}
    return record


class ReportTest(unittest.TestCase):
    def report(self, record, forge=VERIFIER, files=None, symlinks=None):
        scenario = Scenario(record, files=files, symlinks=symlinks)
        run = prepare_run(record, scenario.files, RULES, scenario.confirmation, symlinks=scenario.symlinks,
                          revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE, forge=forge)
        results = finish_run(run, validate_review(run, passing_review(run)), [],
                             bound_inputs(record, scenario.files, RULES, symlinks=scenario.symlinks,
                                          revision=REVISION, kb_version="0.1.0", worker_evidence=EVIDENCE))
        return run, results, render_report(run, results)

    def test_the_verifier_and_the_recorded_builder_are_separate(self):
        run, results, rendered = self.report(built_record())
        self.assertIn(f"- [Harness Forge]({FORGE_URL}) verifier: 0.3.0 (source unknown)", rendered)
        self.assertIn(f"- Recorded builder: Harness Forge 0.2.0 (checkout {'a' * 7}, clean)", rendered)
        self.assertIn("- Built outputs: match that build", rendered)
        data = report_data(run, results)
        self.assertEqual(verifier(data["tool_versions"]), VERIFIER)
        self.assertEqual(data["builds"], {"A": {"forge": BUILDER, "outputs_changed": []}})
        self.assertIn("harness-forge-checkers", data["tool_versions"])

    def test_an_edited_missing_or_retyped_output_is_changed(self):
        record = built_record()
        files, links = build_files(record)
        skill = next(path for path in files if path.endswith("SKILL.md"))
        link = next(iter(links))
        edited = {**files, skill: files[skill] + b"\nEdited by hand.\n"}
        _, _, rendered = self.report(record, files=edited, symlinks={link: "../../elsewhere"})
        self.assertIn(f"- Built outputs: changed since that build: {', '.join(sorted((link, skill)))}", rendered)
        builds = recorded_builds(self.report(record, symlinks={})[0])
        self.assertEqual(builds["A"]["outputs_changed"], [link])

    def test_no_build_record_leaves_builder_and_outputs_unknown(self):
        run, results, rendered = self.report(skill_decision())
        self.assertIn("- Recorded builder: unknown (no build record)", rendered)
        self.assertIn("- Built outputs: unknown (no build record)", rendered)
        self.assertNotIn("match that build", rendered)
        self.assertNotIn("builds", report_data(run, results))

    def test_a_build_record_changed_during_verification_invalidates_the_result(self):
        record = built_record()
        scenario = Scenario(record)
        self.assertEqual(scenario.finish()["A"].harness_result, "verified")
        changed = copy.deepcopy(record)
        changed.part("A")["build"]["forge"] = VERIFIER
        current = bound_inputs(changed, scenario.files, RULES, symlinks=scenario.symlinks, revision=REVISION,
                               kb_version="0.1.0", worker_evidence=EVIDENCE)
        self.assertNotEqual(scenario.finish(current=current)["A"].harness_result, "verified")

    def test_a_run_prepared_without_provenance_collects_this_copys_once(self):
        scenario = Scenario(skill_decision())
        self.assertEqual(verifier(scenario.run.tool_versions), forge_provenance())

    def test_an_older_run_without_verifier_entries_renders_unknown(self):
        run, results, _ = self.report(built_record())
        older = copy.deepcopy(run)
        older.tool_versions = {"harness-forge-checkers": run.tool_versions["harness-forge-checkers"]}
        self.assertIn(f"- [Harness Forge]({FORGE_URL}) verifier: unknown", render_report(older, results))


if __name__ == "__main__":
    unittest.main()
