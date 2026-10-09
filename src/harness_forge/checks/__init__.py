"""Pinned deterministic checkers, by catalog id.

Each checker is ``fn(files, facts, params) -> (status, reason)`` with
status ``pass``, ``fail`` or ``not-applicable``. Checkers read the bytes
they are given; they never execute or import checked files. The runner
calls them in a separate process (``runner.run_check``).
"""

from harness_forge.checks import links, outside, selection, skills

REGISTRY = {**skills.REGISTRY, **links.REGISTRY, **outside.REGISTRY, **selection.REGISTRY}
