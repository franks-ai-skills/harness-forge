"""Re-verification scope and which parts changed bound inputs affect."""

from __future__ import annotations

import json
import re

from harness_forge.documents import validate
from harness_forge.models import Decision, VerificationRun

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


def scope_options(decision: Decision, fixed: set[str], changed: set[str], judged_counts: dict[str, int]) -> list[dict]:
    """The two choices, each with its exact parts and judged-rule count."""
    options = []
    for label, parts in (("Review (recommended)", review_scope(decision, fixed, changed)),
                         ("Extensive review (rarely needed)", extensive_scope(decision))):
        options.append({"label": label, "parts": sorted(parts),
                        "judged_rules": sum(judged_counts.get(part_id, 0) for part_id in parts), "link_checks": True})
    return options


def affected_parts(run: VerificationRun, current: dict) -> set[str]:
    """Parts whose bound inputs differ between the frozen run and now."""
    frozen = run.frozen_inputs
    parts = set(frozen["parts"]) | set(current["parts"])
    if any(frozen[key] != current[key] for key in _GLOBAL_INPUTS):
        return parts
    affected = {part_id for part_id in parts if frozen["parts"].get(part_id) != current["parts"].get(part_id)}
    if frozen["revision"] != current["revision"]:
        affected |= set(run.requirement_parts)
    changed_paths = {path for path in set(frozen["files"]) | set(current["files"])
                     if frozen["files"].get(path) != current["files"].get(path)}
    changed_paths |= {path for path in set(frozen["symlinks"]) | set(current["symlinks"])
                      if frozen["symlinks"].get(path) != current["symlinks"].get(path)}
    for path in changed_paths:
        for part_id, owned in run.part_files.items():
            prefix = run.part_dirs.get(part_id)
            if path in owned or (prefix and path.startswith(prefix + "/")) or path in run.part_links.get(part_id, []):
                affected.add(part_id)
    return affected


def preserved_parts(previous: VerificationRun, current: dict, confirmation) -> set[str]:
    """Parts whose earlier results may be kept: inputs unchanged and the same contract confirmed now."""
    if confirmation is None or confirmation.contract_digest != previous.contract_digest:
        return set()
    unchanged = set(previous.frozen_inputs["parts"]) - affected_parts(previous, current)
    return unchanged & set(confirmation.part_ids)


def earlier_run(report_text: str) -> tuple[VerificationRun, dict[str, dict]]:
    """The run a report records, enough to compare its bound inputs, and each part's result.

    The report is a repository file like any other record: what it can do
    is let a Review keep the judged results of parts whose bound inputs and
    contract are unchanged; script and link checks always run again.
    """
    blocks = re.findall(r"^## Report data\n\n```json\n(.*?)\n```", report_text, re.S | re.M)
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
        goal = result.get("goal_verdict")
        if not complete or not (goal is None or (isinstance(goal, dict) and isinstance(goal.get("verdict"), str)
                                                 and isinstance(goal.get("reason"), str))):
            raise ValueError(f"the earlier report's results for part {result.get('part_id')} are incomplete")
    run = VerificationRun(run_id=data["run_id"], decision_digest=data["decision_digest"],
                          contract_digest=data["contract_digest"] or "", applicable_rules={},
                          frozen_inputs=data["bound_inputs"], confirmed_parts=list(data["confirmed_parts"]),
                          part_files=part_map["files"], part_dirs=part_map["dirs"], part_links=part_map["links"],
                          requirement_parts=part_map["requirement_parts"])
    return run, {result["part_id"]: result for result in data["parts"]}
