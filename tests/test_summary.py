"""The confirmation summary and the details file (#84): what the user reads, and everything the digest binds."""

import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import yaml
from markdown_it import MarkdownIt

from harness_forge.contracts import render_contract, write_decision
from harness_forge.documents import canonical_digest
from harness_forge.models import AcceptedRevision
from harness_forge.summary import fenced, render_details, render_summary, text
from tests.builders import EVIDENCE, decision, part

FORGE = Path(__file__).resolve().parents[1]
LONG = "The checklist names " + "every step of the reproduction, " * 46 + "in order."
CONTROL = {"control": "codeowners-agent-config",
           "prerequisites": ["outside.codeowners.agent-config", "outside.merge.no-agent-bypass"],
           "availability": "available", "required_for_goal": True}
PRECAUTION = {**CONTROL, "control": "branch-protection-precaution", "required_for_goal": False}
REUSE = {"name": "repro-steps", "source": "https://example.com/repro-steps/commit/abc123", "license": "MIT",
         "maintenance": "maintained"}
DRIFT = [{"rule_id": "skills.description.length", "finding_id": "F1", "slug": "repro", "category": "compatibility",
          "summary": "The vendor raised the limit.",
          "finding": {"claim": "Longer", "source": "https://docs.example.com/limits", "fetched": "2026-10-09",
                      "quote": "Up to 2048.", "context": "Limits. Up to 2048."}},
         {"rule_id": "skills.name-format", "finding_id": "F2", "slug": "repro", "category": "ordinary",
          "summary": "Names may hold digits.",
          "finding": {"claim": "Digits", "source": "https://docs.example.com/names", "fetched": "2026-10-09",
                      "quote": "Digits allowed.", "context": "Names. Digits allowed."}}]
CHECKED = [{"rule_id": "skills.description.length", "retired": False, "warnings": [
    "lowers the error rule skills.description.length from error to warning",
    "changes its parameters for the error rule skills.description.length, which may loosen it"],
    "changes": [{"field": "params"}, {"field": "severity"}]}]
OVERRIDE = {"rule_id": "skills.description.length", "pinned_kb_version": "0.1.0", "retired": True,
            "reason": "The team's own reason, sha256 " + "e" * 64 + ".", "date": "2026-10-09", "link": "pending"}
DETAILS = "/tmp/run dir/a&amp;b/#x/50%/new\nline/contract-details-0123456789abcdef.md"


def accepted(*rules):
    entries = [{"part_id": part_id, "rule": {"id": rule_id, "kind": kind, "statement": statement}}
               for part_id, rule_id, kind, statement in rules]
    return AcceptedRevision(rule_ids=[entry["rule"]["id"] for entry in entries], rules=entries,
                            brief_digests={entry["part_id"]: "1" * 64 for entry in entries},
                            digest=canonical_digest(entries))


def scenario(**extra):
    a = part("A", "Write a reproduction checklist when asked", outside_controls=[CONTROL, PRECAUTION],
             goal_conflict={"choice": "keep", "reason": "The team accepts the error.", "date": "2026-10-10",
                            "chosen": "skills", "rule_ids": ["selection.skills.side-effects"]})
    b = part("B", "Reuse the team's steps skill", outcome="reuse", reuse=REUSE,
             goal_conflict={"choice": "recommended", "reason": "Switch.", "date": "2026-10-10", "chosen": "hooks",
                            "rule_ids": ["selection.hooks.events"]})
    record = decision(a, b, **extra)
    revision = accepted(("A", "idea.repro.a.steps", "must", LONG),
                        ("A", "idea.repro.a.no-upload", "must-not", "The checklist uploads logs to the tracker"),
                        ("A", "idea.repro.a.env", "should", "The checklist names the environment"),
                        ("A", "idea.repro.a.guess", "should-not", "The checklist guesses missing steps"))
    return record, revision


def summary(record, revision, overrides=(), checked=(), drift=DRIFT, states=("kept", "open"), details=DETAILS,
            refused=None, research=None):
    contract = render_contract(record, revision, list(overrides), worker_evidence=EVIDENCE, drift=list(drift))
    shown = render_summary(
        contract, record.record, checked_overrides=list(checked),
        refused=refused if refused is not None else [{"part_id": "C", "problem": "its selection is stale"}],
        research=research if research is not None else [
            {"part_id": "A", "accepted": "stale", "deep_research": "none",
             "next": "run the deep pass for part A, then accept its requirements, even none"},
            {"part_id": "B", "accepted": "current", "deep_research": "current", "next": "nothing"}],
        drift=contract.content["drift"], drift_states=list(states),
        conditions={"outside.codeowners.agent-config": "CODEOWNERS covers the agent configuration",
                    "outside.merge.no-agent-bypass": "No agent can bypass the merge rules"},
        details_path=details)
    return contract, shown


def tokens(markdown):
    return MarkdownIt("commonmark").enable("table").enable("strikethrough").parse(markdown)


def inline_children(markdown):
    return [child for token in tokens(markdown) if token.type == "inline" for child in token.children or []]


class SummaryContentTest(unittest.TestCase):
    def setUp(self):
        self.record, self.revision = scenario()
        self.contract, self.summary = summary(self.record, self.revision, [OVERRIDE], CHECKED)
        self.plain = " ".join(self.summary.replace("\\", "").split())

    def test_it_shows_the_idea_goals_scope_and_each_part(self):
        for expected in ("## Contract summary: Turn approved bug reports into reproduction checklists",
                         "- Reproduction checklists from approved bug reports",
                         "**Scope:** Claude Code and Codex · reach repository · hosting github, free plan, public",
                         "### Part A: part-a", "- **Goal:** Write a reproduction checklist when asked",
                         "- **Outcome:** harness-only · **Mechanism:** skills",
                         "### Part B: part-b", "- **Reused:** repro-steps, pinned source "
                         "https://example.com/repro-steps/commit/abc123",
                         "**Workers:** best-effort, isolation not certified."):
            self.assertIn(expected, self.summary)

    def test_every_requirement_is_listed_in_full_with_its_kind(self):
        for expected in (f"  - Must: {text(LONG, '    ')}", "  - Must not: The checklist uploads logs to the tracker",
                         "  - Should: The checklist names the environment",
                         "  - Should not: The checklist guesses missing steps"):
            self.assertIn(expected, self.summary)
        self.assertIn(LONG, self.plain, "a long statement is never cut")
        self.assertGreater(len(LONG), 1500)

    def test_only_controls_required_for_the_goal_with_readable_prerequisites(self):
        self.assertIn("**Outside controls required for the goal** (recommended, not verified):", self.summary)
        self.assertIn("Recorded as available during intake. It works as a gate only when:", self.summary)
        for condition in ("CODEOWNERS covers the agent configuration", "No agent can bypass the merge rules",
                          "It targets the real refs and resources it protects"):
            self.assertIn(condition, self.plain)
        self.assertNotIn("branch-protection-precaution", self.summary)
        self.assertNotIn("outside.merge.no-agent-bypass", self.summary, "prerequisites read as statements")

    def test_conflicts_overrides_refusals_research_and_drift(self):
        self.assertIn("kept as it is (The team accepts the error.). It stays an error, so the part is never verified.",
                      self.plain)
        self.assertIn("switched to the recommended mechanism (Switch.). Verification will assess the current choice "
                      "and goal; confirmation does not verify the part.", self.plain)
        self.assertIn("- `skills.description.length`: changes the pinned rule's params, severity", self.summary)
        for warning in CHECKED[0]["warnings"]:
            self.assertIn(f"Warning: it {warning}", self.plain)
        self.assertIn("- Part C: its selection is stale", self.summary)
        self.assertIn("- Part A: accepted requirements stale, deep research none; next: run the deep pass for part "
                      "A, then accept its requirements, even none", self.summary)
        self.assertNotIn("Part B: accepted requirements current", self.summary)
        self.assertIn("- `skills.description.length` (finding F1 of research `repro`): The vendor raised the "
                      "limit.; kept the pinned rule", self.summary)
        self.assertIn("(finding F2 of research `repro`): Names may hold digits.; decision pending", self.summary)

    def test_no_forge_diagnostics_or_fetched_text(self):
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", self.summary))
        for absent in ("sha256", self.contract.digest, "claude-opus-5-5", "independent-process", "tools: Read",
                       "Up to 2048.", "Digits allowed.", "docs.example.com", "Untrusted content", "Picture"):
            self.assertNotIn(absent, self.summary)

    def test_identifiers_in_the_users_text_and_full_warnings_stay(self):
        record, revision = scenario(goals=["Keep sha256 " + "a" * 64 + " and model claude-opus-5-5 as written"])
        _, shown = summary(record, revision, checked=[{**CHECKED[0], "warnings": ["names sha256 " + "b" * 64]}])
        self.assertIn("Keep sha256 " + "a" * 64 + " and model claude-opus-5-5 as written", shown)
        self.assertIn("Warning: it names sha256 " + "b" * 64, shown)

    def test_the_details_link_decodes_to_the_file(self):
        links = [child for child in inline_children(self.summary) if child.type == "link_open"]
        self.assertEqual(len(links), 1)
        self.assertEqual(urllib.parse.unquote(links[0].attrs["href"]), DETAILS)

    def test_a_new_idea_without_parts(self):
        record = decision()
        record.record["parts"] = []
        _, shown = summary(record, None, drift=(), states=(), refused=[], research=[])
        self.assertIn("No parts yet: this confirms the idea and its goals.", shown)


class SummaryContainmentTest(unittest.TestCase):
    HOSTILE = ("## Injected heading\n- [ ] task\n<script>alert(1)</script>\n```\nfenced\n```\n"
               "[click](javascript:alert(1)) | a | b |\n\n1. numbered\n---\n===  after\u0085 &amp; ~~x~~ *em* _u_ "
               "![img](x.png) <https://evil.example> \\")

    def test_record_text_renders_as_text_only(self):
        record, revision = scenario(idea="Idea " + self.HOSTILE, goals=[self.HOSTILE],
                                    parts=[part("A", self.HOSTILE, goal_conflict={
                                        "choice": "keep", "reason": self.HOSTILE, "date": "2026-10-10",
                                        "chosen": "skills", "rule_ids": ["selection.skills.side-effects"]})])
        drift = [{**DRIFT[0], "summary": self.HOSTILE}]
        _, shown = summary(record, revision, drift=drift, states=("open",),
                           refused=[{"part_id": "A", "problem": self.HOSTILE}],
                           research=[{"part_id": "A", "accepted": "none", "deep_research": "none",
                                      "next": self.HOSTILE}])
        parsed = tokens(shown)
        headings = [parsed[index + 1].content for index, token in enumerate(parsed) if token.type == "heading_open"]
        self.assertEqual(len(headings), 5, headings)  # the summary, part A, refused parts, research and drift
        self.assertTrue(all(token.type not in ("html_block", "fence", "code_block", "hr", "table_open",
                                               "ordered_list_open") for token in parsed),
                        [token.type for token in parsed])
        children = inline_children(shown)
        self.assertEqual([child.type for child in children if child.type in ("link_open", "image")], ["link_open"])
        self.assertFalse([child.type for child in children if child.type in ("html_inline", "s_open", "em_open")])
        self.assertIn("\\u2028after\\u0085", shown)
        rendered = MarkdownIt("commonmark").render(shown)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("## Injected heading", rendered, "the text is shown, not interpreted")

    def test_the_details_keep_fetched_protections_inside_a_closed_fence(self):
        hostile = "````\n# heading\n````` and   \"quoted\""
        drift = [{**DRIFT[0], "finding": {**DRIFT[0]["finding"], "quote": hostile, "context": hostile}}]
        record, revision = scenario()
        contract = render_contract(record, revision, [], worker_evidence=EVIDENCE, drift=drift)
        details = render_details(contract, record.record, worker_evidence=EVIDENCE)
        parsed = tokens(details)
        fences = [token for token in parsed if token.type == "fence"]
        self.assertEqual([token.info for token in fences], ["text", "json", "json"])
        self.assertEqual(fences[0].content, contract.text, "the readable contract is one literal block")
        self.assertIn("Untrusted content from a fetched page", fences[0].content)
        self.assertIn(json.dumps(hostile, ensure_ascii=True), fences[0].content)
        self.assertFalse([token for token in parsed if token.type == "heading_open" and parsed[parsed.index(token) + 1]
                          .content == "heading"])


class DetailsFileTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-summary-")))
        specification = importlib.util.spec_from_file_location("forge_summary", FORGE / "scripts" / "forge.py")
        self.forge = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.forge)
        self.record, _ = scenario(parts=[part("A", "Write a reproduction checklist when asked")])
        self.decision = write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        self.evidence = self.root / "workers.json"
        self.evidence.write_text(json.dumps({"worker_evidence": EVIDENCE}), encoding="utf-8")

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.forge.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def run_dir(self):
        code, output, stderr = self.cli("run-dir", "--root", self.root)
        self.assertEqual(code, 0, stderr)
        return Path(output["path"])

    def prepare(self, run, *extra):
        return self.cli("prepare-contract", "--decision", self.decision, "--evidence", self.evidence,
                        "--run-dir", run, *extra)

    def test_the_file_matches_its_checksum_and_the_digest(self):
        run = self.run_dir()
        code, output, stderr = self.prepare(run)
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("text", output)
        path = Path(output["details"]["path"])
        self.assertEqual(path.parent, run)
        data = path.read_bytes()
        import hashlib
        self.assertEqual(hashlib.sha256(data).hexdigest(), output["details"]["sha256"])
        self.assertTrue(path.name.startswith("contract-details-" + output["details"]["sha256"][:16]))
        parsed = tokens(data.decode("utf-8"))
        blocks = [token.content for token in parsed if token.type == "fence" and token.info == "json"]
        self.assertEqual(canonical_digest(json.loads(blocks[0])), output["digest"])
        self.assertEqual(json.loads(blocks[1]), EVIDENCE, "the worker evidence the digest references, in full")
        self.assertIn(output["digest"], data.decode("utf-8"))
        self.assertIn("is not inside it", data.decode("utf-8"))
        link = [child for child in inline_children(output["summary"]) if child.type == "link_open"][0]
        self.assertEqual(urllib.parse.unquote(link.attrs["href"]), str(path))

    def test_the_same_preparation_reuses_its_file_and_a_different_file_is_never_replaced(self):
        run = self.run_dir()
        _, first, _ = self.prepare(run)
        before = sorted(run.iterdir())
        code, again, stderr = self.prepare(run)
        self.assertEqual((code, again["details"], again["digest"]), (0, first["details"], first["digest"]), stderr)
        self.assertEqual(sorted(run.iterdir()), before)
        Path(first["details"]["path"]).write_text("edited\n", encoding="utf-8")
        code, _, stderr = self.prepare(run)
        self.assertEqual(code, 2)
        self.assertIn("already exists with other content; it is left unchanged", stderr)
        self.assertEqual(Path(first["details"]["path"]).read_text(encoding="utf-8"), "edited\n")

    def test_only_an_allocated_run_directory_of_this_repository(self):
        other = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="forge-other-")))
        (other / ".harness-forge/runs/r").mkdir(parents=True)
        elsewhere = self.root / "notes"
        elsewhere.mkdir()
        linked = self.root / ".harness-forge/runs/linked"
        self.run_dir()
        linked.symlink_to(other / ".harness-forge/runs/r")
        for run, reason in ((elsewhere, "not a run directory"), (self.root / ".harness-forge/runs", "not a run dir"),
                            (other / ".harness-forge/runs/r", "another repository"),
                            (linked, "a symbolic link")):
            with self.subTest(run=str(run)):
                code, _, stderr = self.prepare(run)
                self.assertEqual(code, 2, stderr)
                self.assertIn(reason, stderr)
        self.assertEqual(list((other / ".harness-forge/runs/r").iterdir()), [])

    def test_unchanged_inputs_keep_their_digest_and_a_changed_input_needs_a_new_confirmation(self):
        run = self.run_dir()
        _, first, _ = self.prepare(run)
        self.assertEqual(first["digest"], render_contract(self.record, None, [], worker_evidence=EVIDENCE).digest)
        _, second, _ = self.prepare(self.run_dir())
        self.assertEqual(second["digest"], first["digest"], "the run directory and file path are not bound")
        self.assertNotEqual(second["details"]["path"], first["details"]["path"])
        self.record.record["goals"] = ["A weakened goal"]
        write_decision(self.root, self.record, slug="repro", date="2026-10-10")
        _, changed, _ = self.prepare(run)
        self.assertNotEqual(changed["digest"], first["digest"])
        self.assertIn("- A weakened goal", changed["summary"])

    def test_an_oversized_result_is_refused_whole(self):
        record, _ = scenario(parts=[part(letter, letter + " goal " + "x" * 19000) for letter in "ABCDE"])
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, output, stderr = self.prepare(self.run_dir())
        self.assertEqual((code, output), (2, None))
        self.assertIn("65536", stderr)


if __name__ == "__main__":
    unittest.main()
