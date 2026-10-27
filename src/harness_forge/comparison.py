# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""What changed since an earlier confirmation the agent names, for the confirmation summary (#99).

The agent names the relay it wrote after the user's last confirmation of this decision record. Its run
directory holds the details file Forge wrote for that confirmation, with the bound content. Both are local files:
the comparison is a hint about what changed, never proof that the user approved anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness_forge import runfiles
from harness_forge.contracts import RELAY_KEYS
from harness_forge.documents import canonical_digest

_DETAILS_NAME = re.compile(r"(research-proposal|configuration-proposal|configuration-summary)-[0-9a-f]{16}\.md")
_PART_ID = re.compile(r"[A-Z][0-9]*")
MAX_CANDIDATES = 50
DECISION_LINE = "- Decision record: "
WEAKER = {"must": "should", "must-not": "should-not"}


class Unavailable(ValueError):
    """Why no comparison can be made with the named relay."""


def _relay(path: Path, root: Path) -> tuple[dict, Path]:
    found = runfiles.located(path)
    if found is None or len(found[1]) != 4 or found[1][1] != "runs":
        raise Unavailable("the relay is not in a run directory .harness-forge/runs/<run id>/")
    try:
        if not Path(found[0]).samefile(root):
            raise Unavailable("the relay belongs to another repository")
        relay = json.loads(runfiles.read_bytes(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"the relay cannot be read: {error}") from None
    if not isinstance(relay, dict) or set(relay) != RELAY_KEYS:
        raise Unavailable("the relay does not hold exactly a confirmation's fields")
    part_ids = relay["part_ids"]
    if relay["choice"] != "confirm":
        raise Unavailable("the relay does not confirm")
    if not isinstance(relay["displayed_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", relay["displayed_digest"]):
        raise Unavailable("the relay's digest is malformed")
    if relay["run_id"] != found[1][2]:
        raise Unavailable("the relay's run id is not its run directory's")
    if (not isinstance(part_ids, list) or len(set(map(str, part_ids))) != len(part_ids)
            or not all(isinstance(item, str) and _PART_ID.fullmatch(item) for item in part_ids)):
        raise Unavailable("the relay's part ids are malformed")
    return relay, Path(found[0]).joinpath(*found[1][:-1])


def _bound_content(details: str) -> dict | None:
    """The JSON under "## Bound content" in a details file render_details wrote, or None."""
    lines = details.split("\n")
    try:
        start = lines.index("## Bound content")
    except ValueError:
        return None
    for index in range(start + 1, len(lines)):
        fence = re.fullmatch(r"(`{3,})json", lines[index])
        if fence:
            try:
                end = lines.index(fence.group(1), index + 1)
                return json.loads("\n".join(lines[index + 1:end]))
            except ValueError:
                return None
        if lines[index].startswith("## "):
            return None
    return None


def load(relay_path: str | Path, root: Path, decision_path: str) -> dict:
    """The earlier confirmation the relay names: ``relay``, ``run_id``, ``digest``, ``part_ids`` and ``content``.

    ``decision_path`` is the current record's path relative to the repository. Raises Unavailable with the
    reason when the relay, its details file or their link to this record cannot be established.
    """
    from harness_forge.summary import code  # the details file names its record as summary.code renders it

    relay, directory = _relay(Path(relay_path), Path(root))
    try:
        candidates = [name for name in runfiles.names(directory) if _DETAILS_NAME.fullmatch(name)][:MAX_CANDIDATES]
    except (OSError, ValueError) as error:
        raise Unavailable(f"the run directory cannot be listed: {error}") from None
    reasons = set()
    for name in candidates:
        try:
            details = runfiles.read_bytes(directory / name).decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            reasons.add("a details file cannot be read")
            continue
        content = _bound_content(details)
        if content is None or canonical_digest(content) != relay["displayed_digest"]:
            continue
        if f"{DECISION_LINE}{code(decision_path)}" not in details.split("\n"):
            reasons.add("the details file does not name this decision record")
            continue
        known = {part["id"] for part in content.get("parts", []) if isinstance(part, dict)}
        if not set(relay["part_ids"]) <= known:
            reasons.add("the relay names parts the confirmed content does not hold")
            continue
        return {"relay": Path(relay_path).name, "run_id": relay["run_id"], "digest": relay["displayed_digest"],
                "part_ids": sorted(relay["part_ids"]), "content": content}
    raise Unavailable(sorted(reasons)[0] if reasons else "no details file in its run directory holds the "
                      "confirmed content")


def _requirements(part: dict) -> dict[str, dict]:
    return {entry["rule_id"]: entry for entry in part.get("requirements", [])}


def compare(earlier: dict, part_ids: list[str], current: dict) -> dict:
    """What changed between the confirmed content of ``part_ids`` and the current bound content.

    Requirements match by rule id. A kind change from must to should, or must-not to should-not, and a removed
    must or must-not are weakened; a statement edit is a wording change, judged neither weaker nor stronger.
    """
    before = {part["id"]: part for part in earlier.get("parts", []) if part["id"] in part_ids}
    now = {part["id"]: part for part in current["parts"]}
    parts = []
    for part_id in sorted(set(before) | set(now)):
        if part_id not in now:
            parts.append({"id": part_id, "status": "removed"})
            continue
        if part_id not in before:
            parts.append({"id": part_id, "status": "unconfirmed"})
            continue
        old, new = before[part_id], now[part_id]
        fields = [field for field in ("goal", "outcome", "mechanism") if old.get(field) != new.get(field)]
        old_rules, new_rules = _requirements(old), _requirements(new)
        weakened, changed, added, removed = [], [], [], []
        for rule_id in sorted(set(old_rules) | set(new_rules)):
            was, now_rule = old_rules.get(rule_id), new_rules.get(rule_id)
            if was is None:
                added.append(rule_id)
            elif now_rule is None:
                removed.append({"rule_id": rule_id, "kind": was["kind"], "statement": was["statement"]})
                if was["kind"] in WEAKER:
                    weakened.append(rule_id)
            elif (was["kind"], was["statement"]) != (now_rule["kind"], now_rule["statement"]):
                if WEAKER.get(was["kind"]) == now_rule["kind"]:
                    weakened.append(rule_id)
                else:
                    changed.append({"rule_id": rule_id, "kind": [was["kind"], now_rule["kind"]],
                                    "wording": was["statement"] != now_rule["statement"]})
        if fields or weakened or changed or added or removed:
            parts.append({"id": part_id, "status": "changed", "fields": fields, "weakened": weakened,
                          "changed": changed, "new": added, "removed": removed})
    overrides = ({entry["rule_id"] for entry in earlier.get("overrides", [])},
                 {entry["rule_id"] for entry in current["overrides"]})
    return {"parts": parts, "goals_changed": earlier.get("goals") != current["goals"],
            "overrides_added": sorted(overrides[1] - overrides[0]),
            "overrides_removed": sorted(overrides[0] - overrides[1])}


def unchanged(changes: dict) -> bool:
    return not (changes["parts"] or changes["goals_changed"] or changes["overrides_added"]
                or changes["overrides_removed"])
