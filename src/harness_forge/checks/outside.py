# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Outside-control hand-off checker. It never inspects platform settings."""

from __future__ import annotations

import re

from harness_forge.checks.common import text
from harness_forge.documents import UnreadableMarkdown, markdown_lines, markdown_section


FORGE_URL = "https://github.com/franks-ai-skills/harness-forge"
KB_URL = "https://github.com/franks-ai-skills/agent-harness-kb"
# The Outside controls section's opening line; {kb_version} is the pinned KB release its link names.
OUTSIDE_INTRO = ("Outside controls are settings the repository owner or operator configures outside the skills' "
                 f"instructions, for example on the Git host or in CI. [Harness Forge]({FORGE_URL}) recommends them "
                 "from the intake answers; it does not set them up or check them. Background: [Outside gates]"
                 f"({KB_URL}/blob/v{{kb_version}}/docs/guide/outside-gates.md).")
REACH_START = f"[Harness Forge]({FORGE_URL}) builds "

# Every line Forge writes below the heading of one of its README sections.
# A section holding any other line is not read: its other text could hide or
# fake an entry (a comment, indented code, a setext heading), so the check
# fails and the writer refuses to replace it. The readable entry and the
# earlier one are both read, so that a README written before upgrades in place.
ENTRY = re.compile(r"- `(?P<control>[^`]+)` \(part (?P<part>[A-Z][A-Z0-9]*)\): (?P<rest>.+)")
READABLE_ENTRY = re.compile(r"- \*\*(?P<name>[^*`]+)\*\* \(`(?P<control>[^`]+)`(?:, part (?P<part>[A-Z][A-Z0-9]*))?\): "
                            r"(?P<rest>.+)")
_INTRO = (re.escape(OUTSIDE_INTRO.split("{kb_version}")[0]) + r"[0-9]+\.[0-9]+\.[0-9]+"
          + re.escape(OUTSIDE_INTRO.split("{kb_version}")[1]))
SECTION_LINES = {
    "Outside controls": (ENTRY.pattern, READABLE_ENTRY.pattern, _INTRO, r"  - .+", r"None recommended\."),
    "Where the skills load": (r"Forge builds .+", re.escape(REACH_START) + ".+"),
}


def foreign_lines(section: str, heading: str) -> list[str]:
    """Lines below the section's heading that Forge does not write there."""
    patterns = SECTION_LINES[heading]
    lines = [line for _, line, _ in markdown_lines(section)][1:]
    return [line for line in lines if line.strip() and not any(re.fullmatch(p, line) for p in patterns)]


def _section(markdown: str, heading: str) -> list[str] | str | None:
    """The section's lines below its heading, or why they are not read, or None without the section."""
    bounds = markdown_section(markdown, heading)
    if bounds is None:
        return None
    section = markdown[slice(*bounds)]
    foreign = foreign_lines(section, heading)
    if foreign:
        return f"the '## {heading}' section holds lines Harness Forge did not write: {foreign[0][:120]!r}"
    return [line for _, line, _ in markdown_lines(section)][1:]


def check_readme_coverage(files, facts, params):
    controls = facts.get("outside_controls")
    if controls is None:
        return "fail", "the decision record's outside controls were not supplied"
    readme_path = facts.get("readme_path", "README.md")
    if readme_path not in files:
        return ("pass", "no outside controls") if not controls else ("fail", f"{readme_path} is missing")
    try:
        lines = _section(text(files[readme_path]), params["heading"])
    except UnreadableMarkdown as error:
        return "fail", f"{readme_path} holds Markdown Harness Forge cannot read reliably: {error}"
    if lines is None:
        return ("pass", "no outside controls") if not controls else ("fail", f"no '## {params['heading']}' section")
    if isinstance(lines, str):
        return "fail", lines
    # An entry names its control, part and status in fixed places; a name or a word elsewhere in a line counts
    # for nothing. An entry without a part stands for the decision's only part, never for one of several.
    parts = facts.get("decision_part_ids") or []
    only = parts[0] if len(parts) == 1 else None
    listed = set()
    for line in lines:
        entry = ENTRY.fullmatch(line) or READABLE_ENTRY.fullmatch(line)
        if not entry or not (entry["rest"] == params["status"] or entry["rest"].startswith(params["status"] + ".")):
            continue
        part = entry["part"] or only
        if part:
            listed.add((entry["control"], part))
    missing = [f"{control['control']} (part {control['part_id']})" for control in controls
               if (control["control"], control["part_id"]) not in listed]
    return ("fail", "not listed: " + ", ".join(missing)) if missing else ("pass", f"{len(controls)} control(s) listed")


REGISTRY = {"outside.readme-coverage": ("1.3.0", check_readme_coverage)}
