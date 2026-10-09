"""Contracts, live confirmation, intake state and the README hand-off.

A contract is the whole current agreement for an idea, shown to the user
in the current session. Only a relay of the user's explicit answer for
the exact displayed digest and run confirms it. Records loaded from the
repository, including their confirmation history, are data: they can
render the question but never confirm it.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

from harness_forge.documents import canonical_digest, load_document, safe_path, split_frontmatter, validate
from harness_forge.models import AcceptedRevision, Confirmation, Contract, Decision
from harness_forge.selection import next_question

WORKER_POLICY = "best-effort-v1"
WORKER_DISCLOSURE = (
    "Research, source-support and review workers run as independent CLI processes with explicit "
    "role inputs and the available restrictions. Complete tool absence and context, file and network "
    "isolation are not guaranteed; use trusted projects and operator-approved inputs."
)
RELAY_KEYS = {"choice", "displayed_digest", "part_ids", "run_id"}
OUTSIDE_HEADING = "Outside controls"
OUTSIDE_STATUS = "recommended, not verified"


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
            "goal": part["goal"],
            "outcome": part["outcome"],
            "mechanism": part["chosen"],
            "recommended": part["recommended"],
            "override_reason": part["override_reason"],
            "outside_controls": [dict(control) for control in part["outside_controls"]],
            "requirements": requirements.get(part["id"], []),
        }
        for part in decision.record["parts"]
    ]


def _part_text(part: dict) -> list[str]:
    lines = [f"Part {part['id']}: {part['goal']}"]
    mechanism = part["mechanism"] or "no harness build"
    lines.append(f"  Outcome: {part['outcome']}; mechanism: {mechanism} (recommended: {part['recommended'] or 'none'})")
    if part["override_reason"]:
        lines.append(f"  Override reason: {part['override_reason']}")
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


def render_contract(
    decision: Decision,
    revision: AcceptedRevision | None,
    overrides: list[dict],
    *,
    worker_evidence: list[dict] | tuple = (),
    previous: list[dict] | None = None,
) -> Contract:
    """The whole current contract with readable text and a binding digest."""
    validate(decision.record, "decision")
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
    content = {
        "idea": decision.record["idea"],
        "targets": decision.record["targets"],
        "parts": parts,
        "revision_digest": revision_digest,
        "overrides": [{"rule_id": o["rule_id"], "digest": o["digest"]} for o in override_entries],
        "worker_policy": worker_policy,
    }
    digest = canonical_digest(content)

    lines = [f"Contract for: {decision.record['idea']}", f"Targets: {', '.join(decision.record['targets'])}", ""]
    for part in parts:
        lines += _part_text(part) + [""]
    lines.append(f"Accepted Idea KB revision: {'sha256 ' + revision_digest if revision_digest else 'none yet'}")
    if override_entries:
        lines.append("Rule overrides for this repository (they block the parts until their adoption flow exists):")
        for entry in override_entries:
            lines.append(f"  - {entry['rule_id']} (sha256 {entry['digest']}):")
            lines.extend("      " + line for line in entry["content"].splitlines())
    else:
        lines.append("Rule overrides: none")
    evidence_text = (
        f"worker evidence sha256 {worker_policy['evidence_digest']}"
        if worker_policy["evidence_digest"] else "no worker evidence recorded yet"
    )
    lines += [
        f"Worker policy: {WORKER_POLICY}, isolation not-certified. {WORKER_DISCLOSURE} ({evidence_text})",
        f"Contract digest: {digest}",
    ]
    lines += _hint(previous, parts)
    return Contract(parts=parts, overrides=override_entries, worker_policy=worker_policy,
                    text="\n".join(lines) + "\n", digest=digest)


def confirmation_problem(
    confirmation: Confirmation | None, decision: Decision, revision: AcceptedRevision | None,
    overrides: list[dict] | tuple, worker_evidence: list[dict] | tuple,
) -> str | None:
    """Why a confirmation does not cover the complete current contract, or None.

    The contract is rendered again from the current inputs; the confirmation
    holds only for exactly that digest. Builder and verifier share this check.
    """
    if confirmation is None:
        return "no live confirmation in this session"
    if overrides:
        return "rule overrides block confirmation until their adoption flow exists"
    current = render_contract(decision, revision, list(overrides), worker_evidence=worker_evidence)
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
        or not part_ids
        or len(set(part_ids)) != len(part_ids)
        or not set(part_ids) <= known
        or contract.overrides
    ):
        return None
    return Confirmation(run_id=run_id, contract_digest=contract.digest, part_ids=sorted(part_ids))


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
    path = safe_path(root, f".harness/decisions/{date}-{slug}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{_dump(decision.record)}---\n{decision.body.rstrip()}\n", encoding="utf-8")
    return path


def load_decision(path: Path, *, root: Path | None = None) -> Decision:
    """Load a decision record as data; it never confirms anything."""
    record = load_document(path, "decision", root=root)
    _, body = split_frontmatter(Path(path).read_text(encoding="utf-8"))
    return Decision(record=record, body=body)


# --- README hand-off --------------------------------------------------------


def _outside_section(decision: Decision) -> str:
    lines = [f"## {OUTSIDE_HEADING}", ""]
    entries = []
    for part in decision.record["parts"]:
        for control in part["outside_controls"]:
            role = "required for the goal" if control["required_for_goal"] else "precaution"
            entries.append(
                f"- `{control['control']}` (part {part['id']}): {OUTSIDE_STATUS}. {role.capitalize()}; "
                f"availability {control['availability']}. Prerequisites: {', '.join(control['prerequisites'])}."
            )
    lines += entries or ["None recommended."]
    return "\n".join(lines) + "\n"


def update_outside_section(readme: str, decision: Decision) -> str:
    """Replace or add the result README's Outside controls section only."""
    section = _outside_section(decision)
    match = re.search(rf"^## {re.escape(OUTSIDE_HEADING)}[ \t]*\n", readme, re.M)
    if not match:
        return readme.rstrip("\n") + "\n\n" + section
    following = re.search(r"^## ", readme[match.end():], re.M)
    end = match.end() + following.start() if following else len(readme)
    tail = readme[end:]
    return readme[: match.start()] + section + ("\n" + tail if tail else "")
