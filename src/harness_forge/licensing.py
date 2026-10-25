# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Whether a publicly shared result repository identifies licensing information (#58).

A heuristic over conventional locations, never a legal determination: a
match is not proof of a license or of what it covers, and no match does
not prove there are no terms. Forge never chooses, adds or changes a
license and never blocks a build for this. Only the repository's root and
its README.md are read; links are reported, never followed.
"""

from __future__ import annotations

import re
from pathlib import Path

from harness_forge.documents import UnreadableMarkdown, canonical_digest, markdown_headings, markdown_lines

# A conventional licence file's base name, alone or with a suffix after a separator
# (LICENSE.md, LICENSE-MIT, COPYING.txt), not any name that merely starts with it.
_FILE = re.compile(r"(license|licence|copying|unlicense)([.\-_].+)?", re.I)
_HEADING = re.compile(r"(license|licence|licensing)\s*:?", re.I)
_SPDX = "SPDX-License-Identifier:"
CHECKED = ("a LICENSE, LICENCE, COPYING or UNLICENSE file in the repository root, with or without a suffix such as "
           "LICENSE.md or LICENSE-MIT", "a LICENSES/ directory in the repository root",
           "a License, Licence or Licensing heading in README.md", "an SPDX-License-Identifier line in README.md")
GUIDANCE = ("https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/"
            "customizing-your-repository/licensing-a-repository")


def applies(record: dict) -> bool:
    """The skill is shared publicly: a public reach, or a public repository. Unknown visibility alone is not."""
    return record["reach"] == "public" or record["hosting"]["visibility"] == "public"


def _root_entries(root: Path, found: list[dict], problems: list[str]) -> None:
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as error:
        problems.append(f"the repository root could not be listed: {error.strerror or error}")
        return
    for entry in entries:
        name = entry.name
        directory = name.casefold() == "licenses"
        if not (directory or _FILE.fullmatch(name)):
            continue
        if entry.is_symlink():
            # Not followed: a link alone shows nothing about what it points to.
            found.append({"path": name, "kind": "link, not inspected"})
        elif directory and entry.is_dir():
            found.append({"path": name + "/", "kind": "licenses directory"})
        elif not directory and entry.is_file():
            found.append({"path": name, "kind": "license file"})


def _readme(root: Path, found: list[dict], problems: list[str]) -> None:
    path = root / "README.md"
    if path.is_symlink():
        found.append({"path": "README.md", "kind": "link, not inspected"})
        return
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        headings = markdown_headings(text)
        lines = markdown_lines(text)
    except (OSError, UnicodeDecodeError, UnreadableMarkdown) as error:
        problems.append(f"README.md could not be read: {error}")
        return
    for _, _, heading in headings:
        if _HEADING.fullmatch(heading.strip()):
            found.append({"path": "README.md", "kind": f"heading {heading.strip()!r}"})
    for _, line, code in lines:
        if not code and line.strip().startswith(_SPDX):
            found.append({"path": "README.md", "kind": line.strip()[:200]})


def check(record: dict, root: Path) -> dict:
    """What the conventional locations show, and the basis an answer is recorded for."""
    shared = applies(record)
    found: list[dict] = []
    problems: list[str] = []
    if shared:
        _root_entries(root, found, problems)
        _readme(root, found, problems)
    inspected = [item for item in found if item["kind"] != "link, not inspected"]
    status = ("not applicable" if not shared else "incomplete" if problems
              else "identified" if inspected else "missing")
    # Only what bears on the question: other edits, such as the intake's own README sections, leave it.
    basis = canonical_digest({"applies": shared, "found": found, "problems": problems})
    recorded = record.get("licensing")
    ask = status in ("missing", "incomplete") and not (recorded and recorded.get("basis") == basis)
    return {"applies": shared, "status": status, "checked": list(CHECKED) if shared else [], "found": found,
            "problems": problems, "basis": basis, "ask": ask, "recorded": recorded, "guidance": GUIDANCE}
