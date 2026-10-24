"""Tests for Harness Forge.

Run them with any Python 3.11 or newer (AGENTS.md). The package is
imported from ``src/`` without being installed, and importing it puts the
bundled dependencies in ``vendor/`` first on the import path before any
test module imports them.
"""

import sys
from pathlib import Path

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
if _SOURCE not in sys.path:
    sys.path.insert(0, _SOURCE)

import harness_forge  # noqa: E402,F401  activates vendor/
