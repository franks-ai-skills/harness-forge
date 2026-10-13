"""Create Forge's Python environment once: a virtual environment with exactly the locked wheels.

Usage: python3 scripts/setup.py [--wheelhouse DIR]

Downloads the pinned packages in requirements.lock from PyPI (or takes
them from a prepared wheelhouse) with --require-hashes and
--only-binary :all:, so pip accepts no other file and runs no build
code. The environment goes to Forge's own data directory
(HARNESS_FORGE_HOME, else the user data directory), keyed by the lock's
digest; nothing else on the system changes. Prints one JSON object.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harness_forge import environment  # noqa: E402

MINIMUM_PYTHON = (3, 11)
INSTALL_TIMEOUT = 600


def install_command(python: Path, lock: Path, *, wheelhouse: Path | None = None) -> list[str]:
    """Python and pip isolated: no module from the working directory, no PIP_* variable or user configuration."""
    command = [str(python), "-I", "-m", "pip", "--isolated", "--no-input", "install", "--require-hashes", "--only-binary",
               ":all:", "--no-deps"]
    if wheelhouse is not None:
        command += ["--no-index", "--find-links", str(wheelhouse)]
    return command + ["-r", str(lock)]


IMPORT_CHECK = "import jsonschema, referencing, yaml"


def lock_path(target: Path) -> Path:
    return target.with_name(target.name + ".lock")


def _create(path: Path) -> None:
    venv.EnvBuilder(with_pip=True).create(path)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT, **kwargs)


def _clean_environment(environ) -> dict[str, str]:
    """Without PIP_* and PYTHON* variables; PIP_CONFIG_FILE=os.devnull makes pip skip every configuration file,
    including global and site ones that --isolated still reads."""
    clean = {name: value for name, value in environ.items() if not name.startswith(("PIP_", "PYTHON"))}
    return {**clean, "PIP_CONFIG_FILE": os.devnull}


@contextlib.contextmanager
def _working_directory(path: Path):
    """venv runs `python -m ensurepip`, which would import an ensurepip.py from the working directory."""
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _failure(target: Path, error: str, detail: list[str] | None = None) -> dict:
    result = {"ok": False, "existing": False, "environment": str(target), "error": error}
    if detail:
        result["detail"] = detail
    return result


def setup(root: Path, environ, *, run=_run, create=_create, wheelhouse: Path | None = None) -> dict:
    """Create the environment at its final path under a lock, unless a finished one exists.

    A virtual environment cannot be moved, so it is built in place and counts only once the marker is written;
    a failed attempt removes what it built. A second setup while one runs stops instead of waiting.
    """
    target = environment.environment_dir(root, environ)
    if environment.complete(root, target):
        return {"ok": True, "existing": True, "environment": str(target)}
    if sys.version_info < MINIMUM_PYTHON:
        return _failure(target, f"Python {'.'.join(map(str, MINIMUM_PYTHON))} or newer is needed")
    lock = lock_path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        lock.mkdir()
    except FileExistsError:
        return _failure(target, f"another setup is running; if none is, remove {lock} and try again")
    except OSError as error:
        return _failure(target, str(error))
    built = False
    try:
        if environment.complete(root, target):
            return {"ok": True, "existing": True, "environment": str(target)}
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            return _failure(target, f"{target} exists and is not a Forge environment; remove it and try again")
        if target.exists():
            shutil.rmtree(target)
        built = True
        with _working_directory(target.parent):
            create(target)
        python = environment.python_of(target)
        clean = _clean_environment(environ)
        completed = run(install_command(python, root / "requirements.lock", wheelhouse=wheelhouse), env=clean,
                        cwd=str(target.parent))
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-5:]
            return _failure(target, "pip could not install the locked packages", tail)
        imported = run([str(python), "-I", "-c", IMPORT_CHECK], env=clean, cwd=str(target.parent))
        if imported.returncode != 0:
            tail = (imported.stderr or imported.stdout or "").strip().splitlines()[-5:]
            return _failure(target, "the installed packages cannot be imported", tail)
        (target / environment.MARKER).write_text(environment.lock_digest(root), encoding="utf-8")
        built = False
        return {"ok": True, "existing": False, "environment": str(target)}
    except (OSError, subprocess.SubprocessError) as error:
        return _failure(target, str(error))
    finally:
        if built:
            shutil.rmtree(target, ignore_errors=True)
        lock.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wheelhouse", type=Path, help="install offline from wheels downloaded with the same lock")
    arguments = parser.parse_args(argv)
    result = setup(ROOT, os.environ, wheelhouse=arguments.wheelhouse)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
