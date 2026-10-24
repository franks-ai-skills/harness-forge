# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Forge's per-user data directory, where diagnostics are kept.

Forge installs nothing there: its dependencies are bundled in vendor/
(harness_forge.bundle). Nothing here changes user configuration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

HOME_VARIABLE = "HARNESS_FORGE_HOME"


def forge_home(environ: Mapping[str, str], *, platform: str = sys.platform) -> Path:
    """HARNESS_FORGE_HOME, else the platform's per-user data directory."""
    if environ.get(HOME_VARIABLE):
        return Path(environ[HOME_VARIABLE])
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or Path(environ.get("USERPROFILE", "~")) / "AppData" / "Local",
                    "harness-forge")
    data = environ.get("XDG_DATA_HOME") or str(Path(environ.get("HOME") or Path.home()) / ".local" / "share")
    return Path(data) / "harness-forge"
