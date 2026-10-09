"""Outside-control hand-off checker. It never inspects platform settings."""

from __future__ import annotations

import re

from harness_forge.checks.common import text


def _section(markdown: str, heading: str) -> str | None:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.M)
    if not match:
        return None
    following = re.search(r"^##\s", markdown[match.end():], re.M)
    return markdown[match.end(): match.end() + following.start()] if following else markdown[match.end():]


def check_readme_coverage(files, facts, params):
    controls = facts.get("outside_controls")
    if controls is None:
        return "fail", "the decision record's outside controls were not supplied"
    readme_path = facts.get("readme_path", "README.md")
    if readme_path not in files:
        return ("pass", "no outside controls") if not controls else ("fail", f"{readme_path} is missing")
    section = _section(text(files[readme_path]), params["heading"])
    if section is None:
        return ("pass", "no outside controls") if not controls else ("fail", f"no '## {params['heading']}' section")
    lines = section.splitlines()
    missing = []
    for control in controls:
        part = rf"\bpart {re.escape(control['part_id'])}\b"
        if not any(control["control"] in line and re.search(part, line) and params["status"] in line for line in lines):
            missing.append(f"{control['control']} (part {control['part_id']})")
    return ("fail", "not listed: " + ", ".join(missing)) if missing else ("pass", f"{len(controls)} control(s) listed")


REGISTRY = {"outside.readme-coverage": ("1.0.0", check_readme_coverage)}
