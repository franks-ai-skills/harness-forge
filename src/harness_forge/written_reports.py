# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Which verification reports Forge itself wrote, kept outside the repository (#102).

Forge chooses the re-verification scope without asking, so no user choice vets the earlier report any more. A
Review keeps earlier results only from a report whose exact bytes Forge recorded when it wrote them, for this
repository and this report path. The record lives in Forge's own data directory: it shows that the repository's
report was not edited since, not that nothing else with the user's access changed both.

Everything fails closed: a missing, unreadable or mismatching record means every part is judged again.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path

from harness_forge.environment import forge_home

VERSION = 1


class NotRecorded(ValueError):
    """Why an earlier report's results cannot be kept."""


def _paths(environ, root: Path) -> tuple[Path, Path]:
    """The resolved repository root and its record file, which must lie outside the repository."""
    repository = Path(root).resolve()
    directory = (forge_home(environ) / "reports").resolve()
    if directory == repository or repository in directory.parents:
        raise NotRecorded("Forge's data directory lies inside the repository, so it cannot vouch for a report there")
    name = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:32]
    return repository, directory / f"{name}.json"


def _load(path: Path, repository: Path) -> dict:
    try:
        if path.is_symlink():
            raise NotRecorded("Forge's record of its reports is a symbolic link")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRecorded("Forge has no record of reports it wrote for this repository") from None
    except (OSError, ValueError) as error:
        raise NotRecorded(f"Forge's record of its reports cannot be read: {error}") from None
    if (not isinstance(data, dict) or data.get("version") != VERSION or data.get("repository") != str(repository)
            or not isinstance(data.get("reports"), dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in data["reports"].items())):
        raise NotRecorded("Forge's record of its reports is malformed or for another repository")
    return data


def check(environ, root: Path, report: Path) -> None:
    """Raise NotRecorded unless Forge recorded writing exactly this report at this path."""
    repository, path = _paths(environ, root)
    reports = _load(path, repository)["reports"]
    relative = Path(report).resolve().relative_to(repository).as_posix()
    if relative not in reports:
        raise NotRecorded(f"Forge has no record of writing {relative}")
    if hashlib.sha256(Path(report).read_bytes()).hexdigest() != reports[relative]:
        raise NotRecorded(f"{relative} changed since Forge wrote it")


def record(environ, root: Path, report: Path) -> None:
    """Record the report file as it is now; see record_bytes."""
    record_bytes(environ, root, report, Path(report).read_bytes())


def record_bytes(environ, root: Path, report: Path, content: bytes) -> None:
    """Record ``content`` as the report Forge writes at ``report``; keeps the entries of its other report paths.

    verify records before it writes (#137): raises NotRecorded when the record cannot be written, and the
    previous record stays, since the new one replaces it only in one atomic step.
    """
    repository, path = _paths(environ, root)
    try:
        data = _load(path, repository)
    except NotRecorded:
        data = {"version": VERSION, "repository": str(repository), "reports": {}}
    relative = Path(report).resolve().relative_to(repository).as_posix()
    data["reports"][relative] = hashlib.sha256(content).hexdigest()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise NotRecorded(f"Forge could not record the report: {error}") from None
