"""#102: a Review keeps earlier results only from a report Forge recorded writing, outside the repository."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from harness_forge import written_reports
from harness_forge.written_reports import NotRecorded, check, record


class WrittenReportsTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-written-")))
        self.root = base / "repo"
        self.report = self.root / ".harness/reports/repro.md"
        self.report.parent.mkdir(parents=True)
        self.report.write_text("# Verification report\n", encoding="utf-8")
        self.environ = {"HARNESS_FORGE_HOME": str(base / "home")}

    def reason(self, environ=None):
        with self.assertRaises(NotRecorded) as raised:
            check(environ or self.environ, self.root, self.report)
        return str(raised.exception)

    def test_a_recorded_report_is_trusted_until_it_changes(self):
        self.assertIn("no record of reports it wrote for this repository", self.reason())
        record(self.environ, self.root, self.report)
        check(self.environ, self.root, self.report)
        self.report.write_text("# Verification report (edited)\n", encoding="utf-8")
        self.assertIn(".harness/reports/repro.md changed since Forge wrote it", self.reason())

    def test_each_report_path_keeps_its_own_entry(self):
        other = self.root / ".harness/reports/other.md"
        other.write_text("# Other\n", encoding="utf-8")
        record(self.environ, self.root, self.report)
        record(self.environ, self.root, other)
        check(self.environ, self.root, self.report)
        check(self.environ, self.root, other)

    def test_an_unrecorded_path_another_repository_and_a_broken_record_fail_closed(self):
        record(self.environ, self.root, self.report)
        unrecorded = self.root / ".harness/reports/new.md"
        unrecorded.write_text("# New\n", encoding="utf-8")
        with self.assertRaisesRegex(NotRecorded, "no record of writing .harness/reports/new.md"):
            check(self.environ, self.root, unrecorded)
        _, path = written_reports._paths(self.environ, self.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "repository": "/elsewhere"}), encoding="utf-8")
        self.assertIn("malformed or for another repository", self.reason())
        path.write_text("{not json", encoding="utf-8")
        self.assertIn("cannot be read", self.reason())
        path.unlink()
        target = path.with_name("elsewhere.json")
        target.write_text(json.dumps(data), encoding="utf-8")
        os.symlink(target, path)
        self.assertIn("symbolic link", self.reason())

    def test_a_data_directory_inside_the_repository_vouches_for_nothing(self):
        inside = {"HARNESS_FORGE_HOME": str(self.root / ".forge-home")}
        with self.assertRaisesRegex(NotRecorded, "lies inside the repository"):
            record(inside, self.root, self.report)
        self.assertIn("lies inside the repository", self.reason(inside))
        link = self.root.parent / "linked-home"
        os.symlink(self.root / "data", link)
        self.assertIn("lies inside the repository", self.reason({"HARNESS_FORGE_HOME": str(link)}))

    def test_an_unwritable_record_is_reported_and_leaves_the_report(self):
        blocked = self.root.parent / "blocked"
        blocked.write_text("a file where the data directory would be\n", encoding="utf-8")
        with self.assertRaisesRegex(NotRecorded, "could not record"):
            record({"HARNESS_FORGE_HOME": str(blocked)}, self.root, self.report)
        self.assertTrue(self.report.is_file())


class RecordFirstTest(unittest.TestCase):
    """#137: verify records before it writes, so a record it cannot write leaves the report and the scope alone."""

    def setUp(self):
        import importlib.util
        from types import SimpleNamespace

        from harness_forge import runfiles
        forge = Path(__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location("forge_record_first", forge / "scripts" / "forge.py")
        self.cli = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.cli)
        base = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-record-first-")))
        self.root = base / "repo"
        self.root.mkdir()
        allocated = runfiles.allocate(self.root)
        self.run_dir = Path(allocated["path"])
        self.relay = self.run_dir / "relay.json"
        self.run = SimpleNamespace(run_id=allocated["run_id"], attempt_id="20261029T120000Z-0123abcd")
        self.home = base / "home"
        self.cli.ENVIRON = {"HARNESS_FORGE_HOME": str(self.home)}
        self.target = self.root / ".harness/reports/repro.md"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# Earlier report\n", encoding="utf-8")
        record(self.cli.ENVIRON, self.root, self.target)

    def write(self, rendered="# New report\n"):
        return self.cli._write_recorded(self.root, self.target, rendered, self.relay, self.run)

    def test_a_recorded_report_is_written(self):
        written, recorded = self.write()
        self.assertEqual((written, recorded), (str(self.target), "recorded"))
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# New report\n")
        check(self.cli.ENVIRON, self.root, self.target)

    def test_a_record_that_cannot_be_replaced_leaves_report_record_and_scope(self):
        from unittest import mock
        with mock.patch.object(written_reports.os, "replace", side_effect=PermissionError("Operation not permitted")):
            written, recorded = self.write()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Earlier report\n")
        check(self.cli.ENVIRON, self.root, self.target)  # the earlier record still vouches for it: no wider scope
        fallback = self.run_dir / "report-20261029T120000Z-0123abcd.md"
        self.assertEqual(written, str(fallback))
        self.assertEqual(fallback.read_text(encoding="utf-8"), "# New report\n")
        self.assertIn("the repository's report was not updated", recorded)
        self.assertIn(f"this run's report is at {fallback.relative_to(self.root).as_posix()}", recorded)
        self.assertEqual(list(self.home.rglob("*.tmp")), [], "no temporary record is left behind")

    def test_a_record_directory_that_cannot_be_made_writes_the_fallback(self):
        self.cli.ENVIRON = {"HARNESS_FORGE_HOME": str(self.root.parent / "blocked")}
        (self.root.parent / "blocked").write_text("a file\n", encoding="utf-8")
        written, recorded = self.write()
        self.assertTrue(written.endswith("report-20261029T120000Z-0123abcd.md"))
        self.assertTrue(recorded.startswith("not recorded: Forge could not record the report"))

    def test_a_taken_fallback_name_writes_nothing_and_says_so(self):
        self.cli.ENVIRON = {"HARNESS_FORGE_HOME": str(self.root.parent / "blocked")}
        (self.root.parent / "blocked").write_text("a file\n", encoding="utf-8")
        (self.run_dir / "report-20261029T120000Z-0123abcd.md").write_text("other\n", encoding="utf-8")
        written, recorded = self.write()
        self.assertIsNone(written)
        self.assertIn("this run's report could not be written", recorded)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Earlier report\n")

    def test_a_relay_outside_the_run_directory_writes_nothing(self):
        self.cli.ENVIRON = {"HARNESS_FORGE_HOME": str(self.root.parent / "blocked")}
        (self.root.parent / "blocked").write_text("a file\n", encoding="utf-8")
        self.relay = self.root / "relay.json"
        written, recorded = self.write()
        self.assertIsNone(written)
        self.assertIn("could not be written", recorded)

    def test_equal_bytes_are_recorded_again_not_skipped(self):
        written, recorded = self.write("# Earlier report\n")
        self.assertEqual((written, recorded), (str(self.target), "recorded"))
        self.cli.ENVIRON = {"HARNESS_FORGE_HOME": str(self.root.parent / "blocked")}
        (self.root.parent / "blocked").write_text("a file\n", encoding="utf-8")
        _, recorded = self.write("# Earlier report\n")
        self.assertIn("the repository's report already holds this report", recorded)

    def test_a_report_write_that_fails_after_recording_widens_the_next_scope(self):
        from unittest import mock
        with mock.patch.object(self.cli, "write_report", side_effect=OSError("disk full")):
            written, recorded = self.write()
        self.assertIsNone(written)
        self.assertIn("recorded, but the report could not be written: disk full", recorded)
        self.assertIn("if the report does not match the record, the next verification judges every part", recorded)
        with self.assertRaisesRegex(NotRecorded, "changed since Forge wrote it"):
            check(self.cli.ENVIRON, self.root, self.target)

    def test_a_real_edit_between_runs_still_widens_the_scope(self):
        self.write()
        self.target.write_text("# New report, edited by hand\n", encoding="utf-8")
        with self.assertRaisesRegex(NotRecorded, "changed since Forge wrote it"):
            check(self.cli.ENVIRON, self.root, self.target)


class SandboxGuidanceTest(unittest.TestCase):
    def test_verify_and_research_prepare_for_a_blocking_sandbox(self):
        forge = Path(__file__).resolve().parents[1]
        for skill, command in (("harness-verify", "verify"), ("harness-research", "research")):
            body = " ".join((forge / "skills" / skill / "SKILL.md").read_text(encoding="utf-8").split())
            with self.subTest(skill=skill):
                self.assertIn("Workers need access a session sandbox may block.", body)
                self.assertIn(f"before the first `{command}`, tell the user what it needs and ask them to approve "
                              "running it outside the sandbox", body)
                self.assertIn("not every one does", body)
        verify = " ".join((forge / "skills/harness-verify/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("tell the user where this run's report is (`report`)", verify)


if __name__ == "__main__":
    unittest.main()
