"""The question-visibility check flags questions about content the user may not have seen (#44)."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

CHECK_PATH = Path(__file__).resolve().parents[1] / "tools/transcript_checks.py"


def load_check():
    specification = importlib.util.spec_from_file_location("forge_transcript_checks", CHECK_PATH)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CHECK = load_check()


def human(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(*blocks, **extra):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}, **extra}


def text(value):
    return {"type": "text", "text": value}


def thinking(value):
    return {"type": "thinking", "thinking": value}


def ask(*questions, call_id="q1"):
    return {"type": "tool_use", "id": call_id, "name": "AskUserQuestion", "input": {"questions": [
        {"question": question, "header": "Q", "multiSelect": False,
         "options": [{"label": "A. Yes", "description": "Record them as they are, with every detail unchanged."},
                     {"label": "B. No", "description": "Tell me what to change before anything is recorded."}]}
        for question in questions]}}


def bash(call_id="b1"):
    return {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": "true"}}


def result(call_id, content="ok"):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": content}]}}


GOALS = ("Proposed goals:\n" + "\n".join(f"{n}. The skill does step {n} of the procedure, "
                                          "only when the user invokes it, and records nothing else." for n in range(1, 6)))


def run(*events):
    return CHECK.question_visibility([json.dumps(event) for event in events])


def kinds(report):
    return [finding["kind"] for finding in report["findings"]]


class QuestionVisibilityTest(unittest.TestCase):
    def test_goals_only_in_thinking_are_flagged(self):
        report = run(human("I want a skill that drafts prompts."), assistant(thinking(GOALS)),
                     assistant(ask("Do these goals capture what you want the skill to achieve?")))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])
        self.assertEqual(report["findings"][0]["visible_chars"], 0)

    def test_goals_shown_as_text_first_pass(self):
        report = run(human("I want a skill that drafts prompts."), assistant(text(GOALS)),
                     assistant(ask("Do these goals capture what you want the skill to achieve?")))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["questions"], 1)

    def test_content_inside_the_question_passes(self):
        report = run(human("go"), assistant(ask("Do these goals capture what you want? " + GOALS)))
        self.assertEqual(report["findings"], [])

    def test_a_plain_question_needs_no_presentation(self):
        report = run(human("go"), assistant(ask("Which coding agents should this skill work in?")))
        self.assertEqual(report["findings"], [])

    def test_several_questions_in_one_call_are_bundled(self):
        report = run(human("go"), assistant(text(GOALS)),
                     assistant(ask("Will an agent open pull requests?", "Will it run without prompts?")))
        self.assertEqual(kinds(report), ["bundled"])
        self.assertEqual(report["findings"][0]["questions"], 2)

    def test_text_after_the_question_does_not_count(self):
        report = run(human("go"), assistant(ask("Which survey findings should feed into the plan?")),
                     assistant(text(GOALS)))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])

    def test_a_tool_result_starts_a_new_window(self):
        # The findings arrive in a tool result and are never written out before the question.
        report = run(human("go"), assistant(text(GOALS)), assistant(bash()), result("b1", "F1 F6 F7"),
                     assistant(thinking("Six findings passed.")),
                     assistant(ask("Which survey findings should feed into the plan?")))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])

    def test_the_answer_to_a_question_starts_a_new_window(self):
        report = run(human("go"), assistant(text(GOALS)), assistant(ask("Do these goals hold?")),
                     result("q1", "A"), assistant(ask("Do these four suggested answers hold?", call_id="q2")))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])
        self.assertEqual(report["questions"], 2)

    def test_a_new_human_message_starts_a_new_window(self):
        report = run(human("go"), assistant(text(GOALS)), human("what table are you referring to?"),
                     assistant(ask("Do these goals hold?")))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])

    def test_meta_and_synthetic_user_messages_keep_the_window(self):
        report = run(human("go"), assistant(text(GOALS)),
                     {"type": "user", "isMeta": True, "message": {"role": "user", "content": [
                         {"type": "text", "text": "Base directory for this skill: /plugin/skills/harness-intake"}]}},
                     human("<task-notification>\n<status>completed</status>\n</task-notification>"),
                     assistant(ask("Do these goals hold?")))
        self.assertEqual(report["findings"], [])

    def test_sidechain_text_does_not_count(self):
        report = run(human("go"), assistant(text(GOALS), isSidechain=True),
                     assistant(text(GOALS), parent_tool_use_id="toolu_child"),
                     assistant(ask("Do these goals hold?")))
        self.assertEqual(kinds(report), ["possible-unseen-reference"])

    def test_other_event_types_are_ignored(self):
        report = run({"type": "api-request-blob", "blob": "…"}, {"type": "attachment"}, human("go"),
                     assistant(text(GOALS)), assistant(ask("Do these goals hold?")))
        self.assertEqual(report, {"questions": 1, "findings": [], "problems": []})

    def test_malformed_input_is_reported(self):
        report = CHECK.question_visibility(["{not json", json.dumps({"type": "assistant", "message": "text"}),
                                            json.dumps({"type": "user", "message": {"content": 5}})])
        self.assertEqual(len(report["problems"]), 3)

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            for events, code in (([human("go"), assistant(text(GOALS)), assistant(ask("Do these goals hold?"))], 0),
                                 ([human("go"), assistant(ask("Do these goals hold?"))], 1)):
                path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(CHECK.main([str(path)]), code)
            path.write_text("{not json\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(CHECK.main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
