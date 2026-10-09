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
