"""Flag questions whose content the user may not have seen, in a recorded Claude Code session.

    python tools/transcript_checks.py <session.jsonl>

Development tooling for reviewing harness-intake and harness-research
sessions, not part of the product and not a compliance gate. It reads a
Claude Code session file or a `--output-format stream-json` stream and
checks every AskUserQuestion call against the assistant text written
before it in the same window. A window starts at the user's last message
or at the last tool result, including the answer to a previous question:
content a tool returned has to be presented after it arrived. Only text
blocks count: thinking, tool calls and tool results are not the
assistant's presentation, and text written after the call cannot support
it. Text followed by another tool call and its result no longer counts,
so a question asked after an intervening tool result can be flagged
although its content was shown. Sub-agent (sidechain) events are
ignored.

Findings:
- `bundled`: one call holds more than one question. It does not detect
  several facts bundled into a single question.
- `possible-unseen-reference`: the question refers to content ("these
  goals", "the table above", findings, a contract) while the window's
  visible text and the question's own text are short; choices don't
  count, since they name answers rather than the content asked about. A
  heuristic for human review: a long window does not prove the content
  was shown, and a short one does not prove it was not.

Prints JSON. Exit 0 with no findings, 1 with findings, 2 when the input
has malformed or unsupported events.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REFERENCE = re.compile(r"\b(these|those|above|below|table|list|findings?|goals|proposed|proposals?|contract|"
                       r"requirements)\b", re.I)
SHORT = 200
SYNTHETIC = ("<task-notification>", "<command-name>", "<local-command-", "<command-message>")


def _blocks(message: dict) -> list[dict] | None:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list) and all(isinstance(block, dict) for block in content):
        return content
    return None


def _human(event: dict, blocks: list[dict]) -> bool:
    """A message the person typed: not meta, not a tool result, not a synthetic notice."""
    if event.get("isMeta") or any(block.get("type") == "tool_result" for block in blocks):
        return False
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text").lstrip()
    return bool(text) and not text.startswith(SYNTHETIC)


def _questions(call: dict) -> list[str] | None:
    """The question strings of an AskUserQuestion call, or None when its input has another shape."""
    arguments = call.get("input")
    questions = arguments.get("questions") if isinstance(arguments, dict) else None
    if not isinstance(questions, list) or not questions:
        return None
    if not all(isinstance(question, dict) and isinstance(question.get("question"), str) for question in questions):
        return None
    return [question["question"] for question in questions]


def question_visibility(lines: list[str]) -> dict:
    """Findings per AskUserQuestion call, and the input problems that make the result incomplete."""
    findings, problems, asked = [], [], set()
    window: list[str] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not JSON")
            continue
        if not isinstance(event, dict):
            problems.append(f"line {number}: not a JSON object")
            continue
        if event.get("type") not in ("user", "assistant"):
            continue
        if event.get("isSidechain") or event.get("parent_tool_use_id"):
            continue
        message = event.get("message")
        blocks = _blocks(message) if isinstance(message, dict) else None
        if blocks is None:
            problems.append(f"line {number}: {event['type']} event without a message of content blocks")
            continue
        if event["type"] == "user":
            if any(block.get("type") == "tool_result" for block in blocks) or _human(event, blocks):
                window = []
            continue
        for block in blocks:
            if block.get("type") == "text":
                window.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                asked.add(block.get("id"))
                questions = _questions(block)
                if questions is None:
                    problems.append(f"line {number}: AskUserQuestion input without a list of questions")
                    continue
                visible = sum(len(part.strip()) for part in window)
                found = {"line": number, "question": (questions or [""])[0][:200], "visible_chars": visible}
                if len(questions) > 1:
                    findings.append(dict(found, kind="bundled", questions=len(questions)))
                if any(REFERENCE.search(text) and len(text) < SHORT for text in questions) and visible < SHORT:
                    findings.append(dict(found, kind="possible-unseen-reference"))
    return {"questions": len(asked), "findings": findings, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", type=Path)
    arguments = parser.parse_args(argv)
    result = question_visibility(arguments.session.read_text(encoding="utf-8").splitlines())
    print(json.dumps(result, indent=1))
    return 2 if result["problems"] else 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
