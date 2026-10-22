"""One retry of a review reply that validation rejects, with both attempts kept as evidence (issue #30)."""

import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed, preflight
from harness_forge.contracts import update_outside_section
from harness_forge.diagnostics import Diagnostics
from harness_forge.report import render_report, report_data
from harness_forge.runtime import load_cases
from harness_forge.workflow import verify_live
from tests.test_adapters import PARENT, claude_stream
from tests.test_build_skill import REVISION
from tests.test_pipeline import ROLES, ScriptedRunner, confirmed, staged_request
from tests.test_verify import RULES, build_files, skill_decision

FORGE = Path(__file__).resolve().parents[1]


class ReviewReplies(ScriptedRunner):
    """The review worker answers each attempt as the test plans it: ok, duplicate, unknown, write or limit."""

    def __init__(self, *plans):
        super().__init__()
        self.plans, self.reviews = list(plans), []

    def __call__(self, command, *, cwd, env, stdin, timeout):
        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if not (self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin) \
                or len(self.reviews) == self.roles.count("review"):
            return answer
        self.reviews.append(staged_request(stdin))
        plan = self.plans[len(self.reviews) - 1]
        output = json.loads(json.loads(answer.stdout.splitlines()[-1])["result"])
        if plan == "duplicate":
            output["verdicts"].append(dict(output["verdicts"][0]))
        elif plan == "unknown":
            output["verdicts"][0] = {**output["verdicts"][0], "rule_id": "skills.no-such-rule"}
        elif plan == "write":
            return Completed(0, claude_stream(output, calls=("Write",)), "", False)
        elif plan == "limit":
            return Completed(1, "", "Error: you have reached your usage limit\n", False)
        return Completed(0, claude_stream(output, calls=("Read",)), "", False)


class ReviewRetryTest(unittest.TestCase):
    def setUp(self):
        self.record = skill_decision()
        self.files, self.symlinks = build_files(self.record)
        self.files["README.md"] = update_outside_section("# Repro\n", self.record).encode()
        self.home = tempfile.TemporaryDirectory(prefix="forge-review-retry-")

    def tearDown(self):
        self.home.cleanup()

    def verify(self, runner, diagnostics=None):
        planned = [preflight("claude-code", role, runner=runner, environ=PARENT)["configuration"] for role in ROLES]
        contract, confirmation = confirmed(self.record, REVISION, planned, "verify-1")
        run, results = verify_live(self.record, self.files, self.symlinks, RULES, REVISION, "claude-code",
                                   contract=contract, confirmation=confirmation, worker_evidence=planned,
                                   cases=load_cases(FORGE / "evals" / "smoke" / "vertical.yaml"),
                                   runner=runner, environ=PARENT, kb_version="0.1.0", diagnostics=diagnostics)
        return run, {result.part_id: result for result in results}["A"]

    def test_a_rejected_reply_is_retried_once_and_the_retry_counts(self):
        for plan in ("duplicate", "unknown"):
            with self.subTest(plan=plan):
                runner = ReviewReplies(plan, "ok")
                diagnostics = Diagnostics(Path(self.home.name) / plan)
                run, part = self.verify(runner, diagnostics)
                self.assertEqual(runner.roles.count("review"), 2)
                self.assertEqual(run.worker_violations, [])
                self.assertEqual(part.harness_result, "verified", part.rule_results)
                first, second = runner.reviews
                self.assertNotEqual(first["request_id"], second["request_id"])
                self.assertEqual(first["inputs"], second["inputs"], "the same frozen inputs and staged files")
                attempts = run.review_attempts
                self.assertEqual([(a["attempt"], a["outcome"]) for a in attempts], [(1, "rejected"), (2, "accepted")])
                self.assertIn("duplicate verdict" if plan == "duplicate" else "was not requested", attempts[0]["error"])
                self.assertTrue(all(a["evidence"]["role"] == "review" for a in attempts), "each launch's evidence")
                data = report_data(run, [part])
                self.assertEqual(data["review_attempts"], attempts)
                self.assertIn("Review reply 1 was rejected and the review ran once more", render_report(run, [part]))
                kept = diagnostics.directory / "review" / "attempt-1" / "launch.json"
                rejected = json.loads(kept.read_text(encoding="utf-8"))
                self.assertEqual(rejected["error"], attempts[0]["error"])
                self.assertIn("verdicts", rejected["output"])

    def test_a_second_rejection_leaves_the_parts_not_verified(self):
        runner = ReviewReplies("duplicate", "unknown")
        diagnostics = Diagnostics(Path(self.home.name))
        run, part = self.verify(runner, diagnostics)
        self.assertEqual(runner.roles.count("review"), 2)
        self.assertEqual(part.harness_result, "not verified")
        self.assertEqual(len(run.worker_violations), 2)
        self.assertIn("duplicate verdict", run.worker_violations[0])
        self.assertIn("on retry", run.worker_violations[1])
        self.assertEqual([a["outcome"] for a in run.review_attempts], ["rejected", "rejected"])
        self.assertTrue((diagnostics.directory / "review" / "attempt-2" / "launch.json").is_file())

    def test_a_retry_that_does_not_complete_leaves_the_parts_not_verified(self):
        runner = ReviewReplies("duplicate", "limit")
        run, part = self.verify(runner)
        self.assertEqual(part.harness_result, "not verified")
        self.assertIn("usage limit", run.worker_violations[1])
        self.assertEqual([a["outcome"] for a in run.review_attempts], ["rejected", "failed"])

    def test_only_a_completed_reply_that_validation_rejects_is_retried(self):
        for plan, message in (("limit", "usage limit"), ("write", "")):
            with self.subTest(plan=plan):
                runner = ReviewReplies(plan, "ok")
                run, part = self.verify(runner)
                self.assertEqual(runner.roles.count("review"), 1)
                self.assertEqual(part.harness_result, "not verified")
                self.assertIn(message, run.worker_violations[0])
                self.assertEqual(run.review_attempts, [])

    def test_a_valid_review_is_not_retried_and_its_report_keeps_its_form(self):
        runner = ReviewReplies("ok")
        run, part = self.verify(runner)
        self.assertEqual(runner.roles.count("review"), 1)
        self.assertEqual(run.review_attempts, [])
        self.assertNotIn("review_attempts", report_data(run, [part]))

    def test_diagnostics_that_cannot_be_written_never_change_the_verdicts(self):
        blocked = Path(self.home.name) / "file"
        blocked.write_text("not a directory", encoding="utf-8")
        diagnostics = Diagnostics(blocked / "runs")
        run, part = self.verify(ReviewReplies("duplicate", "ok"), diagnostics)
        self.assertEqual(part.harness_result, "verified", part.rule_results)
        self.assertTrue(diagnostics.problems)


if __name__ == "__main__":
    unittest.main()
