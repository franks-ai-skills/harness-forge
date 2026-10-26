# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""A run's own files in <repository>/.harness-forge/runs/<run id>/, kept out of git by its own .gitignore.

Below the repository root, every directory is opened without following a symbolic link, one name at a time from
its parent's descriptor, and a file is checked on its opened descriptor: nothing swapped in during the walk is
read, written or created through.
"""

from __future__ import annotations

import datetime
import errno
import os
import secrets
import stat
import subprocess
from pathlib import Path

DIRECTORY = ".harness-forge"
GITIGNORE = "*\n"
MAX_INPUT_BYTES = 1024 * 1024
UNTRACKED_NOTE = ("git tracks files under .harness-forge/; its .gitignore does not untrack them "
                  "(git rm --cached removes them from the index)")
# Replaced by tests.
GIT = "git"


class RunFilesError(ValueError):
    pass


def new_run_id() -> str:
    """The UTC time to the second and eight random hex digits: sorts by time and does not collide by chance."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)


# Replaced by tests.
RUN_ID = new_run_id


def _why(directory: int, name: str, error: OSError) -> str:
    try:
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
    except OSError:
        return error.strerror or str(error)
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if error.errno == errno.ENOTDIR or not stat.S_ISDIR(mode):
        return "not a directory"
    return error.strerror or str(error)


def _descend(directory: int, names: list[str], shown: Path, create: bool = False) -> int:
    """Open each name below directory as a real directory, creating missing ones when asked; closes directory."""
    for name in names:
        shown = shown / name
        try:
            if create:
                try:
                    os.mkdir(name, 0o755, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        except OSError as error:
            reason = _why(directory, name, error)
            os.close(directory)
            raise RunFilesError(f"{shown}: {reason}; only a real directory is used") from error
        os.close(directory)
        directory = child
    return directory


def _open_root(root: Path) -> int:
    try:
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise RunFilesError(f"{root}: {error.strerror or error}") from error


def located(path: str | os.PathLike) -> tuple[Path, list[str]] | None:
    """The repository root and the names below it, for a path inside a .harness-forge directory; otherwise None."""
    parts = Path(os.path.abspath(path)).parts
    if DIRECTORY not in parts[:-1]:
        return None
    index = parts.index(DIRECTORY)
    return Path(*parts[:index]), list(parts[index:])


def _open_file(path: str | os.PathLike, flags: int) -> tuple[int, int, str] | None:
    """(file, its directory, its name) for a path below .harness-forge, opened with flags; None elsewhere."""
    found = located(path)
    if found is None:
        return None
    root, names = found
    directory = _descend(_open_root(root), names[:-1], root)
    try:
        descriptor = os.open(names[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=directory)
    except OSError as error:
        if error.errno == errno.EEXIST:
            reason = "already exists; a run file is never overwritten, use a new name"
        else:
            reason = "a symbolic link" if error.errno == errno.ELOOP else error.strerror or str(error)
        os.close(directory)
        raise RunFilesError(f"{path}: {reason}") from error
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError as error:
        os.close(descriptor)
        os.close(directory)
        raise RunFilesError(f"{path}: {error.strerror or error}") from error
    if not regular:
        os.close(descriptor)
        os.close(directory)
        raise RunFilesError(f"{path}: not a regular file; only a regular file is used")
    return descriptor, directory, names[-1]


def read_bytes(path: str | os.PathLike, limit: int = MAX_INPUT_BYTES) -> bytes | None:
    """A file below .harness-forge, bounded while it is read; None for a path elsewhere."""
    opened = _open_file(path, os.O_RDONLY)
    if opened is None:
        return None
    descriptor, directory, _ = opened
    try:
        chunks, size = [], 0
        while chunk := os.read(descriptor, 65536):
            size += len(chunk)
            if size > limit:
                raise RunFilesError(f"{path}: larger than {limit // (1024 * 1024)} MiB")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)
        os.close(directory)


def create(path: str | os.PathLike, text: str) -> bool:
    """Write a new file below .harness-forge, never replacing one; False for a path elsewhere."""
    opened = _open_file(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    if opened is None:
        return False
    descriptor, directory, name = opened
    try:
        data = memoryview(text.encode("utf-8"))
        while data:
            data = data[os.write(descriptor, data):]
    except OSError as error:
        os.close(descriptor)
        os.unlink(name, dir_fd=directory)  # created above, so nothing else is removed
        os.close(directory)
        raise RunFilesError(f"{path}: {error.strerror or error}") from error
    os.close(descriptor)
    os.close(directory)
    return True


def _gitignore(directory: int, shown: Path) -> str:
    """Create .gitignore with exactly GITIGNORE, or keep a regular one unchanged."""
    try:
        descriptor = os.open(".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                             dir_fd=directory)
    except FileExistsError:
        mode = os.stat(".gitignore", dir_fd=directory, follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode):
            raise RunFilesError(f"{shown}: a symbolic link; only a regular file is used") from None
        if not stat.S_ISREG(mode):
            raise RunFilesError(f"{shown}: not a regular file; only a regular file is used") from None
        return "kept"
    try:
        os.write(descriptor, GITIGNORE.encode("ascii"))
    finally:
        os.close(descriptor)
    return "created"


def tracked_files(root: Path) -> int | None:
    """How many files under .harness-forge git tracks; None without git or outside a work tree."""
    try:
        listed = subprocess.run([GIT, "-C", str(root), "ls-files", "-z", "--", DIRECTORY], capture_output=True,
                                timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    return len([name for name in listed.stdout.split(b"\0") if name])


def allocate(root: str | os.PathLike) -> dict:
    """Create this run's directory, refusing one that exists, and .harness-forge/.gitignore when it is missing."""
    root = Path(os.path.abspath(root))
    forge = _descend(_open_root(root), [DIRECTORY], root, create=True)
    try:
        gitignore = _gitignore(forge, root / DIRECTORY / ".gitignore")
    except BaseException:
        os.close(forge)
        raise
    runs = _descend(forge, ["runs"], root / DIRECTORY, create=True)
    run_id = RUN_ID()
    try:
        os.mkdir(run_id, 0o755, dir_fd=runs)
    except FileExistsError:
        raise RunFilesError(f"{root / DIRECTORY / 'runs' / run_id}: already exists; a run directory is "
                            "allocated once") from None
    finally:
        os.close(runs)
    tracked = tracked_files(root)
    return {"run_id": run_id, "path": str(root / DIRECTORY / "runs" / run_id), "gitignore": gitignore,
            "tracked_files": tracked, "note": UNTRACKED_NOTE if tracked else None}
