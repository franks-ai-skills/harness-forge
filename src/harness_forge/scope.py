# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Re-verification scope and which parts changed bound inputs affect."""

from __future__ import annotations

import json
import re

from harness_forge.documents import markdown_headings, markdown_section, validate
from harness_forge.models import Decision, RuntimeResult, VerificationRun

_GLOBAL_INPUTS = ("idea", "kb_version", "rules", "overrides", "checkers", "worker")


def _neighbours(decision: Decision) -> dict[str, set[str]]:
    graph = {part["id"]: set() for part in decision.record["parts"]}
    for part in decision.record["parts"]:
        for link in part["connects_to"]:
            if link["to"] in graph:
                graph[part["id"]].add(link["to"])
                graph[link["to"]].add(part["id"])
    return graph


def review_scope(decision: Decision, fixed: set[str], changed: set[str]) -> set[str]:
    """Review: the fixed and changed parts plus every part linked to them, transitively."""
    graph = _neighbours(decision)
    unknown = (fixed | changed) - set(graph)
    if unknown:
        raise ValueError(f"unknown parts: {', '.join(sorted(unknown))}")
    scope, queue = set(), list(fixed | changed)
    while queue:
        part_id = queue.pop()
        if part_id not in scope:
            scope.add(part_id)
            queue.extend(graph[part_id] - scope)
    return scope


def extensive_scope(decision: Decision) -> set[str]:
    """Extensive review: every part."""
    return {part["id"] for part in decision.record["parts"]}


def affected_parts(run: VerificationRun, current: dict, owners: VerificationRun | None = None) -> set[str]:
    """Parts whose bound inputs differ between the frozen run and now.

    ``owners`` is a run prepared from the current decision: its file
    ownership maps changed paths too, so an earlier report's edited
    ownership cannot hide a changed file.
    """
    frozen = run.frozen_inputs
    parts = set(frozen["parts"]) | set(current["parts"])
    if any(frozen[key] != current[key] for key in _GLOBAL_INPUTS):
        return parts
    affected = {part_id for part_id in parts if frozen["parts"].get(part_id) != current["parts"].get(part_id)}
    # A part's runtime cases and fixtures; history without them counts as changed.
    affected |= {part_id for part_id in parts
                 if (frozen.get("cases") or {}).get(part_id) != (current.get("cases") or {}).get(part_id)}
    maps = [run] + ([owners] if owners is not None else [])
    if frozen["revision"] != current["revision"]:
        affected |= {part_id for owner in maps for part_id in owner.requirement_parts}
    changed_paths = {path for path in set(frozen["files"]) | set(current["files"])
                     if frozen["files"].get(path) != current["files"].get(path)}
    changed_paths |= {path for path in set(frozen["symlinks"]) | set(current["symlinks"])
                      if frozen["symlinks"].get(path) != current["symlinks"].get(path)}
    for path in changed_paths:
        for owner in maps:
            for part_id, owned in owner.part_files.items():
                prefix = owner.part_dirs.get(part_id)
                roots = owner.part_dependency_roots.get(part_id, [])
                if (path in owned or (prefix and path.startswith(prefix + "/"))
                        or any(path == root or path.startswith(root + "/") for root in roots)
                        or path in owner.part_links.get(part_id, [])):
                    affected.add(part_id)
    return affected


def preserved_parts(previous: VerificationRun, current: dict, confirmation,
                    owners: VerificationRun | None = None) -> set[str]:
    """Parts whose earlier results may be kept: inputs unchanged and the same contract confirmed now."""
    if confirmation is None or confirmation.contract_digest != previous.contract_digest:
        return set()
    unchanged = set(previous.frozen_inputs["parts"]) - affected_parts(previous, current, owners)
    return unchanged & set(confirmation.part_ids)


_UNSETTLED = ("unresolved", "missing", "error")


def settled(result: dict) -> bool:
    """An earlier result a Review may keep: nothing left open, and a build that was verified."""
    return (result["harness_result"] != "not verified"
            and not any(item["result"] in _UNSETTLED for item in result["rule_results"]))


def earlier_run(report_text: str) -> tuple[VerificationRun, dict[str, dict]]:
    """The run a report records, enough to compare its bound inputs, and each part's result.

    The report is a repository file like any other record: what it can do
    is let a Review keep the judged results of parts whose bound inputs and
    contract are unchanged; script and link checks always run again.
    """
    # Only a real heading counts: one inside a code block, or a second one, is not the data.
    headings = [offset for offset, level, title in markdown_headings(report_text) if level == 2 and title == "Report data"]
    start, end = markdown_section(report_text, "Report data") if len(headings) == 1 else (0, 0)
    blocks = re.findall(r"\A## Report data\r?\n\r?\n```json\r?\n(.*?)\r?\n```[ \t]*\s*\Z", report_text[start:end], re.S)
    if len(blocks) != 1:
        raise ValueError("the earlier report has no single report data block")
    try:
        data = json.loads(blocks[0])
    except ValueError as error:
        raise ValueError(f"the earlier report's data is not JSON: {error}") from error
    validate(data, "report")
    if "bound_inputs" not in data or "part_map" not in data:
        raise ValueError("the earlier report predates re-verification; run an Extensive review")
    part_map = data["part_map"]
    parts = set(data["bound_inputs"]["parts"])
    named = set(part_map["files"]) | set(part_map["dirs"]) | set(part_map["links"]) | set(part_map["requirement_parts"])
    named |= {result["part_id"] for result in data["parts"]} | set(data["answers"])
    if not named <= parts:
        raise ValueError(f"the earlier report names parts its bound inputs lack: {', '.join(sorted(named - parts))}")
    for result in data["parts"]:
        complete = all(isinstance(item, dict) and all(isinstance(item.get(key), str)
                                                      for key in ("rule_id", "check", "result", "reason"))
                       for item in result.get("rule_results", []))
        rule_ids = [item.get("rule_id") for item in result.get("rule_results", []) if isinstance(item, dict)]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError(f"the earlier report judges a rule twice for part {result.get('part_id')}")
        goal = result.get("goal_verdict")
        if not complete or not (goal is None or (isinstance(goal, dict) and isinstance(goal.get("verdict"), str)
                                                 and isinstance(goal.get("reason"), str))):
            raise ValueError(f"the earlier report's results for part {result.get('part_id')} are incomplete")
    run = VerificationRun(run_id=data["run_id"], decision_digest=data["decision_digest"],
                          contract_digest=data["contract_digest"] or "", applicable_rules={},
                          frozen_inputs=data["bound_inputs"], confirmed_parts=list(data["confirmed_parts"]),
                          part_files=part_map["files"], part_dirs=part_map["dirs"], part_links=part_map["links"],
                          part_dependencies=part_map.get("dependencies", {}),
                          part_dependency_roots=part_map.get("dependency_roots", {}),
                          requirement_parts=part_map["requirement_parts"], attempt_id=data.get("attempt_id"))
    # Its runtime evidence, inventory included, stays with any result a Review keeps.
    run.runtime_results = [RuntimeResult(**item) for item in data["runtime_results"]]
    return run, {result["part_id"]: result for result in data["parts"]}
