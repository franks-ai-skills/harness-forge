"""Re-verification scope and which parts changed bound inputs affect."""

from __future__ import annotations

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
