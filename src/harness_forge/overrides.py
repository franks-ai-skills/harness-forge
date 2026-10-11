"""Repository rule overrides: adopting a drift correction for one repository.

An override replaces or retires one pinned Harness KB rule in the repository
that holds it, in every stage: selection, building and verification. It is
a committed file at ``.harness/overrides/<rule-id>.yaml`` and data like any
other repository record: it applies only after the user confirmed the
contract that shows it in the current session. It may change a rule's
statement, parameters, severity and applies_to, or retire the rule; a
selection rule's conditions change only in its params.when, since selection
never reads applies_to. It can only refer to catalog checkers and never
adds executable code. Goal-fit
checks come from the decision record, so retiring a rule never removes the
check that a part meets its accepted goal.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from harness_forge.checks import REGISTRY
from harness_forge.documents import load_document, safe_path, schema_errors, validate
from harness_forge.models import Confirmation, Rule

OVERRIDES_DIR = ".harness/overrides"
# What an override may change; sources may only gain the drift finding's own source.
CHANGEABLE = ("statement", "params", "severity", "severity_rationale", "applies_to", "sources")
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


class OverridesPending(RuntimeError):
    """Overrides exist, but no confirmation in this session covers them."""


def load_overrides(repository: Path) -> list[dict]:
    """Every file in the repository's override directory, each a schema-valid override named for its rule.

    A file that is not one, or a directory reached through a link, is
    refused: an unreadable override must not be silently left out.
    """
    directory = safe_path(repository, OVERRIDES_DIR)
    if not directory.is_dir():
        return []
    overrides = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".yaml" or path.is_symlink() or not path.is_file():
            raise ValueError(f"{path}: the override directory holds only <rule-id>.yaml override files")
        override = load_document(path, "override", root=Path(repository).resolve())
        if path.name != f"{override['rule_id']}.yaml":
            raise ValueError(f"{path}: an override is named for its rule, {override['rule_id']}.yaml")
        overrides.append(override)
    return overrides


def write_override(repository: Path, override: dict) -> Path:
    """Validate the record and write ``.harness/overrides/<rule-id>.yaml`` in that repository."""
    validate(override, "override")
    path = safe_path(repository, f"{OVERRIDES_DIR}/{override['rule_id']}.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(override, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return path


def _guarding(rule: Rule) -> str | None:
    """Why loosening this rule needs an explicit warning: an error, a security rule or a goal-conflict rule."""
    if rule.concept == "security" or rule.id.startswith(("security.", "outside.")):
        return "security rule"
    if rule.severity == "error":
        return "error rule"
    if rule.checker == "selection.match" and "excludes" in (rule.params or {}).get("implies", {}):
        return "eligibility rule, which makes a chosen mechanism a goal conflict"
    return None


def _supported(finding: dict, research: list[dict]) -> bool:
    """The finding is one this repository's research kept, with a passed quote check and a supported verdict."""
    for record in research:
        if finding in record["findings"]:
            return any(entry["finding_id"] == finding["id"] and entry["quote_check"] == "pass"
                       and entry["verdict"] == "supported" for entry in record["support"])
    return False


def _when_values(predicate: dict) -> list[tuple[str, list]]:
    """Each (fact, values tested) of a selection predicate."""
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        return [pair for item in operand for pair in _when_values(item)]
    if operator == "not":
        return _when_values(operand)
    fact, value = operand
    return [(fact, list(value) if operator == "in" else [value])]


def check_override(override: dict, pinned: list[Rule], *, kb_version: str, catalog: dict[str, dict],
                   research: list[dict], answerable: dict[str, list] | None = None) -> dict:
    """Whether an override can be adopted, with what it changes and the warnings to show before deciding.

    ``answerable`` maps each fact an intake question asks (and each derived
    one) to its possible answers; a selection rule's override may test no
    other fact and no other value, or selection would wait for an answer
    nobody can give.

    ``status`` is ``valid``, ``invalid`` or ``kb-updated``; a KB update also
    sets ``kb_comparison``: ``incorporated`` or ``removed`` (propose removing
    the override) or ``differs`` (ask whether to keep it).
    """
    validate(override, "override")
    by_id = {rule.id: rule for rule in pinned}
    old = by_id.get(override["rule_id"])
    retired = override.get("retired") is True
    new = Rule.from_dict(override["rule"]) if "rule" in override else None
    result = {"rule_id": override["rule_id"], "status": "valid", "problems": [], "warnings": [], "changes": [],
              "retired": retired, "old": old, "new": new, "kb_comparison": None}
    problems = result["problems"]
    if override["pinned_kb_version"] != kb_version:
        result["status"] = "kb-updated"
        if old is None:
            result["kb_comparison"] = "incorporated" if retired else "removed"
        else:
            result["kb_comparison"] = "incorporated" if new is not None and new.to_dict() == old.to_dict() else "differs"
        next_step = {"incorporated": "the new release contains the correction; propose removing the override",
                     "removed": "the rule is gone from the new release; propose removing the override",
                     "differs": "ask whether to keep the override for the new release"}[result["kb_comparison"]]
        problems.append(f"the override was adopted for Harness KB {override['pinned_kb_version']}, and Forge pins "
                        f"{kb_version}: {next_step}")
        return result
    if old is None:
        problems.append(f"{override['rule_id']} is not a pinned Harness KB rule")
    if not _supported(override["drift"]["finding"], research):
        problems.append(f"finding {override['drift']['finding']['id']} is not a finding this repository's research "
                        "kept with a passed quote check and a supported verdict")
    if new is not None and old is not None:
        if new.id != old.id:
            problems.append(f"the new rule's id {new.id} is not {old.id}")
        before, after = old.to_dict(), new.to_dict()
        for key in sorted(set(before) | set(after)):
            if before.get(key) == after.get(key) or key == "id":
                continue
            if key not in CHANGEABLE:
                problems.append(f"an override may not change {key}")
                continue
            if key == "applies_to" and old.checker == "selection.match":
                # Selection decides from params.when alone; an applies_to it never reads would be ignored silently.
                problems.append("selection does not evaluate applies_to: change the rule's conditions in params.when")
                continue
            result["changes"].append({"field": key, "old": before.get(key), "new": after.get(key)})
        added = set(new.sources) - set(old.sources)
        if not set(old.sources) <= set(new.sources) or added - {override["drift"]["finding"]["source"]}:
            problems.append("sources may only gain the drift finding's own source")
        if new.check == "script":
            checker = catalog.get(new.checker or "")
            if checker is None or new.checker not in REGISTRY:
                problems.append(f"{new.checker} is not a catalog checker")
            else:
                errors = schema_errors(new.params or {}, checker["params"])
                if errors:
                    problems.append(f"invalid params for {new.checker}: {errors[0]}")
        params_valid = not any(problem.startswith("invalid params") for problem in problems)
        if answerable is not None and params_valid and new.checker == "selection.match" \
                and isinstance(new.params, dict) and isinstance(new.params.get("when"), dict):
            for fact, values in _when_values(new.params["when"]):
                if fact not in answerable:
                    problems.append(f"the new rule tests {fact}, which no intake question asks")
                    continue
                for value in values:
                    if not any(value == allowed and type(value) is type(allowed) for allowed in answerable[fact]):
                        problems.append(f"the new rule tests {fact} for {value!r}, but {fact} is never {value!r}")
        if not result["changes"] and not problems:
            problems.append("the new rule is the pinned rule; an override must change it")
    guard = _guarding(old) if old else None
    if guard and not problems:
        if retired:
            result["warnings"].append(f"retires the {guard} {old.id}: nothing checks it in this repository any more")
        else:
            changed = {change["field"]: change for change in result["changes"]}
            if "severity" in changed and _SEVERITY_ORDER[new.severity] < _SEVERITY_ORDER[old.severity]:
                result["warnings"].append(f"lowers the {guard} {old.id} from {old.severity} to {new.severity}")
            for field, what in (("applies_to", "where it applies"), ("params", "its parameters"),
                                ("statement", "what it requires")):
                if field in changed:
                    result["warnings"].append(f"changes {what} for the {guard} {old.id}, which may loosen it")
    if problems:
        result["status"] = "invalid"
    return result


def describe(override: dict, checked: dict) -> list[str]:
    """The old and new rule side by side, with the source quote and every warning, as the user sees them."""
    finding = override["drift"]["finding"]
    lines = [f"Override of {override['rule_id']} for Harness KB {override['pinned_kb_version']}, adopted "
             f"{override['date']} (issue: {override['link']}). Reason: {override['reason']}"]
    old, new = checked["old"], checked["new"]
    if old is not None:
        lines.append(f"  Pinned: {old.severity}: {old.statement}" + (f"; params {old.params}" if old.params else ""))
    if checked["retired"]:
        lines.append("  New: retired")
    elif new is not None:
        lines.append(f"  New: {new.severity}: {new.statement}" + (f"; params {new.params}" if new.params else ""))
        lines += [f"    {change['field']}: {change['old']!r} -> {change['new']!r}" for change in checked["changes"]]
    lines.append(f"  Source: {finding['source']}: \"{finding['quote']}\"")
    lines += [f"  Warning: it {warning}" for warning in checked["warnings"]]
    lines += [f"  Problem: {problem}" for problem in checked["problems"]]
    return lines


def apply_overrides(rules: list[Rule], overrides: list[dict]) -> list[Rule]:
    """The rules with each override's rule in place of the pinned one, or without it when retired."""
    by_id = {override["rule_id"]: override for override in overrides}
    applied = []
    for rule in rules:
        override = by_id.get(rule.id)
        if override is None:
            applied.append(rule)
        elif "rule" in override:
            applied.append(Rule.from_dict(copy.deepcopy(override["rule"])))
    return applied


def effective_rules(pinned: list[Rule], overrides: list[dict], confirmation: Confirmation | None) -> list[Rule]:
    """The rules that apply: the pinned rules, with adopted overrides only under a confirmation.

    The caller has checked that the confirmation is for the contract that
    shows these overrides; without one, overrides apply nowhere.
    """
    if overrides and confirmation is None:
        ids = ", ".join(sorted(override["rule_id"] for override in overrides))
        raise OverridesPending(f"rule overrides ({ids}) apply only after a contract confirmation in this session")
    return apply_overrides(pinned, overrides)
