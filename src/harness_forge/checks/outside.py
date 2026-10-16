# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Outside-control hand-off checker. It never inspects platform settings."""

from __future__ import annotations

import re

from harness_forge.checks.common import text
from harness_forge.documents import UnreadableMarkdown, markdown_lines, markdown_section


# Every line Forge writes below the heading of one of its README sections.
# A section holding any other line is not read: its other text could hide or
# fake an entry (a comment, indented code, a setext heading), so the check
# fails and the writer refuses to replace it.
ENTRY = re.compile(r"- `(?P<control>[^`]+)` \(part (?P<part>[A-Z][A-Z0-9]*)\): (?P<rest>.+)")
SECTION_LINES = {
    "Outside controls": (ENTRY.pattern, r"  - .+", r"None recommended\."),
    "Where the skills load": (r"Forge builds .+",),
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
        return f"the '## {heading}' section holds lines Forge did not write: {foreign[0][:120]!r}"
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
        return "fail", f"{readme_path} holds Markdown Forge cannot read reliably: {error}"
    if lines is None:
        return ("pass", "no outside controls") if not controls else ("fail", f"no '## {params['heading']}' section")
    if isinstance(lines, str):
        return "fail", lines
    # An entry names its control, part and status in fixed places; a word elsewhere in a line counts for nothing.
    listed = {(entry["control"], entry["part"]) for line in lines if (entry := ENTRY.fullmatch(line))
              and (entry["rest"] == params["status"] or entry["rest"].startswith(params["status"] + "."))}
    missing = [f"{control['control']} (part {control['part_id']})" for control in controls
               if (control["control"], control["part_id"]) not in listed]
    return ("fail", "not listed: " + ", ".join(missing)) if missing else ("pass", f"{len(controls)} control(s) listed")


REGISTRY = {"outside.readme-coverage": ("1.1.0", check_readme_coverage)}
