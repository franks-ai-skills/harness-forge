"""Proposing and filing a Harness KB issue or comment about drift, only with the user's approval of its text."""

import unittest

from harness_forge.issues import KB_REPOSITORY, find_issue, gh_publisher, gh_search, propose, publish
from harness_forge.adapters.base import Completed
from tests.test_research import finding

FINDING = finding(source="https://docs.example-vendor.com/skills/limits", quote="Descriptions may be 2048 characters.",
                  context="Limits. Descriptions may be 2048 characters.").to_dict()
DRIFT = {"rule_id": "skills.description.length", "finding_id": "F1", "category": "compatibility",
         "summary": "The vendor raised the description limit to 2,048 characters."}
OPEN = {"number": 7, "title": "skills.description.length is outdated", "url": "https://github.com/x/y/issues/7",
        "state": "OPEN", "body": "The limit changed."}


class ProposalTest(unittest.TestCase):
    def test_a_new_issue_or_a_comment_on_the_open_one(self):
        issue = propose(DRIFT, FINDING, None, kb_version="0.1.0")
        self.assertEqual((issue["kind"], issue["target"]), ("issue", KB_REPOSITORY))
        self.assertEqual(issue["title"], "Drift: skills.description.length may be outdated")
        for text in ("Rule `skills.description.length` (Harness KB 0.1.0) may be outdated.", DRIFT["summary"],
                     "Source: https://docs.example-vendor.com/skills/limits",
                     'Quote: "Descriptions may be 2048 characters."',
                     "the user approved this text before it was filed"):
            self.assertIn(text, issue["body"])
        self.assertRegex(issue["digest"], r"^[0-9a-f]{64}$")
        comment = propose(DRIFT, FINDING, OPEN, kb_version="0.1.0")
        self.assertEqual((comment["kind"], comment["target"], comment["title"]),
                         ("comment", "https://github.com/x/y/issues/7", None))
        self.assertNotEqual(comment["digest"], issue["digest"])

    def test_only_an_open_issue_naming_the_rule_matches(self):
        closed = {**OPEN, "state": "CLOSED"}
        other = {**OPEN, "title": "skills.name.format", "body": "Unrelated."}
        self.assertEqual(find_issue("skills.description.length", lambda rule_id: [closed, other, OPEN]), OPEN)
        self.assertIsNone(find_issue("skills.description.length", lambda rule_id: [closed, other]))


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.proposal = propose(DRIFT, FINDING, None, kb_version="0.1.0")
        self.filed = []

    def publisher(self, proposal):
        self.filed.append(proposal)
        return "https://github.com/franks-ai-skills/agent-harness-kb/issues/12"

    def relay(self, choice="approve", **changes):
        return {"choice": choice, "displayed_digest": self.proposal["digest"], "run_id": "drift-1", **changes}

    def test_only_an_approval_of_the_shown_text_files_it(self):
        self.assertEqual(publish(self.proposal, self.relay(), self.publisher, run_id="drift-1"),
                         "https://github.com/franks-ai-skills/agent-harness-kb/issues/12")
        self.assertEqual(self.filed, [self.proposal])

    def test_decline_pending_and_mismatches_file_nothing(self):
        cases = {"not published": self.relay("decline"), "pending": self.relay("pending"),
                 "pending ": self.relay(displayed_digest="0" * 64), "pending  ": self.relay(run_id="drift-2"),
                 "pending   ": {**self.relay(), "approved_by": "user"}, "pending    ": None}
        for expected, relay in cases.items():
            with self.subTest(relay=relay):
                self.assertEqual(publish(self.proposal, relay, self.publisher, run_id="drift-1"), expected.strip())
        self.assertEqual(self.filed, [])

    def test_a_publisher_must_return_the_link(self):
        with self.assertRaisesRegex(ValueError, "link"):
            publish(self.proposal, self.relay(), lambda proposal: "filed", run_id="drift-1")


class GitHubCliTest(unittest.TestCase):
    def test_search_and_filing_use_the_existing_gh_login(self):
        calls = []

        def runner(command, *, cwd, env, stdin, timeout):
            calls.append(command)
            if command[:3] == ["gh", "issue", "list"]:
                return Completed(0, '[{"number": 7, "title": "t", "url": "https://github.com/x/y/issues/7", '
                                    '"state": "OPEN", "body": "b"}]', "", False)
            return Completed(0, "https://github.com/x/y/issues/8\n", "", False)

        self.assertEqual(gh_search(runner, environ={})("skills.description.length")[0]["number"], 7)
        self.assertEqual(calls[0][:6], ["gh", "issue", "list", "--repo", KB_REPOSITORY, "--state"])
        self.assertIn("skills.description.length in:title,body", calls[0])
        proposal = propose(DRIFT, FINDING, None, kb_version="0.1.0")
        self.assertEqual(gh_publisher(runner, environ={})(proposal), "https://github.com/x/y/issues/8")
        self.assertEqual(calls[1][:5], ["gh", "issue", "create", "--repo", KB_REPOSITORY])
        comment = propose(DRIFT, FINDING, OPEN, kb_version="0.1.0")
        gh_publisher(runner, environ={})(comment)
        self.assertEqual(calls[2][:4], ["gh", "issue", "comment", "https://github.com/x/y/issues/7"])
        self.assertTrue(all("auth" not in command for command in calls), "never logs in or switches accounts")


if __name__ == "__main__":
    unittest.main()
