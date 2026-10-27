# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""What the user confirms: a short Markdown summary, and a details file with everything it binds.

The title says what the confirmation is for: a research proposal, a configuration proposal before building, or a
configuration summary before verification. It is presentation only.

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
from harness_forge.comparison import DECISION_LINE, unchanged
from harness_forge.models import Contract

_ESCAPED = frozenset("\\`*_[]<>#|~!&")
_BLOCK_START = re.compile(r"([-+=])|(\d+)([.)])")
# By the purpose of the confirmation, which the guidance names.
TITLES = {"research": "Research proposal", "configuration": "Configuration proposal",
          "verification": "Configuration summary"}


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


def _value(value) -> str:
    """A rule field's value: text in quotation marks, anything else as compact JSON."""
    return f"\u201c{value}\u201d" if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _override_change(checked: dict) -> list[str]:
    """What adopting the override changes, field by field, from check_override's structured result."""
    old = checked.get("old")
    if checked["retired"]:
        pinned = f": {old.severity}: {old.statement}" if old is not None else ""
        return [f"retires the pinned rule{text(pinned, '  ')}"]
    lines = ["changes the pinned rule:"]
    for change in checked["changes"]:
        before, after = change.get("old"), change.get("new")
        if change["field"] == "params" and isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    lines.append(f"  - {text('params.' + key, '    ')}: from {text(_value(before.get(key)), '    ')} "
                                 f"to {text(_value(after.get(key)), '    ')}")
        else:
            lines.append(f"  - {text(change['field'], '    ')}: from {text(_value(before), '    ')} to "
                         f"{text(_value(after), '    ')}")
    return lines


def render_listing(contract: Contract, record: dict, *, checked_overrides: list[dict], refused: list[dict],
                   research: list[dict], drift: list[dict], conditions: dict[str, str], drift_states: list[str]) -> str:
    """Everything the confirmation covers, in full, for the details file: what the summary once listed.

    ``checked_overrides`` are check_override's results in the contract's override order; ``drift_states`` gives
    drift_state for each entry of ``drift``. ``conditions`` maps a pinned rule id to its statement.
    """
    conditions = {**conditions, **FORGE_CONDITIONS}
    hosting = record["hosting"]
    lines = [f"### Idea: {heading(record['idea'])}", "", "**Goals**", ""]
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
            first, *more = _override_change(checked)
            lines += [f"- {code(checked['rule_id'])}: {first}", *more]
            lines += [f"  - Warning: it {text(warning, '    ')}" for warning in checked["warnings"]]
        lines.append("")
        warnings = [(checked["rule_id"], warning) for checked in checked_overrides for warning in checked["warnings"]]
        if warnings:
            lines += ["### Override warnings", ""]
            lines += [f"- {code(rule_id)}: it {text(warning, '  ')}" for rule_id, warning in warnings]
            lines.append("")
    if refused:
        lines += ["### Parts not offered for confirmation", ""]
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
        for entry, state in zip(drift, drift_states):
            lines.append(f"- {code(entry['rule_id'])} (finding {text(entry['finding_id'])} of research "
                         f"{code(entry['slug'])}): {text(entry['summary'], '  ')}; {_DRIFT_DECIDED[state]}")
        lines.append("")
    lines += ["**Workers:** best-effort, isolation not certified.", ""]
    return "\n".join(lines)


_DRIFT_DECIDED = {"kept": "kept the pinned rule", "adopted": "adopted the override", "open": "decision pending"}


def render_changes(comparison: dict | None, reason: str | None) -> str:
    """The comparison with the earlier confirmation, in full, for the details file."""
    if comparison is None:
        return f"{text(reason or 'No earlier confirmation was named, so nothing is compared.')}\n"
    changes = comparison["changes"]
    lines = [f"Compared with the confirmation of parts {', '.join(comparison['part_ids']) or 'none'} as recorded in "
             f"{code(comparison['relay'])} (run {code(comparison['run_id'])}, digest {code(comparison['digest'])}). "
             "It rests on local files, not on proof of approval.", ""]
    if unchanged(changes):
        return "\n".join(lines + ["Nothing changed.", ""])
    if changes["goals_changed"]:
        lines.append("- The idea's goals changed.")
    for label, ids in (("added", changes["overrides_added"]), ("removed", changes["overrides_removed"])):
        if ids:
            lines.append(f"- Overrides {label}: {', '.join(code(rule_id) for rule_id in ids)}")
    for part in changes["parts"]:
        if part["status"] == "removed":
            lines.append(f"- Part {text(part['id'])}: removed")
        elif part["status"] == "unconfirmed":
            lines.append(f"- Part {text(part['id'])}: not confirmed before")
        else:
            lines.append(f"- Part {text(part['id'])}:")
            if part["fields"]:
                lines.append(f"  - changed: {', '.join(part['fields'])}")
            for rule_id in part["weakened"]:
                lines.append(f"  - weakened: {code(rule_id)}")
            for entry in part["changed"]:
                kinds = " → ".join(dict.fromkeys(entry["kind"]))
                lines.append(f"  - changed: {code(entry['rule_id'])} ({kinds}"
                             f"{', wording changed' if entry['wording'] else ''})")
            for rule_id in part["new"]:
                lines.append(f"  - new: {code(rule_id)}")
            for entry in part["removed"]:
                lines.append(f"  - removed: {code(entry['rule_id'])}, "
                             f"{KIND_LABELS.get(entry['kind'], entry['kind'])}: {text(entry['statement'], '    ')}")
    return "\n".join(lines + [""])


# --- The summary the user reads (#99) -----------------------------------------

# The owner's limit for everything visible at a confirmation; the summary keeps headroom for the question.
LIMITS = {"lines": 27, "words": 265, "chars": 1575}
SUMMARY_LIMITS = {"lines": 25, "words": 245, "chars": 1450}
QUESTION_LIMITS = {"lines": 1, "words": 20, "chars": 125}
OPTIONS = ("Confirm", "Leave pending")
ITEM_CHARS = 200
_TEXT_CHARS = 120
_SHOWN_PARTS = 6


_SINGULAR = {"weakened requirements": "weakened requirement", "other requirement changes": "other requirement change",
             "other changes": "other change", "goal conflicts": "goal conflict", "overrides": "override",
             "override warnings": "override warning", "drift entries": "drift entry", "refused parts": "refused part",
             "parts": "part"}


class SummaryTooLong(ValueError):
    """The lines a summary must hold cannot fit within its limits."""


def summary_size(value: str) -> dict:
    """Lines (each ends with a newline), words (split on Unicode whitespace) and characters (code points)."""
    return {"lines": value.count("\n"), "words": len(value.split()), "chars": len(value)}


def fits(value: str, limits: dict) -> bool:
    size = summary_size(value)
    return all(size[key] <= limits[key] for key in limits)


def _short(value, limit: int = _TEXT_CHARS) -> str:
    """Record text on one line, cut before escaping so that no escape or code span is split."""
    value = " ".join(str(value).split())
    return text(value if len(value) <= limit else value[:limit - 1].rstrip() + "…")


def _names(ids: list[str]) -> str:
    return ", ".join(code(item) for item in ids)


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


class _Item:
    """One summary line in up to several forms, richest first, each with what it leaves out by kind."""

    def __init__(self, kind: str, variants: list[tuple[str, dict]], count: dict | None = None):
        self.kind = kind
        self.variants = variants
        self.count = count or {kind: 1}  # what leaving the whole line out leaves out


def _change_items(comparison: dict) -> list[_Item]:
    changes, items = comparison["changes"], []
    if changes["goals_changed"]:
        items.append(_Item("other changes", [("- The idea's goals changed.", {})]))
    added, removed = changes["overrides_added"], changes["overrides_removed"]
    if added or removed:
        line = "- Overrides " + "; ".join(part for part in (f"added {_names(added)}" if added else "",
                                                           f"removed {_names(removed)}" if removed else "") if part)
        items.append(_Item("other changes", [(line + ".", {})]))
    for part in changes["parts"]:
        name = text(part["id"])
        if part["status"] in ("removed", "unconfirmed"):
            label = "removed" if part["status"] == "removed" else "not confirmed before"
            items.append(_Item("parts", [(f"- Part {name}: {label}.", {})]))
            continue
        if part["fields"]:
            items.append(_Item("parts", [(f"- Part {name}: {' and '.join(part['fields'])} changed.", {})]))
        weakened = part["weakened"]
        other = ([("changed", entry["rule_id"]) for entry in part["changed"]] + [("new", rule_id) for rule_id in part["new"]]
                 + [("removed", entry["rule_id"]) for entry in part["removed"] if entry["rule_id"] not in weakened])
        if not weakened and not other:
            continue
        counts = {"weakened requirements": len(weakened), "other requirement changes": len(other)}
        variants = []
        # Names go from the end first: the other changes, then the weakened ones, then only counts remain.
        for shown_weak in range(len(weakened), -1, -1):
            for shown_other in (range(len(other), -1, -1) if shown_weak == len(weakened) else (0,)):
                pieces = []
                if weakened:
                    named = _names(weakened[:shown_weak])
                    rest = len(weakened) - shown_weak
                    pieces.append(f"{_plural(len(weakened), 'weakened')}" + (f" ({named}" + (
                        f" and {rest} more)" if rest else ")") if shown_weak else ""))
                groups = {}
                for label, rule_id in other[:shown_other]:
                    groups.setdefault(label, []).append(rule_id)
                for label in ("changed", "new", "removed"):
                    total = sum(1 for entry in other if entry[0] == label)
                    if total:
                        named = groups.get(label, [])
                        pieces.append(f"{total} {label}" + (f" ({_names(named)}" + (
                            f" and {total - len(named)} more)" if total > len(named) else ")") if named else ""))
                omitted = {"weakened requirements": len(weakened) - shown_weak,
                           "other requirement changes": len(other) - shown_other}
                variants.append((f"- Part {name} requirements: {'; '.join(pieces)}.",
                                 {key: value for key, value in omitted.items() if value}))
        items.append(_Item("other requirement changes", variants,
                           {key: value for key, value in counts.items() if value}))
    return items


def _status(comparison: dict | None, reason: str | None, nothing: bool) -> str:
    if comparison is None:
        return _short(reason, ITEM_CHARS - 2) if reason else "No earlier confirmation was named, so nothing here is compared."
    ids = comparison["part_ids"]
    shown = ", ".join(text(part_id) for part_id in ids[:_SHOWN_PARTS])
    if len(ids) > _SHOWN_PARTS:
        shown += f" and {len(ids) - _SHOWN_PARTS} more parts"
    run_id = comparison["run_id"] if len(comparison["run_id"]) <= 40 else comparison["run_id"][:39] + "…"
    where = (f"parts {shown}" if ids else "the idea") + (f" (run {code(run_id)}, {code(comparison['digest'][:12])}, "
                                                          f"as recorded in {code(comparison['relay'][:60])})")
    return f"Nothing changed since the confirmation of {where}." if nothing else f"Changes since the confirmation of {where}:"


def render_question(title: str, part_ids: list[str]) -> str:
    """The confirmation question the skills ask as it is, within QUESTION_LIMITS."""
    what = title[0].lower() + title[1:]
    if not part_ids:
        target = "the idea"
    else:
        target = ("part " if len(part_ids) == 1 else "parts ") + ", ".join(part_ids)
    question = f"Confirm the {what} above and its linked details for {target}?"
    return question if fits(question + "\n", QUESTION_LIMITS) else f"Confirm the {what} above and its linked details for the listed parts?"


def render_summary(contract: Contract, record: dict, *, title: str, checked_overrides: list[dict], refused: list[dict],
                   research: list[dict], drift: list[dict], conditions: dict[str, str], details_path: str,
                   drift_states: list[str], comparison: dict | None = None, comparison_reason: str | None = None) -> str:
    """The summary the agent shows unchanged before the confirmation question, within SUMMARY_LIMITS.

    The lines go in the order the user decides on them: what changed since the named earlier confirmation, goal
    conflicts and warnings, refused parts and open research, then each part's outcome and mechanism. A line that
    does not fit is left out or shortened, and what that leaves out is counted by kind in the line before the
    details link. Raises SummaryTooLong when the title, the comparison status, that count and the link alone do
    not fit.
    """
    nothing = comparison is not None and unchanged(comparison["changes"])
    items: list[_Item] = _change_items(comparison) if comparison and not nothing else []
    for part in contract.parts:
        conflict = part.get("goal_conflict")
        if conflict:
            line = (f"- Goal conflict, part {text(part['id'])}: {text(conflict['chosen'])} is ruled out by "
                    f"{_names(conflict['rule_ids'][:2])}; {_RESOLUTIONS[conflict['choice']]}.")
            items.append(_Item("goal conflicts", [(f"{line} {_CONSEQUENCES[conflict['choice']]}", {}), (line, {})]))
    for checked in checked_overrides:
        items.append(_Item("overrides", [(f"- Override {code(checked['rule_id'])}: "
                                          f"{'retires' if checked['retired'] else 'changes'} the pinned rule.", {})]))
        for warning in checked["warnings"]:
            items.append(_Item("override warnings", [(f"  - Warning: it {_short(warning)}", {})]))
    states = list(zip(drift, drift_states))
    for entry, state in states:
        if state == "open":
            items.append(_Item("drift entries", [(f"- Drift on {code(entry['rule_id'])}: {_short(entry['summary'])}; "
                                                  "decision pending.", {})]))
    for item in refused:
        items.append(_Item("refused parts", [(f"- Not offered, part {text(item['part_id'])}: "
                                              f"{_short(item['problem'])}", {})]))
    for item in research:
        if item["accepted"] != "current":
            line = (f"- Research to do, part {text(item['part_id'])}: requirements {text(item['accepted'])}, deep "
                    f"research {text(item['deep_research'])}")
            items.append(_Item("open research", [(f"{line}; next: {_short(item['next'])}", {}), (f"{line}.", {})]))
    for part in contract.parts:
        required = sum(1 for control in part["outside_controls"] if control["required_for_goal"])
        facts = [f"{text(part['outcome'])} · {text(part['mechanism']) if part['mechanism'] else 'no harness build'}",
                 _plural(len(part["requirements"]), "accepted requirement")]
        if part["reuse"]:
            facts.append(f"reuses {_short(part['reuse']['name'], 60)}")
        if required:
            facts.append(_plural(required, "outside control") + " required for the goal")
        items.append(_Item("parts", [(f"- Part {text(part['id'])} ({code(part['name'][:60])}): {'; '.join(facts)}.",
                                      {})]))
    if not contract.parts:
        items.append(_Item("parts", [("- No parts yet: this confirms the idea and its goals.", {})]))
    for entry, state in states:
        if state != "open":
            items.append(_Item("drift entries", [(f"- Drift on {code(entry['rule_id'])}: {_DRIFT_DECIDED[state]}.",
                                                  {})]))
    items.append(_Item("worker policy", [("- Workers: best-effort, isolation not certified.", {})]))

    head = [f"## {title}", "", _status(comparison, comparison_reason, nothing)]
    name = details_path.rsplit("/", 1)[-1]
    link = f"**Details:** [{text(name)}]({link_destination(details_path)})"

    def render(lines: list[str], omitted: dict) -> str:
        counted = [f"{count} {_SINGULAR.get(kind, kind) if count == 1 else kind}"
                   for kind, count in omitted.items() if count]
        tail = ["", f"Not shown here, see the details: {', '.join(counted)}."] if counted else []
        return "\n".join(head + lines + tail + ["", link, ""])

    def add(total: dict, more: dict) -> dict:
        merged = dict(total)
        for key, value in more.items():
            merged[key] = merged.get(key, 0) + value
        return merged

    nothing_shown = {}
    for item in items:
        nothing_shown = add(nothing_shown, item.count)
    if not fits(render([], nothing_shown), SUMMARY_LIMITS):
        raise SummaryTooLong(_too_long(head, link, render([], nothing_shown)))
    usable = [[(line, leaves) for line, leaves in item.variants if len(line) <= ITEM_CHARS] for item in items]
    # First as many lines as fit in priority order, each in its shortest form; then, in the same order, each
    # shown line in the richest form that still fits.
    chosen: dict[int, int] = {}

    def state(picks: dict[int, int]) -> str:
        omitted = {}
        for index, item in enumerate(items):
            omitted = add(omitted, usable[index][picks[index]][1] if index in picks else item.count)
        return render([usable[index][picks[index]][0] for index in sorted(picks)], omitted)

    for index in range(len(items)):
        if usable[index] and fits(state({**chosen, index: len(usable[index]) - 1}), SUMMARY_LIMITS):
            chosen[index] = len(usable[index]) - 1
    for index in sorted(chosen):
        for richer in range(chosen[index]):
            if fits(state({**chosen, index: richer}), SUMMARY_LIMITS):
                chosen[index] = richer
                break
    return state(chosen)


def _too_long(head: list[str], link: str, minimal: str) -> str:
    """Which mandatory line keeps the summary from fitting, and its size."""
    room = SUMMARY_LIMITS["chars"]
    if len(link) + 1 > room // 2:
        return (f"The summary cannot fit within its limits: the details link line is {len(link):,} characters; use a "
                "run directory with a shorter path")
    size = summary_size(minimal)
    over = [f"{size[key]:,} {key} of {SUMMARY_LIMITS[key]:,}" for key in SUMMARY_LIMITS if size[key] > SUMMARY_LIMITS[key]]
    return ("The summary cannot fit within its limits: the title, status, omission and link lines need "
            + " and ".join(over))


def render_details(contract: Contract, record: dict, *, title: str, worker_evidence: list[dict] | tuple,
                   decision_path: str | None = None, listing: str | None = None, changes: str | None = None) -> str:
    """Everything the confirmation digest binds, readable and exact, as literal blocks.

    ``decision_path`` names the record (repository-relative), so a later comparison can tell which record this
    confirmation was for. ``listing`` (render_listing) and ``changes`` (render_changes) hold, in full, what the
    summary may leave out. None of them is bound by the digest.
    """
    lines = [f"# {title} details: {heading(record['idea'])}", "",
             f"- The confirmation digest {code(contract.digest)} is what a confirmation names; Forge checks it.",
             "- This file's own sha256 is returned by prepare-contract and is not inside it.",
             "- Neither is a decision for you."]
    if decision_path is not None:
        lines.append(f"{DECISION_LINE}{code(decision_path)}")
    lines.append("")
    if changes is not None:
        lines += ["## Changes since the earlier confirmation", "", changes.rstrip("\n"), ""]
    if listing is not None:
        lines += ["## Everything this confirms", "", listing.rstrip("\n"), ""]
    lines += ["## Readable explanation", "", fenced(contract.text, "text"), "",
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


def details_name(details: str, title: str) -> tuple[str, str]:
    """The details file's name and its sha256: the name carries the checksum, so a name holds one content."""
    digest = hashlib.sha256(details.encode("utf-8")).hexdigest()
    return f"{title.lower().replace(' ', '-')}-{digest[:16]}.md", digest
