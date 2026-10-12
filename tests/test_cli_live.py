"""The live CLI commands end to end in a temporary repository, with scripted workers and fetches."""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from harness_forge.contracts import load_decision, write_decision
from harness_forge.documents import load_document
from harness_forge.overrides import load_overrides
from harness_forge.research import load_revision, write_research
from harness_forge.adapters.base import Completed
from tests.test_adapters import CLAUDE_API_KEY, PARENT, FakeRunner, claude_stream
from tests.test_build_skill import DESCRIPTION, NAME, PROCEDURE
from tests.builders import part
from tests.test_contracts import OVERRIDE
from tests.test_verify import skill_decision
from tests.test_workflow import ResearchRunner, fetch_page, finding

FORGE = Path(__file__).resolve().parents[1]


class NotApplicableRunner(ResearchRunner):
    """The reviewer answers "not applicable" for one rule and passes the rest."""

    def __init__(self, findings, rule_id):
        super().__init__(findings)
        self.rule_id = rule_id

    def __call__(self, command, *, cwd, env, stdin, timeout):
        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        if self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin:
            event = json.loads(answer.stdout.splitlines()[-1])
            output = json.loads(event["result"])
            for verdict in output["verdicts"]:
                if verdict["rule_id"] == self.rule_id:
                    verdict.update(verdict="not-applicable", reason="It writes text only.")
            return Completed(0, claude_stream(output, calls=("Read",)), "", False)
        return answer


class Cli:
    def __init__(self, runner, root):
        specification = importlib.util.spec_from_file_location("forge_cli_live", FORGE / "scripts" / "forge.py")
        self.module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.module)
        self.module.RUNNER, self.module.FETCH, self.module.ENVIRON = runner, fetch_page, PARENT
        self.root = Path(root)
        self.parts = ["A"]  # the parts a confirmation names

    def __call__(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main([str(argument) for argument in arguments])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def write(self, name, value):
        path = self.root / ".scratch" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")
        return path

    def confirm(self, decision, run_id, *extra):
        code, contract, stderr = self("prepare-contract", "--decision", decision, "--evidence", self.root / ".scratch/workers.json",
                                      *extra)
        assert code == 0, stderr
        return self.write(f"relay-{run_id}.json", {"choice": "confirm", "displayed_digest": contract["digest"],
                                                   "part_ids": self.parts, "run_id": run_id})


class LiveCommandsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="forge-live-cli-")
        self.root = Path(self.directory.name)
        record = skill_decision(status="decided")
        record.record["accepted_revision"] = None
        self.decision = write_decision(self.root, record, slug="repro", date="2026-10-10")
        (self.root / "README.md").write_text("# Repro\n", encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_workers_reports_the_planned_configurations(self):
        cli = Cli(FakeRunner(), self.root)
        code, output, _ = cli("workers", "--harness", "claude-code")
        self.assertEqual(code, 0)
        self.assertTrue(output["ready"])
        self.assertEqual([c["role"] for c in output["worker_evidence"]], ["research", "source-support", "review"])

    def test_workers_asks_before_an_api_key_login(self):
        cli = Cli(FakeRunner(login=CLAUDE_API_KEY), self.root)
        code, output, _ = cli("workers", "--harness", "claude-code")
        self.assertEqual(code, 1)
        self.assertTrue(all(role["needs_cost_question"] for role in output["roles"]))
        code, output, _ = cli("workers", "--harness", "claude-code", "--login-answer", "continue")
        self.assertEqual(code, 0)

    def test_research_refuses_a_relay_for_another_contract(self):
        cli = Cli(ResearchRunner([finding()]), self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.write("relay.json", {"choice": "confirm", "displayed_digest": "0" * 64, "part_ids": ["A"],
                                         "run_id": "intake-1"})
        code, output, stderr = cli("research", "--decision", self.decision, "--pass", "deep", "--part", "A",
                                   "--harness", "claude-code", "--evidence", evidence, "--relay", relay,
                                   "--root", self.root, "--slug", "repro")
        self.assertEqual(code, 2)
        self.assertIn("does not confirm", stderr)
        self.assertEqual(cli.module.RUNNER.worker_calls, [])

    def build_ready(self, runner):
        """Workers, research, acceptance and build, as the skills run them; returns what verify needs."""
        cli = Cli(runner, self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)

        relay = cli.confirm(self.decision, "intake-1")
        code, output, stderr = cli("research", "--decision", self.decision, "--pass", "deep", "--part", "A",
                                   "--harness", "claude-code", "--evidence", evidence, "--relay", relay,
                                   "--root", self.root, "--slug", "repro", "--write")
        self.assertEqual(code, 0, stderr)
        self.assertEqual([f["id"] for f in output["kept"]], ["F1"])
        self.assertTrue((self.root / ".harness/research/repro.md").exists())

        proposed = cli.write("proposed.json", {"requirements": [
            {"id": "idea.repro.a.platform", "concept": "idea", "kind": "must", "severity": "error", "check": "judged",
             "statement": "The checklist names the platform the reproducer runs on",
             "applies_to": {"harnesses": ["claude-code", "codex"]}, "evidence": "Vendor",
             "sources": ["https://docs.example-vendor.com/repro/platforms"], "contested": False}]})
        choices = cli.write("choices.json", {"idea.repro.a.platform": "accept"})
        code, output, stderr = cli("accept", "--decision", self.decision, "--part", "A", "--proposed", proposed,
                                   "--choices", choices, "--root", self.root, "--slug", "repro")
        self.assertEqual(code, 0, stderr)
        revision_path = self.root / ".harness/research/repro.rules.yaml"
        revision = load_revision(revision_path)
        self.assertEqual(load_decision(self.decision).record["accepted_revision"]["digest"], revision.digest)

        relay = cli.confirm(self.decision, "verify-1", "--revision", revision_path)
        description = cli.write("description.txt", DESCRIPTION)
        procedure = cli.write("procedure.md", PROCEDURE)
        code, output, stderr = cli("build", "--decision", self.decision, "--part", "A", "--revision", revision_path,
                                   "--evidence", evidence, "--relay", relay, "--description", description,
                                   "--procedure", procedure, "--root", self.root)
        self.assertEqual(code, 0, stderr)
        self.assertTrue((self.root / f".agents/skills/{NAME}/SKILL.md").exists())
        self.assertTrue((self.root / f".claude/skills/{NAME}").is_symlink())
        self.assertEqual(load_decision(self.decision).part("A")["status"], "built")

        from harness_forge.contracts import update_outside_section
        (self.root / "README.md").write_text(update_outside_section("# Repro\n", load_decision(self.decision)),
                                             encoding="utf-8")
        relay = cli.confirm(self.decision, "verify-1", "--revision", revision_path)
        return cli, ["--decision", self.decision, "--root", self.root, "--revision", revision_path,
                     "--evidence", evidence, "--relay", relay, "--harness", "claude-code",
                     "--cases", FORGE / "evals/smoke/vertical.yaml", "--slug", "repro", "--write"]

    def test_research_accept_build_and_verify(self):
        runner = ResearchRunner([finding()])
        cli, arguments = self.build_ready(runner)
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["unresolved"], [])
        self.assertEqual(output["parts"], [{"part_id": "A", "status": "verified", "harness_result": "verified",
                                            "goal_result": "met by the verified build"}])
        self.assertTrue((self.root / ".harness/reports/repro.md").exists())
        self.assertEqual(runner.roles, ["research", "source-support", "review", "baseline", "baseline", "runtime", "runtime", "runtime"])

    def test_a_decision_edited_during_review_is_not_verified(self):
        decision = self.decision

        class Editing(ResearchRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin:
                    text = Path(decision).read_text(encoding="utf-8")
                    Path(decision).write_text(text.replace("every_time: usually", "every_time: always"), encoding="utf-8")
                return answer

        cli, arguments = self.build_ready(Editing([finding()]))
        self.assertIn("every_time: usually", Path(decision).read_text(encoding="utf-8"))
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 1, stderr)
        self.assertEqual(output["parts"][0]["harness_result"], "not verified")
        self.assertIn("every_time: always", Path(decision).read_text(encoding="utf-8"))

    def test_a_not_applicable_error_rule_waits_for_the_users_confirmation(self):
        rule = "skills.invocation.side-effects-user-invoked"
        runner = NotApplicableRunner([finding()], rule)
        cli, arguments = self.build_ready(runner)
        code, output, _ = cli("verify", *arguments)
        self.assertEqual(code, 1)
        [unresolved] = output["unresolved"]
        self.assertEqual({key: unresolved[key] for key in ("part_id", "rule_id", "reason")},
                         {"part_id": "A", "rule_id": rule, "reason": "It writes text only."})
        self.assertRegex(unresolved["confirmation"], rf"^A:{rule}:[0-9a-f]{{16}}$")
        self.assertEqual(output["parts"][0]["harness_result"], "not verified")

        code, output, stderr = cli("verify", *arguments, "--confirm-not-applicable", unresolved["confirmation"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["unresolved"], [])
        self.assertEqual(output["parts"][0]["harness_result"], "verified")
        self.assertIn(f"`{rule}` | error | judged | not-applicable",
                      (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8"))

    def test_a_confirmation_holds_only_for_the_inputs_it_was_given_for(self):
        rule = "skills.invocation.side-effects-user-invoked"
        cli, arguments = self.build_ready(NotApplicableRunner([finding()], rule))
        code, output, _ = cli("verify", *arguments, "--confirm-not-applicable", f"A:{rule}:{'0' * 16}")
        self.assertEqual(code, 1)
        self.assertEqual([item["rule_id"] for item in output["unresolved"]], [rule])

    def test_a_confirmation_needs_a_part_a_rule_and_its_token(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        for value in ("skills.invocation.side-effects-user-invoked", "A:skills.invocation.side-effects-user-invoked"):
            code, _, stderr = cli("verify", *arguments, "--confirm-not-applicable", value)
            self.assertEqual(code, 2)
            self.assertIn("PART:RULE:TOKEN", stderr)

    def test_cases_need_an_explicit_invocation_and_a_near_miss_per_part(self):
        import yaml
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        cases = yaml.safe_load((FORGE / "evals/smoke/vertical.yaml").read_text(encoding="utf-8"))["cases"]
        positives = cli.write("positives.yaml", yaml.safe_dump({"cases": [c for c in cases if c["mode"] != "negative"]}))
        option = arguments.index("--cases") + 1
        code, _, stderr = cli("verify", *arguments[:option], positives, *arguments[option + 1:])
        self.assertEqual(code, 2)
        self.assertIn("part A needs an explicit invocation and a near miss", stderr)

    def test_the_recorded_requirements_cannot_be_left_out(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        option = arguments.index("--revision")
        without = arguments[:option] + arguments[option + 2:]
        code, _, stderr = cli("verify", *without)
        self.assertEqual(code, 2)
        self.assertIn("accepted requirements", stderr)
        code, _, stderr = cli("prepare-contract", "--decision", self.decision)
        self.assertEqual(code, 2)
        self.assertIn("accepted requirements", stderr)

        other = cli.write("other.rules.yaml", (self.root / ".harness/research/repro.rules.yaml").read_text(
            encoding="utf-8").replace("names the platform", "names the operating system"))
        code, _, stderr = cli("verify", *arguments[:option + 1], other, *arguments[option + 2:])
        self.assertEqual(code, 2)
        self.assertIn("accepted requirements", stderr)

    def test_requirements_belong_to_the_part_they_are_accepted_for(self):
        cli = Cli(FakeRunner(), self.root)
        proposed = cli.write("proposed.json", {"requirements": [
            {"id": "idea.repro.b.platform", "concept": "idea", "kind": "must", "severity": "error", "check": "judged",
             "statement": "The checklist names the platform", "applies_to": {"harnesses": ["claude-code"]},
             "evidence": "Vendor", "sources": ["https://docs.example-vendor.com/repro/platforms"], "contested": False}]})
        choices = cli.write("choices.json", {"idea.repro.b.platform": "accept"})
        code, _, stderr = cli("accept", "--decision", self.decision, "--part", "A", "--proposed", proposed,
                              "--choices", choices, "--root", self.root, "--slug", "repro")
        self.assertEqual(code, 2)
        self.assertIn("idea.repro.b.platform belongs to part B, not A", stderr)
        self.assertFalse((self.root / ".harness/research/repro.rules.yaml").exists())

    def test_requirements_researched_for_another_goal_are_stale(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        text = Path(self.decision).read_text(encoding="utf-8")
        Path(self.decision).write_text(text.replace("goal: Write a reproduction checklist when asked",
                                                    "goal: Write a security vulnerability report"), encoding="utf-8")
        self.assertEqual(load_decision(self.decision).part("A")["goal"], "Write a security vulnerability report")
        option = arguments.index("--relay") + 1
        relay = cli.confirm(self.decision, "verify-1", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, _, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 2)
        self.assertIn("researched for another brief", stderr)

    def test_an_override_file_applies_only_with_a_new_confirmation(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        overrides = self.root / ".harness/overrides"
        overrides.mkdir(parents=True)
        research = load_document(self.root / ".harness/research/repro.md", "research")
        override = {**OVERRIDE, "drift": {"finding": research["findings"][0], "support_verdict": "supported"}}
        (overrides / "skills.description.concise.yaml").write_text(yaml.safe_dump(override), encoding="utf-8")
        code, output, stderr = cli("prepare-contract", "--decision", self.decision, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Rule overrides for this repository (they apply once you confirm this contract):", output["text"])
        self.assertIn("The team prefers longer descriptions.", output["text"])
        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 2)
        self.assertIn("does not confirm the current contract", stderr)
        option = dict(zip(arguments[::2], arguments[1::2]))
        code, _, stderr = cli("check", "--decision", self.decision, "--root", self.root, "--revision", option["--revision"])
        self.assertEqual(code, 2)
        self.assertIn("apply only after a contract confirmation", stderr)
        (overrides / "notes.txt").write_text("not an override", encoding="utf-8")
        code, _, stderr = cli("prepare-contract", "--decision", self.decision)
        self.assertEqual(code, 2)
        self.assertIn("notes.txt", stderr)

    def test_an_adopted_override_is_checked_shown_and_bound_to_the_confirmation(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        pinned = next(rule for rule in yaml.safe_load((FORGE / "snapshot/rules/skills.yaml").read_text())
                      if rule["id"] == "skills.description.length")
        request = cli.write("override.json", {
            "rule_id": "skills.description.length", "finding_id": "F1", "reason": "The vendor raised the limit.",
            "rule": {**pinned, "params": {"max_chars": 2048},
                     "statement": "The description is present and at most 2,048 characters"}})
        code, output, stderr = cli("override", "--root", self.root, "--input", request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["status"], "valid")
        self.assertEqual(output["written"], None, "shown first; written only with --write")
        self.assertIn("  Pinned: error: The description is present and at most 1,024 characters", "\n".join(output["text"]))
        self.assertTrue(any("for the error rule skills.description.length" in w for w in output["warnings"]))
        self.assertEqual(output["override"]["link"], "pending")
        code, output, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        self.assertTrue((self.root / ".harness/overrides/skills.description.length.yaml").exists())

        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 2, "the old confirmation does not cover the override")
        self.assertIn("does not confirm", stderr)
        option = arguments.index("--relay") + 1
        relay = cli.confirm(self.decision, "verify-2", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, output, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 0, stderr)
        report = (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        self.assertIn("- Harness result: verified with 1 override (skills.description.length)", report)
        self.assertIn("- Rule overrides: 1 adopted: `skills.description.length` (sha256 ", report)

        code, output, _ = cli("overrides", "--root", self.root)
        self.assertEqual([(item["rule_id"], item["status"]) for item in output["overrides"]],
                         [("skills.description.length", "valid")])
        path = self.root / ".harness/overrides/skills.description.length.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("The vendor raised the limit.", "Changed."),
                        encoding="utf-8")
        code, _, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 2, "a changed override needs a new confirmation")
        self.assertIn("does not confirm", stderr)

    def test_after_a_kb_update_the_user_keeps_or_removes_an_override(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        request = cli.write("override.json", {"rule_id": "skills.description.concise", "finding_id": "F1",
                                              "reason": "Longer descriptions.", "retired": True})
        code, output, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        path = self.root / ".harness/overrides/skills.description.concise.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("pinned_kb_version: 0.1.0",
                                                                 "pinned_kb_version: 0.0.9"), encoding="utf-8")
        code, output, _ = cli("overrides", "--root", self.root)
        self.assertEqual(code, 1)
        self.assertEqual((output["overrides"][0]["status"], output["overrides"][0]["kb_comparison"]),
                         ("kb-updated", "differs"))
        code, _, stderr = cli("prepare-contract", "--decision", self.decision, "--revision",
                              self.root / ".harness/research/repro.rules.yaml")
        self.assertEqual(code, 2, "an override from another KB release applies nowhere until the user decides")
        self.assertIn("ask whether to keep the override", stderr)
        code, output, stderr = cli("overrides", "--root", self.root, "--keep", "skills.description.concise")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["overrides"][0]["status"], "valid")
        code, output, stderr = cli("overrides", "--root", self.root, "--remove", "skills.description.concise")
        self.assertEqual((code, output["overrides"]), (0, []), stderr)
        self.assertFalse(path.exists())

    def test_an_override_of_unsupported_research_is_refused(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        request = cli.write("override.json", {"rule_id": "skills.description.length", "finding_id": "F9",
                                              "reason": "No such finding.", "retired": True})
        code, _, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 2)
        self.assertIn("F9", stderr)
        self.assertFalse((self.root / ".harness/overrides").exists())

    def test_an_override_whose_support_lapsed_says_how_to_refresh_it(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        request = cli.write("override.json", {"rule_id": "skills.description.concise", "finding_id": "F1",
                                              "reason": "Longer descriptions.", "retired": True})
        self.assertEqual(cli("override", "--root", self.root, "--input", request, "--write")[0], 0)
        # Research written before support entries named the finding they checked.
        path = self.root / ".harness/research/repro.md"
        record = load_document(path, "research")
        for entry in record["support"]:
            entry.pop("finding_digest", None)
        write_research(self.root, record, "Older research.\n")
        revision = ["--revision", self.root / ".harness/research/repro.rules.yaml"]
        code, _, stderr = cli("prepare-contract", "--decision", self.decision, *revision)
        self.assertEqual(code, 2)
        self.assertIn("forge.py overrides --remove skills.description.concise", stderr)
        code, output, stderr = cli("overrides", "--root", self.root, "--remove", "skills.description.concise")
        self.assertEqual((code, output["overrides"]), (0, []), stderr)
        code, _, stderr = cli("prepare-contract", "--decision", self.decision, *revision)
        self.assertEqual(code, 0, "without the override the pinned rule applies, and research can run again")

    def test_select_applies_overrides_only_under_a_confirmation(self):
        cli, _ = self.build_ready(ResearchRunner([finding()]))
        request = cli.write("override.json", {"rule_id": "selection.eligibility.own-context-skills", "finding_id": "F1",
                                              "reason": "Skills can fork their own context.", "retired": True})
        code, _, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        record = load_decision(self.decision)
        selection = cli.write("select.json", {"answers": {**record.part("A")["answers"], "own_context": True},
                                              "whole_idea": record.record["facts"], "chosen": "skills"})
        code, output, stderr = cli("select", "--input", selection, "--root", self.root)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["goal_conflict"]["severity"], "error", "unconfirmed, the pinned rules decide")
        preview = output["override_preview"]
        self.assertEqual(preview["overrides"], ["selection.eligibility.own-context-skills"])
        self.assertIn("applies once the contract that shows these overrides is confirmed", preview["note"])
        self.assertNotEqual((preview["goal_conflict"] or {}).get("severity"), "error")

        revision = self.root / ".harness/research/repro.rules.yaml"
        relay = cli.confirm(self.decision, "intake-3", "--revision", revision)
        code, output, stderr = cli("select", "--input", selection, "--root", self.root, "--decision", self.decision,
                                   "--revision", revision, "--evidence", self.root / ".scratch/workers.json",
                                   "--relay", relay)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["overrides"], ["selection.eligibility.own-context-skills"])
        self.assertNotEqual((output["goal_conflict"] or {}).get("severity"), "error")

    def test_an_override_asks_its_own_new_question(self):
        cli, _ = self.build_ready(ResearchRunner([finding()]))
        record = load_decision(self.decision)
        selection = cli.write("select.json", {"answers": record.part("A")["answers"],
                                              "whole_idea": record.record["facts"]})
        _, selected, _ = cli("select", "--input", selection)
        winner = next(row["rules"][0] for row in selected["assessment"] if row["state"] == "recommended")
        pinned = next(rule for rule in yaml.safe_load((FORGE / "snapshot/rules/selection.yaml").read_text())["rules"]
                      if rule["id"] == winner)
        stricter = {**pinned, "params": {**pinned["params"], "when": {"all": [pinned["params"]["when"],
                                                                              {"eq": ["script_checkable", True]}]}}}
        request = cli.write("override.json", {"rule_id": winner, "finding_id": "F1", "reason": "Narrower.",
                                              "rule": stricter})
        code, _, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        revision = self.root / ".harness/research/repro.rules.yaml"
        relay = cli.confirm(self.decision, "intake-3", "--revision", revision)
        code, output, stderr = cli("select", "--input", selection, "--root", self.root, "--decision", self.decision,
                                   "--revision", revision, "--evidence", self.root / ".scratch/workers.json",
                                   "--relay", relay)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output["pending_question"], (output["question"] or {}).get("id")),
                         ("script_checkable", "script_checkable"))

        unanswerable = {**pinned, "params": {**pinned["params"], "when": {"eq": ["made_up_fact", True]}}}
        request = cli.write("override-2.json", {"rule_id": winner, "finding_id": "F1", "reason": "Unanswerable.",
                                                "rule": unanswerable})
        code, output, _ = cli("override", "--root", self.root, "--input", request)
        self.assertEqual(code, 1)
        self.assertTrue(any("made_up_fact, which no intake question asks" in p for p in output["problems"]))
        wrong_value = {**pinned, "params": {**pinned["params"], "when": {"eq": ["script_checkable", "yes"]}}}
        request = cli.write("override-3.json", {"rule_id": winner, "finding_id": "F1", "reason": "Wrong value.",
                                                "rule": wrong_value})
        code, output, _ = cli("override", "--root", self.root, "--input", request)
        self.assertEqual(code, 1)
        self.assertTrue(any("script_checkable is never 'yes'" in p for p in output["problems"]), output["problems"])

    def test_unresolved_compatibility_drift_blocks_the_affected_part(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        path = self.root / ".harness/research/repro.md"
        record = load_document(path, "research")
        record["drift"] = [{"rule_id": "skills.description.length", "finding_id": record["findings"][0]["id"],
                            "summary": "The vendor raised the description limit.", "category": "compatibility"}]
        write_research(self.root, record, "Drift recorded.\n")
        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 2, "recorded drift changes the contract")
        self.assertIn("does not confirm", stderr)
        self.reconfirm(cli, arguments)
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 1, stderr)
        self.assertEqual(output["parts"][0]["status"], "blocked")
        self.assertEqual(output["parts"][0]["harness_result"], "not verified")
        self.assertIn("unresolved compatibility drift on skills.description.length",
                      (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8"))

        option = arguments.index("--slug") + 1
        code, output, stderr = cli("verify", *arguments[:option], "different-report", *arguments[option + 1:])
        self.assertEqual(code, 1, stderr)
        self.assertEqual(output["parts"][0]["status"], "blocked", "the report's name does not hide the drift")

    def test_overrides_or_drift_added_during_review_invalidate_the_run(self):
        root = self.root

        def add_override():
            research = load_document(root / ".harness/research/repro.md", "research")
            override = {**OVERRIDE, "drift": {"finding": research["findings"][0], "support_verdict": "supported"}}
            (root / ".harness/overrides").mkdir(parents=True, exist_ok=True)
            (root / ".harness/overrides/skills.description.concise.yaml").write_text(yaml.safe_dump(override),
                                                                                     encoding="utf-8")

        def add_drift():
            record = load_document(root / ".harness/research/repro.md", "research")
            record["drift"] = [{"rule_id": "skills.description.length", "finding_id": record["findings"][0]["id"],
                                "summary": "The vendor raised the description limit.", "category": "compatibility"}]
            write_research(root, record, "Drift recorded.\n")

        for change in (add_override, add_drift):
            with self.subTest(change=change.__name__):
                class Changing(ResearchRunner):
                    def __call__(self, command, *, cwd, env, stdin, timeout):
                        answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                        if self.roles and self.roles[-1] == "review" and stdin and "```json" in stdin:
                            change()
                        return answer

                self.tearDown()
                self.setUp()
                root = self.root
                cli, arguments = self.build_ready(Changing([finding()]))
                code, output, stderr = cli("verify", *arguments)
                self.assertEqual(code, 1, stderr)
                self.assertEqual(output["parts"][0]["harness_result"], "not verified")

    def test_an_empty_revision_still_binds_its_brief(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        proposed = cli.write("proposed-empty.json", {"requirements": []})
        choices = cli.write("choices-empty.json", {})
        code, output, stderr = cli("accept", "--decision", self.decision, "--part", "A", "--proposed", proposed,
                                   "--choices", choices, "--root", self.root, "--slug", "repro")
        self.assertEqual((code, output["rule_ids"]), (0, []), stderr)
        text = Path(self.decision).read_text(encoding="utf-8")
        Path(self.decision).write_text(text.replace("goal: Write a reproduction checklist when asked",
                                                    "goal: Write a security vulnerability report"), encoding="utf-8")
        revision = self.root / ".harness/research/repro.rules.yaml"
        relay = cli.confirm(self.decision, "verify-1", "--revision", revision)
        option = arguments.index("--relay") + 1
        code, _, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 2)
        self.assertIn("researched for another brief", stderr)

    def test_another_parts_requirements_do_not_cover_a_built_part(self):
        from harness_forge.models import AcceptedRevision
        from harness_forge.research import make_brief, revision_digest, write_revision
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        record = load_decision(self.decision)
        record.record["parts"].append(part("B", "Never merge without review", outcome="outside-instead",
                                           recommended=None, chosen=None))
        # A requirements file that holds only B's (empty) acceptance, as an edit could leave it.
        briefs = {"B": make_brief(record, "deep", "B").digest}
        only_b = AcceptedRevision(rule_ids=[], rules=[], brief_digests=briefs, digest=revision_digest(briefs, []))
        revision = write_revision(self.root, "repro", only_b)
        record.record["accepted_revision"] = {"digest": only_b.digest, "rule_ids": []}
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        relay = cli.confirm(self.decision, "verify-1", "--revision", revision)
        option = arguments.index("--relay") + 1
        code, _, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 2)
        self.assertIn("part A has no accepted requirements", stderr)

    def conflict(self):
        """Part A chose a skill although its own-context answer rules skills out (the Claude Code trial's case)."""
        record = load_decision(self.decision)
        record.part("A")["answers"]["own_context"] = True
        record.part("A").update(recommended="subagents", override_reason="I prefer a skill")
        write_decision(self.root, record, slug="repro", date="2026-10-10")

    def test_select_reports_a_goal_conflict_for_the_chosen_mechanism(self):
        cli = Cli(FakeRunner(), self.root)
        record = load_decision(self.decision)
        request = cli.write("select.json", {"answers": {**record.part("A")["answers"], "own_context": True},
                                            "whole_idea": record.record["facts"], "chosen": "skills",
                                            "goal": record.part("A")["goal"]})
        code, output, stderr = cli("select", "--input", request)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["mechanism"], "subagents")
        self.assertEqual(output["goal_conflict"]["severity"], "error")
        self.assertEqual(output["goal_conflict"]["choices"], ["recommended", "adjust-goal", "keep"])

    def test_build_waits_for_the_goal_conflict_and_verify_keeps_its_error(self):
        self.conflict()
        cli = Cli(ResearchRunner([finding()]), self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.confirm(self.decision, "verify-1")
        build = ["build", "--decision", self.decision, "--part", "A", "--evidence", evidence, "--relay", relay,
                 "--description", cli.write("description.txt", DESCRIPTION),
                 "--procedure", cli.write("procedure.md", PROCEDURE), "--root", self.root]
        code, _, stderr = cli(*build)
        self.assertEqual(code, 2)
        self.assertIn("goal conflict", stderr)
        self.assertIn("selection.eligibility.own-context-skills", stderr)

        code, output, stderr = cli("resolve-conflict", "--decision", self.decision, "--part", "A", "--choice", "keep",
                                   "--reason", "A skill is what my team knows.")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output["next_stage"], output["remaining_error_ids"]),
                         ("report", ["selection.eligibility.own-context-skills"]))
        self.assertEqual(load_decision(self.decision).part("A")["goal_conflict"]["choice"], "keep")
        code, _, stderr = cli(*build)
        self.assertEqual(code, 2, "the changed record needs a new confirmation")
        self.assertIn("does not confirm", stderr)
        relay = cli.confirm(self.decision, "verify-2")
        build[build.index("--relay") + 1] = relay
        code, _, stderr = cli(*build)
        self.assertEqual(code, 2, "a build still needs the accepted requirements")
        self.assertIn("--revision is required", stderr)

    def test_verify_reports_a_kept_conflict_as_an_error(self):
        self.conflict()
        code, _, stderr = Cli(FakeRunner(), self.root)("resolve-conflict", "--decision", self.decision, "--part", "A",
                                                       "--choice", "keep", "--reason", "A skill is what my team knows.")
        self.assertEqual(code, 0, stderr)
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 1, stderr)
        self.assertEqual(output["parts"][0]["harness_result"], "not verified")
        [conflict] = output["goal_conflicts"]
        self.assertEqual({key: conflict[key] for key in ("part_id", "severity", "chosen", "recommended", "kept",
                                                          "rule_ids", "choices")},
                         {"part_id": "A", "severity": "error", "chosen": "skills", "recommended": "subagents",
                          "kept": True, "rule_ids": ["selection.eligibility.own-context-skills"],
                          "choices": ["recommended", "adjust-goal", "keep"]})
        self.assertTrue(conflict["goal"] and conflict["pros"] and conflict["cons"] and conflict["adjust"])
        self.assertTrue(conflict["failing"][0]["statement"] and conflict["failing"][0]["sources"])
        report = (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        line = next(line for line in report.splitlines() if line.startswith("| `selection.goal-conflict`"))
        self.assertIn("| error | script | fail | skills is ruled out by selection.eligibility.own-context-skills; "
                      "the user kept it", line)
        self.assertTrue(line.rstrip(" |").endswith(", ".join(conflict["failing"][0]["sources"])))

    def test_a_stale_recommendation_is_not_built(self):
        record = load_decision(self.decision)
        record.part("A")["recommended"] = "subagents"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        cli = Cli(FakeRunner(), self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.confirm(self.decision, "verify-1")
        code, _, stderr = cli("build", "--decision", self.decision, "--part", "A", "--evidence", evidence,
                              "--relay", relay, "--description", cli.write("description.txt", DESCRIPTION),
                              "--procedure", cli.write("procedure.md", PROCEDURE), "--root", self.root)
        self.assertEqual(code, 2)
        self.assertIn("is not what its answers give now", stderr)

    def reconfirm(self, cli, arguments, run_id="verify-drift"):
        """A new confirmation after the contract changed, such as recorded drift; updates arguments in place."""
        relay = cli.confirm(self.decision, run_id, "--revision", self.root / ".harness/research/repro.rules.yaml")
        arguments[arguments.index("--relay") + 1] = relay
        return relay

    def record_drift(self, cli, rule="skills.description.length", category="compatibility"):
        code, output, stderr = cli("drift", "--root", self.root, "--slug", "repro", "--finding", "F1", "--rule", rule,
                                   "--category", category, "--summary", "The vendor raised the limit.")
        self.assertEqual(code, 0, stderr)
        return output

    def test_drift_is_recorded_reported_upstream_and_decided(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        filed = []
        cli.module.ISSUES = (lambda rule_id: [], lambda proposal: filed.append(proposal) or "https://github.com/k/b/issues/3")
        output = self.record_drift(cli)
        self.assertEqual((output["entry"]["category"], output["blocking"]), ("compatibility", True))
        self.reconfirm(cli, arguments)
        code, output, _ = cli("verify", *arguments)
        self.assertEqual((code, output["parts"][0]["status"]), (1, "blocked"))
        self.assertEqual(output["drift"], [{"rule_id": "skills.description.length", "finding_id": "F1",
                                            "category": "compatibility", "state": "open", "blocking": True,
                                            "blocking_parts": ["A"], "issue": None}])

        proposal_path = self.root / ".scratch/proposal.json"
        code, proposal, stderr = cli("drift-issue", "--root", self.root, "--rule", "skills.description.length",
                                     "--finding", "F1", "--run-id", "drift-1", "--out", proposal_path)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(proposal["kind"], "issue")
        self.assertIn("Rule `skills.description.length`", proposal["body"])
        for choice, expected in (("pending", "pending"), ("decline", "not published")):
            relay = cli.write(f"issue-{choice}.json", {"choice": choice, "displayed_digest": proposal["digest"],
                                                       "run_id": "drift-1"})
            code, output, stderr = cli("publish-issue", "--root", self.root, "--proposal", proposal_path,
                                       "--relay", relay)
            self.assertEqual((code, output["issue"]), (0, expected), stderr)
            code, output, _ = cli("verify", *arguments)
            self.assertEqual(output["parts"][0]["status"], "blocked", "no issue answer unblocks the part")
            self.assertEqual(output["drift"][0]["issue"], expected)
        self.assertEqual(filed, [])

        code, output, stderr = cli("drift-decision", "--decision", self.decision, "--root", self.root, "--rule",
                                   "skills.description.length", "--finding", "F1", "--choice", "keep",
                                   "--reason", "The pinned limit is the portable one.")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 2, "the decision changed the contract")
        option = arguments.index("--relay") + 1
        relay = cli.confirm(self.decision, "verify-2", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, output, stderr = cli("prepare-contract", "--decision", self.decision, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml")
        self.assertIn("Drift decisions:\n  - skills.description.length (finding F1 of research repro): keep the "
                      "pinned rule (The pinned limit is the portable one.). Drift: The vendor raised the limit. "
                      "Source: https://docs.example-vendor.com/repro/platforms", output["text"])
        code, output, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["drift"][0]["state"], "kept")
        research = self.root / ".harness/research/repro.md"
        research.write_text(research.read_text(encoding="utf-8").replace("The vendor raised the limit.",
                                                                         "The vendor removed the limit."),
                            encoding="utf-8")
        code, _, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 2, "the edited drift changed the contract")
        relay = cli.confirm(self.decision, "verify-3", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, output, _ = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual((code, output["drift"][0]["state"]), (1, "open"), "the keep covered the earlier drift only")
        request = cli.write("override.json", {"rule_id": "skills.description.length", "finding_id": "F1",
                                              "reason": "Adopt after all.", "retired": True})
        code, output, _ = cli("override", "--root", self.root, "--input", request)
        self.assertEqual(output["override"]["link"], "not published", "the earlier issue answer carries over")

    def test_adopting_drift_takes_a_confirmed_override_and_an_approved_issue_is_linked(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        cli.module.ISSUES = (lambda rule_id: [], lambda proposal: "https://github.com/k/b/issues/3")
        self.record_drift(cli)
        code, _, stderr = cli("drift-decision", "--decision", self.decision, "--root", self.root, "--rule",
                              "skills.description.length", "--finding", "F1", "--choice", "adopt", "--reason", "x")
        self.assertEqual(code, 2)
        self.assertIn("forge.py override", stderr)
        pinned = next(rule for rule in yaml.safe_load((FORGE / "snapshot/rules/skills.yaml").read_text())
                      if rule["id"] == "skills.description.length")
        request = cli.write("override.json", {"rule_id": "skills.description.length", "finding_id": "F1",
                                              "reason": "The vendor raised the limit.",
                                              "rule": {**pinned, "params": {"max_chars": 2048}}})
        code, _, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        code, _, stderr = cli("drift-decision", "--decision", self.decision, "--root", self.root, "--rule",
                              "skills.description.length", "--finding", "F1", "--choice", "adopt",
                              "--reason", "The vendor raised the limit.")
        self.assertEqual(code, 0, stderr)

        proposal_path = self.root / ".scratch/proposal.json"
        _, proposal, _ = cli("drift-issue", "--root", self.root, "--rule", "skills.description.length",
                             "--finding", "F1", "--run-id", "drift-1", "--out", proposal_path)
        relay = cli.write("approve.json", {"choice": "approve", "displayed_digest": proposal["digest"],
                                           "run_id": "drift-1"})
        code, output, stderr = cli("publish-issue", "--root", self.root, "--proposal", proposal_path, "--relay", relay)
        self.assertEqual((code, output["issue"]), (0, "https://github.com/k/b/issues/3"), stderr)
        self.assertEqual(load_overrides(self.root)[0]["link"], "https://github.com/k/b/issues/3")

        option = arguments.index("--relay") + 1
        relay = cli.confirm(self.decision, "verify-2", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, output, stderr = cli("verify", *arguments[:option], relay, *arguments[option + 1:])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["drift"][0]["state"], "adopted")

    def test_break_fix_and_review_scopes(self):
        record = load_decision(self.decision)
        record.record["parts"].append(part("B", "Never merge without review", outcome="outside-instead",
                                           recommended=None, chosen=None))
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        runner = ResearchRunner([finding()])
        original_confirm = Cli.confirm

        def confirm_both(cli, decision, run_id, *extra):
            cli.parts = ["A", "B"]
            return original_confirm(cli, decision, run_id, *extra)

        Cli.confirm = confirm_both
        try:
            cli, arguments = self.build_ready(runner)
        finally:
            Cli.confirm = original_confirm
        revision = ["--revision", self.root / ".harness/research/repro.rules.yaml"]
        workers = self.root / ".scratch/workers.json"

        def scope(fixed="A"):
            relay = arguments[arguments.index("--relay") + 1]
            code, output, stderr = cli("scope", "--decision", self.decision, "--root", self.root, *revision,
                                       "--evidence", workers, "--relay", relay, "--slug", "repro", "--fixed", fixed,
                                       "--cases", FORGE / "evals/smoke/vertical.yaml")
            self.assertEqual(code, 0, stderr)
            return output

        def choose(option, run_id="verify-1"):
            return cli.write(f"scope-{option['digest'][:8]}.json", {"choice": "review",
                                                                    "displayed_digest": option["digest"],
                                                                    "run_id": run_id})

        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A", "--scope-relay",
                              cli.write("none.json", {"choice": "review", "displayed_digest": "0" * 64,
                                                      "run_id": "verify-1"}))
        self.assertEqual(code, 2, "a Review needs an earlier report to compare with")
        self.assertIn("no earlier report", stderr)
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)
        self.assertEqual([p["harness_result"] for p in output["parts"]], ["verified", "not applicable"])

        skill = self.root / f".agents/skills/{NAME}/SKILL.md"
        original = skill.read_text(encoding="utf-8")
        skill.write_text(original.replace(f"name: {NAME}", "name: Broken_Name"), encoding="utf-8")
        code, output, _ = cli("verify", *arguments)
        self.assertEqual((code, output["parts"][0]["harness_result"]), (1, "not verified"))
        skill.write_text(original, encoding="utf-8")  # the builder's repair of the requested artifact

        code, _, stderr = cli("scope", "--decision", self.decision, "--root", self.root, *revision,
                              "--evidence", workers, "--slug", "repro", "--fixed", "A")
        self.assertEqual(code, 2, "the scope is computed for the confirmed contract")
        review, extensive = scope()["options"]
        self.assertEqual((review["label"], review["parts"]), ("Review (recommended)", ["A"]))
        [kept] = review["kept"]
        self.assertEqual((kept["part_id"], kept["run_id"], kept["harness_result"]), ("B", "verify-1", "not applicable"))
        self.assertTrue(kept["judgments"] and all({"rule_id", "result", "reason"} <= set(item)
                                                  for item in kept["judgments"]), "the exact kept judgments")
        self.assertRegex(review["digest"], r"^[0-9a-f]{64}$")
        self.assertEqual((extensive["parts"], extensive["kept"]), (["A", "B"], []))
        self.assertGreater(review["judged_rules"], 0)
        self.assertGreater(extensive["judged_rules"], review["judged_rules"])

        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A")
        self.assertEqual(code, 2, "keeping earlier results needs the user's choice of the displayed scope")
        self.assertIn("--scope-relay", stderr)
        wrong = cli.write("scope-wrong.json", {"choice": "review", "displayed_digest": "0" * 64, "run_id": "verify-1"})
        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A", "--scope-relay", wrong)
        self.assertEqual(code, 2)
        self.assertIn("not the Review the user chose", stderr)
        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A", "--scope-relay",
                              choose(review, run_id="another-session"))
        self.assertEqual(code, 2, "the scope answer belongs to this run's confirmation")
        self.assertIn("not the Review the user chose", stderr)

        readme = self.root / "README.md"
        before = readme.read_text(encoding="utf-8")
        readme.write_text(before + "\nChanged after the user chose.\n", encoding="utf-8")
        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A", "--scope-relay", choose(review))
        self.assertEqual(code, 2, "an input changed after the choice changes the plan")
        readme.write_text(before, encoding="utf-8")

        roles = len(runner.roles)
        code, output, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A", "--scope-relay",
                                   choose(review))
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output["judged_parts"], output["kept_parts"]), (["A"], ["B"]))
        self.assertEqual([p["harness_result"] for p in output["parts"]], ["verified", "not applicable"])
        self.assertEqual(runner.roles[roles:], ["review", "baseline", "baseline", "runtime", "runtime", "runtime"])
        report = (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        self.assertIn("- Review scope: Review (recommended): judged A; kept from run verify-1: B", report)
        self.assertIn("(kept from run verify-1)", report)

        code, output, stderr = cli("verify", *arguments, "--scope", "extensive")
        self.assertEqual(code, 0, stderr)
        report = (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        self.assertIn("- Review scope: Extensive review (rarely needed): judged A, B", report)
        self.assertNotIn("(kept from run", report)

        # A changed contract preserves nothing: the displayed Review is the executed one.
        record = load_decision(self.decision)
        record.part("B")["goal"] = "Never merge an agent's change without a review"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        cli.parts = ["A", "B"]
        arguments[arguments.index("--relay") + 1] = cli.confirm(self.decision, "verify-3", *revision)
        review = scope(fixed="")["options"][0]
        self.assertEqual((review["parts"], review["kept"]), (["A", "B"], []))
        code, output, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "", "--scope-relay",
                                   choose(review, run_id="verify-3"))
        self.assertEqual((code, output["judged_parts"]), (0, review["parts"]), stderr)

        # A retired part leaves the earlier report's parts behind without breaking the comparison.
        record = load_decision(self.decision)
        record.record["parts"] = [item for item in record.record["parts"] if item["id"] != "B"]
        record.record["retired_ids"] = ["B"]
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        self.assertEqual(cli("readme", "--decision", self.decision, "--root", self.root)[0], 0)
        cli.parts = ["A"]
        arguments[arguments.index("--relay") + 1] = cli.confirm(self.decision, "verify-4", *revision)
        self.assertEqual(scope(fixed="")["options"][0]["parts"], ["A"])

    def test_a_review_judges_every_part_whose_inputs_changed(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)
        (self.root / "README.md").write_text((self.root / "README.md").read_text(encoding="utf-8") + "\nMore.\n",
                                             encoding="utf-8")
        code, output, stderr = cli("scope", "--decision", self.decision, "--root", self.root, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml", "--slug", "repro", "--fixed", "",
                                   "--evidence", self.root / ".scratch/workers.json",
                                   "--relay", arguments[arguments.index("--relay") + 1])
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output["changed"], output["options"][0]["parts"]), (["A"], ["A"]))

    def test_a_review_carries_a_kept_parts_runtime_results(self):
        """Kept results keep their runtime evidence, inventory included, in the choice and the report."""
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        code, first, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)
        relay = arguments[arguments.index("--relay") + 1]
        code, output, stderr = cli("scope", "--decision", self.decision, "--root", self.root, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml", "--slug", "repro", "--fixed", "",
                                   "--evidence", self.root / ".scratch/workers.json", "--relay", relay,
                                   "--cases", arguments[arguments.index("--cases") + 1])
        self.assertEqual(code, 0, stderr)
        review = output["options"][0]
        self.assertEqual(review["parts"], [])
        [kept] = review["kept"]
        self.assertEqual([(item["case_id"], item["result"], item["inventory"]) for item in kept["runtime"]],
                         [(item["case_id"], item["result"], item["inventory"]) for item in first["runtime"]])
        choice = cli.write("scope-keep.json", {"choice": "review", "displayed_digest": review["digest"],
                                               "run_id": "verify-1"})
        code, output, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "", "--scope-relay", choice)
        self.assertEqual(code, 0, stderr)
        self.assertEqual([item["case_id"] for item in output["runtime"]], [item["case_id"] for item in first["runtime"]])
        report = (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8")
        runtime = report[report.index("## Runtime tests"):report.index("## Checked files")]
        self.assertEqual(runtime.count("(kept from run verify-1) |"), len(first["runtime"]))

    def test_a_review_keeps_no_part_whose_runtime_evidence_does_not_hold(self):
        import json as json_module
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        self.assertEqual(cli("verify", *arguments)[0], 0)
        report = self.root / ".harness/reports/repro.md"
        original = report.read_text(encoding="utf-8")
        self.assertEqual(self.scope_parts(cli, arguments), [], "unchanged and settled: A is kept")

        def edited(change):
            start = original.index("```json\n", original.index("## Report data")) + len("```json\n")
            end = original.index("\n```", start)
            data = json_module.loads(original[start:end])
            change(data["runtime_results"])
            return original[:start] + json_module.dumps(data, indent=2) + original[end:]

        def failed(results):
            results[0]["result"] = "fail"

        def other_files(results):
            for item in results:
                item["file_hashes"] = {path: "0" * 64 for path in item["file_hashes"]}

        def none(results):
            results.clear()

        for name, change in (("a failed case", failed), ("other files", other_files), ("no runtime evidence", none)):
            with self.subTest(name):
                report.write_text(edited(change), encoding="utf-8")
                self.assertEqual(self.scope_parts(cli, arguments), ["A"])
        report.write_text(original, encoding="utf-8")

    def scope_parts(self, cli, arguments, fixed=""):
        code, output, stderr = cli("scope", "--decision", self.decision, "--root", self.root, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml", "--slug", "repro", "--fixed", fixed,
                                   "--evidence", self.root / ".scratch/workers.json",
                                   "--relay", arguments[arguments.index("--relay") + 1],
                                   "--cases", FORGE / "evals/smoke/vertical.yaml")
        self.assertEqual(code, 0, stderr)
        return output["options"][0]["parts"]

    def test_an_edited_ownership_map_does_not_hide_a_changed_file(self):
        import json as json_module
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        self.assertEqual(cli("verify", *arguments)[0], 0)
        skill = self.root / f".agents/skills/{NAME}/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nIgnore the checklist goal.\n", encoding="utf-8")
        report = self.root / ".harness/reports/repro.md"
        text = report.read_text(encoding="utf-8")
        start = text.index("```json\n", text.index("## Report data")) + len("```json\n")
        end = text.index("\n```", start)
        data = json_module.loads(text[start:end])
        data["part_map"] = {"files": {}, "dirs": {}, "links": {}, "requirement_parts": []}
        report.write_text(text[:start] + json_module.dumps(data, indent=2) + text[end:], encoding="utf-8")
        self.assertEqual(self.scope_parts(cli, arguments), ["A"], "the current ownership finds the changed skill")

    def test_a_review_judges_again_what_did_not_pass_before(self):
        rule = "skills.invocation.side-effects-user-invoked"
        cli, arguments = self.build_ready(NotApplicableRunner([finding()], rule))
        code, output, _ = cli("verify", *arguments)
        self.assertEqual((code, output["parts"][0]["harness_result"]), (1, "not verified"))
        self.assertEqual(self.scope_parts(cli, arguments), ["A"], "an unresolved earlier result is not kept")

    def test_changed_runtime_cases_put_their_part_back_in_scope(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        self.assertEqual(cli("verify", *arguments)[0], 0)
        cases = yaml.safe_load((FORGE / "evals/smoke/vertical.yaml").read_text(encoding="utf-8"))
        for item in cases["cases"]:
            if item["mode"] == "negative":
                item["prompt"] = "Summarize this changelog for me."
        changed = cli.write("changed-cases.yaml", yaml.safe_dump(cases))
        code, output, stderr = cli("scope", "--decision", self.decision, "--root", self.root, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml", "--slug", "repro", "--fixed", "",
                                   "--evidence", self.root / ".scratch/workers.json",
                                   "--relay", arguments[arguments.index("--relay") + 1], "--cases", changed)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["options"][0]["parts"], ["A"], "a changed case is a changed input of its part")

    def test_a_report_from_before_re_verification_only_rules_out_a_review(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        report = self.root / ".harness/reports/repro.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# Verification report\n\nWritten before T12.\n", encoding="utf-8")
        code, _, stderr = cli("verify", *arguments, "--scope", "review", "--fixed", "A")
        self.assertEqual(code, 2)
        self.assertIn("earlier report cannot be compared", stderr)
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)

    def test_resolve_conflict_applies_overrides_as_select_does(self):
        cli, _ = self.build_ready(ResearchRunner([finding()]))
        request = cli.write("override.json", {"rule_id": "selection.eligibility.own-context-skills", "finding_id": "F1",
                                              "reason": "Skills can fork their own context.", "retired": True})
        code, _, stderr = cli("override", "--root", self.root, "--input", request, "--write")
        self.assertEqual(code, 0, stderr)
        self.conflict()
        code, _, stderr = cli("resolve-conflict", "--decision", self.decision, "--part", "A", "--choice", "keep",
                              "--reason", "x")
        self.assertEqual(code, 0, "unconfirmed, the pinned rules still see the conflict")
        relay = cli.confirm(self.decision, "intake-3", "--revision", self.root / ".harness/research/repro.rules.yaml")
        code, _, stderr = cli("resolve-conflict", "--decision", self.decision, "--part", "A", "--choice", "keep",
                              "--reason", "x", "--revision", self.root / ".harness/research/repro.rules.yaml",
                              "--evidence", self.root / ".scratch/workers.json", "--relay", relay)
        self.assertEqual(code, 2, "under a confirmation the retired rule leaves no conflict, as select shows")
        self.assertIn("no goal conflict", stderr)

    def test_a_missing_github_cli_is_reported(self):
        cli, _ = self.build_ready(ResearchRunner([finding()]))
        self.record_drift(cli)

        def missing(rule_id):
            raise FileNotFoundError("gh")

        cli.module.ISSUES = (missing, missing)
        code, _, stderr = cli("drift-issue", "--root", self.root, "--rule", "skills.description.length",
                              "--finding", "F1", "--run-id", "drift-1", "--out", self.root / ".scratch/p.json")
        self.assertEqual(code, 2)
        self.assertIn("searching the KB's issues failed", stderr)

    def test_drift_on_a_selection_rule_blocks_the_parts_it_selected(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        record = load_decision(self.decision)
        request = cli.write("select.json", {"answers": record.part("A")["answers"],
                                            "whole_idea": record.record["facts"]})
        _, selected, _ = cli("select", "--input", request)
        winner = next(row["rules"][0] for row in selected["assessment"] if row["state"] == "recommended")
        self.record_drift(cli, rule=winner)
        self.reconfirm(cli, arguments)
        code, output, _ = cli("verify", *arguments)
        self.assertEqual((code, output["parts"][0]["status"]), (1, "blocked"))
        self.assertIn(f"unresolved compatibility drift on {winner}",
                      (self.root / ".harness/reports/repro.md").read_text(encoding="utf-8"))
        option = dict(zip(arguments[::2], arguments[1::2]))
        code, _, stderr = cli("build", "--decision", self.decision, "--part", "A", "--root", self.root,
                              *[item for key in ("--revision", "--evidence", "--relay") for item in (key, option[key])],
                              "--description", cli.write("description.txt", DESCRIPTION),
                              "--procedure", cli.write("procedure.md", PROCEDURE))
        self.assertEqual(code, 2)
        self.assertIn(f"open compatibility drift on {winner}", stderr)

    def test_build_and_verify_share_one_drift_policy(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        option = dict(zip(arguments[::2], arguments[1::2]))
        build = ["build", "--decision", self.decision, "--part", "A", "--root", self.root,
                 *[item for key in ("--revision", "--evidence", "--relay") for item in (key, option[key])],
                 "--description", cli.write("description.txt", DESCRIPTION),
                 "--procedure", cli.write("procedure.md", PROCEDURE)]
        # Drift on a rule that does not apply to A (it needs every_time: always) blocks neither step.
        self.record_drift(cli, rule="skills.goal.no-must-hold-rule-in-skill")
        build[build.index("--relay") + 1] = self.reconfirm(cli, arguments)
        self.assertEqual(cli(*build)[0], 0, "build agrees with verify that the rule does not apply")
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual(code, 0, stderr)
        self.assertEqual((output["drift"][0]["blocking"], output["drift"][0]["blocking_parts"]), (False, []),
                         "the report agrees too")
        # A security rule's drift is security drift whatever its recorded category says.
        path = self.root / ".harness/research/repro.md"
        record = load_document(path, "research")
        record["drift"] = [{"rule_id": "security.secrets.none-in-files", "finding_id": "F1", "summary": "Edited.",
                            "category": "ordinary"}]
        write_research(self.root, record, "Edited.\n")
        build[build.index("--relay") + 1] = self.reconfirm(cli, arguments, run_id="verify-drift-2")
        code, _, stderr = cli(*build)
        self.assertEqual(code, 2)
        self.assertIn("open security drift on security.secrets.none-in-files", stderr)
        code, output, _ = cli("verify", *arguments)
        self.assertEqual((code, output["drift"][0]["blocking"], output["drift"][0]["category"]), (1, True, "security"))
        self.assertEqual(output["drift"][0]["blocking_parts"], ["A"])

    def test_a_report_that_cannot_be_rendered_is_a_diagnostic(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))

        def too_long(run, results):
            raise ValueError("report: parts/0/rule_results/0/reason: too long")

        cli.module.render_report = too_long
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual((code, output), (2, None))
        self.assertIn("the report could not be written: report: parts/0/rule_results/0/reason", stderr)

    def test_a_file_that_cannot_be_written_is_a_diagnostic(self):
        cli = Cli(FakeRunner(), self.root)
        readme = self.root / "README.md"
        readme.chmod(0o444)
        try:
            code, output, stderr = cli("readme", "--decision", self.decision, "--root", self.root)
        finally:
            readme.chmod(0o644)
        self.assertEqual((code, output), (2, None))
        self.assertIn("forge.py:", stderr)
        self.assertIn("README.md", stderr)

    def test_a_report_path_that_cannot_be_written_is_a_diagnostic(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        (self.root / ".harness/reports/repro.md").mkdir(parents=True)
        code, output, stderr = cli("verify", *arguments)
        self.assertEqual((code, output), (2, None))
        self.assertIn("the report could not be written", stderr)

    def test_finding_ids_never_repeat_a_dropped_findings_id(self):
        runner = ResearchRunner([finding(quote="This sentence is not on the page.")])
        cli = Cli(runner, self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.confirm(self.decision, "intake-1")
        research = ["research", "--decision", self.decision, "--harness", "claude-code", "--evidence", evidence,
                    "--relay", relay, "--root", self.root, "--slug", "repro", "--write"]
        code, output, stderr = cli(*research, "--pass", "survey")
        self.assertEqual((code, output["kept"]), (0, []), stderr)
        runner.findings = [finding()]
        code, output, stderr = cli(*research, "--pass", "deep", "--part", "A")
        self.assertEqual(code, 0, stderr)
        record = load_document(self.root / ".harness/research/repro.md", "research")
        ids = [entry["finding_id"] for entry in record["support"]]
        self.assertEqual(len(ids), len(set(ids)), "one support entry per finding id")
        self.assertEqual([f["id"] for f in record["findings"]], ["F2"], "the dropped survey finding was F1")
        self.assertEqual(self.record_drift_for("F2")["entry"]["finding_id"], "F2")

    def test_drift_whose_finding_research_replaced_is_superseded_explicitly(self):
        runner = ResearchRunner([finding()])
        cli = Cli(runner, self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        research = ["research", "--decision", self.decision, "--harness", "claude-code", "--evidence", evidence,
                    "--root", self.root, "--slug", "repro", "--write", "--pass", "deep", "--part", "A"]
        self.assertEqual(cli(*research, "--relay", cli.confirm(self.decision, "intake-1"))[0], 0)
        self.record_drift_for("F1")
        # Research again: F1 is replaced by F2, and the F1 drift is left without its finding.
        code, output, stderr = cli(*research, "--relay", cli.confirm(self.decision, "intake-2"))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["dangling_drift"], [{"rule_id": "skills.description.length", "finding_id": "F1"}])
        code, _, stderr = cli("drift-decision", "--decision", self.decision, "--root", self.root, "--rule",
                              "skills.description.length", "--finding", "F1", "--choice", "keep", "--reason", "x")
        self.assertEqual(code, 2)
        self.assertIn("finding F1 is no longer in research repro", stderr)
        output = self.record_drift_for("F2")
        self.assertEqual(output["superseded"], ["F1"])
        record = load_document(self.root / ".harness/research/repro.md", "research")
        self.assertEqual([entry["finding_id"] for entry in record["drift"]], ["F2"])

    def record_drift_for(self, finding_id):
        cli = Cli(FakeRunner(), self.root)
        code, output, stderr = cli("drift", "--root", self.root, "--slug", "repro", "--finding", finding_id, "--rule",
                                   "skills.description.length", "--category", "compatibility", "--summary", "x")
        self.assertEqual(code, 0, stderr)
        return output

    def test_research_covers_only_confirmed_parts(self):
        record = load_decision(self.decision)
        record.record["parts"].append(part("B", "Never merge without review", outcome="outside-instead",
                                           recommended=None, chosen=None))
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        runner = ResearchRunner([finding()])
        cli = Cli(runner, self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.confirm(self.decision, "intake-1")  # names part A only
        research = ["research", "--decision", self.decision, "--harness", "claude-code", "--evidence", evidence,
                    "--relay", relay, "--root", self.root, "--slug", "repro"]
        for extra, message in ((["--pass", "deep", "--part", "B"], "part B is not confirmed"),
                               (["--pass", "survey"], "the survey brief holds part B, which is not confirmed")):
            with self.subTest(extra=extra):
                code, _, stderr = cli(*research, *extra)
                self.assertEqual(code, 2)
                self.assertIn(message, stderr)
        self.assertEqual(runner.worker_calls, [], "no worker starts")
        code, _, stderr = cli(*research, "--pass", "deep", "--part", "A")
        self.assertEqual(code, 0, stderr)

    def test_recorded_answers_must_be_declared_facts_of_their_scope(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        for change, message in (
            (lambda record: record.part("A")["answers"].update(target_set="codex"),
             "part A answers target_set, which is not a part question"),
            (lambda record: record.part("A")["answers"].update(every_time="sometimes"),
             "part A answers every_time with 'sometimes'"),
            (lambda record: record.record["facts"].update(job="procedure"),
             "the idea answers job, which is not a whole-idea question"),
        ):
            with self.subTest(message=message):
                record = load_decision(self.decision)
                original = load_decision(self.decision)
                change(record)
                write_decision(self.root, record, slug="repro", date="2026-10-10")
                for command in (["verify", *arguments], ["prepare-contract", "--decision", self.decision, "--revision",
                                                         self.root / ".harness/research/repro.rules.yaml"]):
                    code, _, stderr = cli(*command)
                    self.assertEqual(code, 2, command[0])
                    self.assertIn(message, stderr)
                write_decision(self.root, original, slug="repro", date="2026-10-10")

    def test_two_skill_parts_each_accept_their_own_requirements(self):
        record = load_decision(self.decision)
        record.record["parts"].append(part("B", "Summarize a reproduction checklist", slug="checklist-summary"))
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        cli = Cli(ResearchRunner([finding()]), self.root)
        cli.parts = ["A", "B"]
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        revision = self.root / ".harness/research/repro.rules.yaml"
        for part_id in ("A", "B"):
            recorded = ["--revision", revision] if revision.exists() else []
            relay = cli.confirm(self.decision, f"intake-{part_id}", *recorded)
            code, _, stderr = cli("research", "--decision", self.decision, "--pass", "deep", "--part", part_id,
                                  "--harness", "claude-code", "--evidence", evidence, "--relay", relay,
                                  "--root", self.root, "--slug", "repro", "--write", *recorded)
            self.assertEqual(code, 0, stderr)
            rule_id = f"idea.repro.{part_id.lower()}.platform"
            proposed = cli.write(f"proposed-{part_id}.json", {"requirements": [
                {"id": rule_id, "concept": "idea", "kind": "must", "severity": "error", "check": "judged",
                 "statement": "The checklist names the platform", "applies_to": {"harnesses": ["claude-code"]},
                 "evidence": "Vendor", "sources": ["https://docs.example-vendor.com/repro/platforms"],
                 "contested": False}]})
            code, output, stderr = cli("accept", "--decision", self.decision, "--part", part_id, "--proposed", proposed,
                                       "--choices", cli.write(f"choices-{part_id}.json", {rule_id: "accept"}),
                                       "--root", self.root, "--slug", "repro")
            self.assertEqual(code, 0, stderr)
        accepted = load_revision(revision)
        self.assertEqual(accepted.rule_ids, ["idea.repro.a.platform", "idea.repro.b.platform"])
        self.assertEqual(sorted(accepted.brief_digests), ["A", "B"])

        def build(part_id, run_id, *extra):
            relay = cli.confirm(self.decision, run_id, "--revision", revision)
            return cli("build", "--decision", self.decision, "--part", part_id, "--revision", revision,
                       "--evidence", evidence, "--relay", relay, "--root", self.root,
                       "--description", cli.write("description.txt", DESCRIPTION),
                       "--procedure", cli.write("procedure.md", PROCEDURE), *extra)

        self.assertEqual(build("A", "build-a")[0], 0)
        self.assertEqual(build("B", "build-b")[0], 0)
        record = load_decision(self.decision)
        record.part("B")["goal"] = "Summarize a security report"
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, _, stderr = build("B", "build-b2")
        self.assertEqual(code, 2)
        self.assertIn("researched for another brief than part B's", stderr)
        code, _, stderr = build("A", "build-a2")
        self.assertEqual(code, 0, stderr)

        # Combining B into A retires B; the next acceptance drops B's requirements.
        from harness_forge.contracts import combine_parts
        write_decision(self.root, combine_parts(load_decision(self.decision), "A", "B"), slug="repro", date="2026-10-10")
        cli.parts = ["A"]
        code, _, stderr = build("A", "build-a3")
        self.assertEqual(code, 2)
        self.assertIn("accept the requirements of any part again", stderr)
        code, output, stderr = cli("accept", "--decision", self.decision, "--part", "A", "--proposed",
                                   self.root / ".scratch/proposed-A.json", "--choices",
                                   self.root / ".scratch/choices-A.json", "--root", self.root, "--slug", "repro")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(output["dropped_parts"], ["B"])
        self.assertEqual(sorted(load_revision(revision).brief_digests), ["A"])
        code, _, stderr = build("A", "build-a4", "--replace",
                                ".agents/skills/reproduction-checklist/references/requirements.md")
        self.assertEqual(code, 0, stderr)

    def test_acceptance_needs_the_parts_deep_research_and_its_sources(self):
        runner = ResearchRunner([finding()])
        cli = Cli(runner, self.root)
        requirement = {"id": "idea.repro.a.platform", "concept": "idea", "kind": "must", "severity": "error",
                       "check": "judged", "statement": "The checklist names the platform",
                       "applies_to": {"harnesses": ["claude-code"]}, "evidence": "Vendor",
                       "sources": ["https://docs.example-vendor.com/repro/platforms"], "contested": False}
        accept = ["accept", "--decision", self.decision, "--part", "A", "--root", self.root, "--slug", "repro",
                  "--choices", cli.write("choices.json", {"idea.repro.a.platform": "accept"})]
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed.json", {"requirements": [requirement]}))
        self.assertEqual(code, 2)
        self.assertIn("no deep research for part A", stderr)

        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.confirm(self.decision, "intake-1")
        code, _, stderr = cli("research", "--decision", self.decision, "--pass", "deep", "--part", "A", "--harness",
                              "claude-code", "--evidence", evidence, "--relay", relay, "--root", self.root,
                              "--slug", "repro", "--write")
        self.assertEqual(code, 0, stderr)
        unsourced = {**requirement, "sources": ["https://x.com/someone/status/1"]}
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed-x.json", {"requirements": [unsourced]}))
        self.assertEqual(code, 2)
        self.assertIn("cites https://x.com/someone/status/1, which no kept finding of part A's deep research has",
                      stderr)
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed.json", {"requirements": [requirement]}))
        self.assertEqual(code, 0, stderr)

        # A finding edited into the record, outside the brief and without support, is no provenance.
        path = self.root / ".harness/research/repro.md"
        record = load_document(path, "research")
        record["findings"].append({**record["findings"][0], "id": "F9", "source": "https://x.com/someone/status/1",
                                   "brief_digest": "0" * 64})
        write_research(self.root, record, "Edited.\n")
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed-x.json", {"requirements": [unsourced]}))
        self.assertEqual(code, 2)
        self.assertIn("which no kept finding of part A's deep research has", stderr)

        # A finding edited in place keeps its id, but not the checks its earlier content passed.
        replaced = {**requirement, "sources": ["https://docs.example-vendor.com/other"]}
        record = load_document(path, "research")
        record["findings"] = [{**record["findings"][0], "source": "https://docs.example-vendor.com/other",
                               "claim": "Another claim.", "quote": "Another quote."}]
        write_research(self.root, record, "Edited.\n")
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed-r.json", {"requirements": [replaced]}))
        self.assertEqual(code, 2)
        self.assertIn("which no kept finding of part A's deep research has", stderr)

        # Contradictory support entries for one finding make the record invalid, also when edited by hand.
        import yaml
        text = path.read_text(encoding="utf-8")
        _, frontmatter, body = text.split("---\n", 2)
        record = yaml.safe_load(frontmatter)
        record["support"].append({**record["support"][0], "quote_check": "fail", "verdict": "unsupported"})
        path.write_text(f"---\n{yaml.safe_dump(record, sort_keys=False)}---\n{body}", encoding="utf-8")
        code, _, stderr = cli(*accept, "--proposed", cli.write("proposed.json", {"requirements": [requirement]}))
        self.assertEqual(code, 2)
        self.assertIn("research support entries use an id more than once: F1", stderr)

    def test_an_older_revision_without_a_part_is_replaced_by_the_next_acceptance(self):
        import yaml

        from harness_forge.documents import canonical_digest
        cli = Cli(ResearchRunner([finding()]), self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        code, _, stderr = cli("research", "--decision", self.decision, "--pass", "deep", "--part", "A", "--harness",
                              "claude-code", "--evidence", evidence, "--relay", cli.confirm(self.decision, "intake-1"),
                              "--root", self.root, "--slug", "repro", "--write")
        self.assertEqual(code, 0, stderr)
        legacy = {"brief_digest": "0" * 64, "rules": []}
        legacy["digest"] = canonical_digest(legacy)
        revision = self.root / ".harness/research/repro.rules.yaml"
        revision.write_text(yaml.safe_dump(legacy), encoding="utf-8")
        record = load_decision(self.decision)
        record.record["accepted_revision"] = {"digest": legacy["digest"], "rule_ids": []}
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        code, _, stderr = cli("prepare-contract", "--decision", self.decision, "--revision", revision)
        self.assertEqual(code, 2)
        self.assertIn("accept its part's requirements again", stderr)
        code, output, stderr = cli("accept", "--decision", self.decision, "--part", "A", "--root", self.root,
                                   "--slug", "repro", "--proposed", cli.write("none.json", {"requirements": []}),
                                   "--choices", cli.write("choices.json", {}))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sorted(load_revision(revision).brief_digests), ["A"])
        self.assertEqual(load_decision(self.decision).record["accepted_revision"]["digest"], output["digest"])
        relay = cli.confirm(self.decision, "after", "--revision", revision)
        self.assertTrue(Path(relay).is_file())

    def test_a_built_part_needs_accepted_requirements(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        record = load_decision(self.decision)
        record.record["accepted_revision"] = None
        write_decision(self.root, record, slug="repro", date="2026-10-10")
        relay = cli.confirm(self.decision, "verify-2")
        option = arguments.index("--revision")
        without = arguments[:option] + arguments[option + 2:]
        without[without.index("--relay") + 1] = relay
        code, _, stderr = cli("verify", *without)
        self.assertEqual(code, 2)
        self.assertIn("part A has no accepted requirements", stderr)

    def test_case_ids_must_be_unique(self):
        import yaml
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        cases = yaml.safe_load((FORGE / "evals/smoke/vertical.yaml").read_text(encoding="utf-8"))["cases"]
        explicit = next(c for c in cases if c["mode"] == "explicit")
        for item in cases:
            if item["mode"] == "implicit":
                item["id"] = explicit["id"]
        shared = cli.write("shared.yaml", yaml.safe_dump({"cases": cases}))
        option = arguments.index("--cases") + 1
        code, _, stderr = cli("verify", *arguments[:option], shared, *arguments[option + 1:])
        self.assertEqual(code, 2)
        self.assertIn(f"case id {explicit['id']!r} is used twice", stderr)

    def test_a_built_skill_needs_runtime_cases(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        option = arguments.index("--cases")
        code, _, stderr = cli("verify", *arguments[:option], *arguments[option + 2:])
        self.assertEqual(code, 2)
        self.assertIn("part A is a built skill: pass its runtime cases with --cases", stderr)

    def test_a_rebuild_over_a_changed_skill_shows_the_diff(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        option = dict(zip(arguments[::2], arguments[1::2]))
        procedure = cli.write("procedure-2.md", PROCEDURE + "\n5. Name the browser version.\n")
        code, output, stderr = cli("build", "--decision", self.decision, "--part", "A", "--root", self.root,
                                   *[item for key in ("--revision", "--evidence", "--relay") for item in (key, option[key])],
                                   "--description", cli.write("description.txt", DESCRIPTION), "--procedure", procedure)
        self.assertEqual(code, 1, stderr)
        self.assertFalse(output["built"])
        self.assertIn("+5. Name the browser version.", output["diff"])

    def test_readme_writes_the_outside_controls_with_their_conditions(self):
        cli = Cli(FakeRunner(), self.root)
        code, output, stderr = cli("readme", "--decision", self.decision, "--root", self.root)
        self.assertEqual(code, 0, stderr)
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("# Repro\n"))
        self.assertIn("`codeowners-agent-config` (part A): recommended, not verified", readme)
        self.assertIn("together with the setting that requires code-owner review (`outside.codeowners.agent-config`)", readme)
        self.assertEqual(output["written"], "README.md")

    def test_build_refuses_without_a_matching_confirmation(self):
        cli = Cli(FakeRunner(), self.root)
        _, workers, _ = cli("workers", "--harness", "claude-code")
        evidence = cli.write("workers.json", workers)
        relay = cli.write("relay.json", {"choice": "confirm", "displayed_digest": "0" * 64, "part_ids": ["A"],
                                         "run_id": "verify-1"})
        description = cli.write("description.txt", DESCRIPTION)
        procedure = cli.write("procedure.md", PROCEDURE)
        code, _, stderr = cli("build", "--decision", self.decision, "--part", "A", "--evidence", evidence,
                              "--relay", relay, "--description", description, "--procedure", procedure,
                              "--root", self.root)
        self.assertEqual(code, 2, stderr)
        self.assertFalse((self.root / ".agents").exists())


if __name__ == "__main__":
    unittest.main()
