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
    command = [str(python), "-m", "pip", "install", "--require-hashes", "--only-binary", ":all:", "--no-deps"]
    if wheelhouse is not None:
        command += ["--no-index", "--find-links", str(wheelhouse)]
    return command + ["-r", str(lock)]


def _create(path: Path) -> None:
    venv.EnvBuilder(with_pip=True, clear=True).create(path)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=INSTALL_TIMEOUT, **kwargs)


def setup(root: Path, environ, *, run=_run, create=_create, wheelhouse: Path | None = None) -> dict:
    """Create the environment unless a finished one exists; a failed attempt leaves nothing behind."""
    target = environment.environment_dir(root, environ)
    if environment.complete(root, target):
        return {"ok": True, "existing": True, "environment": str(target)}
    if sys.version_info < MINIMUM_PYTHON:
        return {"ok": False, "existing": False, "environment": str(target),
                "error": f"Python {'.'.join(map(str, MINIMUM_PYTHON))} or newer is needed"}
    staging = target.with_name(target.name + ".partial")
    for path in (staging, target):
        if path.exists():
            shutil.rmtree(path)
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        create(staging)
        completed = run(install_command(environment.python_of(staging), root / "requirements.lock",
                                        wheelhouse=wheelhouse))
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-5:]
            return {"ok": False, "existing": False, "environment": str(target),
                    "error": "pip could not install the locked packages", "detail": tail}
        (staging / environment.MARKER).write_text(environment.lock_digest(root), encoding="utf-8")
        staging.rename(target)
        return {"ok": True, "existing": False, "environment": str(target)}
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "existing": False, "environment": str(target), "error": str(error)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wheelhouse", type=Path, help="install offline from wheels downloaded with the same lock")
    arguments = parser.parse_args(argv)
    result = setup(ROOT, os.environ, wheelhouse=arguments.wheelhouse)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
