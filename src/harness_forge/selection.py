"""Compute a part's recommendation from confirmed, normalized answers.

Selection rules (``selection.match``) come from the pinned KB. Within
each kind the data decides priority: the first matching outcome rule and
the first matching concept rule win; every matching control applies.
When an unanswered fact could still change the result, the result is a
question, never a default.
"""

from __future__ import annotations

import itertools
from typing import Iterable

from harness_forge.models import Recommendation, Rule
from harness_forge.predicates import evaluate_partial

_OTHER = object()  # stands for any value no rule tests
_BUILT = ("harness-only", "outside-alongside")
_MAX_ASSIGNMENTS = 200_000


def normalize(values: dict) -> dict:
    """Validate scalar answers; strip strings. Raise ValueError otherwise."""
    normalized = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise ValueError(f"answer key {key!r} is not a string")
        if isinstance(value, str):
            value = value.strip()
        elif not isinstance(value, (bool, int)):
            raise ValueError(f"answer {key!r} must be a string, boolean or integer, not {type(value).__name__}")
        normalized[key] = value
    return normalized


def qualifying_candidates(candidates: Iterable[dict]) -> list[dict]:
    """Reuse candidates with confirmed fit, a known license and maintenance."""
    return [
        candidate for candidate in candidates
        if candidate.get("fit") == "confirmed"
        and str(candidate.get("license", "")).strip() not in ("", "unknown")
        and candidate.get("maintenance") == "maintained"
        and candidate.get("concept")
    ]


def _kind(rule: Rule) -> str:
    (kind,) = rule.params["implies"]
    return kind


def _predicate_facts(predicate: dict) -> list[tuple[str, list]]:
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        return [pair for item in operand for pair in _predicate_facts(item)]
    if operator == "not":
        return _predicate_facts(operand)
    fact, value = operand
    return [(fact, value if operator == "in" else [value])]


def _referenced(rules: list[Rule]) -> list[str]:
    seen: list[str] = []
    for rule in rules:
        for fact, _ in _predicate_facts(rule.params["when"]):
            if fact not in seen:
                seen.append(fact)
    return seen


def _matches(rule: Rule, facts: dict) -> bool | None:
    return evaluate_partial(rule.params["when"], facts)


def _of_kind(rules: list[Rule], kind: str) -> list[Rule]:
    return [rule for rule in rules if _kind(rule) == kind]


def _evaluate(rules: list[Rule], facts: dict) -> tuple:
    """(outcome rule, matching concept rules in order, matching control rules)."""
    outcome = next((rule for rule in _of_kind(rules, "outcome") if _matches(rule, facts)), None)
    builds = outcome is not None and outcome.params["implies"]["outcome"] in _BUILT + ("reuse",)
    concepts = [rule for rule in _of_kind(rules, "concept") if builds and _matches(rule, facts)]
    controls = [rule for rule in _of_kind(rules, "control") if _matches(rule, facts)]
    return outcome, concepts, controls


def _signature(evaluated: tuple) -> tuple:
    outcome, concepts, controls = evaluated
    outcome_name = outcome.params["implies"]["outcome"] if outcome else None
    concept = concepts[0].params["implies"]["concept"] if concepts else None
    return outcome_name, concept, tuple(sorted(rule.params["implies"]["control"]["id"] for rule in controls))


def _open_facts(rules: list[Rule], facts: dict) -> set[str]:
    """Unconfirmed facts that can still change the result.

    Rules after a definitely matching outcome or concept rule are never
    reached; concept rules do not matter when the outcome builds nothing.
    """
    open_facts: set[str] = set()

    def note(rule):
        open_facts.update(fact for fact, _ in _predicate_facts(rule.params["when"]) if fact not in facts)

    outcome_decided = None
    for rule in _of_kind(rules, "outcome"):
        result = _matches(rule, facts)
        if result is None:
            note(rule)
        elif result:
            outcome_decided = rule.params["implies"]["outcome"] if not open_facts else None
            break
    if outcome_decided is None or outcome_decided in _BUILT + ("reuse",):
        for rule in _of_kind(rules, "concept"):
            result = _matches(rule, facts)
            if result is None:
                note(rule)
            elif result:
                break
    for rule in _of_kind(rules, "control"):
        if _matches(rule, facts) is None:
            note(rule)
    return open_facts


def _decide(rules: list[Rule], facts: dict, domains: dict[str, list], order: list[str]) -> tuple[tuple | None, str | None]:
    """Return (evaluation, None) when decided, else (None, deciding fact)."""
    open_facts = _open_facts(rules, facts)
    unknown = [fact for fact in order if fact in open_facts]
    unknown += sorted(open_facts - set(unknown))
    if not unknown:
        return _evaluate(rules, facts), None
    choices = [domains[fact] for fact in unknown]
    total = 1
    for values in choices:
        total *= len(values)
    if total > _MAX_ASSIGNMENTS:
        return None, unknown[0]
    results = {values: _signature(_evaluate(rules, {**facts, **dict(zip(unknown, values))}))
               for values in itertools.product(*choices)}
    if len(set(results.values())) == 1:
        return _evaluate(rules, {**facts, **dict(zip(unknown, next(iter(results))))}), None
    for index, fact in enumerate(unknown):
        grouped: dict[tuple, set] = {}
        for values, signature in results.items():
            grouped.setdefault(values[:index] + values[index + 1:], set()).add(signature)
        if any(len(signatures) > 1 for signatures in grouped.values()):
            return None, fact
    return None, unknown[0]


def _domains_from_rules(rules: list[Rule]) -> dict[str, list]:
    """Values the rules test per fact, plus one value no rule tests."""
    domains: dict[str, list] = {}
    for rule in rules:
        for fact, values in _predicate_facts(rule.params["when"]):
            for value in values:
                if value not in domains.setdefault(fact, []):
                    domains[fact].append(value)
    return {fact: values + [_OTHER] for fact, values in domains.items()}


def _pages(rules: Iterable[Rule]) -> list[str]:
    pages: list[str] = []
    for rule in rules:
        for source in rule.sources:
            if source.startswith("docs/") and source not in pages:
                pages.append(source)
    return pages


def _facts(answers: dict, whole_idea: dict, candidates: list[dict]) -> tuple[dict, list[dict]]:
    facts = {**normalize(whole_idea), **normalize(answers)}
    qualifying = qualifying_candidates(candidates)
    facts["reuse_confirmed"] = bool(qualifying)
    return facts, qualifying


def recommend(answers: dict, whole_idea: dict, rules: list[Rule], candidates: list[dict]) -> Recommendation:
    """Recommend an outcome, mechanism and controls, or name the deciding question."""
    facts, qualifying = _facts(answers, whole_idea, candidates)
    selection_rules = [rule for rule in rules if rule.checker == "selection.match"]
    evaluated, pending = _decide(selection_rules, facts, _domains_from_rules(selection_rules), _referenced(selection_rules))
    if evaluated is None:
        return Recommendation(outcome=None, mechanism=None, rule_ids=[], pages=[], pending_question=pending)

    outcome_rule, concept_rules, control_rules = evaluated
    outcome = outcome_rule.params["implies"]["outcome"]
    chosen = concept_rules[0] if concept_rules and outcome in _BUILT else None
    mechanism = chosen.params["implies"]["concept"] if chosen else None
    reuse = None
    if outcome == "reuse":
        reuse = dict(sorted(qualifying, key=lambda candidate: candidate["name"])[0])
        mechanism = reuse["concept"]

    alternatives, seen_concepts = [], {mechanism}
    for rule in concept_rules:
        concept = rule.params["implies"]["concept"]
        if concept in seen_concepts:
            continue
        seen_concepts.add(concept)
        alternatives.append({"concept": concept, "pros": rule.params.get("pros", []),
                             "cons": rule.params.get("cons", []), "rules": [rule.id]})

    availability = facts.get("plan_controls", "unknown")
    controls: dict[str, dict] = {}
    for rule in control_rules:
        control = rule.params["implies"]["control"]
        merged = controls.setdefault(control["id"], {
            "id": control["id"], "family": control["family"], "prerequisites": [],
            "required_for_goal": False, "availability": availability, "rules": [],
        })
        merged["prerequisites"] = sorted(set(merged["prerequisites"]) | set(control["prerequisites"]))
        merged["required_for_goal"] = merged["required_for_goal"] or control["required_for_goal"]
        merged["rules"].append(rule.id)

    cited = [outcome_rule, *([chosen] if chosen else []), *control_rules]
    return Recommendation(
        outcome=outcome,
        mechanism=mechanism,
        rule_ids=[rule.id for rule in cited],
        pages=_pages(cited),
        alternatives=alternatives,
        controls=[controls[key] for key in sorted(controls)],
        pros=list(chosen.params.get("pros", [])) if chosen else [],
        cons=list(chosen.params.get("cons", [])) if chosen else [],
        reuse=reuse,
    )


def next_question(part: dict, whole_idea: dict, selection: dict) -> dict | None:
    """The next question that could change the part's result, or None."""
    facts = {**normalize(whole_idea), **normalize(part.get("answers", {}))}
    facts.setdefault("reuse_confirmed", False)
    rules = [Rule.from_dict(rule) for rule in selection["rules"]]
    domains = {fact["id"]: list(fact["values"]) for fact in selection["facts"]}
    questions = {question["id"]: question for question in selection["questions"]}
    order = [question["id"] for question in selection["questions"]]
    order += [fact for fact in _referenced(rules) if fact not in order]
    _, pending = _decide(rules, facts, domains, order)
    if pending is None:
        return None
    if pending not in questions:
        raise ValueError(f"fact {pending!r} decides the result but has no question")
    return dict(questions[pending])
