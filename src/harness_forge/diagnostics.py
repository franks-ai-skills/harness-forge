# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Each runtime launch's raw output, kept outside the repository so a result can be diagnosed later.

Keeping them is best effort: a launch's result never depends on whether its
diagnostics could be written; what could not be written is listed instead.
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
from pathlib import Path

from harness_forge.adapters.base import Completed


class Diagnostics:
    """One verification run's directory: ``cases/<case id>/`` and ``baselines/<launch>/``.

    ``home`` is where runs are kept, such as Forge's per-user data directory;
    ``repository`` is the checkout being verified, which must not hold them.
    Nothing is kept until ``open`` names the run.
    """

    def __init__(self, home: Path | None, repository: Path | None = None):
        self.home, self.repository = home, repository
        self.directory: Path | None = None
        self.problems: list[str] = []

    def open(self, run_id: str) -> None:
        if self.home is None or self.directory is not None:
            return
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]', '-', run_id)[:64]}-{secrets.token_hex(4)}"
        directory = self.home / name
        try:
            resolved = directory.resolve()
            if self.repository is not None and resolved.is_relative_to(self.repository.resolve()):
                self.problems.append(f"diagnostics are not kept inside the repository ({resolved})")
                return
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.mkdir(mode=0o700)
        except OSError as error:
            self.problems.append(f"the diagnostics directory could not be created: {error}")
            return
        self.directory = directory

    def keep(self, kind: str, name: str, launch: dict, completed: Completed | None = None) -> None:
        """Write one launch: ``launch.json``, and its stdout and stderr when it ran."""
        if self.directory is None:
            return
        target = self.directory / kind / name
        try:
            target.mkdir(parents=True, mode=0o700)
            if completed is not None:
                (target / "stdout.txt").write_text(completed.stdout, encoding="utf-8", errors="replace")
                (target / "stderr.txt").write_text(completed.stderr, encoding="utf-8", errors="replace")
                launch = {**launch, "returncode": completed.returncode, "timed_out": completed.timed_out}
            (target / "launch.json").write_text(json.dumps(launch, indent=2, ensure_ascii=False, default=str),
                                                encoding="utf-8")
        except OSError as error:
            self.problems.append(f"{kind}/{name}: {error}")
