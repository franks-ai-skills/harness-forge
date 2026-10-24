# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Forge's bundled runtime dependencies in vendor/, and the check that they are usable.

Standard library only: forge.py runs this before anything bundled is
imported. vendor/manifest.json lists every bundled file with its sha256;
the check compares the files on disk with it. Matching hashes show that
the bundle is the one the manifest describes, not who published it.
Nothing here downloads, installs or changes user configuration.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor"
MANIFEST = VENDOR / "manifest.json"
MINIMUM_PYTHON = (3, 11)
# Top-level modules the bundle provides; each must come from vendor/.
PACKAGES = ("yaml", "markdown_it", "mdurl", "fastjsonschema")
# Written by Python or the file system, never part of the bundle.
_IGNORED_DIRECTORIES = {"__pycache__"}
_IGNORED_NAMES = {".DS_Store"}


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def expected_files() -> dict[str, str]:
    try:
        files = manifest().get("files", {})
    except (OSError, ValueError):
        return {}
    return files if isinstance(files, dict) else {}


def _present() -> set[str]:
    found = set()
    for path in VENDOR.rglob("*"):
        relative = path.relative_to(VENDOR)
        if path.is_dir() or set(relative.parts[:-1]) & _IGNORED_DIRECTORIES or path.name in _IGNORED_NAMES \
                or path.suffix == ".pyc" or path == MANIFEST:
            continue
        found.add(relative.as_posix())
    return found


def problems(*, check_python: bool = True, version: tuple = tuple(sys.version_info[:3])) -> list[str]:
    """Why the bundle cannot be used here; empty when it can."""
    found = []
    if check_python and tuple(version[:2]) < MINIMUM_PYTHON:
        found.append(f"Python {'.'.join(map(str, version))} is too old; Forge needs Python "
                     f"{'.'.join(map(str, MINIMUM_PYTHON))} or newer")
    try:
        files = manifest()["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("no files listed")
    except (OSError, ValueError, KeyError) as error:
        return found + [f"vendor/manifest.json is missing or unreadable ({error})"]
    present = _present()
    for relative, digest in sorted(files.items()):
        path = VENDOR / relative
        if relative not in present:
            found.append(f"vendor/{relative} is missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            found.append(f"vendor/{relative} differs from the manifest")
    found += [f"vendor/{relative} is not part of the bundle" for relative in sorted(present - set(files))]
    return found


def repair(found: list[str]) -> str:
    if any(problem.startswith("Python") for problem in found):
        return "Run Forge with Python 3.11 or newer (python3 --version shows the version)."
    return "Reinstall or update the Harness Forge plugin; Forge downloads nothing itself."


def activate() -> None:
    """Put vendor/ first on the import path; refuse a bundled package already imported from elsewhere."""
    vendor = str(VENDOR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    for name in PACKAGES:
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None) if module is not None else None
        if module is not None and (origin is None or not Path(origin).resolve().is_relative_to(VENDOR)):
            raise ImportError(f"{name} was imported from {origin or 'an unknown location'} before Forge; "
                              "Forge uses only its bundled copy in vendor/")


def origins() -> dict[str, str | None]:
    """Where each bundled package was imported from, for the environment report and tests."""
    return {name: getattr(sys.modules.get(name), "__file__", None) for name in PACKAGES}
