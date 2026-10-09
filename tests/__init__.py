"""Tests for Harness Forge.

Run them with the locked environment (AGENTS.md). The package is imported
from ``src/`` without being installed.
"""

import sys
from pathlib import Path

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
if _SOURCE not in sys.path:
    sys.path.insert(0, _SOURCE)
