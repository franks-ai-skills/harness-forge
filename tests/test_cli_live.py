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
from harness_forge.research import load_revision, write_research
from harness_forge.adapters.base import Completed
from tests.test_adapters import CLAUDE_API_KEY, PARENT, FakeRunner, claude_stream
from tests.test_build_skill import DESCRIPTION, NAME, PROCEDURE
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
                                                   "part_ids": ["A"], "run_id": run_id})


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
        self.assertEqual(runner.roles, ["research", "source-support", "review", "baseline", "runtime", "runtime", "runtime"])

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

    def test_an_override_file_in_the_repository_blocks_without_being_named(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        overrides = self.root / ".harness/overrides"
        overrides.mkdir(parents=True)
        research = load_document(self.root / ".harness/research/repro.md", "research")
        override = {**OVERRIDE, "drift": {"finding": research["findings"][0], "support_verdict": "supported"}}
        (overrides / "skills.description.concise.yaml").write_text(yaml.safe_dump(override), encoding="utf-8")
        code, output, stderr = cli("prepare-contract", "--decision", self.decision, "--revision",
                                   self.root / ".harness/research/repro.rules.yaml")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Rule overrides for this repository", output["text"])
        self.assertIn("The team prefers longer descriptions.", output["text"])
        code, _, stderr = cli("verify", *arguments)
        self.assertEqual(code, 2)
        self.assertIn("rule overrides in .harness/overrides block", stderr)
        option = dict(zip(arguments[::2], arguments[1::2]))
        code, _, stderr = cli("check", "--decision", self.decision, "--root", self.root, "--revision", option["--revision"])
        self.assertEqual(code, 2)
        self.assertIn("rule overrides", stderr)
        (overrides / "notes.txt").write_text("not an override", encoding="utf-8")
        code, _, stderr = cli("prepare-contract", "--decision", self.decision)
        self.assertEqual(code, 2)
        self.assertIn("notes.txt", stderr)

    def test_unresolved_compatibility_drift_blocks_the_affected_part(self):
        cli, arguments = self.build_ready(ResearchRunner([finding()]))
        path = self.root / ".harness/research/repro.md"
        record = load_document(path, "research")
        record["drift"] = [{"rule_id": "skills.description.length", "finding_id": record["findings"][0]["id"],
                            "summary": "The vendor raised the description limit.", "category": "compatibility"}]
        write_research(self.root, record, "Drift recorded.\n")
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
