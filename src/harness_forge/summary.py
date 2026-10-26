# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""The contract as the user confirms it: a short Markdown summary, and a details file with everything it binds.

The summary is shown in the conversation as Markdown, so every record text in it is escaped to literal text.
The details file holds the full readable contract and the exact bound content inside code fences, where nothing
is interpreted; fetched text keeps its own label and escaping there. Neither changes the contract digest.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse

from harness_forge.contracts import (
    AVAILABILITY, FORGE_CONDITIONS, KIND_LABELS, OUTSIDE_STATUS, TARGET_NAMES, effective_prerequisites,
    pinned_controls,
)
from harness_forge.models import Contract

_ESCAPED = frozenset("\\`*_[]<>#|~!&")
_BLOCK_START = re.compile(r"([-+=])|(\d+)([.)])")
DETAILS_PREFIX = "contract-details-"


def text(value, indent: str = "") -> str:
    """Record text as literal Markdown text: no construct of its own, controls visible, line breaks kept.

    Each line break becomes a hard break; continuation lines carry ``indent`` so they stay in their list item.
    """
    lines = []
    for line in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip(" \t")
        escaped = []
        for char in line:
            if char in _ESCAPED:
                escaped.append("\\" + char)
            elif unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"):
                escaped.append(f"\\u{ord(char):04x}")
            else:
                escaped.append(char)
        line = "".join(escaped)
        start = _BLOCK_START.match(line)
        if start and start.group(1):
            line = "\\" + line
        elif start and (len(line) == start.end() or line[start.end()] == " "):
            line = start.group(2) + "\\" + line[len(start.group(2)):]
        lines.append(line)
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    while len(lines) > 1 and not lines[0]:
        lines.pop(0)
    # A blank line would end the paragraph; a lone backslash keeps the break visible instead.
    return ("\\\n" + indent).join(line or "\\" for line in lines) if len(lines) > 1 else lines[0]


def heading(value) -> str:
    """Record text for a heading, which holds one line: its line breaks become spaces."""
    return text(" ".join(str(value).split()))


def code(value) -> str:
    """An identifier as inline code that no backtick inside it can close."""
    value = " ".join(str(value).split())
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * (longest + 1)
    padded = f" {value} " if value.startswith("`") or value.endswith("`") or not value.strip() else value
    return f"{fence}{padded}{fence}"


def fenced(body: str, info: str = "") -> str:
    """A code block whose fence is longer than any backtick run inside it, so its content stays literal."""
    longest = max((len(run) for run in re.findall(r"`{3,}", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{body.rstrip(chr(10))}\n{fence}"


def link_destination(path: str) -> str:
    """An absolute path as a link destination: percent-encoded, so spaces, '&', '#', '%' and newlines survive."""
    return urllib.parse.quote(path, safe="/")


def _targets(targets: list[str]) -> str:
    names = [TARGET_NAMES.get(target, target) for target in targets]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


_CONSEQUENCES = {
    "keep": "It stays an error, so the part is never verified.",
    "recommended": "Verification will assess the current choice and goal; confirmation does not verify the part.",
    "adjust-goal": "Verification will assess the current choice and goal; confirmation does not verify the part.",
}
_RESOLUTIONS = {"keep": "kept as it is", "recommended": "switched to the recommended mechanism",
                "adjust-goal": "goal adjusted"}


def _part(part: dict, conditions: dict[str, str]) -> list[str]:
    lines = [f"### Part {heading(part['id'])}: {heading(part['name'])}", "",
             f"- **Goal:** {text(part['goal'], '  ')}",
             f"- **Outcome:** {text(part['outcome'])} · **Mechanism:** "
             + (text(part["mechanism"]) if part["mechanism"] else "no harness build")]
    if part["reuse"]:
        lines.append(f"- **Reused:** {text(part['reuse']['name'], '  ')}, pinned source "
                     f"{text(part['reuse']['source'], '  ')}")
    required = [control for control in part["outside_controls"] if control["required_for_goal"]]
    if required:
        lines.append(f"- **Outside controls required for the goal** ({OUTSIDE_STATUS}):")
        known = pinned_controls()
        for control in required:
            name = " ".join(known.get(control["control"], {}).get("name", control["control"]).split())
            prerequisites = [" ".join((conditions.get(rule_id) or rule_id).split())
                             for rule_id in effective_prerequisites(control)]
            lines.append(f"  - {text(name, '    ')}: {AVAILABILITY[control['availability']]}. It works as a gate "
                         "only when:")
            lines += [f"    - {text(condition, '      ')}" for condition in prerequisites]
    if part["requirements"]:
        lines.append("- **Accepted requirements:**")
        lines += [f"  - {KIND_LABELS[item['kind']]}: {text(item['statement'], '    ')}" for item in part["requirements"]]
    else:
        lines.append("- **Accepted requirements:** none")
    conflict = part.get("goal_conflict")
    if conflict:
        lines.append(f"- **Goal conflict:** {text(conflict['chosen'])} is ruled out by "
                     f"{', '.join(code(rule_id) for rule_id in conflict['rule_ids'])}; "
                     f"{_RESOLUTIONS[conflict['choice']]} ({text(conflict['reason'], '  ')}). "
                     f"{_CONSEQUENCES[conflict['choice']]}")
    return lines


def _override_change(checked: dict) -> str:
    if checked["retired"]:
        return "retires the pinned rule"
    fields = [change["field"] for change in checked["changes"]]
    return "changes the pinned rule's " + (", ".join(fields) if fields else "content")


def render_summary(contract: Contract, record: dict, *, checked_overrides: list[dict], refused: list[dict],
                   research: list[dict], drift: list[dict], conditions: dict[str, str], details_path: str,
                   drift_states: list[str]) -> str:
    """The summary the agent shows unchanged before the confirmation question.

    ``checked_overrides`` are check_override's results in the contract's override order; ``drift_states`` gives
    drift_state for each entry of ``drift``. ``conditions`` maps a pinned rule id to its statement.
    """
    conditions = {**conditions, **FORGE_CONDITIONS}
    hosting = record["hosting"]
    lines = [f"## Contract summary: {heading(record['idea'])}", "", "**Goals**", ""]
    lines += [f"- {text(goal, '  ')}" for goal in record["goals"]]
    lines += ["", f"**Scope:** {_targets(record['targets'])} · reach {text(record['reach'])} · hosting "
              f"{text(hosting['platform'])}, {text(hosting['plan'])} plan, {text(hosting['visibility'])}", ""]
    if not contract.parts:
        lines += ["No parts yet: this confirms the idea and its goals.", ""]
    for part in contract.parts:
        lines += _part(part, conditions) + [""]
    if checked_overrides:
        lines += ["### Rule overrides", ""]
        for checked in checked_overrides:
            lines.append(f"- {code(checked['rule_id'])}: {_override_change(checked)}")
            lines += [f"  - Warning: it {text(warning, '    ')}" for warning in checked["warnings"]]
        lines.append("")
    if refused:
        lines += ["### Not offered for confirmation", ""]
        lines += [f"- Part {text(item['part_id'])}: {text(item['problem'], '  ')}" for item in refused]
        lines.append("")
    open_research = [item for item in research if item["accepted"] != "current"]
    if open_research:
        lines += ["### Research to do", ""]
        lines += [f"- Part {text(item['part_id'])}: accepted requirements {text(item['accepted'])}, deep research "
                  f"{text(item['deep_research'])}; next: {text(item['next'], '  ')}" for item in open_research]
        lines.append("")
    if drift:
        lines += ["### Drift", ""]
        decided = {"kept": "kept the pinned rule", "adopted": "adopted the override", "open": "decision pending"}
        for entry, state in zip(drift, drift_states):
            lines.append(f"- {code(entry['rule_id'])} (finding {text(entry['finding_id'])} of research "
                         f"{code(entry['slug'])}): {text(entry['summary'], '  ')}; {decided[state]}")
        lines.append("")
    name = details_path.rsplit("/", 1)[-1]
    lines += ["**Workers:** best-effort, isolation not certified.", "",
              f"**Details:** [{text(name)}]({link_destination(details_path)})", ""]
    return "\n".join(lines)


def render_details(contract: Contract, record: dict, *, worker_evidence: list[dict] | tuple) -> str:
    """Everything the confirmation digest binds, readable and exact, as literal blocks."""
    lines = [f"# Contract details: {heading(record['idea'])}", "",
             f"- The confirmation digest {code(contract.digest)} is what a confirmation names; Forge checks it.",
             "- This file's own sha256 is returned by prepare-contract and is not inside it.",
             "- Neither is a decision for you.", "",
             "## Readable explanation", "", fenced(contract.text, "text"), "",
             "## Bound content", "",
             "The exact content the confirmation digest is computed from.", "",
             fenced(json.dumps(contract.content, indent=2, sort_keys=True, ensure_ascii=False), "json"), ""]
    lines += ["## Rule overrides", ""]
    if contract.overrides:
        for entry in contract.overrides:
            lines += [f"### {code(entry['rule_id'])}", "", f"sha256 {code(entry['digest'])}", "",
                      fenced(entry["content"], "yaml"), ""]
    else:
        lines += ["None.", ""]
    lines += ["## Worker evidence", ""]
    lines += [fenced(json.dumps(list(worker_evidence), indent=2, sort_keys=True, ensure_ascii=False), "json"), ""] \
        if worker_evidence else ["None recorded.", ""]
    return "\n".join(lines)


def details_name(details: str) -> tuple[str, str]:
    """The details file's name and its sha256: the name carries the checksum, so a name holds one content."""
    digest = hashlib.sha256(details.encode("utf-8")).hexdigest()
    return f"{DETAILS_PREFIX}{digest[:16]}.md", digest
