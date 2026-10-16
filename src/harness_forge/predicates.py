# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Evaluate the closed all/any/not/eq/in predicate grammar over facts."""

from __future__ import annotations


class UnknownFact(KeyError):
    """A predicate needs a fact that has not been confirmed."""


def evaluate(predicate: dict, facts: dict) -> bool:
    (operator, operand), = predicate.items()
    if operator == "all":
        return all(evaluate(item, facts) for item in operand)
    if operator == "any":
        return any(evaluate(item, facts) for item in operand)
    if operator == "not":
        return not evaluate(operand, facts)
    fact, value = operand
    if fact not in facts:
        raise UnknownFact(fact)
    if operator == "eq":
        return facts[fact] == value
    if operator == "in":
        return facts[fact] in value
    raise ValueError(f"unknown predicate operator {operator!r}")


def evaluate_partial(predicate: dict, facts: dict) -> bool | None:
    """Three-valued evaluation: None when unconfirmed facts decide the result."""
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        results = [evaluate_partial(item, facts) for item in operand]
        decisive = operator == "any"
        if decisive in results:
            return decisive
        return None if None in results else not decisive
    if operator == "not":
        result = evaluate_partial(operand, facts)
        return None if result is None else not result
    fact, value = operand
    if fact not in facts:
        return None
    return facts[fact] == value if operator == "eq" else facts[fact] in value
