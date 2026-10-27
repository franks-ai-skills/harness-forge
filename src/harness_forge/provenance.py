# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Which Harness Forge copy is running: its declared version and, for a Git checkout, its source.

Recorded for the build of a part and for a verification run (#52, #55).
Every value is "unknown" on its own when it cannot be established: a copy
without Git metadata is not thereby an installed plugin, and a failed
status check does not make a checkout clean. Git runs only in Forge's own
directory, never in the target repository.
"""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
UNKNOWN = "unknown"
KEYS = ("version", "source", "commit", "tree")
# The report's tool_versions keys for the verifying copy; runtime results' harness_version is the agent's.
VERIFIER_PREFIX = "harness-forge-verifier-"
_GIT_TIMEOUT = 10


def git_bytes(root: Path, *arguments: str) -> bytes | None:
    """Git's output in ``root`` as bytes, or None when it is missing, fails or times out."""
    # No repository configuration may run a program here, and the caller's GIT_* variables
    # cannot point Git at another repository.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        completed = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root),
                                    *arguments], capture_output=True, timeout=_GIT_TIMEOUT,
                                   env=environment, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _git(root: Path, *arguments: str) -> str | None:
    """Git's output in ``root`` as text, or None when it is missing, fails or times out."""
    output = git_bytes(root, *arguments)
    return None if output is None else output.decode("utf-8", "replace")


def _version(root: Path) -> str:
    try:
        version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return UNKNOWN
    # The decision and report schemas hold at most 200 characters on one line.
    valid = isinstance(version, str) and version.strip() and len(version) <= 200 and version.isprintable()
    return version if valid else UNKNOWN


def collect(root: Path, git: Callable[..., str | None] = _git) -> dict[str, str]:
    """The provenance of the Forge copy at ``root``: version, source, commit and tree."""
    result = {"version": _version(root), "source": UNKNOWN, "commit": UNKNOWN, "tree": UNKNOWN}
    top = git(root, "rev-parse", "--show-toplevel")
    # Only a repository whose top is Forge's own directory is its checkout; a copy inside another
    # repository, such as a plugin cache under a versioned home directory, is not.
    try:
        own = top is not None and Path(top.strip()).resolve() == root.resolve()
    except OSError:
        own = False
    if not own:
        return result
    result["source"] = "checkout"
    commit = git(root, "rev-parse", "HEAD")
    if commit and len(commit.strip()) in (40, 64):
        result["commit"] = commit.strip()
    status = git(root, "status", "--porcelain", "--untracked-files=normal")
    if status is not None:
        result["tree"] = "uncommitted changes" if status.strip() else "clean"
    return result


@lru_cache(maxsize=1)
def forge_provenance() -> dict[str, str]:
    """The running copy's provenance, collected once per process."""
    return collect(ROOT)


def verifier(tool_versions: dict) -> dict[str, str] | None:
    """The verifying copy's provenance from a run's tool_versions; None for a run that recorded none."""
    found = {key: tool_versions[VERIFIER_PREFIX + key] for key in KEYS if VERIFIER_PREFIX + key in tool_versions}
    return found or None


def describe(provenance: dict | None) -> str:
    """One line for a report: "0.2.0 (checkout 4056c94, uncommitted changes)"."""
    if not provenance:
        return UNKNOWN
    value = {key: provenance.get(key) or UNKNOWN for key in KEYS}
    if value["source"] != "checkout":
        return f"{value['version']} (source unknown)"
    commit = value["commit"][:7] if value["commit"] != UNKNOWN else "commit unknown"
    tree = value["tree"] if value["tree"] != UNKNOWN else "changes unknown"
    return f"{value['version']} (checkout {commit}, {tree})"
