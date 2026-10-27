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


if __name__ == "__main__":
    unittest.main()
