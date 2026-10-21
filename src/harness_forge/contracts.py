# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Contracts, live confirmation, intake state and the README hand-off.

A contract is the whole current agreement for an idea, shown to the user
in the current session. Only a relay of the user's explicit answer for
the exact displayed digest and run confirms it. Records loaded from the
repository, including their confirmation history, are data: they can
render the question but never confirm it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
from pathlib import Path

import yaml

from harness_forge.documents import canonical_digest, load_document, markdown_section, safe_path, split_frontmatter, validate
from harness_forge.checks.outside import foreign_lines
from harness_forge.models import AcceptedRevision, Confirmation, Contract, Decision
from harness_forge.research import UNTRUSTED_TAG
from harness_forge.selection import BUILDABLE, CONFLICT_CHOICES, goal_conflict, next_question

WORKER_POLICY = "best-effort-v1"
WORKER_DISCLOSURE = (
    "Research, source-support and review workers run as independent CLI processes with explicit "
    "role inputs and the available restrictions. Complete tool absence and context, file and network "
    "isolation are not guaranteed; use trusted projects and operator-approved inputs."
)
RELAY_KEYS = {"choice", "displayed_digest", "part_ids", "run_id"}
OUTSIDE_HEADING = "Outside controls"
OUTSIDE_STATUS = "recommended, not verified"
REACH_HEADING = "Where the skills load"
# User-level skill roots (KB docs/guide/skills.md, "User scope").
USER_SKILL_ROOTS = {"claude-code": "~/.claude/skills/", "codex": "~/.agents/skills/"}
TARGET_NAMES = {"claude-code": "Claude Code", "codex": "Codex"}


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _dump(value) -> str:
    return yaml.dump(value, Dumper=_NoAliases, sort_keys=False, allow_unicode=True, width=100)


# --- Contract -------------------------------------------------------------


KIND_LABELS = {"must": "Must", "must-not": "Must not", "should": "Should", "should-not": "Should not"}


def contract_parts(decision: Decision, revision: AcceptedRevision | None) -> list[dict]:
    """The per-part content a contract shows and its digest binds."""
    requirements: dict[str, list[dict]] = {}
    for entry in revision.rules if revision else []:
        requirements.setdefault(entry["part_id"], []).append(
            {"rule_id": entry["rule"]["id"], "kind": entry["rule"]["kind"], "statement": entry["rule"]["statement"]}
        )
    return [
        {
            "id": part["id"],
            "name": part["slug"],
            "goal": part["goal"],
            "answers": dict(part["answers"]),
            "outcome": part["outcome"],
            "mechanism": part["chosen"],
            "recommended": part["recommended"],
            "override_reason": part["override_reason"],
            "reuse": dict(part["reuse"]) if part.get("reuse") else None,
            "goal_conflict": dict(part["goal_conflict"]) if part.get("goal_conflict") else None,
            # The links decide the Review scope, so an edit after confirmation must change the contract.
            "connects_to": [dict(link) for link in part["connects_to"]],
            "outside_controls": [dict(control) for control in part["outside_controls"]],
            "requirements": requirements.get(part["id"], []),
        }
        for part in decision.record["parts"]
    ]


def _pairs(values: dict) -> str:
    """Confirmed answers as readable ``name: value`` pairs."""
    if not values:
        return "none"
    return "; ".join(f"{key}: {str(value).lower() if isinstance(value, bool) else value}"
                     for key, value in sorted(values.items()))


def part_ids_problem(record: dict) -> str | None:
    """Results, evidence and requirements are keyed by part id, so one id used twice hides a part."""
    seen = set()
    for part in record["parts"]:
        if part["id"] in seen:
            return f"part id {part['id']!r} is used twice"
        seen.add(part["id"])
    return None


def _consistency_problem(record: dict) -> str | None:
    """Facts stored twice must agree, or the contract would show both readings."""
    targets = set(record["targets"])
    expected = "both" if targets == {"claude-code", "codex"} else next(iter(targets))
    facts = record["facts"]
    if "target_set" in facts and facts["target_set"] != expected:
        return f"facts.target_set {facts['target_set']!r} contradicts targets {sorted(targets)}"
    if "reach" in facts and facts["reach"] != record["reach"]:
        return f"facts.reach {facts['reach']!r} contradicts reach {record['reach']!r}"
    parts = {part["id"] for part in record["parts"]}
    for part in record["parts"]:
        reuse = part.get("reuse") or {}
        if reuse.get("dependencies") and not reuse.get("path"):
            return f"part {part['id']} declares dependencies of a reused skill that is not in the repository"
    for candidate in record.get("research_candidates", []):
        if "part_id" in candidate and candidate["part_id"] not in parts:
            return f"research candidate {candidate['name']!r} names the unknown part {candidate['part_id']!r}"
    return None


def _part_text(part: dict) -> list[str]:
    lines = [f"Part {part['id']}: {part['goal']}", f"  Name: {part['name']}", f"  Answers: {_pairs(part['answers'])}",
             "  Linked to: " + (", ".join(f"{link['to']} ({link['how']})" for link in part["connects_to"]) or "none")]
    mechanism = part["mechanism"] or "no harness build"
    lines.append(f"  Outcome: {part['outcome']}; mechanism: {mechanism} (recommended: {part['recommended'] or 'none'})")
    if part["override_reason"]:
        lines.append(f"  Override reason: {part['override_reason']}")
    conflict = part.get("goal_conflict")
    if conflict:
        answers = {"keep": "kept", "recommended": "switched to the recommended mechanism",
                   "adjust-goal": "goal adjusted"}
        lines.append(f"  Goal conflict: {conflict['chosen']} is ruled out by {', '.join(conflict['rule_ids'])}; "
                     f"{answers[conflict['choice']]} ({conflict['reason']})")
    if part["reuse"]:
        reuse = part["reuse"]
        lines.append(f"  Reused artifact: {reuse['name']} from {reuse['source']}; license {reuse['license']}; "
                     f"maintenance {reuse['maintenance']}; form {reuse.get('variant') or 'not recorded'}; "
                     f"local path {reuse.get('path') or 'none, not available locally'}"
                     + (f"; skill home {reuse['skill_home']}" if reuse.get("skill_home") else "")
                     + (f"; reviewed dependencies {', '.join(reuse['dependencies'])}" if reuse.get("dependencies")
                        else ""))
    if part["outside_controls"]:
        lines.append("  Outside controls (recommended, not verified):")
        for control in part["outside_controls"]:
            role = "required for the goal" if control["required_for_goal"] else "precaution, not required for the goal"
            lines.append(
                f"    - {control['control']}: {role}; availability {control['availability']}; "
                f"prerequisites {', '.join(control['prerequisites'])}"
            )
    else:
        lines.append("  Outside controls: none")
    if part["requirements"]:
        lines.append("  Accepted requirements:")
        lines.extend(f"    - {item['rule_id']}: {KIND_LABELS[item['kind']]}: {item['statement']}" for item in part["requirements"])
    else:
        lines.append("  Accepted requirements: none")
    return lines


def _hint(previous: list[dict] | None, parts: list[dict]) -> list[str]:
    if previous is None:
        return []
    before = {part["id"]: part for part in previous}
    changes = []
    for part in parts:
        old = before.get(part["id"])
        if old is None:
            changes.append(f"part {part['id']} is new")
        else:
            fields = [key for key in part if part[key] != old.get(key)]
            if fields:
                changes.append(f"part {part['id']} changed: {', '.join(fields)}")
    changes += [f"part {part_id} was removed" for part_id in before if part_id not in {p['id'] for p in parts}]
    if not changes:
        return ["Hint from an untrusted record: no differences from the last recorded confirmation."]
    return ["Hint from an untrusted record (it can be edited like any file): " + "; ".join(changes) + "."]


_LONG_VALUE = 120


def _readable_control(control: str) -> str:
    """A configured control as the user reads it: a command as its words, a long value by its size and hash."""
    def short(value: str) -> str:
        if len(value) <= _LONG_VALUE:
            return value
        return f"<{len(value)} characters, sha256 {hashlib.sha256(value.encode()).hexdigest()[:12]}>"

    label, _, value = control.partition(": ")
    if label == "command":
        try:
            words = json.loads(value)
        except ValueError:
            words = None
        if isinstance(words, list):
            return "command: " + " ".join(shlex.quote(str(word)) if len(str(word)) <= _LONG_VALUE
                                          else short(str(word)) for word in words)
    return short(control)


def _worker_lines(worker_evidence: list[dict] | tuple) -> list[str]:
    """Each planned worker's configuration, the review worker's included, so the user sees what they approve."""
    if not worker_evidence:
        return []
    lines = ["Planned workers:"]
    for evidence in worker_evidence:
        lines.append(f"  - {evidence['role']}: {evidence['harness']} {evidence['harness_version']}, "
                     f"model {evidence['model']}, {evidence['launcher']}")
        lines += [f"      {_readable_control(control)}" for control in evidence["configured_controls"]]
    return lines


def render_contract(
    decision: Decision,
    revision: AcceptedRevision | None,
    overrides: list[dict],
    *,
    worker_evidence: list[dict] | tuple = (),
    previous: list[dict] | None = None,
    described: dict[str, list[str]] | None = None,
    drift: list[dict] | tuple = (),
) -> Contract:
    """The whole current contract with readable text and a binding digest.

    ``described`` maps an override's rule id to its description (old and new
    rule, source quote, warnings); without one the record itself is shown.
    ``drift`` is the repository's recorded drift, each entry with its
    finding and research slug: adding, editing or deleting one changes the
    contract, so a part a drift blocked cannot be unblocked by an edit.
    """
    validate(decision.record, "decision")
    duplicate = part_ids_problem(decision.record)
    if duplicate:
        raise ValueError(duplicate)
    problem = _consistency_problem(decision.record)
    if problem:
        raise ValueError(f"the decision record contradicts itself: {problem}")
    for evidence in worker_evidence:
        validate(evidence, "worker-evidence")
    parts = contract_parts(decision, revision)
    override_entries = [
        {"rule_id": override["rule_id"], "content": _dump(override), "digest": canonical_digest(override)}
        for override in overrides
    ]
    worker_policy = {
        "policy": WORKER_POLICY,
        "isolation_status": "not-certified",
        "disclosure": WORKER_DISCLOSURE,
        "evidence_digest": canonical_digest(list(worker_evidence)) if worker_evidence else None,
    }
    revision_digest = revision.digest if revision else None
    shown_drift = [_drift_shown(entry) for entry in drift]
    content = {
        "idea": decision.record["idea"],
        "goals": list(decision.record["goals"]),
        "picture": decision.record["picture"],
        "drift": shown_drift,
        "targets": decision.record["targets"],
        "reach": decision.record["reach"],
        "hosting": decision.record["hosting"],
        "facts": decision.record["facts"],
        "parts": parts,
        "revision_digest": revision_digest,
        "overrides": [{"rule_id": o["rule_id"], "digest": o["digest"]} for o in override_entries],
        "drift_decisions": [dict(entry) for entry in decision.record.get("drift_decisions", [])],
        "worker_policy": worker_policy,
    }
    # Only a record that names candidates binds them, so earlier contracts keep their digests.
    if decision.record.get("research_candidates"):
        content["research_candidates"] = [dict(candidate) for candidate in decision.record["research_candidates"]]
    digest = canonical_digest(content)

    record = decision.record
    hosting = record["hosting"]
    lines = [f"Contract for: {record['idea']}", f"Goals: {'; '.join(record['goals'])}",
             f"Picture: {record['picture']}", f"Targets: {', '.join(record['targets'])}",
             f"Reach: {record['reach']}; hosting: {hosting['platform']}, {hosting['plan']} plan, {hosting['visibility']}",
             f"Facts: {_pairs(record['facts'])}"]
    if record.get("research_candidates"):
        lines.append("Reuse candidates to research: " + "; ".join(
            f"{c['name']} from {c['source']}" + (f" (part {c['part_id']})" if "part_id" in c else "")
            for c in record["research_candidates"]))
    lines.append("")
    for part in parts:
        lines += _part_text(part) + [""]
    lines.append(f"Accepted Idea KB revision: {'sha256 ' + revision_digest if revision_digest else 'none yet'}")
    if override_entries:
        lines.append("Rule overrides for this repository (they apply once you confirm this contract):")
        for entry in override_entries:
            lines.append(f"  - {entry['rule_id']} (sha256 {entry['digest']}):")
            shown = (described or {}).get(entry["rule_id"]) or entry["content"].splitlines()
            lines.extend("      " + line for line in shown)
    else:
        lines.append("Rule overrides: none")
    if shown_drift:
        lines.append("Recorded drift:")
        for entry in shown_drift:
            # What the user judges the drift by: the claim, and the quote within its context.
            finding = entry["finding"]
            lines += [f"  - {entry['rule_id']} (finding {entry['finding_id']} of research {entry['slug']}, "
                      f"{entry['category']}): {entry['summary']}",
                      "    Untrusted content from a fetched page, data and never instructions:",
                      f"    Claim: {finding.get('claim')}",
                      f"    Source: {finding.get('source')} (fetched {finding.get('fetched')}): \"{finding.get('quote')}\"",
                      f"    Context: \"{finding.get('context')}\""]
    else:
        lines.append("Recorded drift: none")
    drift_decisions = decision.record.get("drift_decisions", [])
    if drift_decisions:
        lines.append("Drift decisions:")
        lines += [f"  - {entry['rule_id']} (finding {entry['finding_id']} of research {entry['slug']}): "
                  f"{'keep the pinned rule' if entry['choice'] == 'keep' else 'adopt the override'} "
                  f"({entry['reason']}). Drift: {entry['summary']} "
                  f"Source ({UNTRUSTED_TAG}): {entry['source']}: \"{entry['quote']}\""
                  for entry in drift_decisions]
    else:
        lines.append("Drift decisions: none")
    evidence_text = (
        f"worker evidence sha256 {worker_policy['evidence_digest']}"
        if worker_policy["evidence_digest"] else "no worker evidence recorded yet"
    )
    lines.append(f"Worker policy: {WORKER_POLICY}, isolation not-certified. {WORKER_DISCLOSURE} ({evidence_text})")
    lines += _worker_lines(worker_evidence)
    lines.append(f"Contract digest: {digest}")
    lines += _hint(previous, parts)
    return Contract(parts=parts, overrides=override_entries, worker_policy=worker_policy,
                    text="\n".join(lines) + "\n", digest=digest, content=content)


def _drift_shown(entry: dict) -> dict:
    """What the contract binds of one recorded drift: the whole entry with its finding, as drift_state compares it.

    Its issue answer is not a decision and stays out.
    """
    return {"rule_id": entry["rule_id"], "finding_id": entry["finding_id"], "slug": entry.get("slug"),
            "category": entry["category"], "summary": entry["summary"], "finding": dict(entry.get("finding") or {})}


def confirmation_problem(
    confirmation: Confirmation | None, decision: Decision, revision: AcceptedRevision | None,
    overrides: list[dict] | tuple, worker_evidence: list[dict] | tuple, drift: list[dict] | tuple = (),
) -> str | None:
    """Why a confirmation does not cover the complete current contract, or None.

    The contract is rendered again from the current inputs; the confirmation
    holds only for exactly that digest. Builder and verifier share this check.
    """
    if confirmation is None:
        return "no live confirmation in this session"
    try:
        current = render_contract(decision, revision, list(overrides), worker_evidence=worker_evidence, drift=drift)
    except ValueError as error:
        return str(error)
    if confirmation.contract_digest != current.digest:
        return "the confirmation is for another contract than the current one"
    return None


def confirm_contract(contract: Contract, relay: dict | None, run_id: str) -> Confirmation | None:
    """A Confirmation only for an explicit, current-session relay of this contract.

    The relay is built by the active agent after the user selects Confirm
    or answers unambiguously. This checks the bindings, not the human origin.
    """
    if not isinstance(relay, dict) or set(relay) != RELAY_KEYS:
        return None
    part_ids = relay["part_ids"]
    known = {part["id"] for part in contract.parts}
    if (
        relay["choice"] != "confirm"
        or relay["displayed_digest"] != contract.digest
        or relay["run_id"] != run_id
        or not isinstance(part_ids, list)
        # No part ids only for an idea before its split, which has no parts to confirm yet.
        or (not part_ids and bool(known))
        or len(set(part_ids)) != len(part_ids)
        or not set(part_ids) <= known
    ):
        return None
    return Confirmation(run_id=run_id, contract_digest=contract.digest, part_ids=sorted(part_ids))


# --- Goal conflicts ---------------------------------------------------------


def _linked(record: dict, part_id: str) -> list[str]:
    """The part and every part linked to it through connects_to, transitively."""
    graph = {part["id"]: set() for part in record["parts"]}
    for part in record["parts"]:
        for link in part["connects_to"]:
            if link["to"] in graph:
                graph[part["id"]].add(link["to"])
                graph[link["to"]].add(part["id"])
    seen, queue = set(), [part_id]
    while queue:
        current = queue.pop()
        if current not in seen:
            seen.add(current)
            queue.extend(graph[current] - seen)
    return sorted(seen)


def resolve_goal_conflict(decision: Decision, part_id: str, choice: str, adjusted_goal: str | None, reason: str, *,
                          rules: list, date: str) -> dict:
    """Apply the user's answer to a part's goal-conflict question.

    ``recommended`` switches to the recommended mechanism: its builder, the
    intake when this release cannot build it, or the README hand-off when
    an outside control replaces any mechanism. ``adjust-goal`` keeps the old
    goal as history and clears the answers the conflict rests on, so the
    part goes back through its questions, selection and research before
    anything is rebuilt. ``keep`` changes nothing: the error stays, and the
    part is never verified. Each answer is recorded with its reason; the
    changed record needs a new contract confirmation.
    """
    if choice not in CONFLICT_CHOICES:
        raise ValueError(f"choice must be one of {', '.join(CONFLICT_CHOICES)}, not {choice!r}")
    if not reason or not reason.strip():
        raise ValueError("record the user's reason for the answer")
    record = copy.deepcopy(decision.record)
    part = next(item for item in record["parts"] if item["id"] == part_id)
    conflict = goal_conflict(part, record["facts"], rules)
    if conflict is None or conflict["severity"] != "error":
        raise ValueError(f"part {part_id} has no goal conflict to resolve")
    failing = [item["rule_id"] for item in conflict["failing"]]
    part["goal_conflict"] = {"choice": choice, "reason": reason.strip(), "date": date, "chosen": part["chosen"],
                             "rule_ids": failing}
    result = {"invalidated_parts": [], "next_stage": "report", "remaining_error_ids": failing}
    if choice == "recommended":
        mechanism = conflict["recommended"]
        part.update(chosen=mechanism, override_reason=None)
        if mechanism in BUILDABLE:
            stage = "build"
        else:
            stage = "intake" if mechanism else "readme"
        result.update(invalidated_parts=_linked(record, part_id), next_stage=stage, remaining_error_ids=[])
    elif choice == "adjust-goal":
        adjusted = (adjusted_goal or "").strip()
        if not adjusted or adjusted == part["goal"]:
            raise ValueError("adjust-goal needs the adjusted goal, different from the current one")
        part["goal_history"] = list(part.get("goal_history", [])) + [
            {"goal": part["goal"], "replaced": date, "reason": reason.strip()}]
        part["goal"] = adjusted
        resting = {fact for rule in rules if rule.id in failing for fact in _when_facts(rule.params["when"])}
        part["answers"] = {key: value for key, value in part["answers"].items() if key not in resting}
        record["research"]["deep_digests"].pop(part_id, None)
        result.update(invalidated_parts=_linked(record, part_id), next_stage="questions", remaining_error_ids=[])
    if choice != "keep" and part["status"] != "blocked":
        part["status"] = "decided"
    return {"decision": Decision(record=record, body=decision.body), **result}


def _when_facts(predicate: dict) -> list[str]:
    (operator, operand), = predicate.items()
    if operator in ("all", "any"):
        return [fact for item in operand for fact in _when_facts(item)]
    if operator == "not":
        return _when_facts(operand)
    return [operand[0]]


# --- Intake state ---------------------------------------------------------


def _copy(decision: Decision) -> Decision:
    return Decision(record=copy.deepcopy(decision.record), body=decision.body)


def _part_ids(record: dict) -> list[str]:
    return [part["id"] for part in record["parts"]]


def _dedupe(links: list[dict], self_id: str) -> list[dict]:
    result = []
    for link in links:
        if link["to"] != self_id and link not in result:
            result.append(link)
    return result


def combine_parts(decision: Decision, keep: str, retire: str) -> Decision:
    """Combine two parts: ``keep`` stays, ``retire`` is retired and never reused."""
    record = _copy(decision).record
    ids = _part_ids(record)
    if keep == retire or keep not in ids or retire not in ids:
        raise ValueError(f"cannot combine {keep!r} and {retire!r}")
    retired = next(part for part in record["parts"] if part["id"] == retire)
    record["parts"] = [part for part in record["parts"] if part["id"] != retire]
    for part in record["parts"]:
        links = part["connects_to"] + (retired["connects_to"] if part["id"] == keep else [])
        part["connects_to"] = _dedupe([{**link, "to": keep if link["to"] == retire else link["to"]} for link in links], part["id"])
    record["retired_ids"] = record["retired_ids"] + [retire]
    return Decision(record=record, body=decision.body)


def split_part(decision: Decision, part_id: str, goals: list[str]) -> Decision:
    """Split a part into numbered parts (B → B1, B2); links to it go to each new part."""
    record = _copy(decision).record
    if part_id not in _part_ids(record) or len(goals) < 2:
        raise ValueError(f"cannot split {part_id!r} into {len(goals)} parts")
    letter = part_id[0]
    used = {int(i[1:]) for i in _part_ids(record) + record["retired_ids"] if i[0] == letter and i[1:]}
    numbers, candidate = [], 1
    while len(numbers) < len(goals):
        if candidate not in used:
            numbers.append(candidate)
        candidate += 1
    new_ids = [f"{letter}{number}" for number in numbers]
    index = _part_ids(record).index(part_id)
    original = record["parts"][index]
    children = []
    for new_id, goal in zip(new_ids, goals):
        child = copy.deepcopy(original)
        child.update({"id": new_id, "goal": goal, "slug": f"{original['slug']}-{new_id[1:]}", "status": "decided"})
        children.append(child)
    record["parts"][index:index + 1] = children
    for part in record["parts"]:
        links = []
        for link in part["connects_to"]:
            links += [{**link, "to": new_id} for new_id in new_ids] if link["to"] == part_id else [link]
        part["connects_to"] = _dedupe(links, part["id"])
    record["retired_ids"] = record["retired_ids"] + [part_id]
    return Decision(record=record, body=decision.body)


def new_part_id(decision: Decision) -> str:
    """The next unused letter; retired ids count as used."""
    used = {part_id[0] for part_id in _part_ids(decision.record) + decision.record["retired_ids"]}
    for code in range(ord("A"), ord("Z") + 1):
        if chr(code) not in used:
            return chr(code)
    raise ValueError("all part letters are used")


SURVEY_QUESTION = {
    "id": "survey_findings_used", "text": "Which survey findings should the intake use before splitting the idea?",
    "choices": ["use the findings listed above", "use none of them"],
}
SPLIT_QUESTION = {
    "id": "split_confirmed", "text": "Is this split into parts right, or should parts be combined, split or added?",
    "choices": ["confirm the split", "correct the split"],
}


def next_intake_question(decision: Decision, selection: dict) -> dict | None:
    """Exactly one next question, or None when every part is decided.

    Order: whole-idea questions, survey use, split confirmation, then
    each part's deciding questions.
    """
    record = decision.record
    facts = record["facts"]
    for question in selection["questions"]:
        fact = next(f for f in selection["facts"] if f["id"] == question["id"])
        if fact["scope"] == "idea" and question["id"] not in facts:
            return {"part_id": None, **question}
    if "survey_findings_used" not in record:
        return {"part_id": None, **SURVEY_QUESTION}
    if record.get("split_confirmed") is not True:
        return {"part_id": None, **SPLIT_QUESTION}
    for part in record["parts"]:
        question = next_question({"answers": part["answers"]}, facts, selection)
        if question is not None:
            return {"part_id": part["id"], **question}
    return None


# --- Decision records -----------------------------------------------------


def write_decision(root: Path, decision: Decision, *, slug: str, date: str) -> Path:
    """Write ``.harness/decisions/<date>-<slug>.md`` after validating the record."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"invalid slug {slug!r} or date {date!r}")
    validate(decision.record, "decision")
    duplicate = part_ids_problem(decision.record)
    if duplicate:
        raise ValueError(duplicate)
    path = safe_path(root, f".harness/decisions/{date}-{slug}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{_dump(decision.record)}---\n{decision.body.rstrip()}\n", encoding="utf-8")
    return path


def load_decision(path: Path, *, root: Path | None = None) -> Decision:
    """Load a decision record as data; it never confirms anything."""
    record = load_document(path, "decision", root=root)
    duplicate = part_ids_problem(record)
    if duplicate:
        raise ValueError(f"{path}: {duplicate}")
    _, body = split_frontmatter(Path(path).read_text(encoding="utf-8"))
    return Decision(record=record, body=body)


# --- README hand-off --------------------------------------------------------


# Conditions Forge words itself. The reviewer judges every recommendation by the first two, and by the
# third where a control lists it; a condition that only repeated a rule's statement got different verdicts
# for identical entries (owner test).
FORGE_CONDITIONS = {
    "outside.gate.real-refs": "It targets the real refs and resources it protects, such as the default and release "
                              "branches and tags or the production accounts",
    "outside.gate.periodic-verification": "Its configuration is checked periodically, because gate configuration "
                                          "drifts silently",
}
EVERY_CONTROL = ("outside.gate.real-refs", "outside.gate.periodic-verification")


def effective_prerequisites(control: dict) -> list[str]:
    """The conditions under which a recorded control works as a gate: its own, then those every control has.

    The README lists them, and verification judges exactly these against the control.
    """
    return list(dict.fromkeys([*control["prerequisites"], *EVERY_CONTROL]))
# The agent-config paths the KB's configuration and outside-gates guides name for owner review, per target
# harness; skills live in .agents/ for both.
AGENT_CONFIG = {"claude-code": ("`.claude/`", "`CLAUDE.md`", "`.mcp.json`"),
                "codex": ("`.codex/`", "`AGENTS.md`", "`AGENTS.override.md`")}


def _codeowners_condition(targets: list[str]) -> str:
    paths = ["`.agents/`", *dict.fromkeys(path for target in targets for path in AGENT_CONFIG.get(target, ()))]
    return (f"A CODEOWNERS entry names an owner for every file that configures the agent, including "
            f"{', '.join(paths)}, `.devcontainer/`, the CI workflow files and the CODEOWNERS file itself, and the "
            "branch rule requires code-owner review")


def _outside_section(decision: Decision, conditions: dict[str, str]) -> str:
    lines = [f"## {OUTSIDE_HEADING}", ""]
    entries = []
    conditions = {**conditions, **FORGE_CONDITIONS,
                  "outside.codeowners.agent-config": _codeowners_condition(decision.record["targets"])}
    for part in decision.record["parts"]:
        for control in part["outside_controls"]:
            role = "required for the goal" if control["required_for_goal"] else "precaution"
            entries.append(
                f"- `{control['control']}` (part {part['id']}): {OUTSIDE_STATUS}. {role.capitalize()}; "
                f"availability {control['availability']}. It works as a gate only when:"
            )
            for rule_id in effective_prerequisites(control):
                # A statement may span lines; the README holds it on one, as Forge reads it back.
                condition = " ".join((conditions.get(rule_id) or "").split())
                entries.append(f"  - {condition} (`{rule_id}`)" if condition else f"  - `{rule_id}`")
    lines += entries or ["None recommended."]
    return "\n".join(lines) + "\n"


def update_outside_section(readme: str, decision: Decision, conditions: dict[str, str] | None = None) -> str:
    """Replace or add the result README's Outside controls section only.

    `conditions` maps a prerequisite rule id to its statement, so the user
    reads what to configure instead of a rule id.
    """
    return _replace_section(readme, OUTSIDE_HEADING, _outside_section(decision, conditions or {}))


def update_readme(readme: str, decision: Decision, conditions: dict[str, str] | None = None) -> str:
    """The result README with the sections the intake owns: Outside controls and, for a wider reach, where skills load."""
    return _replace_section(update_outside_section(readme, decision, conditions), REACH_HEADING, _reach_section(decision))


def _reach_section(decision: Decision) -> str | None:
    """Built skills load only in this repository; say so when the user asked for a wider reach."""
    record = decision.record
    names = [part["slug"] for part in record["parts"]
             if part["outcome"] in ("harness-only", "outside-alongside") and part["chosen"] in BUILDABLE]
    if record["reach"] == "repository" or not names:
        return None
    locations = " and ".join(f"`{USER_SKILL_ROOTS[target]}<name>/` for {TARGET_NAMES[target]}"
                             for target in record["targets"] if target in USER_SKILL_ROOTS)
    if record["reach"] == "user":
        wanted, use = "all of your repositories", "To use them everywhere, copy each skill's folder to your user skills location"
    else:
        wanted, use = ("public", "This release packages no plugin: publish this repository, and each user copies "
                       "each skill's folder to their user skills location")
    return (f"## {REACH_HEADING}\n\n"
            f"Forge builds {', '.join(f'`{name}`' for name in names)} into this repository "
            f"(`.agents/skills/<name>/`), and a skill there loads only in this repository, although the idea's "
            f"reach is {wanted}. {use}: {locations}. Forge verifies the skills in this repository, not a copy.\n")


def _replace_section(readme: str, heading: str, section: str | None) -> str:
    """Replace, add or, without a section, remove the README section under ``## heading``.

    Forge writes only sections its own grammar accepts, so that it can read
    and replace them again.

    Only real headings count: one inside a code fence is the user's text, and
    the section ends at the next heading of level two or higher.
    """
    if section is not None and foreign_lines(section, heading):
        raise ValueError(f"Forge's '## {heading}' section would hold a line it cannot read back: "
                         f"{foreign_lines(section, heading)[0][:120]!r}")
    bounds = markdown_section(readme, heading)
    if bounds is None:
        if section is None:
            return readme
        appended = readme.rstrip("\n") + "\n\n" + section
        # The section must show as a heading of its own: an unclosed code fence or HTML block would take it in.
        if markdown_section(appended, heading) != (len(appended) - len(section), len(appended)):
            raise ValueError("README.md ends inside an unclosed code fence or HTML block; close it so that "
                             "Forge's section shows")
        return appended
    found, end = bounds
    # Only a section Forge wrote is replaced or removed; other text in it stays the user's.
    foreign = foreign_lines(readme[found:end], heading)
    if foreign:
        raise ValueError(f"README.md's '## {heading}' section holds lines Forge did not write; move them out of "
                         f"the section: {foreign[0][:120]!r}")
    tail = readme[end:]
    if section is None:
        return readme[:found].rstrip("\n") + "\n" + ("\n" + tail if tail else "")
    return readme[:found] + section + ("\n" + tail if tail else "")