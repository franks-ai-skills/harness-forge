# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Whether a result repository identifies licensing information (#58), and Forge's offer of its own license (#91).

A heuristic over conventional locations, never a legal determination: a
match is not proof of a license or of what it covers, and no match does
not prove there are no terms. Only the repository's root and its
README.md are read; links are reported, never followed. Every repository
is inspected; the public-reuse question applies only to a publicly shared
one. Forge never chooses, replaces or changes a license on its own and
never blocks a build for this: it adds one license, its own
AGPL-3.0-only, and only after the user's explicit "Yes, please".
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from pathlib import Path

from harness_forge.documents import UnreadableMarkdown, canonical_digest, markdown_headings, markdown_lines
from harness_forge.documents import markdown_section as markdown_section_bounds

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
# The license Forge ships and offers (#91): its own LICENSE, byte for byte, never templated or fetched.
FORGE_LICENSE = Path(__file__).resolve().parents[2] / "LICENSE"
LICENSE_SHA256 = "8d56b405468aad11f87ab5763f901e276e08d9646ff5c8481b1762b6b789e9ed"
LICENSE_ID = "AGPL-3.0-only"
LICENSE_HEADING = "License"
LICENSE_SECTION = (f"## {LICENSE_HEADING}\n\nExcept where separate terms are stated, this repository is licensed under "
                   "the GNU Affero General Public License v3.0 only ([AGPL-3.0-only](LICENSE)). Third-party material "
                   "retains its applicable terms.\n")
OFFER = {
    "explanation": [
        "GNU Affero General Public License v3.0 only (`AGPL-3.0-only`), the license Harness Forge itself uses.",
        "It permits use, modification and sharing under its conditions; distributing modified versions requires the "
        "corresponding source under the license, and modified versions supporting remote network interaction must "
        "offer their corresponding source to those remote users.",
        "This is not legal advice.",
        "The action adds a repository-root LICENSE and a README license declaration. It applies only to material you "
        "are entitled to license; fetched content, reused skills and other third-party material retain their "
        "applicable terms.",
        "Harness Forge's Generated Output Exception will not be copied.",
    ],
    "question": "Would you like Harness Forge to add this license and its README declaration to this repository?",
    "options": ["Yes, please", "No, thanks"],
}


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
    _root_entries(root, found, problems)
    _readme(root, found, problems)
    inspected = [item for item in found if item["kind"] != "link, not inspected"]
    # A licensing location behind a link was not read: nothing settles it either way.
    status = ("incomplete" if problems or (len(inspected) < len(found) and not inspected)
              else "identified" if inspected else "missing")
    # Only what bears on the question: other edits, such as the intake's own README sections, leave it.
    basis = canonical_digest({"applies": shared, "found": found, "problems": problems})
    recorded = record.get("licensing")
    answered = bool(recorded and recorded.get("basis") == basis)
    ask = shared and status in ("missing", "incomplete") and not answered
    offer = status == "missing" and not answered
    return {"applies": shared, "status": status, "checked": list(CHECKED), "found": found,
            "problems": problems, "basis": basis, "ask": ask, "offer": OFFER if offer else None,
            "recorded": recorded, "guidance": GUIDANCE}


class LicenseNotAdded(ValueError):
    """The license was not added; ``written`` names what a failed write had already completed."""

    def __init__(self, message: str, written: list[str] = ()):
        super().__init__(message + (f"; already written: {', '.join(written)}" if written else ""))
        self.written = list(written)


def forge_license() -> bytes:
    """The LICENSE this Forge ships, refused unless it is exactly the AGPL-3.0 text Forge was released with."""
    try:
        data = FORGE_LICENSE.read_bytes()
    except OSError as error:
        raise LicenseNotAdded(f"Forge's own LICENSE could not be read: {error.strerror or error}") from error
    if hashlib.sha256(data).hexdigest() != LICENSE_SHA256:
        raise LicenseNotAdded("Forge's own LICENSE is not the expected AGPL-3.0 text (sha256 differs); nothing added")
    return data


def _readme_update(root: Path, idea: str) -> tuple[str, bool]:
    """README.md with the License section appended, and whether README.md exists; refused when it cannot show it."""
    path = root / "README.md"
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        current, exists = f"# {idea}\n", False
    else:
        if not stat.S_ISREG(mode):
            raise LicenseNotAdded("README.md is not a regular file; nothing added")
        try:
            current, exists = path.read_text(encoding="utf-8"), True
        except (OSError, UnicodeDecodeError) as error:
            raise LicenseNotAdded(f"README.md could not be read: {error}") from error
    if markdown_section_bounds(current, LICENSE_HEADING) is not None:
        raise LicenseNotAdded("README.md already has a License section; nothing added")
    updated = current.rstrip("\n") + "\n\n" + LICENSE_SECTION
    # The section must show as a heading of its own: an unclosed code fence or HTML block would take it in.
    if markdown_section_bounds(updated, LICENSE_HEADING) != (len(updated) - len(LICENSE_SECTION), len(updated)):
        raise LicenseNotAdded("README.md ends inside an unclosed code fence or HTML block; close it first; nothing "
                              "added")
    return updated, exists


def _write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
    finally:
        os.close(descriptor)


def _rewrite(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        os.ftruncate(descriptor, 0)
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
    finally:
        os.close(descriptor)


def add_license(record: dict, root: Path) -> dict:
    """Write Forge's LICENSE and the README's License section into an eligible repository.

    The caller has the user's explicit "Yes, please" from this session and records the answer afterwards. Every
    check runs before the first write; a failed write raises LicenseNotAdded naming what was already written.
    """
    root = Path(root).resolve()
    result = check(record, root)
    if not result["offer"]:
        raise LicenseNotAdded(f"the repository is not eligible for the offer (licensing status {result['status']}"
                              + (", an answer is recorded for it" if result["status"] == "missing" else "")
                              + "); nothing added")
    data = forge_license()
    try:
        taken = [entry.name for entry in root.iterdir()
                 if _FILE.fullmatch(entry.name) or entry.name.casefold() == "licenses"]
    except OSError as error:
        raise LicenseNotAdded(f"the repository root could not be listed: {error.strerror or error}") from error
    if taken or os.path.lexists(root / "LICENSE"):
        # Whatever kind of entry holds a licensing name, nothing is added beside it.
        raise LicenseNotAdded(f"{', '.join(sorted(taken)) or 'LICENSE'} already exists; nothing added")
    readme, readme_exists = _readme_update(root, record["idea"])
    written: list[str] = []
    try:
        _write_new(root / "LICENSE", data)
        written.append("LICENSE")
        if readme_exists:
            _rewrite(root / "README.md", readme.encode("utf-8"))
        else:
            _write_new(root / "README.md", readme.encode("utf-8"))
        written.append("README.md")
    except OSError as error:
        raise LicenseNotAdded(f"writing failed: {error.strerror or error}", written) from error
    after = check(record, root)
    return {"written": written, "license": LICENSE_ID, "sha256": hashlib.sha256(data).hexdigest(),
            "basis": after["basis"], "status": after["status"]}
