# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Harness Forge's deterministic core; stages are added after feasibility.

Importing the package puts the bundled dependencies in vendor/ first on
the import path (harness_forge.bundle), so every module uses them.
"""

from harness_forge import bundle as _bundle

_bundle.activate()
