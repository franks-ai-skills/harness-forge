"""Compute a part's recommendation from confirmed, normalized answers.

Selection has four kinds of rules from the pinned KB (policy/selection.md):

- outcome rules, in order, first match wins;
- eligibility rules (``excludes``), which rule a mechanism out when a
  confirmed answer conflicts with a sourced "When not" condition;
- preference rules (``concept``), in Forge's documented order: the first
  eligible match is recommended, later eligible matches are viable
  alternatives;
- control rules, which all apply.

Eligibility applies to reuse too: a candidate whose mechanism is ruled
out is not reused, and the result names the conflict.

When an unanswered fact can still change the outcome, mechanism or
controls, the result is a question, never a default. Citations and the
per-mechanism assessment rest only on the answers actually given.
"""

from __future__ import annotations

import itertools
from typing import Iterable

from harness_forge.models import Recommendation, Rule
from harness_forge.predicates import evaluate_partial

CONCEPTS = ("instructions", "configuration", "permissions-and-sandbox", "mcp", "skills", "commands",
            "subagents", "hooks", "plugins", "automation")
BUILDABLE = ("skills", "commands")
_OTHER = object()  # stands for any value no rule tests
_BUILT = ("harness-only", "outside-alongside")
_WITHOUT_MECHANISM = {"harness-only": "nothing-fits", "outside-alongside": "outside-instead"}
_MAX_ASSIGNMENTS = 200_000
_POLICY = "policy/selection.md"


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
    """Reuse candidates with confirmed fit, a known license and active maintenance."""
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


def _of_kind(rules: list[Rule], kind: str) -> list[Rule]:
    return [rule for rule in rules if _kind(rule) == kind]


def _matches(rule: Rule, facts: dict) -> bool | None:
    return evaluate_partial(rule.params["when"], facts)


def _predicate_facts(predicate: dict) -> list[tuple[str, list]]:
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        return [pair for item in operand for pair in _predicate_facts(item)]
    if operator == "not":
        return _predicate_facts(operand)
    fact, value = operand
    return [(fact, value if operator == "in" else [value])]


def _unknown_facts(rule: Rule, facts: dict) -> set[str]:
    return {fact for fact, _ in _predicate_facts(rule.params["when"]) if fact not in facts}


def _referenced(rules: list[Rule]) -> list[str]:
    seen: list[str] = []
    for rule in rules:
        for fact, _ in _predicate_facts(rule.params["when"]):
            if fact not in seen:
                seen.append(fact)
    return seen


def _eligible(rules: list[Rule], concept: str, facts: dict) -> bool | None:
    """False if a confirmed answer rules the mechanism out, None if that is still open."""
    results = [_matches(rule, facts) for rule in _of_kind(rules, "excludes")
               if rule.params["implies"]["excludes"] == concept]
    if True in results:
        return False
    return None if None in results else True


def _reusable(rules: list[Rule], facts: dict, reuse_concepts: tuple[str, ...]) -> str | None:
    """The mechanism of the first candidate that no confirmed answer rules out."""
    return next((concept for concept in reuse_concepts if _eligible(rules, concept, facts)), None)


def _evaluate(rules: list[Rule], facts: dict, reuse_concepts: tuple[str, ...] = ()) -> dict:
    """Every layer for facts that decide all relevant rules."""
    reused = _reusable(rules, facts, reuse_concepts)
    outcome_rule = next((rule for rule in _of_kind(rules, "outcome") if _matches(rule, facts)
                         and (rule.params["implies"]["outcome"] != "reuse" or reused)), None)
    outcome = outcome_rule.params["implies"]["outcome"] if outcome_rule else None
    excluded: dict[str, list[Rule]] = {}
    for rule in _of_kind(rules, "excludes"):
        if _matches(rule, facts):
            excluded.setdefault(rule.params["implies"]["excludes"], []).append(rule)
    preferred = [rule for rule in _of_kind(rules, "concept")
                 if _matches(rule, facts) and rule.params["implies"]["concept"] not in excluded]
    blocked_by = []
    if outcome in _BUILT and not preferred:
        candidates = {rule.params["implies"]["concept"] for rule in _of_kind(rules, "concept") if _matches(rule, facts)}
        blocked_by = [rule for concept in sorted(candidates) for rule in excluded.get(concept, [])]
        if blocked_by:
            outcome = _WITHOUT_MECHANISM[outcome]
    builds = outcome in _BUILT + ("reuse",)
    return {
        "outcome_rule": outcome_rule, "outcome": outcome, "blocked_by": blocked_by,
        "reused": reused if outcome == "reuse" else None,
        "preferred": preferred if builds else [],
        "controls": [rule for rule in _of_kind(rules, "control") if _matches(rule, facts)],
    }


def _signature(evaluated: dict) -> tuple:
    """What a question could change: outcome, mechanism, its variant (such as a hook's handler) and controls."""
    concept = variant = None
    if evaluated["outcome"] in _BUILT and evaluated["preferred"]:
        concept = evaluated["preferred"][0].params["implies"]["concept"]
        variant = evaluated["preferred"][0].params.get("variant")
    controls = tuple(sorted(rule.params["implies"]["control"]["id"] for rule in evaluated["controls"]))
    return evaluated["outcome"], concept or evaluated["reused"], variant, controls


def _exclusion_facts(rules: list[Rule], concept: str, facts: dict) -> set[str]:
    return set().union(*(_unknown_facts(rule, facts) for rule in _of_kind(rules, "excludes")
                         if rule.params["implies"]["excludes"] == concept and _matches(rule, facts) is None))


def _open_facts(rules: list[Rule], facts: dict, reuse_concepts: tuple[str, ...] = ()) -> set[str]:
    """Unconfirmed facts that can still change the outcome, mechanism or controls."""
    open_facts: set[str] = set()
    outcome = None
    for rule in _of_kind(rules, "outcome"):
        result = _matches(rule, facts)
        if result is None:
            open_facts |= _unknown_facts(rule, facts)
        elif result:
            implied = rule.params["implies"]["outcome"]
            if implied == "reuse":
                states = []
                for concept in reuse_concepts:
                    states.append(_eligible(rules, concept, facts))
                    if states[-1] is None:
                        open_facts |= _exclusion_facts(rules, concept, facts)
                    if states[-1] is not False:
                        break
                if not states or states[-1] is not True:
                    continue  # no candidate is known to be eligible; a later outcome may apply
            outcome = implied if not open_facts else None
            break
    if outcome is None or outcome in _BUILT + ("reuse",):
        exclusions = _of_kind(rules, "excludes")
        for rule in _of_kind(rules, "concept"):
            result = _matches(rule, facts)
            if result is False:
                continue
            concept = rule.params["implies"]["concept"]
            own = [exclusion for exclusion in exclusions if exclusion.params["implies"]["excludes"] == concept]
            if result is None:
                open_facts |= _unknown_facts(rule, facts)
            for exclusion in own:
                if _matches(exclusion, facts) is None:
                    open_facts |= _unknown_facts(exclusion, facts)
            if result and all(_matches(exclusion, facts) is False for exclusion in own):
                break
    for rule in _of_kind(rules, "control"):
        if _matches(rule, facts) is None:
            open_facts |= _unknown_facts(rule, facts)
    return open_facts


def _decide(rules: list[Rule], facts: dict, domains: dict[str, list], order: list[str],
            reuse_concepts: tuple[str, ...] = ()) -> tuple[dict | None, str | None]:
    """Return (evaluation, None) when decided, else (None, deciding fact)."""
    open_facts = _open_facts(rules, facts, reuse_concepts)
    unknown = [fact for fact in order if fact in open_facts]
    unknown += sorted(open_facts - set(unknown))
    if not unknown:
        return _evaluate(rules, facts, reuse_concepts), None
    choices = [domains[fact] for fact in unknown]
    total = 1
    for values in choices:
        total *= len(values)
    if total > _MAX_ASSIGNMENTS:
        return None, unknown[0]
    results = {values: _signature(_evaluate(rules, {**facts, **dict(zip(unknown, values))}, reuse_concepts))
               for values in itertools.product(*choices)}
    if len(set(results.values())) == 1:
        return _evaluate(rules, {**facts, **dict(zip(unknown, next(iter(results))))}, reuse_concepts), None
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


def _assessment(rules: list[Rule], facts: dict, mechanism: str | None, winner: Rule | None) -> list[dict]:
    """One state per mechanism, based only on the answers given."""
    table = []
    for concept in CONCEPTS:
        exclusions = [r for r in _of_kind(rules, "excludes") if r.params["implies"]["excludes"] == concept]
        preferences = [r for r in _of_kind(rules, "concept") if r.params["implies"]["concept"] == concept]
        excluded = [r for r in exclusions if _matches(r, facts) is True]
        open_exclusions = [r for r in exclusions if _matches(r, facts) is None]
        matched = [r for r in preferences if _matches(r, facts) is True]
        open_preferences = [r for r in preferences if _matches(r, facts) is None]
        entry = {"concept": concept, "rules": [], "reasons": [], "missing_facts": []}
        if concept == mechanism:
            entry.update(state="recommended", rules=[winner.id] if winner else [],
                         reasons=[winner.statement] if winner else [])
        elif excluded:
            entry.update(state="ruled out", rules=[r.id for r in excluded], reasons=[r.statement for r in excluded])
        elif open_exclusions or (not matched and open_preferences):
            unresolved = open_exclusions + open_preferences
            missing = sorted(set().union(*(_unknown_facts(r, facts) for r in unresolved)))
            entry.update(state="unresolved", rules=[r.id for r in unresolved], missing_facts=missing)
        elif matched:
            entry.update(state="viable alternative", rules=[r.id for r in matched], reasons=[r.statement for r in matched])
        else:
            entry.update(state="not preferred", reasons=["no preference rule shows an advantage for this goal"])
        table.append(entry)
    return table


def _facts(answers: dict, whole_idea: dict, candidates: Iterable[dict]) -> tuple[dict, list[dict]]:
    facts = {**normalize(whole_idea), **normalize(answers)}
    qualifying = sorted(qualifying_candidates(candidates), key=lambda candidate: candidate["name"])
    facts["reuse_confirmed"] = bool(qualifying)
    return facts, qualifying


def _concepts(qualifying: list[dict]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(candidate["concept"] for candidate in qualifying))


def _established(rules: list[Rule], kind: str, value, facts: dict) -> Rule | None:
    """The first rule of a kind that implies value and holds for the given answers."""
    return next((rule for rule in _of_kind(rules, kind)
                 if rule.params["implies"][kind] == value and _matches(rule, facts) is True), None)


def recommend(answers: dict, whole_idea: dict, rules: list[Rule], candidates: list[dict]) -> Recommendation:
    """Recommend an outcome, mechanism and controls, or name the deciding question."""
    facts, qualifying = _facts(answers, whole_idea, candidates)
    selection_rules = [rule for rule in rules if rule.checker == "selection.match"]
    concepts = _concepts(qualifying)
    evaluated, pending = _decide(selection_rules, facts, _domains_from_rules(selection_rules),
                                 _referenced(selection_rules), concepts)
    if evaluated is None:
        return Recommendation(outcome=None, mechanism=None, rule_ids=[], pages=[], pending_question=pending)

    outcome = evaluated["outcome"]
    mechanism = None
    if outcome in _BUILT and evaluated["preferred"]:
        mechanism = evaluated["preferred"][0].params["implies"]["concept"]
    reuse = None
    if outcome == "reuse":
        reuse = dict(next(candidate for candidate in qualifying if candidate["concept"] == evaluated["reused"]))
        mechanism = reuse["concept"]
    # A confirmed fit never overrides a requirement: name each conflict.
    reuse_conflicts = []
    for candidate in qualifying:
        excluding = [rule for rule in _of_kind(selection_rules, "excludes")
                     if rule.params["implies"]["excludes"] == candidate["concept"] and _matches(rule, facts) is True]
        if excluding:
            reuse_conflicts.append({"candidate": candidate["name"], "concept": candidate["concept"],
                                    "rules": [rule.id for rule in excluding],
                                    "reasons": [rule.statement for rule in excluding]})

    # The result holds for every remaining answer. Cite only rules whose
    # conditions hold for the answers given; name the facts left unasked.
    unasked = sorted(_open_facts(selection_rules, facts, concepts))
    if evaluated["blocked_by"]:
        cited_outcome = [rule for rule in evaluated["blocked_by"] if _matches(rule, facts) is True]
    else:
        established = _established(selection_rules, "outcome", outcome, facts)
        cited_outcome = [established] if established else []
    winner = _established(selection_rules, "concept", mechanism, facts) if mechanism and outcome in _BUILT else None
    assessment = _assessment(selection_rules, facts, mechanism, winner)

    alternatives = []
    for entry in assessment:
        if entry["state"] != "viable alternative":
            continue
        rule = next(r for r in selection_rules if r.id == entry["rules"][0])
        if winner is not None:
            why = f"{winner.id} comes before {rule.id} in Forge's preference order ({_POLICY})"
        else:
            why = f"the outcome {outcome} builds no harness mechanism"
        alternatives.append({"concept": entry["concept"], "pros": rule.params.get("pros", []),
                             "cons": rule.params.get("cons", []), "rules": entry["rules"], "why_not_chosen": why})

    availability = facts.get("plan_controls", "unknown")
    controls: dict[str, dict] = {}
    for rule in evaluated["controls"]:
        control = rule.params["implies"]["control"]
        merged = controls.setdefault(control["id"], {
            "id": control["id"], "family": control["family"], "prerequisites": [],
            "required_for_goal": False, "availability": availability, "rules": [],
        })
        merged["prerequisites"] = sorted(set(merged["prerequisites"]) | set(control["prerequisites"]))
        merged["required_for_goal"] = merged["required_for_goal"] or control["required_for_goal"]
        if _matches(rule, facts) is True:
            merged["rules"].append(rule.id)

    cited = cited_outcome + ([winner] if winner else []) + [r for r in evaluated["controls"] if _matches(r, facts) is True]
    described = winner or (evaluated["preferred"][0] if mechanism and evaluated["preferred"] else None)
    return Recommendation(
        outcome=outcome,
        mechanism=mechanism,
        rule_ids=[rule.id for rule in cited],
        pages=_pages(cited),
        alternatives=alternatives,
        controls=[controls[key] for key in sorted(controls)],
        variant=described.params.get("variant") if described else None,
        pros=list(described.params.get("pros", [])) if described else [],
        cons=list(described.params.get("cons", [])) if described else [],
        reuse=reuse,
        reuse_conflicts=reuse_conflicts,
        unasked_facts=unasked,
        assessment=assessment,
        builder_available=mechanism in BUILDABLE,
        no_standalone_mechanism=bool(evaluated["blocked_by"]),
    )


def next_question(part: dict, whole_idea: dict, selection: dict, candidates: Iterable[dict] = ()) -> dict | None:
    """The next question that could change the part's result, or None."""
    facts, qualifying = _facts(part.get("answers", {}), whole_idea, candidates)
    rules = [Rule.from_dict(rule) for rule in selection["rules"]]
    domains = {fact["id"]: list(fact["values"]) for fact in selection["facts"]}
    questions = {question["id"]: question for question in selection["questions"]}
    order = [question["id"] for question in selection["questions"]]
    order += [fact for fact in _referenced(rules) if fact not in order]
    _, pending = _decide(rules, facts, domains, order, _concepts(qualifying))
    if pending is None:
        return None
    if pending not in questions:
        raise ValueError(f"fact {pending!r} decides the result but has no question")
    return dict(questions[pending])
