"""Repository rule overrides.

Until the adoption flow exists (plan task 12), a nonempty override list
blocks the affected run instead of being applied or silently ignored.
"""

from __future__ import annotations

from harness_forge.models import Confirmation, Rule


class OverridesPending(RuntimeError):
    """Overrides exist but their validated adoption flow is not available."""


def effective_rules(pinned: list[Rule], overrides: list[dict], confirmation: Confirmation | None) -> list[Rule]:
    """The rules that apply: the pinned rules when there are no overrides."""
    if overrides:
        ids = ", ".join(sorted(override.get("rule_id", "?") for override in overrides))
        raise OverridesPending(f"rule overrides ({ids}) block this run until their adoption flow is implemented")
    return list(pinned)
