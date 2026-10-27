# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Requirement acceptance bound to what the user was shown (#100).

compare-requirements gives every requirement decision an id, a display the agent shows as it is, and a digest
over that display and the complete rule. accept then needs a relay that names, for each decision, the digest of
what was shown; a stale or contradicting decision refuses the whole acceptance. Like the confirmation relay, it
binds the content an agent says it presented; it does not prove what the interface displayed.
"""

from __future__ import annotations

import json

from harness_forge.contracts import KIND_LABELS
from harness_forge.documents import canonical_digest
from harness_forge.summary import code, fenced, text

GROUPS = ("unchanged", "unsupported", "changed", "new", "removed")
EARLIER = ("unchanged", "unsupported", "changed", "removed")
GROUP_ID = "group:unchanged"
UNTRUSTED_LINE = "Untrusted content from a fetched page"
_KEYS = {"accept": {"choice", "item_ids", "displayed_digest"}, "reject": {"choice", "item_ids", "displayed_digest"},
         "drop": {"choice", "item_ids", "displayed_digest"},
         "edit": {"choice", "item_ids", "displayed_digest", "edited_digest"}}
_HEADINGS = {"unchanged": "Accepted before, unchanged", "unsupported": "Accepted before, a source lost its support",
             "changed": "Accepted before, changed", "new": "New", "removed": "Accepted before, no longer proposed",
             "edited": "Your edit"}


def _value(value) -> str:
    return text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def display(group: str, rule: dict, *, differences: dict | None = None, findings: list[dict] = ()) -> str:
    """The Markdown the agent shows before asking about one requirement: every field acceptance keeps."""
    lines = [f"**{_HEADINGS[group]}: {code(rule['id'])}**", "",
             f"- {KIND_LABELS.get(rule['kind'], text(rule['kind']))}: {text(rule['statement'], '  ')}",
             f"- Severity {text(rule['severity'])}, check {text(rule['check'])}, concept {text(rule['concept'])}",
             "- Sources: " + (", ".join(text(source) for source in rule["sources"]) or "none")]
    rest = {key: value for key, value in rule.items()
            if key not in ("id", "kind", "statement", "severity", "check", "concept", "sources")}
    if rest:
        lines.append(f"- Other fields: {_value(rest)}")
    for field, change in (differences or {}).items():
        lines.append(f"- Changed {text(field)}: before {_value(change['before'])}, after {_value(change['after'])}")
    for finding in findings:
        lines += ["", f"{UNTRUSTED_LINE}, {text(finding['source'])}:", "",
                  fenced(f"Claim: {finding['claim']}\nQuote: {finding['quote']}")]
    return "\n".join(lines) + "\n"


def _digest(shown: str, rule: dict) -> str:
    return canonical_digest({"display": shown, "rule": rule})


def annotate(groups: dict) -> dict:
    """Each item with its ``item_id``, ``display`` and ``digest``; the unchanged ones also as one ``group``."""
    result = {key: value for key, value in groups.items() if key not in GROUPS}
    for name in GROUPS:
        items = []
        for item in groups.get(name, []):
            rule = item["requirement"]
            shown = display(name, rule, differences=item.get("differences"), findings=item.get("findings", []))
            items.append({**item, "item_id": f"{name}:{rule['id']}", "display": shown, "digest": _digest(shown, rule)})
        result[name] = items
    unchanged = result["unchanged"]
    if unchanged:
        shown = "\n".join([f"**{len(unchanged)} requirements accepted before, unchanged, asked about together**", ""]
                          + [item["display"] for item in unchanged])
        result["group"] = {"item_id": GROUP_ID, "rule_ids": [item["requirement"]["id"] for item in unchanged],
                           "display": shown,
                           "digest": canonical_digest({"display": shown, "items": [i["digest"] for i in unchanged]})}
    else:
        result["group"] = None
    return result


def edited(rule: dict) -> dict:
    """How an edit is shown back before it is recorded, and its digest."""
    shown = display("edited", rule)
    return {"rule_id": rule["id"], "display": shown, "digest": _digest(shown, rule)}


def compact(annotated: dict) -> dict:
    """Ids and digests only, for an output too large to hold every item: --item gives one in full."""
    result = {name: [{"item_id": item["item_id"], "rule_id": item["requirement"]["id"], "digest": item["digest"]}
                     for item in annotated[name]] for name in GROUPS}
    group = annotated["group"]
    result["group"] = {key: group[key] for key in ("item_id", "rule_ids", "digest")} if group else None
    return result


def find_item(annotated: dict, item_id: str) -> dict | None:
    if item_id == GROUP_ID:
        return annotated["group"]
    return next((item for name in GROUPS for item in annotated[name] if item["item_id"] == item_id), None)


def _expected(choice) -> str | None:
    if choice in ("accept", "reject"):
        return choice
    if isinstance(choice, dict) and set(choice) == {"edit"}:
        return "edit"
    return None


def check(annotated: dict, choices: dict, relay, part_id: str) -> list[str]:
    """Every problem with the relay for these choices; empty when the acceptance is bound to what was shown.

    A proposal with a choice needs a decision whose digest is the current one and whose choice agrees. Every
    requirement accepted before needs an explicit decision, also when it is no longer proposed (drop), so that
    silence never removes one. A new proposal without a choice stays pending.
    """
    if not isinstance(relay, dict) or set(relay) != {"run_id", "part_id", "decisions"}:
        return ["the acceptance relay must hold exactly run_id, part_id and decisions"]
    problems = []
    if not isinstance(relay["run_id"], str) or not relay["run_id"]:
        problems.append("the acceptance relay's run_id must be a non-empty string")
    if relay["part_id"] != part_id:
        problems.append(f"the acceptance relay is for part {relay['part_id']!r}, not {part_id}")
    if not isinstance(relay["decisions"], list):
        return problems + ["the acceptance relay's decisions must be a list"]
    decided: dict[str, tuple[dict, dict]] = {}  # item id -> (decision, item)
    for number, decision in enumerate(relay["decisions"], start=1):
        where = f"decision {number}"
        if not isinstance(decision, dict) or decision.get("choice") not in _KEYS or set(decision) != _KEYS[
                decision["choice"]]:
            problems.append(f"{where}: needs the fields of accept, reject, drop or edit (edit adds edited_digest)")
            continue
        item_ids = decision["item_ids"]
        if not isinstance(item_ids, list) or len(item_ids) != 1 or not isinstance(item_ids[0], str):
            problems.append(f"{where}: item_ids must name exactly one item or the unchanged group")
            continue
        item = find_item(annotated, item_ids[0])
        if item is None:
            problems.append(f"{where}: {item_ids[0]} is not an item of this comparison")
            continue
        if decision["displayed_digest"] != item["digest"]:
            problems.append(f"{where}: {item_ids[0]} changed since it was shown; show it again and ask again")
            continue
        if item_ids[0] in decided:
            problems.append(f"{where}: {item_ids[0]} is decided twice")
            continue
        if item_ids[0] == GROUP_ID and decision["choice"] != "accept":
            problems.append(f"{where}: the unchanged group can only be accepted together; ask one at a time instead")
            continue
        if decision["choice"] == "drop" and not item_ids[0].startswith("removed:"):
            problems.append(f"{where}: only a requirement no longer proposed can be dropped")
            continue
        if item_ids[0].startswith("removed:") and decision["choice"] != "drop":
            problems.append(f"{where}: {item_ids[0]} is no longer proposed: drop it, or propose it again")
            continue
        decided[item_ids[0]] = (decision, item)
    # What each proposal's decision is: its own, or the group's.
    by_rule: dict[str, dict] = {}
    for item_id, (decision, item) in decided.items():
        if item_id == GROUP_ID:
            for rule_id in item["rule_ids"]:
                if rule_id in by_rule:
                    problems.append(f"{rule_id} is decided both alone and with the unchanged group")
                by_rule[rule_id] = decision
        elif not item_id.startswith("removed:"):
            rule_id = item["requirement"]["id"]
            if rule_id in by_rule:
                problems.append(f"{rule_id} is decided both alone and with the unchanged group")
            by_rule[rule_id] = decision
    proposed = {item["requirement"]["id"]: name for name in GROUPS if name != "removed" for item in annotated[name]}
    for rule_id, choice in choices.items():
        if rule_id not in proposed:
            continue  # accept_requirements names an unknown requirement itself
        decision = by_rule.get(rule_id)
        expected = _expected(choice)
        if decision is None:
            problems.append(f"{rule_id}: the choice has no decision in the acceptance relay")
        elif expected != decision["choice"]:
            problems.append(f"{rule_id}: the choices file says {expected or choice!r}, the relay {decision['choice']}")
        elif expected == "edit" and decision["edited_digest"] != edited(choice["edit"])["digest"]:
            problems.append(f"{rule_id}: the edit differs from the one shown back; show it and ask again")
    for rule_id, decision in by_rule.items():
        if rule_id not in choices:
            problems.append(f"{rule_id}: the relay decides it, but the choices file has no choice")
    for name in EARLIER:
        for item in annotated[name]:
            rule_id = item["requirement"]["id"]
            if name == "removed":
                if item["item_id"] not in decided:
                    problems.append(f"{rule_id}: accepted before and no longer proposed; drop it explicitly or "
                                    "propose it again")
            elif rule_id not in by_rule:
                problems.append(f"{rule_id}: accepted before; it needs an explicit accept, edit or reject")
    return problems
