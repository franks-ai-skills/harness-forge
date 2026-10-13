"""Runtime tests of a built skill with a fake launcher: no model is called."""

import json
import unittest
from pathlib import Path

import yaml

from harness_forge.adapters.base import Completed
from harness_forge.runtime import BASELINE_PROMPT, load_cases, run_skill_runtime
from harness_forge.verify import finish_run, validate_review
from tests.test_adapters import CLAUDE_API_KEY, CLAUDE_LOGGED_OUT, PARENT, FakeRunner
from tests.test_build_skill import DESCRIPTION, NAME
from tests.test_verify import Scenario, build_files, passing_review, skill_decision

FORGE = Path(__file__).resolve().parents[1]
SKILL_DIR = f".agents/skills/{NAME}"
BUILTIN_PLUGINS = [{"name": "cc-plugin-telemetry", "path": "builtin", "source": "cc-plugin-telemetry@builtin"}]
# The built SKILL.md's description line, which a real read of the candidate prints.
DESCRIPTION_LINE = yaml.dump({"description": DESCRIPTION.strip()}, width=10_000).strip()
# The whole built SKILL.md, which a complete read of the candidate prints.
BUILT = build_files(skill_decision())[0][f"{SKILL_DIR}/SKILL.md"].decode()


def case(mode="explicit", expected="activates", **extra):
    value = {"id": f"repro-{mode}", "part_id": "A", "mode": mode, "prompt": f"Prompt for {mode}.", "expected": expected,
             "permitted_tools": {"claude-code": ["Skill", "Read"], "codex": ["command_execution"]}}
    value.update(extra)
    return value


def tool_result(tool_id, failed=False):
    """The user event that carries a tool call's result, as Claude's stream-json reports it."""
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "Error" if failed else "ok", "is_error": failed}]}}


def claude_run(skills=(NAME,), plugins=BUILTIN_PLUGINS, mcp=(), skill_call=NAME, calls=(), failed=()):
    """``failed`` names the tool call ids whose result is an error; the Skill call's id is "s"."""
    events = [{"type": "system", "subtype": "init", "tools": ["Skill", "Read"], "skills": list(skills),
               "plugins": list(plugins), "mcp_servers": list(mcp), "model": "claude-opus-5-5", "apiKeySource": "none"}]
    blocks = [{"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": skill_call}}] if skill_call else []
    blocks += [{"type": "tool_use", "id": f"c{n}", "name": name, "input": {}} for n, name in enumerate(calls)]
    if blocks:
        events.append({"type": "assistant", "message": {"content": blocks}})
        events += [tool_result(block["id"], block["id"] in failed) for block in blocks]
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": "Checklist written."})
    return "\n".join(json.dumps(event) for event in events) + "\n"


def codex_run(read_skill=True, items=()):
    events = [{"type": "thread.started", "thread_id": "x"}]
    if read_skill:
        events.append({"type": "item.completed", "item": {"id": "r", "type": "command_execution",
                                                          "command": f"cat {SKILL_DIR}/SKILL.md", "exit_code": 0,
                                                          "status": "completed",
                                                          "aggregated_output": BUILT}})
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
            (case(), claude_run(failed={"s"}), "fail"),
            (case(), claude_run(skill_call=None, calls=("Read",)), "fail"),
            (case("negative", "does-not-activate"), claude_run(failed={"s"}), "fail"),
        ):
            with self.subTest(case=test_case["id"], expected=expected, stream=stream[-200:]):
                result = self.run_cases("claude-code", stream, cases=[test_case])[0][0]
                self.assertEqual(result.result, expected, result.reason)

    def test_a_claude_case_cannot_permit_tools_that_run_or_write_code(self):
        """Claude's cases run without a sandbox, so only tools that neither execute nor write are contained."""
        for tool in ("Bash", "Write", "WebFetch"):
            with self.subTest(tool=tool):
                permitted = {"claude-code": ["Skill", "Read", tool], "codex": ["command_execution"]}
                results, runner = self.run_cases("claude-code", claude_run(), cases=[case(permitted_tools=permitted)])
                self.assertEqual(results[0].result, "not-run")
                self.assertIn(f"unavailable: Claude runs the case without a sandbox; {tool}", results[0].reason)
                self.assertEqual([c for c in runner.worker_calls if c["stdin"] != BASELINE_PROMPT], [])
        permitted = {"claude-code": ["Skill", "Read", "Grep", "Glob"], "codex": ["command_execution"]}
        self.assertEqual(self.run_cases("claude-code", claude_run(), cases=[case(permitted_tools=permitted)])[0][0].result,
                         "pass")

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
                    bundled = () if "disableBundledSkills" in " ".join(command) else ("debug", "verify")
                    return Completed(0, claude_run(skills=bundled, skill_call=None), "", False)
                return answer

        runner = Baseline(worker=claude_run(skills=(NAME, "debug", "verify")))
        results, _ = self.run_cases("claude-code", None, runner=runner, baseline_skills=None)
        self.assertEqual(results[0].result, "pass", results[0].reason)
        baseline, without_bundled, case_call = runner.worker_calls
        self.assertEqual(baseline["files"], [], "the baseline workspace holds no candidate")
        self.assertEqual(baseline["command"][:baseline["command"].index("--tools")],
                         case_call["command"][:case_call["command"].index("--tools")])
        self.assertEqual(baseline["command"][baseline["command"].index("--tools") + 1], "")
        settings = json.loads(without_bundled["command"][without_bundled["command"].index("--settings") + 1])
        self.assertIs(settings["disableBundledSkills"], True)

    def test_only_bundled_skills_are_exempt_from_competing(self):
        """A skill still listed with bundled skills turned off is the user's or the organization's: it competes."""
        def runner_for(user_skills):
            class Origins(FakeRunner):
                def __call__(self, command, *, cwd, env, stdin, timeout):
                    answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                    if stdin != BASELINE_PROMPT:
                        return answer
                    bundled_off = "disableBundledSkills" in command[command.index("--settings") + 1]
                    skills = tuple(user_skills) + (() if bundled_off else ("debug", "verify"))
                    return Completed(0, claude_run(skills=skills, skill_call=None), "", False)
            return Origins(worker=claude_run(skills=(NAME, "debug", "verify", *user_skills)))

        personal = self.run_cases("claude-code", None, runner=runner_for(["my-personal"]), baseline_skills=None)[0][0]
        self.assertEqual(personal.result, "not-run")
        self.assertIn("a competing skill was loaded: my-personal", personal.reason)
        runner = runner_for([NAME])
        shadowed = self.run_cases("claude-code", None, runner=runner, baseline_skills=None)[0][0]
        self.assertEqual(shadowed.result, "not-run")
        self.assertIn(f"a user-level skill named {NAME} takes precedence over the candidate", shadowed.reason)
        self.assertEqual(len(runner.worker_calls), 2, "no case launches under a shadowing skill")

    def test_a_successful_read_outside_the_workspace_stops_the_case(self):
        """A skill turned off is only hidden: its files stay readable, so reading one brings it in anyway."""
        def stream(path, failed):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            reads = [{"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": path}},
                     {"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": NAME}}]
            return "\n".join(json.dumps(event) for event in [
                events[0], {"type": "assistant", "message": {"content": reads}},
                tool_result("r", failed), tool_result("s"), events[-1]]) + "\n"

        outside = "/home/someone/.claude/skills/my-personal/SKILL.md"
        for path, failed, expected in ((outside, False, "not-run"), (outside, True, "pass"),
                                       ("../elsewhere/notes.md", False, "not-run"),
                                       (f"{SKILL_DIR}/SKILL.md", False, "pass")):
            with self.subTest(path=path, failed=failed):
                result = self.run_cases("claude-code", stream(path, failed))[0][0]
                self.assertEqual(result.result, expected, result.reason)
                if expected == "not-run":
                    self.assertIn("outside the throwaway workspace", result.reason)

    def test_every_path_a_file_tool_uses_is_checked_through_links(self):
        """The effective location counts: Glob's directory may come from its pattern, Grep takes file_path too,
        and a path through the skill's .claude/skills link resolves where the link points."""
        def stream(tool, arguments):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            calls = [{"type": "tool_use", "id": "t", "name": tool, "input": arguments},
                     {"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": NAME}}]
            return "\n".join(json.dumps(event) for event in [
                events[0], {"type": "assistant", "message": {"content": calls}},
                tool_result("t"), tool_result("s"), events[-1]]) + "\n"

        permitted = case(permitted_tools={"claude-code": ["Skill", "Read", "Grep", "Glob"],
                                          "codex": ["command_execution"]})
        for tool, arguments, expected in (
                ("Glob", {"pattern": "/tmp/external/*.txt", "path": "."}, "not-run"),
                ("Glob", {"pattern": "../*.md"}, "not-run"),
                ("Glob", {"pattern": "**/*.md"}, "pass"),
                ("Glob", {"pattern": "*.md", "path": f"{SKILL_DIR}/references"}, "pass"),
                ("Grep", {"pattern": "x", "file_path": "/etc/hosts"}, "not-run"),
                ("Grep", {"pattern": "x"}, "pass"),
                ("Read", {"file_path": f".claude/skills/{NAME}/../../../../outside.txt"}, "not-run"),
                ("Read", {"file_path": f".claude/skills/{NAME}/SKILL.md"}, "pass")):
            with self.subTest(tool=tool, arguments=arguments):
                result = self.run_cases("claude-code", stream(tool, arguments), cases=[permitted])[0][0]
                self.assertEqual(result.result, expected, result.reason)

    def test_an_outside_read_is_kept_when_the_run_then_times_out_or_fails(self):
        events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
        partial = "\n".join(json.dumps(event) for event in [
            events[0], {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/etc/hosts"}}]}},
            tool_result("r")]) + "\n"
        for options, also in (({"timed_out": True}, "timed out"), ({"returncode": 1}, "exited with 1")):
            with self.subTest(**options):
                result = self.run_cases("claude-code", partial, runner_options=options)[0][0]
                self.assertEqual(result.result, "not-run")
                self.assertIn("outside the throwaway workspace: /etc/hosts", result.reason)
                self.assertIn(also, result.reason)

    def test_only_a_successful_file_call_is_judged_and_globs_by_where_they_search(self):
        def stream(tool, arguments, failed=False):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            calls = [{"type": "tool_use", "id": "t", "name": tool, "input": arguments},
                     {"type": "tool_use", "id": "s", "name": "Skill", "input": {"skill": NAME}}]
            return "\n".join(json.dumps(event) for event in [
                events[0], {"type": "assistant", "message": {"content": calls}},
                tool_result("t", failed), tool_result("s"), events[-1]]) + "\n"

        permitted = case(permitted_tools={"claude-code": ["Skill", "Read", "Glob"], "codex": ["command_execution"]})
        for tool, arguments, failed, expected in (
                ("Glob", {"pattern": f"@@WORKSPACE@@/{SKILL_DIR}/*.md", "path": "/tmp"}, False, "pass"),
                ("Glob", {"pattern": "/tmp/*.md", "path": "."}, False, "not-run"),
                ("Read", {"file_path": "bad\u0000path"}, True, "pass"),
                ("Read", {"file_path": "bad\u0000path"}, False, "not-run")):
            with self.subTest(tool=tool, arguments=arguments, failed=failed):
                result = self.run_cases("claude-code", stream(tool, arguments, failed), cases=[permitted])[0][0]
                self.assertEqual(result.result, expected, result.reason)

    def test_a_path_through_a_link_is_judged_where_the_link_points(self):
        """A link to a shallower folder: three steps up stay inside by spelling, but leave the workspace."""
        import tempfile
        from harness_forge.runtime import _inside

        with tempfile.TemporaryDirectory(prefix="forge-links-") as directory:
            workspace = Path(directory) / "workspace"
            (workspace / "skills" / NAME).mkdir(parents=True)
            (workspace / ".claude" / "skills").mkdir(parents=True)
            (workspace / ".claude" / "skills" / NAME).symlink_to(f"../../skills/{NAME}")
            self.assertFalse(_inside(f".claude/skills/{NAME}/../../../outside.txt", workspace))
            self.assertTrue(_inside(f".claude/skills/{NAME}/SKILL.md", workspace))
            self.assertTrue(_inside(f"{workspace}/notes.md", workspace))
            self.assertFalse(_inside("~/notes.md", workspace))

    def test_each_case_turns_the_user_level_skills_off(self):
        """Skills still listed with bundled skills off (the user's, or built-ins that setting keeps) are turned
        off for the case only; the case's own listing shows whether that worked."""
        class Origins(FakeRunner):
            def __call__(self, command, *, cwd, env, stdin, timeout):
                answer = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                if "--settings" not in command:
                    return answer
                settings = json.loads(command[command.index("--settings") + 1])
                if stdin != BASELINE_PROMPT:
                    off = {name for name, mode in settings.get("skillOverrides", {}).items() if mode == "off"}
                    return Completed(0, claude_run(skills=tuple(s for s in (NAME, "debug", "doctor", "my-personal")
                                                                if s not in off)), "", False)
                skills = ("doctor", "my-personal") + (() if settings.get("disableBundledSkills") else ("debug",))
                return Completed(0, claude_run(skills=skills, skill_call=None), "", False)

        runner = Origins(worker=claude_run())
        result = self.run_cases("claude-code", None, runner=runner, baseline_skills=None)[0][0]
        self.assertEqual(result.result, "pass", result.reason)
        baseline, without_bundled, case_call = runner.worker_calls
        for probe in (baseline, without_bundled):
            self.assertNotIn("skillOverrides", probe["command"][probe["command"].index("--settings") + 1])
        settings = json.loads(case_call["command"][case_call["command"].index("--settings") + 1])
        self.assertEqual(settings["skillOverrides"], {"doctor": "off", "my-personal": "off"})

    def test_an_unobserved_bundled_off_probe_exempts_nothing(self):
        def stream_without(key):
            events = [json.loads(line) for line in claude_run(skills=(), skill_call=None).splitlines()]
            if key == "init":
                events = events[1:]
            else:
                events[0].pop(key)
            return "\n".join(json.dumps(event) for event in events) + "\n"

        for name, answer in (("failed", Completed(1, "", "error", False)),
                             ("timed out", Completed(None, "", "", True)),
                             ("no init event", Completed(0, stream_without("init"), "", False)),
                             ("no skills listed", Completed(0, stream_without("skills"), "", False))):
            class Probe(FakeRunner):
                def __call__(self, command, *, cwd, env, stdin, timeout):
                    result = super().__call__(command, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
                    if stdin != BASELINE_PROMPT:
                        return result
                    if "disableBundledSkills" in command[command.index("--settings") + 1]:
                        return answer
                    return Completed(0, claude_run(skills=(NAME, "my-personal"), skill_call=None), "", False)

            with self.subTest(name):
                runner = Probe(worker=claude_run(skills=(NAME, "my-personal")))
                result = self.run_cases("claude-code", None, runner=runner, baseline_skills=None)[0][0]
                self.assertEqual(result.result, "not-run", result.reason)
                self.assertIn("could not tell bundled skills from the user's", result.reason)
                self.assertEqual(len(runner.worker_calls), 2, "no case launches")

    def test_a_claude_read_of_the_candidates_skill_md_counts_as_activation(self):
        """Its instructions reached the model; a read of a reference alone shows file access, not activation."""
        def read(path, failed=False):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            events[1:1] = [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": path}}]}},
                tool_result("r", failed)]
            return "\n".join(json.dumps(event) for event in events) + "\n"

        inside = read(f"@@WORKSPACE@@/.claude/skills/{NAME}/SKILL.md")
        result = self.run_cases("claude-code", inside)[0][0]
        self.assertEqual(result.result, "pass", result.reason)
        self.assertEqual(result.observed_activation, f"read SKILL.md from the {NAME} skill")
        reference = read(f"@@WORKSPACE@@/.claude/skills/{NAME}/references/requirements.md")
        self.assertEqual(self.run_cases("claude-code", reference)[0][0].result, "fail", "a reference is not the skill")
        escaping = read(f"@@WORKSPACE@@/.claude/skills/{NAME}/../../../outside/SKILL.md")
        self.assertEqual(self.run_cases("claude-code", escaping)[0][0].result, "fail", "the path leaves the skill")
        missing = read(f"@@WORKSPACE@@/.claude/skills/{NAME}/references/absent.md", failed=True)
        self.assertEqual(self.run_cases("claude-code", missing)[0][0].result, "fail", "a failed read loads nothing")
        outside = read("@@WORKSPACE@@/bug-report.md")
        self.assertEqual(self.run_cases("claude-code", outside)[0][0].result, "fail")
        elsewhere = read(f"/home/u/.claude/skills/{NAME}/SKILL.md")
        result = self.run_cases("claude-code", elsewhere)[0][0]
        self.assertEqual((result.result, result.observed_activation), ("not-run", None),
                         "a same-named skill outside the throwaway workspace is not the candidate")
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

        copy = f"---\nname: {NAME}\n{DESCRIPTION_LINE}\n---\n\n# Reproduction checklist\n"
        for name, line in (("echo", command(f"echo {SKILL_DIR}/SKILL.md", 0, f"{SKILL_DIR}/SKILL.md\n")),
                           ("printed name", command(f"printf 'name: {NAME}\\n' # {SKILL_DIR}/SKILL.md", 0,
                                                    f"name: {NAME}\n")),
                           ("printed copy", command(f"printf '---\\nname: {NAME}\\n' # {SKILL_DIR}/SKILL.md", 0,
                                                    f"---\nname: {NAME}\n{DESCRIPTION_LINE}\n# Reproduction "
                                                    "checklist\n")),
                           ("failed cat", command(f"cat {SKILL_DIR}/SKILL.md", 1, "cat: No such file or directory\n")),
                           *((label, command(text, 0, copy)) for label, text in (
                               ("skipped read", f"false && cat {SKILL_DIR}/SKILL.md; printf '{NAME}'"),
                               ("or branch", f"true || nl -ba {SKILL_DIR}/SKILL.md"),
                               ("printed after a read", f"cat {SKILL_DIR}/SKILL.md; printf 'x'"),
                               ("fixture copy", f"cat fixtures/{SKILL_DIR}/SKILL.md"),
                               ("outside the workspace", f"cat ../elsewhere/{SKILL_DIR}/SKILL.md"),
                               ("foreign absolute path", f"cat /tmp/elsewhere/{SKILL_DIR}/SKILL.md"),
                               ("discarded read", f"cat {SKILL_DIR}/SKILL.md > /dev/null; cat fixtures/copied-skill.md"),
                               ("pipeline into a fixture", f"cat {SKILL_DIR}/SKILL.md | cat fixtures/copied-skill.md"),
                               ("partial read", f"sed -n 1,6p {SKILL_DIR}/SKILL.md")))):
            with self.subTest(name):
                result = self.run_cases("codex", line + "\n" + codex_run(read_skill=False))[0][0]
                self.assertEqual(result.result, "not-run", result.reason)
                self.assertIsNone(result.observed_activation)

    def test_only_the_prompt_not_tool_output_can_show_a_slash_invocation(self):
        marker = f"<command-name>/{NAME}</command-name>"
        def run_with(user_event):
            events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
            return "\n".join(json.dumps(event) for event in events[:1] + [user_event] + events[1:]) + "\n"

        invoked = self.run_cases("claude-code", run_with({"type": "user", "message": {"content": marker}}))[0][0]
        self.assertEqual(invoked.result, "pass", invoked.reason)
        as_text = {"type": "user", "message": {"content": [{"type": "text", "text": marker}]}}
        self.assertEqual(self.run_cases("claude-code", run_with(as_text))[0][0].result, "pass")
        read = [{"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "f", "name": "Read", "input": {"file_path": "fixtures/transcript.md"}}]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "f", "is_error": False,
                     "content": f"Earlier session:\n{marker}\n"}]}}]
        events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
        stream = "\n".join(json.dumps(event) for event in events[:1] + read + events[1:]) + "\n"
        from_fixture = self.run_cases("claude-code", stream)[0][0]
        self.assertEqual(from_fixture.result, "fail", from_fixture.reason)
        self.assertIsNone(from_fixture.observed_activation)

    def test_a_complete_read_keeps_the_files_order_and_repeated_lines(self):
        from harness_forge.runtime import _printed

        markers = ["# Steps", "Pause for confirmation.", "## After", "Pause for confirmation."]
        def printed(output):
            return _printed({"aggregated_output": output}, markers)

        self.assertTrue(printed("---\n# Steps\nPause for confirmation.\n\n## After\nPause for confirmation.\n"))
        self.assertTrue(printed("     1\t# Steps\n     2\tPause for confirmation.\n     3\t## After\n"
                                "     4\tPause for confirmation.\n"))
        self.assertFalse(printed("# Steps\nPause for confirmation.\n## After\n"), "the last line was cut off")
        self.assertFalse(printed("Pause for confirmation.\n## After\nPause for confirmation.\n# Steps\n"),
                         "reordered")

    def test_a_marker_quoted_in_another_commands_arguments_is_no_invocation(self):
        marker = f"<command-name>/{NAME}</command-name>"
        quoted = (f"<command-message>debug</command-message>\n<command-name>/debug</command-name>\n"
                  f"<command-args>Why did this transcript say {marker}?</command-args>")
        events = [json.loads(line) for line in claude_run(skill_call=None).splitlines()]
        stream = "\n".join(json.dumps(event) for event in events[:1] + [
            {"type": "user", "message": {"content": quoted}}] + events[1:]) + "\n"
        result = self.run_cases("claude-code", stream)[0][0]
        self.assertEqual(result.result, "fail", result.reason)
        for blocks in (["<command-name>/debug</command-name><command-args>Explain:", f"{marker}</command-args>"],
                       ["<command-name>/debug</command-name>", marker]):
            with self.subTest(blocks=blocks):
                split = {"type": "user", "message": {"content": [{"type": "text", "text": text} for text in blocks]}}
                stream = "\n".join(json.dumps(event) for event in events[:1] + [split] + events[1:]) + "\n"
                self.assertEqual(self.run_cases("claude-code", stream)[0][0].result, "fail")

    def test_a_call_outside_the_list_fails_also_when_the_case_timed_out(self):
        result = self.run_cases("claude-code", claude_run(calls=("Bash",)),
                                runner_options={"timed_out": True})[0][0]
        self.assertEqual(result.result, "fail")
        self.assertIn("tool outside the declared list: Bash", result.reason)

    def test_skill_shell_commands_never_run_in_a_claude_case(self):
        _, runner = self.run_cases("claude-code", claude_run())
        command = runner.worker_calls[0]["command"]
        settings = json.loads(command[command.index("--settings") + 1])
        self.assertIs(settings["disableSkillShellExecution"], True)

    def test_the_workspace_has_the_links_the_repository_has(self):
        _, runner = self.run_cases("claude-code", claude_run())
        self.assertEqual(runner.worker_calls[0]["links"], [f".claude/skills/{NAME}"])
        run = self.scenario.run
        run.part_links["A"] = []
        run.frozen_inputs["symlinks"] = {}
        results, runner = self.run_cases("claude-code", claude_run(skills=()))
        self.assertEqual(runner.worker_calls[0]["links"], [], "a missing link stays missing")
        self.assertEqual(results[0].result, "error")

    def test_a_codex_read_through_its_shell_counts(self):
        output = self.files[f"{SKILL_DIR}/SKILL.md"].decode()
        numbered = "".join(f"{n:6}\t{line}\n" for n, line in enumerate(output.splitlines(), 1))
        line = json.dumps({"type": "item.completed", "item": {
            "id": "r", "type": "command_execution", "exit_code": 0, "status": "completed",
            "command": f"/bin/zsh -lc 'cat request.json; nl -ba {SKILL_DIR}/SKILL.md'", "aggregated_output": numbered}})
        result = self.run_cases("codex", line + "\n" + codex_run(read_skill=False))[0][0]
        self.assertEqual(result.result, "pass", result.reason)

    def test_a_codex_read_by_the_workspaces_absolute_path_counts(self):
        from harness_forge.runtime import _observe_codex

        copy = BUILT
        markers = [line for line in BUILT.splitlines() if line.strip()]
        for path, activated in (("/private/var/ws/" + SKILL_DIR, True), ("/var/ws/" + SKILL_DIR, True),
                                ("/var/other/" + SKILL_DIR, False)):
            with self.subTest(path):
                line = json.dumps({"type": "item.completed", "item": {
                    "id": "r", "type": "command_execution", "exit_code": 0, "status": "completed",
                    "command": f"/bin/zsh -lc 'sed -n 1,200p {path}/SKILL.md'", "aggregated_output": copy}})
                observed = _observe_codex(line + "\n", SKILL_DIR, markers, Path("/var/ws"), Path("/private/var/ws"))
                self.assertEqual(observed["activation"] is not None, activated)

    def test_a_repository_link_that_leaves_the_candidate_is_refused(self):
        run = self.scenario.run
        run.frozen_inputs["symlinks"] = {f".claude/skills/{NAME}": "../../../elsewhere/reproduction-checklist"}
        results, runner = self.run_cases("claude-code", claude_run())
        self.assertEqual(results[0].result, "error")
        self.assertIn("does not point at the candidate", results[0].reason)
        self.assertEqual(runner.worker_calls, [], "nothing launches")

    def test_a_partial_startup_inventory_is_not_observed(self):
        events = [json.loads(line) for line in claude_run().splitlines()]
        for key in ("plugins", "mcp_servers"):
            events[0].pop(key)
        stream = "\n".join(json.dumps(event) for event in events) + "\n"
        self.assertEqual(self.run_cases("claude-code", stream)[0][0].inventory, "not observable")

    def test_each_result_says_whether_the_inventory_was_observed(self):
        self.assertEqual(self.run_cases("claude-code", claude_run())[0][0].inventory, "observed")
        without_init = "\n".join(line for line in claude_run().splitlines() if '"init"' not in line) + "\n"
        self.assertEqual(self.run_cases("claude-code", without_init)[0][0].inventory, "not observable")
        self.assertEqual(self.run_cases("codex", codex_run())[0][0].inventory, "not observable")

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

    def test_malformed_case_files_are_invalid_input(self):
        import tempfile
        good = case()
        variants = {
            "a string case": "cases: [oops]\n",
            "no tools for a harness": yaml.safe_dump({"cases": [{**good, "permitted_tools": {}}]}),
            "an unknown field": yaml.safe_dump({"cases": [{**good, "notes": "x"}]}),
            "a sequence as a key": "cases:\n- ? [a, b]\n  : x\n",
        }
        for name, text in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cases.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
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
