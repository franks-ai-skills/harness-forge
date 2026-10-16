# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Compute a part's recommendation from confirmed, normalized answers.

Selection has five kinds of rules from the pinned KB (policy/selection.md):

- outcome rules, in order, first match wins;
- eligibility rules (``excludes``), which rule a mechanism out when a
  confirmed answer conflicts with a sourced "When not" condition;
- preference rules (``concept``), in Forge's documented order: the first
  eligible match is recommended, later eligible matches are viable
  alternatives;
- variant rules (``variant``), in order: the first that holds names the
  selected mechanism's form, such as a hook's handler, whether it is
  built, reused or an alternative;
- control rules, which all apply.

Eligibility applies to reuse too: a candidate whose mechanism is ruled
out, or whose recorded form (``variant``) is not one its variant rule
accepts, is not reused, and the result names the conflict.

When an unanswered fact can still change the outcome, mechanism, its
variant or the controls, the result is a question, never a default. Citations and the
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


def _variant_rules(rules: list[Rule], concept: str) -> list[Rule]:
    return [rule for rule in _of_kind(rules, "variant") if rule.params["implies"]["variant"]["concept"] == concept]


def _variant_rule(rules: list[Rule], concept: str | None, facts: dict) -> Rule | None:
    """The first variant rule that holds, once the answers decide every rule before it."""
    for rule in _variant_rules(rules, concept):
        result = _matches(rule, facts)
        if result is None:
            return None
        if result:
            return rule
    return None


def _variant(rules: list[Rule], concept: str | None, facts: dict) -> str | None:
    """The mechanism's form, such as a hook's handler."""
    rule = _variant_rule(rules, concept, facts)
    return rule.params["implies"]["variant"]["value"] if rule else None


def _form_fits(rules: list[Rule], option: tuple, facts: dict) -> bool | None:
    """Whether a candidate's recorded form is one the mechanism's variant accepts; None while open."""
    concept, form = option
    for rule in _variant_rules(rules, concept):
        result = _matches(rule, facts)
        if result is None:
            return None
        if result:
            return form in rule.params["implies"]["variant"]["accepts"]
    return True


def _reuse_state(rules: list[Rule], option: tuple, facts: dict) -> bool | None:
    states = (_eligible(rules, option[0], facts), _form_fits(rules, option, facts))
    if False in states:
        return False
    return None if None in states else True


def _variant_facts(rules: list[Rule], concept: str, facts: dict) -> set[str]:
    """Unconfirmed facts that can still decide the mechanism's variant."""
    open_facts: set[str] = set()
    for rule in _variant_rules(rules, concept):
        result = _matches(rule, facts)
        if result is None:
            open_facts |= _unknown_facts(rule, facts)
        elif result:
            break
    return open_facts


def _reusable(rules: list[Rule], facts: dict, reuse_options: tuple[tuple, ...]) -> tuple | None:
    """The (mechanism, form) of the first candidate that no confirmed answer rules out."""
    return next((option for option in reuse_options if _reuse_state(rules, option, facts)), None)


def _evaluate(rules: list[Rule], facts: dict, reuse_options: tuple[tuple, ...] = ()) -> dict:
    """Every layer for facts that decide all relevant rules."""
    reused = _reusable(rules, facts, reuse_options)
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
    reused = reused if outcome == "reuse" else None
    selected = preferred[0].params["implies"]["concept"] if outcome in _BUILT and preferred else reused and reused[0]
    return {
        "outcome_rule": outcome_rule, "outcome": outcome, "blocked_by": blocked_by,
        "reused": reused, "selected": selected, "variant": _variant(rules, selected, facts),
        "preferred": preferred if builds else [],
        "controls": [rule for rule in _of_kind(rules, "control") if _matches(rule, facts)],
    }


def _signature(evaluated: dict) -> tuple:
    """What a question could change: outcome, mechanism, its variant (such as a hook's handler) and controls."""
    controls = tuple(sorted(rule.params["implies"]["control"]["id"] for rule in evaluated["controls"]))
    return evaluated["outcome"], evaluated["selected"], evaluated["variant"], controls


def _exclusion_facts(rules: list[Rule], concept: str, facts: dict) -> set[str]:
    return set().union(*(_unknown_facts(rule, facts) for rule in _of_kind(rules, "excludes")
                         if rule.params["implies"]["excludes"] == concept and _matches(rule, facts) is None))


def _open_facts(rules: list[Rule], facts: dict, reuse_options: tuple[tuple, ...] = ()) -> set[str]:
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
                for option in reuse_options:
                    states.append(_reuse_state(rules, option, facts))
                    if states[-1] is None:
                        open_facts |= _exclusion_facts(rules, option[0], facts)
                    if states[-1] is not False:
                        open_facts |= _variant_facts(rules, option[0], facts)
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
            open_facts |= _variant_facts(rules, concept, facts)
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
            reuse_options: tuple[tuple, ...] = ()) -> tuple[dict | None, str | None]:
    """Return (evaluation, None) when decided, else (None, deciding fact)."""
    open_facts = _open_facts(rules, facts, reuse_options)
    unknown = [fact for fact in order if fact in open_facts]
    unknown += sorted(open_facts - set(unknown))
    if not unknown:
        return _evaluate(rules, facts, reuse_options), None
    choices = [domains[fact] for fact in unknown]
    total = 1
    for values in choices:
        total *= len(values)
    if total > _MAX_ASSIGNMENTS:
        return None, unknown[0]
    results = {values: _signature(_evaluate(rules, {**facts, **dict(zip(unknown, values))}, reuse_options))
               for values in itertools.product(*choices)}
    if len(set(results.values())) == 1:
        return _evaluate(rules, {**facts, **dict(zip(unknown, next(iter(results))))}, reuse_options), None
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


def _form(candidate: dict) -> str | None:
    """A candidate's recorded form, such as a hook's handler; None when not recorded."""
    variant = candidate.get("variant")
    return variant.strip() if isinstance(variant, str) and variant.strip() else None


def _options(qualifying: list[dict]) -> tuple[tuple, ...]:
    return tuple(dict.fromkeys((candidate["concept"], _form(candidate)) for candidate in qualifying))


def _established(rules: list[Rule], kind: str, value, facts: dict) -> Rule | None:
    """The first rule of a kind that implies value and holds for the given answers."""
    return next((rule for rule in _of_kind(rules, kind)
                 if rule.params["implies"][kind] == value and _matches(rule, facts) is True), None)


def recommend(answers: dict, whole_idea: dict, rules: list[Rule], candidates: list[dict]) -> Recommendation:
    """Recommend an outcome, mechanism and controls, or name the deciding question."""
    facts, qualifying = _facts(answers, whole_idea, candidates)
    selection_rules = [rule for rule in rules if rule.checker == "selection.match"]
    options = _options(qualifying)
    evaluated, pending = _decide(selection_rules, facts, _domains_from_rules(selection_rules),
                                 _referenced(selection_rules), options)
    if evaluated is None:
        return Recommendation(outcome=None, mechanism=None, rule_ids=[], pages=[], pending_question=pending)

    outcome = evaluated["outcome"]
    mechanism = None
    if outcome in _BUILT and evaluated["preferred"]:
        mechanism = evaluated["preferred"][0].params["implies"]["concept"]
    reuse = None
    if outcome == "reuse":
        reuse = dict(next(candidate for candidate in qualifying
                          if (candidate["concept"], _form(candidate)) == evaluated["reused"]))
        mechanism = reuse["concept"]
    # A confirmed fit never overrides a requirement: name each conflict.
    reuse_conflicts = []
    for candidate in qualifying:
        excluding = [rule for rule in _of_kind(selection_rules, "excludes")
                     if rule.params["implies"]["excludes"] == candidate["concept"] and _matches(rule, facts) is True]
        rule_ids, reasons = [rule.id for rule in excluding], [rule.statement for rule in excluding]
        required = _variant_rule(selection_rules, candidate["concept"], facts)
        if required and _form(candidate) not in required.params["implies"]["variant"]["accepts"]:
            rule_ids.append(required.id)
            reasons.append(f"needs a {required.params['implies']['variant']['value']}; the candidate's form is "
                           f"{_form(candidate) or 'not recorded'}")
        if rule_ids:
            reuse_conflicts.append({"candidate": candidate["name"], "concept": candidate["concept"],
                                    "rules": rule_ids, "reasons": reasons})

    # The result holds for every remaining answer. Cite only rules whose
    # conditions hold for the answers given; name the facts left unasked.
    unasked = sorted(_open_facts(selection_rules, facts, options))
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
        variant_rule = _variant_rule(selection_rules, entry["concept"], facts)
        open_variant = [] if variant_rule else sorted(_variant_facts(selection_rules, entry["concept"], facts))
        alternatives.append({"concept": entry["concept"],
                             "variant": variant_rule.params["implies"]["variant"]["value"] if variant_rule else None,
                             "variant_missing_facts": open_variant,
                             "pros": rule.params.get("pros", []), "cons": rule.params.get("cons", []),
                             "rules": entry["rules"] + ([variant_rule.id] if variant_rule else []),
                             "pages": _pages([r for r in selection_rules if r.id in entry["rules"]]
                                             + ([variant_rule] if variant_rule else [])),
                             "why_not_chosen": why})

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

    variant_rule = _variant_rule(selection_rules, mechanism, facts) if mechanism else None
    cited = (cited_outcome + ([winner] if winner else []) + ([variant_rule] if variant_rule else [])
             + [r for r in evaluated["controls"] if _matches(r, facts) is True])
    # A built mechanism is described by its preference rule; a reused one
    # only by its candidate's own evidence (``reuse``).
    described = winner or (evaluated["preferred"][0] if outcome in _BUILT and evaluated["preferred"] else None)
    return Recommendation(
        outcome=outcome,
        mechanism=mechanism,
        rule_ids=[rule.id for rule in cited],
        pages=_pages(cited),
        alternatives=alternatives,
        controls=[controls[key] for key in sorted(controls)],
        variant=evaluated["variant"] if mechanism else None,
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
    _, pending = _decide(rules, facts, domains, order, _options(qualifying))
    if pending is None:
        return None
    if pending not in questions:
        raise ValueError(f"fact {pending!r} decides the result but has no question")
    return dict(questions[pending])


# --- Recorded choices -------------------------------------------------------------------------------------------------

CONFLICT_CHOICES = ("recommended", "adjust-goal", "keep")


def recorded_candidates(part: dict) -> list[dict]:
    """A reused part's candidate as the intake confirmed it: fit, license and maintenance from its record."""
    reuse = part.get("reuse")
    if part.get("outcome") != "reuse" or not reuse or not part.get("chosen"):
        return []
    return [{**reuse, "fit": "confirmed", "concept": part["chosen"]}]


def _controls_problem(part: dict, recommendation: Recommendation) -> str | None:
    """Recorded controls must be the computed ones: none omitted or added, no requirement or prerequisite dropped."""
    computed = {control["id"]: control for control in recommendation.controls}
    recorded = {control["control"]: control for control in part["outside_controls"]}
    problems = [f"omits {control_id}" for control_id in sorted(set(computed) - set(recorded))]
    problems += [f"adds {control_id}, which the answers do not call for" for control_id in sorted(set(recorded) - set(computed))]
    for control_id in sorted(set(computed) & set(recorded)):
        if computed[control_id]["required_for_goal"] and not recorded[control_id]["required_for_goal"]:
            problems.append(f"records {control_id} as a precaution although the goal needs it")
        dropped = set(computed[control_id]["prerequisites"]) - set(recorded[control_id]["prerequisites"])
        if dropped:
            problems.append(f"drops prerequisites of {control_id}: {', '.join(sorted(dropped))}")
    return "; ".join(problems) or None


def selection_state(part: dict, whole_idea: dict, rules: list[Rule], candidates: Iterable[dict] | None = None) -> dict:
    """Whether a part's recorded selection is what its confirmed answers give now.

    A recorded recommendation is data: an edited record, a changed override
    or an answer cleared by a goal adjustment makes it stale, and a stale or
    open selection must be decided again before anything is built. The
    outcome, the mechanism and the outside controls are compared; a reused
    part is checked with its recorded candidate.
    """
    candidates = recorded_candidates(part) if candidates is None else list(candidates)
    recommendation = recommend(part["answers"], whole_idea, rules, candidates)
    state = {"recommendation": recommendation, "pending_question": recommendation.pending_question,
             "current": False, "problem": None}
    controls = None if recommendation.pending_question else _controls_problem(part, recommendation)
    if recommendation.pending_question:
        state["problem"] = (f"part {part['id']} still has an open question ({recommendation.pending_question}); "
                            "ask it and run select again")
    elif (recommendation.outcome, recommendation.mechanism) != (part["outcome"], part["recommended"]):
        state["problem"] = (f"part {part['id']}'s recorded {part['outcome']}/{part['recommended'] or 'no mechanism'} "
                            f"is not what its answers give now ({recommendation.outcome}/"
                            f"{recommendation.mechanism or 'no mechanism'}); run select again and record its result")
    elif controls:
        state["problem"] = (f"part {part['id']}'s recorded outside controls are not what its answers give now: "
                            f"it {controls}; run select again and record its controls")
    else:
        state["current"] = True
    return state


def _lifting_values(rules: list[Rule], chosen: str, facts: dict, domains: dict[str, list]) -> list[dict]:
    """Single answer changes after which no confirmed answer rules the chosen mechanism out."""
    excluding = [rule for rule in _of_kind(rules, "excludes")
                 if rule.params["implies"]["excludes"] == chosen and _matches(rule, facts) is True]
    changes = []
    for fact in dict.fromkeys(fact for rule in excluding for fact, _ in _predicate_facts(rule.params["when"])):
        if fact not in facts:
            continue
        values = [value for value in domains.get(fact, []) if value != facts[fact]
                  and _eligible(rules, chosen, {**facts, fact: value}) is not False]
        if values:
            changes.append({"fact": fact, "current": facts[fact], "values": values})
    return changes


def goal_conflict(part: dict, whole_idea: dict, rules: list[Rule], candidates: Iterable[dict] = (),
                  domains: dict[str, list] | None = None) -> dict | None:
    """The conflict between a part's chosen mechanism and its recommendation, or None when they agree.

    A mechanism that a confirmed answer rules out cannot meet a goal the user
    stated: an error with the goal-conflict question's three choices. Any
    other difference only means another mechanism was recommended: a warning.
    ``adjust`` lists single answer changes after which the chosen mechanism is
    no longer ruled out, for wording an adjusted goal.
    """
    chosen = part["chosen"]
    selection_rules = [rule for rule in rules if rule.checker == "selection.match"]
    candidates = list(candidates) or recorded_candidates(part)
    recommendation = recommend(part["answers"], whole_idea, selection_rules, candidates)
    if chosen is None or recommendation.pending_question or chosen == recommendation.mechanism:
        return None
    entry = next((row for row in recommendation.assessment if row["concept"] == chosen), None)
    by_id = {rule.id: rule for rule in selection_rules}
    failing = []
    if entry and entry["state"] == "ruled out":
        failing = [{"rule_id": rule_id, "statement": by_id[rule_id].statement, "sources": list(by_id[rule_id].sources)}
                   for rule_id in entry["rules"]]
    facts = {**normalize(whole_idea), **normalize(part["answers"])}
    return {
        "part_id": part["id"], "severity": "error" if failing else "warning", "goal": part["goal"],
        "chosen": chosen, "chosen_state": entry["state"] if entry else "unknown",
        "recommended": recommendation.mechanism, "recommended_outcome": recommendation.outcome,
        "pros": list(recommendation.pros), "cons": list(recommendation.cons), "failing": failing,
        "choices": list(CONFLICT_CHOICES) if failing else [],
        "adjust": _lifting_values(selection_rules, chosen, facts, domains or {
            fact: [value for value in values if value is not _OTHER]
            for fact, values in _domains_from_rules(selection_rules).items()}) if failing else [],
    }


def conflict_kept(part: dict, conflict: dict) -> bool:
    """Whether the user answered keep for exactly this conflict: the same choice and the same failing rules."""
    recorded = part.get("goal_conflict") or {}
    return (recorded.get("choice") == "keep" and recorded.get("chosen") == conflict["chosen"]
            and sorted(recorded.get("rule_ids", [])) == sorted(item["rule_id"] for item in conflict["failing"]))


def answers_problem(record: dict, selection: dict) -> str | None:
    """Why a decision's recorded answers are not answers to the declared questions, or None.

    Part answers must be part questions and whole-idea facts whole-idea
    ones, each with one of its declared values: a part answer named like a
    whole-idea fact would otherwise shadow it when facts are merged.
    """
    asked = {question["id"] for question in selection["questions"]}
    declared = {fact["id"]: fact for fact in selection["facts"] if fact["id"] in asked}

    def wrong(values: dict, scope: str, owner: str) -> str | None:
        for fact, value in values.items():
            if fact not in declared or declared[fact]["scope"] != scope:
                return f"{owner} answers {fact}, which is not a {'part' if scope == 'part' else 'whole-idea'} question"
            if not any(value == allowed and type(value) is type(allowed) for allowed in declared[fact]["values"]):
                return f"{owner} answers {fact} with {value!r}, not one of {declared[fact]['values']}"
        return None

    problem = wrong(record["facts"], "idea", "the idea")
    for part in record["parts"]:
        problem = problem or wrong(part["answers"], "part", f"part {part['id']}")
    return problem
