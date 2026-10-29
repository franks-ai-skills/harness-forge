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
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor"
MANIFEST = VENDOR / "manifest.json"
MINIMUM_PYTHON = (3, 11)
# Interpreters an old python3 may sit beside on PATH, newest first (#136): probed, never installed.
CANDIDATES = ("python3.14", "python3.13", "python3.12", "python3.11")
_PROBE = "import sys; print('%d.%d.%d' % tuple(sys.version_info[:3]))"
# Top-level modules the bundle provides; each must come from vendor/.
PACKAGES = ("yaml", "markdown_it", "mdurl", "fastjsonschema")
# Written by Python or the file system, never part of the bundle.
_IGNORED_DIRECTORIES = {"__pycache__"}
_IGNORED_NAMES = {".DS_Store"}


def manifest(vendor: Path = VENDOR) -> dict:
    return json.loads((vendor / MANIFEST.name).read_text(encoding="utf-8"))


def _listed(vendor: Path) -> dict[str, str]:
    """The manifest's files: bundle-relative paths to sha256; ValueError when it has another shape."""
    listed = manifest(vendor)
    files = listed.get("files") if isinstance(listed, dict) else None
    if not isinstance(files, dict) or not files or not all(
            isinstance(path, str) and isinstance(digest, str) and len(digest) == 64 for path, digest in files.items()):
        raise ValueError("it does not list the bundled files with their sha256")
    return files


def expected_files(vendor: Path = VENDOR) -> dict[str, str]:
    try:
        return _listed(vendor)
    except (OSError, ValueError):
        return {}


def _present(vendor: Path) -> tuple[set[str], list[str]]:
    """Regular files under vendor/, and links, which a bundle never contains."""
    found, links = set(), []
    for path in vendor.rglob("*"):
        relative = path.relative_to(vendor)
        if path.is_symlink():
            links.append(f"vendor/{relative.as_posix()} is a link, which the bundle never contains")
            continue
        # Python's own caches only: a stray .pyc outside __pycache__ could be imported without its source.
        if path.is_dir() or _IGNORED_DIRECTORIES & set(relative.parts[:-1]) or path.name in _IGNORED_NAMES \
                or relative.as_posix() == MANIFEST.name:
            continue
        found.add(relative.as_posix())
    return found, links


def problems(*, check_python: bool = True, version: tuple = tuple(sys.version_info[:3]),
             vendor: Path = VENDOR) -> list[str]:
    """Why the bundle cannot be used here; empty when it can."""
    found = []
    if check_python and tuple(version[:2]) < MINIMUM_PYTHON:
        found.append(f"Python {'.'.join(map(str, version))} is too old; Forge needs Python "
                     f"{'.'.join(map(str, MINIMUM_PYTHON))} or newer")
    try:
        files = _listed(vendor)
    except (OSError, ValueError) as error:
        return found + [f"vendor/manifest.json is missing or unreadable ({error})"]
    present, links = _present(vendor)
    found += links
    for relative, digest in sorted(files.items()):
        if relative not in present:
            found.append(f"vendor/{relative} is missing")
            continue
        try:
            data = (vendor / relative).read_bytes()
        except OSError as error:
            found.append(f"vendor/{relative} cannot be read ({error.strerror or error})")
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            found.append(f"vendor/{relative} differs from the manifest")
    found += [f"vendor/{relative} is not part of the bundle" for relative in sorted(present - set(files))]
    return found


def candidates(path=None, run=subprocess.run, timeout: int = 10) -> list:
    """Each interpreter of CANDIDATES on PATH that starts and is new enough: command, path and version.

    A candidate only shows that it starts and meets the version; the bundle check still runs under it. Nothing is
    installed or changed. Written for the old Python that runs it: standard library, no newer syntax.
    """
    found, seen = [], set()
    for name in CANDIDATES:
        resolved = shutil.which(name, path=os.environ.get("PATH") if path is None else path)
        if not resolved or os.path.realpath(resolved) in seen:
            continue
        seen.add(os.path.realpath(resolved))
        try:
            completed = run([resolved, "-I", "-c", _PROBE], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
            version = tuple(int(part) for part in completed.stdout.strip().split("."))
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        if completed.returncode == 0 and len(version) == 3 and version[:2] >= MINIMUM_PYTHON:
            found.append({"command": name, "path": resolved, "version": ".".join(map(str, version))})
    return found


def repair(found: list, found_candidates=None) -> str:
    if any(problem.startswith("Python") for problem in found):
        if found_candidates:
            first = found_candidates[0]
            return (f"Run every forge.py command with {first['command']} ({first['path']}, Python "
                    f"{first['version']}) instead of python3, starting again with environment. Forge does not "
                    "install or change Python.")
        return ("Forge needs Python 3.11 or newer, and none of " + ", ".join(CANDIDATES) + " on PATH starts as one. "
                "Install one yourself or put it on PATH; Forge does not install or change Python.")
    return "Reinstall or update the Harness Forge plugin; Forge downloads nothing itself."


def activate() -> None:
    """Put vendor/ first on the import path; refuse a bundled package already imported from elsewhere."""
    vendor = str(VENDOR)
    while vendor in sys.path:
        sys.path.remove(vendor)
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
