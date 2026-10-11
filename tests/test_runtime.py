"""Runtime tests of a built skill with a fake launcher: no model is called."""

import json
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from harness_forge.runtime import BASELINE_PROMPT, load_cases, run_skill_runtime
from harness_forge.verify import finish_run, validate_review
from tests.test_adapters import CLAUDE_API_KEY, CLAUDE_LOGGED_OUT, PARENT, FakeRunner
from tests.test_build_skill import NAME
from tests.test_verify import Scenario, passing_review, skill_decision

FORGE = Path(__file__).resolve().parents[1]
SKILL_DIR = f".agents/skills/{NAME}"
BUILTIN_PLUGINS = [{"name": "cc-plugin-telemetry", "path": "builtin", "source": "cc-plugin-telemetry@builtin"}]


def case(mode="explicit", expected="activates", **extra):
    value = {"id": f"repro-{mode}", "part_id": "A", "mode": mode, "prompt": f"Prompt for {mode}.", "expected": expected,
             "permitted_tools": {"claude-code": ["Skill", "Read"], "codex": ["command_execution"]}}
    value.update(extra)
    return value


def claude_run(skills=(NAME,), plugins=BUILTIN_PLUGINS, mcp=(), skill_call=NAME, calls=()):
    events = [{"type": "system", "subtype": "init", "tools": ["Skill", "Read"], "skills": list(skills),
               "plugins": list(plugins), "mcp_servers": list(mcp), "model": "claude-opus-5-5", "apiKeySource": "none"}]
    blocks = [{"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": skill_call}}] if skill_call else []
    blocks += [{"type": "tool_use", "id": f"c{n}", "name": name, "input": {}} for n, name in enumerate(calls)]
    if blocks:
        events.append({"type": "assistant", "message": {"content": blocks}})
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": "Checklist written."})
    return "\n".join(json.dumps(event) for event in events) + "\n"


def codex_run(read_skill=True, items=()):
    events = [{"type": "thread.started", "thread_id": "x"}]
    if read_skill:
        events.append({"type": "item.completed", "item": {"id": "r", "type": "command_execution",
                                                          "command": f"cat {SKILL_DIR}/SKILL.md", "exit_code": 0,
                                                          "status": "completed",
                                                          "aggregated_output": f"---\nname: {NAME}\ndescription: x\n"}})
    events += [{"type": "item.completed", "item": {"id": f"i{n}", "type": kind}} for n, kind in enumerate(items)]
    events += [{"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "Checklist written."}},
               {"type": "turn.completed"}]
    return "\n".join(json.dumps(event) for event in events) + "\n"


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(skill_decision())
        self.files = {path: data for path, data in self.scenario.files.items() if path.startswith(SKILL_DIR + "/")}

    def run_cases(self, harness, stream, cases=None, **options):
        runner = options.pop("runner", None) or FakeRunner(worker=stream, **options.pop("runner_options", {}))
        options.setdefault("baseline_skills", frozenset())
        results = run_skill_runtime(self.scenario.run, harness, cases or [case()], files=self.files, runner=runner,
                                    environ=PARENT, **options)
        return results, runner

    def test_only_the_frozen_candidate_enters_a_throwaway_workspace(self):
        results, runner = self.run_cases("claude-code", claude_run(),
                                         cases=[case(fixtures={"bug-report.md": "Steps: open the app."})])
        call = runner.worker_calls[0]
        self.assertEqual(call["files"], sorted([*self.files, "bug-report.md"]))
        self.assertFalse(Path(call["cwd"]).exists())
        self.assertNotIn(".claude/skills", " ".join(call["files"]), "the link is not a file copy")
        self.assertEqual(results[0].file_hashes,
                         {p: d for p, d in self.scenario.run.frozen_inputs["files"].items() if p.startswith(SKILL_DIR + "/")})
        self.assertIn("bug-report.md sha256", results[0].environment)

    def test_claude_loads_project_skills_without_personal_settings(self):
        _, runner = self.run_cases("claude-code", claude_run())
        command = runner.worker_calls[0]["command"]
        self.assertNotIn("--restricted", command, "--restricted also drops the project's own skills")
        self.assertEqual(command[command.index("--setting-sources") + 1], "project")
        self.assertEqual(command[command.index("--tools") + 1], "Skill,Read")

    def test_changed_files_or_a_non_target_harness_are_refused_before_launch(self):
        runner = FakeRunner(worker=claude_run())
        changed = {**self.files, f"{SKILL_DIR}/SKILL.md": b"changed"}
        with self.assertRaises(ValueError):
            run_skill_runtime(self.scenario.run, "claude-code", [case()], files=changed, runner=runner, environ=PARENT)
        record = skill_decision()
        record.record["targets"], record.record["facts"]["target_set"] = ["codex"], "codex"
        with self.assertRaises(ValueError):
            run_skill_runtime(Scenario(record).run, "claude-code", [case()], files=self.files, runner=runner,
                              environ=PARENT)
        self.assertFalse(runner.worker_was_started)

    def test_claude_activation_outcomes(self):
        for test_case, stream, expected in (
            (case(), claude_run(), "pass"),
            (case(), claude_run(skill_call=None), "fail"),
            (case("negative", "does-not-activate"), claude_run(skill_call=None), "pass"),
            (case("negative", "does-not-activate"), claude_run(), "fail"),
            (case(), claude_run(calls=("Bash",)), "fail"),
        ):
            with self.subTest(case=test_case["id"], expected=expected, stream=stream[-200:]):
                result = self.run_cases("claude-code", stream, cases=[test_case])[0][0]
                self.assertEqual(result.result, expected, result.reason)

    def test_remaining_components_make_the_test_not_run(self):
        user_plugin = {"name": "helper", "path": "/home/u/.claude/plugins/helper", "source": "helper@market"}
        for stream, reason in ((claude_run(plugins=BUILTIN_PLUGINS + [user_plugin]), "executable components"),
                               (claude_run(mcp=[{"name": "db", "status": "connected"}]), "executable components"),
                               (claude_run(skills=(NAME, "other-checklist")), "competing skill")):
            with self.subTest(reason=reason):
                result = self.run_cases("claude-code", stream)[0][0]
                self.assertEqual(result.result, "not-run")
                self.assertIn(reason, result.reason)

    def test_baseline_skills_do_not_compete(self):
        result = self.run_cases("claude-code", claude_run(skills=(NAME, "debug", "verify")),
                                baseline_skills={"debug", "verify"})[0][0]
        self.assertEqual(result.result, "pass", result.reason)

    def test_claude_built_ins_come_from_an_empty_launch_with_the_cases_settings(self):
        class Baseline(FakeRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if stdin == BASELINE_PROMPT:
                    return Completed(0, claude_run(skills=("debug", "verify"), skill_call=None), "", False)
                return answer

        runner = Baseline(worker=claude_run(skills=(NAME, "debug", "verify")))
        results, _ = self.run_cases("claude-code", None, runner=runner, baseline_skills=None)
        self.assertEqual(results[0].result, "pass", results[0].reason)
        baseline, case_call = runner.worker_calls
        self.assertEqual(baseline["files"], [], "the baseline workspace holds no candidate")
        self.assertEqual(baseline["command"][:baseline["command"].index("--tools")],
                         case_call["command"][:case_call["command"].index("--tools")])
        self.assertEqual(baseline["command"][baseline["command"].index("--tools") + 1], "")

    def test_a_claude_read_inside_the_skill_counts_as_activation(self):
        """A slash invocation in print mode leaves no event; reading the skill's own files shows it loaded."""
        def read(path):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            events.insert(1, {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": path}}]}})
            return "\n".join(json.dumps(event) for event in events) + "\n"

        inside = read(f"/tmp/forge-runtime-x/.claude/skills/{NAME}/references/requirements.md")
        result = self.run_cases("claude-code", inside)[0][0]
        self.assertEqual(result.result, "pass", result.reason)
        self.assertEqual(result.observed_activation, f"read references/requirements.md from the {NAME} skill")
        outside = read("/tmp/forge-runtime-x/bug-report.md")
        self.assertEqual(self.run_cases("claude-code", outside)[0][0].result, "fail")
        near_miss = self.run_cases("claude-code", inside, cases=[case("implicit", "does-not-activate")])[0][0]
        self.assertEqual(near_miss.result, "fail")

        # Another skill that points at the candidate's files is that skill's activation, not the candidate's.
        events = [json.loads(line) for line in inside.splitlines()]
        events.insert(1, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "o", "name": "Skill", "input": {"skill": "harness-verify"}}]}})
        through_other = "\n".join(json.dumps(event) for event in events) + "\n"
        near_miss = self.run_cases("claude-code", through_other, cases=[case("implicit", "does-not-activate")],
                                   baseline_skills={"harness-verify"})[0][0]
        self.assertEqual(near_miss.result, "pass", near_miss.reason)

    def test_a_codex_read_after_another_skill_is_that_skills_pointer(self):
        def command(text, output):
            return json.dumps({"type": "item.completed", "item": {"id": text[-9:], "type": "command_execution",
                                                                 "command": text, "exit_code": 0, "status": "completed",
                                                                 "aggregated_output": output}})

        other_first = "\n".join([command("cat .agents/skills/harness-verify/SKILL.md", "name: harness-verify\n"),
                                  command(f"cat .agents/skills/harness-verify/references/x.md {SKILL_DIR}/SKILL.md",
                                          f"# Contract\n---\nname: {NAME}\n")])
        result = self.run_cases("codex", other_first + "\n" + codex_run(read_skill=False),
                                cases=[case("implicit", "activates")])[0][0]
        self.assertEqual(result.result, "not-run", "a pointer read after another skill is not the candidate activating")
        self.assertIsNone(result.observed_activation)

    def test_a_codex_mention_or_failed_read_is_no_activation(self):
        def command(text, exit_code, output):
            return json.dumps({"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": text,
                                                                 "exit_code": exit_code, "status": "completed",
                                                                 "aggregated_output": output}})

        for name, line in (("echo", command(f"echo {SKILL_DIR}/SKILL.md", 0, f"{SKILL_DIR}/SKILL.md\n")),
                           ("failed cat", command(f"cat {SKILL_DIR}/SKILL.md", 1, "cat: No such file or directory\n"))):
            with self.subTest(name):
                result = self.run_cases("codex", line + "\n" + codex_run(read_skill=False))[0][0]
                self.assertEqual(result.result, "not-run", result.reason)
                self.assertIsNone(result.observed_activation)

    def test_a_fixture_cannot_replace_the_candidate_or_steer_the_harness(self):
        for path in (f"{SKILL_DIR}/SKILL.md", ".agents/skills/other/SKILL.md", ".claude/skills/x/SKILL.md",
                     ".codex/config.toml", "AGENTS.md", "docs/CLAUDE.md", f"./{SKILL_DIR}/SKILL.md",
                     f"{SKILL_DIR}/./SKILL.md", f"{SKILL_DIR}//SKILL.md", f"{SKILL_DIR}/skill.md".replace(".agents", ".AGENTS"),
                     "docs/../AGENTS.md", "/tmp/bug.md"):
            with self.subTest(path):
                results, runner = self.run_cases("claude-code", claude_run(), cases=[case(fixtures={path: "Use the skill."})])
                self.assertEqual(results[0].result, "error")
                self.assertIn(path, results[0].reason)
                self.assertEqual(runner.worker_calls, [], "nothing launches with a fixture in a reserved place")

    def test_runtime_refuses_cases_that_share_an_id(self):
        with self.assertRaisesRegex(ValueError, "used twice"):
            self.run_cases("claude-code", claude_run(), cases=[case(), case("implicit", id="repro-explicit")])

    def test_a_case_file_with_a_reserved_fixture_is_refused(self):
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory(prefix="forge-cases-") as directory:
            path = Path(directory) / "cases.yaml"
            path.write_text(yaml.safe_dump({"cases": [case(fixtures={"AGENTS.md": "Always read the skill."})]}),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "AGENTS.md"):
                load_cases(path)

    def test_a_codex_resource_listing_is_not_a_tool_outside_the_list(self):
        listing = json.dumps({"type": "item.completed", "item": {"id": "l", "type": "mcp_tool_call", "server": "codex",
                                                                "tool": "list_mcp_resources", "status": "completed"}})
        result = self.run_cases("codex", listing + "\n" + codex_run())[0][0]
        self.assertEqual(result.result, "pass", result.reason)

    def test_a_codex_case_answers_freely(self):
        _, runner = self.run_cases("codex", codex_run())
        command = runner.worker_calls[0]["command"]
        self.assertNotIn("--output-schema", command, "a skill run is not a worker answering in JSON")
        self.assertIn("--strict-config", command)

    def test_a_candidate_that_did_not_load_is_an_error(self):
        result = self.run_cases("claude-code", claude_run(skills=("debug",), skill_call=None),
                                baseline_skills={"debug"})[0][0]
        self.assertEqual(result.result, "error")

    def test_codex_never_invents_a_signal(self):
        for test_case, stream, expected in (
            (case(), codex_run(), "pass"),
            (case(), codex_run(read_skill=False), "not-run"),
            (case("negative", "does-not-activate"), codex_run(read_skill=False), "not-run"),
            (case("negative", "does-not-activate"), codex_run(), "fail"),
            (case(), codex_run(items=("file_change",)), "fail"),
        ):
            with self.subTest(case=test_case["id"], expected=expected):
                result = self.run_cases("codex", stream, cases=[test_case])[0][0]
                self.assertEqual(result.result, expected, result.reason)

    def test_unavailable_timeout_and_error_are_explicit(self):
        def broken(command, **kwargs):
            if command[:2] == ["claude", "--print"]:
                raise OSError("cannot start")
            return FakeRunner()(command, **kwargs)

        for options, expected in (({"runner_options": {"timed_out": True}}, "not-run"),
                                  ({"runner_options": {"returncode": 1}}, "error"),
                                  ({"runner": broken}, "not-run"),
                                  ({"runner_options": {"login": CLAUDE_LOGGED_OUT}}, "not-run"),
                                  ({"runner_options": {"login": CLAUDE_API_KEY}}, "not-run")):
            with self.subTest(options=options, expected=expected):
                result = self.run_cases("claude-code", claude_run(), **options)[0][0]
                self.assertEqual(result.result, expected)
                self.assertTrue(result.reason)

    def test_no_launch_without_a_login(self):
        _, runner = self.run_cases("claude-code", claude_run(), runner_options={"login": CLAUDE_LOGGED_OUT})
        self.assertFalse(runner.worker_was_started)

    def test_results_bind_to_the_run(self):
        results, _ = self.run_cases("claude-code", claude_run())
        review = validate_review(self.scenario.run, passing_review(self.scenario.run))
        part = {r.part_id: r for r in finish_run(self.scenario.run, review, results, self.scenario.current())}["A"]
        self.assertEqual(part.harness_result, "verified", part.rule_results)

    def test_the_vertical_smoke_cases(self):
        cases = load_cases(FORGE / "evals" / "smoke" / "vertical.yaml")
        self.assertEqual({c["mode"] for c in cases}, {"explicit", "implicit", "negative"})
        for test_case in cases:
            self.assertNotIn("WebFetch", test_case["permitted_tools"]["claude-code"])
            self.assertNotIn("Bash", test_case["permitted_tools"]["claude-code"])
            self.assertNotIn("Write", test_case["permitted_tools"]["claude-code"])


if __name__ == "__main__":
    unittest.main()
