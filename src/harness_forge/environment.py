"""Where Forge's Python environment lives and when forge.py switches to it.

Standard library only: forge.py consults this before it imports the
locked packages. The environment is a virtual environment that
scripts/setup.py creates once, with the user's consent, from the hashed
requirements.lock. It lives in a Forge-owned data directory, keyed by
the lock's digest, so a plugin update with a new lock gets a new
environment and the plugin's own (replaceable) install directory stays
unchanged. Nothing here changes user configuration.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Mapping

HOME_VARIABLE = "HARNESS_FORGE_HOME"
# Set when forge.py switches to the environment, so the switched process never switches again.
ACTIVE_VARIABLE = "HARNESS_FORGE_ENVIRONMENT"
MARKER = "forge-environment-complete"


def lock_digest(root: Path) -> str:
    return hashlib.sha256((root / "requirements.lock").read_bytes()).hexdigest()[:16]


def forge_home(environ: Mapping[str, str], *, platform: str = sys.platform) -> Path:
    """HARNESS_FORGE_HOME, else the platform's per-user data directory."""
    if environ.get(HOME_VARIABLE):
        return Path(environ[HOME_VARIABLE])
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or Path(environ.get("USERPROFILE", "~")) / "AppData" / "Local",
                    "harness-forge")
    data = environ.get("XDG_DATA_HOME") or str(Path(environ.get("HOME") or Path.home()) / ".local" / "share")
    return Path(data) / "harness-forge"


def environment_dir(root: Path, environ: Mapping[str, str]) -> Path:
    return forge_home(environ) / f"env-{lock_digest(root)}"


def python_of(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def complete(root: Path, directory: Path) -> bool:
    try:
        return (directory / MARKER).read_text(encoding="utf-8") == lock_digest(root) and \
            python_of(directory).exists()
    except OSError:
        return False


def setup_command(root: Path) -> str:
    return f"python3 {root / 'scripts' / 'setup.py'}"


def decide(root: Path, prefix: str, environ: Mapping[str, str]) -> tuple[str, str | None]:
    """("run", None) inside Forge's environment or the checkout's development .venv; ("exec", python) when the
    environment exists; otherwise ("missing", setup command)."""
    directory = environment_dir(root, environ)
    current = Path(prefix).resolve()
    switched = environ.get(ACTIVE_VARIABLE) == str(directory)
    if switched or current in (directory.resolve(), (root / ".venv").resolve()):
        return "run", None
    if complete(root, directory):
        return "exec", str(python_of(directory))
    return "missing", setup_command(root)


def ensure(root: Path, script: str, argv: list[str]) -> None:
    """Continue in this interpreter, switch to the Forge environment, or stop with exit 2 and the setup command."""
    decision, detail = decide(root, sys.prefix, os.environ)
    if decision == "run":
        return
    if decision == "exec":
        os.environ[ACTIVE_VARIABLE] = str(environment_dir(root, os.environ))
        os.execv(detail, [detail, script, *argv])
    print(f"forge.py: the Forge environment is not set up. It installs Forge's pinned Python packages from PyPI "
          f"once, into {environment_dir(root, os.environ)}. Ask the user before running: {detail}",
          file=sys.stderr)
    raise SystemExit(2)
