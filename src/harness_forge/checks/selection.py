"""selection.match: a selection rule's predicate over confirmed answers."""

from __future__ import annotations

from harness_forge.predicates import evaluate


def check_match(files, facts, params):
    matched = evaluate(params["when"], facts.get("answers", {}))
    return ("pass", f"implies {params['implies']}") if matched else ("fail", "predicate does not match")


REGISTRY = {"selection.match": ("1.0.0", check_match)}
