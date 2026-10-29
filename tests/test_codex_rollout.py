"""The Codex rollout observer (a workaround, FORGE #18): only its own run's record of the injection counts."""

import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from harness_forge.runtime import UNOBSERVABLE, _take_rollout, activation_case, rollout_activation

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "codex-rollout" / "dollar-name.jsonl"
THREAD = "01a15d7a-4e3d-7e81-a3d5-95d51a73a312"
DIRECTORY = ".agents/skills/probe-greet"
SKILL = ("---\nname: probe-greet\ndescription: Replies with a fixed greeting word. Use when asked to greet.\n---\n\n"
         "PROBE-MARKER-7f3a: reply with exactly the word PINEAPPLE and nothing else.\n")
STREAM = "\n".join(json.dumps(event) for event in (
    {"type": "thread.started", "thread_id": THREAD}, {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "PINEAPPLE"}},
    {"type": "turn.completed"})) + "\n"


def rollout(workspace: str, thread: str = THREAD) -> str:
    """The probe's rollout (codex-cli 0.163.0, sanitized) as it reads for ``workspace`` and ``thread``."""
    return FIXTURE.read_text(encoding="utf-8").replace("<workspace>", workspace).replace(THREAD, thread)


class RolloutActivationTest(unittest.TestCase):
    def test_the_injected_instructions_of_the_staged_skill_count(self):
        self.assertEqual(rollout_activation(rollout("/w"), THREAD, DIRECTORY, [Path("/w")], SKILL),
                         f"instructions injected for {DIRECTORY}/SKILL.md (session rollout)")

    def test_another_thread_path_or_content_does_not(self):
        other = "01a15d7a-0000-7000-8000-000000000000"
        for label, arguments in (
                ("another thread", (rollout("/w", other), THREAD, DIRECTORY, [Path("/w")], SKILL)),
                ("another workspace", (rollout("/elsewhere"), THREAD, DIRECTORY, [Path("/w")], SKILL)),
                ("other content", (rollout("/w"), THREAD, DIRECTORY, [Path("/w")], SKILL + "More.\n")),
                ("another skill", (rollout("/w"), THREAD, ".agents/skills/other", [Path("/w")], SKILL))):
            with self.subTest(label):
                self.assertIsNone(rollout_activation(*arguments))

    def test_the_name_in_the_prompt_alone_does_not(self):
        lines = [json.loads(line) for line in rollout("/w").splitlines()]
        prompt_only = [line for line in lines
                       if "skills.selected_skill_instructions" not in json.dumps(line)]
        text = "".join(json.dumps(line) + "\n" for line in prompt_only)
        self.assertIn("$probe-greet", text)
        self.assertIsNone(rollout_activation(text, THREAD, DIRECTORY, [Path("/w")], SKILL))


class TakeRolloutTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.sessions = self.home / "sessions" / "2026" / "10" / "21"
        self.sessions.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_only_the_runs_own_rollout_is_read_and_deleted(self):
        own = self.sessions / f"rollout-2026-10-21T10-00-59-{THREAD}.jsonl"
        own.write_text(rollout("/w"), encoding="utf-8")
        other = self.sessions / "rollout-2026-10-21T09-00-00-01a15d7a-0000-7000-8000-000000000000.jsonl"
        other.write_text("{}\n", encoding="utf-8")
        thread, text, problem = _take_rollout(STREAM, {"CODEX_HOME": str(self.home)})
        self.assertEqual((thread, problem), (THREAD, None))
        self.assertIn("skills.selected_skill_instructions", text)
        self.assertFalse(own.exists())
        self.assertTrue(other.exists(), "another session's file is never read or deleted")

    def test_an_unreadable_rollout_is_still_deleted_and_reported(self):
        own = self.sessions / f"rollout-2026-10-21T10-00-59-{THREAD}.jsonl"
        own.write_bytes(rollout("/w").encode() + b"\xe2\x82")
        thread, text, problem = _take_rollout(STREAM, {"CODEX_HOME": str(self.home)})
        self.assertEqual((thread, text), (THREAD, None))
        self.assertIn("could not be read", problem)
        self.assertFalse(own.exists())

    def test_no_thread_or_no_file_reads_nothing(self):
        thread, text, problem = _take_rollout("not json\n", {"CODEX_HOME": str(self.home)})
        self.assertEqual((thread, text), (None, None))
        self.assertIn("printed no thread id, so a session file from it may remain", problem)
        self.assertEqual(_take_rollout(STREAM, {"CODEX_HOME": str(self.home)}), (THREAD, None, None))

    def test_several_files_for_the_thread_are_neither_read_nor_deleted(self):
        for day in ("21", "22"):
            sessions = self.home / "sessions" / "2026" / "10" / day
            sessions.mkdir(parents=True, exist_ok=True)
            (sessions / f"rollout-2026-10-{day}T10-00-59-{THREAD}.jsonl").write_text("{}\n", encoding="utf-8")
        thread, text, problem = _take_rollout(STREAM, {"CODEX_HOME": str(self.home)})
        self.assertEqual((thread, text), (THREAD, None))
        self.assertIn("2 session files", problem)
        self.assertEqual(len(list(self.home.rglob("*.jsonl"))), 2)


class ActivationCaseTest(unittest.TestCase):
    """activation_case with a stand-in Codex that writes its rollout like the probe's."""

    def run_case(self, environ: dict, *, slash: bool = True, content: bytes | None = None,
                 write: bool = True) -> tuple[tuple, list[str]]:
        commands = []
        with tempfile.TemporaryDirectory() as home:
            def runner(command, *, cwd, env, stdin, timeout):
                commands.append(command)
                if "--ephemeral" not in command and write:
                    sessions = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "10" / "21"
                    sessions.mkdir(parents=True, exist_ok=True)
                    (sessions / f"rollout-2026-10-21T10-00-59-{THREAD}.jsonl").write_bytes(
                        content if content is not None else rollout(str(Path(cwd).resolve())).encode())
                return Completed(returncode=0, stdout=STREAM, stderr="", timed_out=False)

            result = activation_case("codex", {f"{DIRECTORY}/SKILL.md": SKILL.encode()}, [DIRECTORY], DIRECTORY,
                                     "$probe-greet", "activates", [], {}, runner=runner,
                                     environ={"CODEX_HOME": home, **environ}, baseline=set(), slash=slash)
            left = list(Path(home).rglob("*.jsonl"))
        self.assertEqual(left, [], "the case's rollout is deleted")
        return result, commands[0]

    def test_by_default_the_injection_is_observed(self):
        """#135: on by default, without a setting, for a user-invoked skill's case."""
        (outcome, reason, activation, _), command = self.run_case({})
        self.assertNotIn("--ephemeral", command)
        self.assertEqual((outcome, activation), ("pass", f"instructions injected for {DIRECTORY}/SKILL.md "
                                                          "(session rollout)"), reason)

    def test_a_case_the_model_starts_stays_ephemeral_and_unobserved(self):
        (outcome, reason, activation, _), command = self.run_case({}, slash=False)
        self.assertIn("--ephemeral", command)
        self.assertEqual((outcome, reason, activation), ("not-run", UNOBSERVABLE["codex"][1], None))

    def test_no_rollout_leaves_the_activation_unobserved(self):
        (outcome, reason, activation, _), _ = self.run_case({}, write=False)
        self.assertEqual((outcome, reason, activation), ("not-run", UNOBSERVABLE["codex"][1], None))

    def test_an_unreadable_rollout_is_an_error_and_still_deleted(self):
        (outcome, reason, activation, _), _ = self.run_case({}, content=b"\xff\xfe not utf-8")
        self.assertEqual((outcome, activation), ("error", None))
        self.assertIn("could not be read", reason)


class BehaviorCaseTest(unittest.TestCase):
    """#135: a Codex behavior case of a user-invoked skill reads its own rollout like an explicit case."""

    CASE = {"id": "greets", "part_id": "A", "mode": "explicit", "prompt": "$probe-greet", "harnesses": ["codex"],
            "network": "none", "writes": [".tmp/**"], "kind": "behavior",
            "expect": {"files": [{"path": ".tmp/greeting.md", "contains": ["PINEAPPLE"]}]}}

    def run_case(self, *, slash=True, content=None, write=True, greeting="PINEAPPLE\n"):
        from harness_forge.behavior import behavior_case
        commands = []
        with tempfile.TemporaryDirectory() as home:
            def runner(command, *, cwd, env, stdin, timeout):
                commands.append(command)
                draft = Path(cwd) / ".tmp" / "greeting.md"
                draft.parent.mkdir(parents=True, exist_ok=True)
                draft.write_text(greeting, encoding="utf-8")
                if "--ephemeral" not in command and write:
                    sessions = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "10" / "29"
                    sessions.mkdir(parents=True, exist_ok=True)
                    (sessions / f"rollout-2026-10-29T10-00-59-{THREAD}.jsonl").write_bytes(
                        content if content is not None else rollout(str(Path(cwd).resolve())).encode())
                return Completed(returncode=0, stdout=STREAM, stderr="", timed_out=False)

            result = behavior_case("codex", dict(self.CASE), {f"{DIRECTORY}/SKILL.md": SKILL.encode()}, DIRECTORY,
                                   "$probe-greet", runner=runner, environ={"CODEX_HOME": home},
                                   version="0.165.0", baseline=set(), slash=slash)
            left = list(Path(home).rglob("*.jsonl"))
        self.assertEqual(left, [], "the case's rollout is deleted")
        return result, commands[0]

    def test_the_rollout_shows_the_activation_and_the_assertions_decide(self):
        (outcome, reason, activation, _), command = self.run_case()
        self.assertNotIn("--ephemeral", command)
        self.assertEqual((outcome, activation), ("pass", f"instructions injected for {DIRECTORY}/SKILL.md "
                                                          "(session rollout)"), reason)

    def test_a_failed_assertion_keeps_the_rollout_activation(self):
        (outcome, reason, activation, _), _ = self.run_case(greeting="hello\n")
        self.assertEqual(outcome, "fail", reason)
        self.assertIn("(session rollout)", activation)

    def test_without_a_rollout_the_behavior_stays_unverified(self):
        (outcome, reason, activation, _), _ = self.run_case(write=False)
        self.assertEqual((outcome, activation), ("unavailable", None))
        self.assertIn("activation was not observed", reason)

    def test_an_unreadable_rollout_claims_no_activation(self):
        (outcome, reason, activation, _), _ = self.run_case(content=b"\xff\xfe not utf-8")
        self.assertEqual((outcome, activation), ("error", None))
        self.assertIn("could not be read", reason)

    def test_an_earlier_failure_keeps_the_rollout_problem_in_its_reason(self):
        """A write outside the declared paths fails the case before activation is judged; the problem stays."""
        from harness_forge.behavior import behavior_case
        with tempfile.TemporaryDirectory() as home:
            def runner(command, *, cwd, env, stdin, timeout):
                (Path(cwd) / "elsewhere.md").write_text("x\n", encoding="utf-8")
                return Completed(returncode=0, stdout="not json\n", stderr="", timed_out=False)

            outcome, reason, activation, _ = behavior_case(
                "codex", dict(self.CASE), {f"{DIRECTORY}/SKILL.md": SKILL.encode()}, DIRECTORY, "$probe-greet",
                runner=runner, environ={"CODEX_HOME": home}, version="0.165.0", baseline=set(), slash=True)
        self.assertEqual((outcome, activation), ("fail", None))
        self.assertIn("changed outside the declared writes: elsewhere.md", reason)
        self.assertIn("also, the case's session rollout could not be identified", reason)

    def test_a_case_the_model_starts_stays_ephemeral(self):
        (outcome, _, activation, _), command = self.run_case(slash=False)
        self.assertIn("--ephemeral", command)
        self.assertEqual((outcome, activation), ("unavailable", None))


if __name__ == "__main__":
    unittest.main()
