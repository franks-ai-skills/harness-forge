"""The temporary Codex rollout observer: only its own run's record of the injected instructions counts."""

import json
import tempfile
import unittest
from pathlib import Path

from harness_forge.adapters.base import Completed
from harness_forge.runtime import TEMPORARY_ROLLOUT, UNOBSERVABLE, _take_rollout, activation_case, rollout_activation

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
        self.assertEqual(_take_rollout("not json\n", {"CODEX_HOME": str(self.home)}), (None, None, None))
        self.assertEqual(_take_rollout(STREAM, {"CODEX_HOME": str(self.home)}), (THREAD, None, None))


class ActivationCaseTest(unittest.TestCase):
    """activation_case with a stand-in Codex that writes its rollout like the probe's."""

    def run_case(self, environ: dict) -> tuple[tuple, list[str]]:
        commands = []
        with tempfile.TemporaryDirectory() as home:
            def runner(command, *, cwd, env, stdin, timeout):
                commands.append(command)
                if "--ephemeral" not in command:
                    sessions = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "10" / "21"
                    sessions.mkdir(parents=True, exist_ok=True)
                    (sessions / f"rollout-2026-10-21T10-00-59-{THREAD}.jsonl").write_text(
                        rollout(str(Path(cwd).resolve())), encoding="utf-8")
                return Completed(returncode=0, stdout=STREAM, stderr="", timed_out=False)

            result = activation_case("codex", {f"{DIRECTORY}/SKILL.md": SKILL.encode()}, [DIRECTORY], DIRECTORY,
                                     "$probe-greet", "activates", [], {}, runner=runner,
                                     environ={"CODEX_HOME": home, **environ}, baseline=set(), slash=True)
            left = list(Path(home).rglob("*.jsonl"))
        self.assertEqual(left, [], "the case's rollout is deleted")
        return result, commands[0]

    def test_with_the_variable_the_injection_is_observed(self):
        (outcome, reason, activation, _), command = self.run_case({TEMPORARY_ROLLOUT: "1"})
        self.assertNotIn("--ephemeral", command)
        self.assertEqual((outcome, activation), ("pass", f"instructions injected for {DIRECTORY}/SKILL.md "
                                                          "(session rollout)"), reason)

    def test_by_default_the_run_stays_ephemeral_and_unobserved(self):
        (outcome, reason, activation, _), command = self.run_case({})
        self.assertIn("--ephemeral", command)
        self.assertEqual((outcome, reason, activation), ("not-run", UNOBSERVABLE["codex"][1], None))


if __name__ == "__main__":
    unittest.main()
