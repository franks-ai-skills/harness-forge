"""Plan and write a portable skill from confirmed, accepted inputs.

The model writes the description and the domain procedure; this module
checks the inputs and bindings, renders the package from templates and
writes only inside the skill's own directories.
"""

from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path

import yaml

from harness_forge.checks.skills import NAME, PLACEHOLDER
from harness_forge.contracts import KIND_LABELS, contract_parts
from harness_forge.models import AcceptedRevision, Confirmation, Contract, Decision, PartResult, Rule

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "skill"
BUILDABLE = ("skills", "commands")


class BuilderUnavailable(RuntimeError):
    """This release has no builder for the part's chosen mechanism or outcome."""


class ConflictError(RuntimeError):
    """Existing content differs from the planned skill."""

    def __init__(self, message: str, diff: str):
        super().__init__(message)
        self.diff = diff


class SymlinkTarget(str):
    """A planned entry that is a relative symbolic link, not a file."""


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def _template(name: str, **values: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _limit(rules: list[Rule], rule_id: str, key: str, default: int) -> int:
    for rule in rules:
        if rule.id == rule_id and rule.params and key in rule.params:
            return rule.params[key]
    return default


SKILL_LABELS = {"must": "Always", "must-not": "Never", "should": "Prefer to", "should-not": "Avoid"}


def _rule_line(rule: dict) -> str:
    statement = rule["statement"]
    if len(statement) > 1 and statement[1].islower():
        statement = statement[0].lower() + statement[1:]
    return f"{SKILL_LABELS[rule['kind']]}: {statement}"


def _check_bindings(decision: Decision, part_id: str, revision: AcceptedRevision,
                    contract: Contract | None, confirmation: Confirmation | None) -> None:
    if confirmation is None or contract is None:
        raise ValueError("building needs the contract confirmed in this session")
    if part_id not in confirmation.part_ids:
        raise ValueError(f"part {part_id} is not confirmed in this session")
    if confirmation.contract_digest != contract.digest:
        raise ValueError("the confirmation is for another contract")
    recorded = decision.record.get("accepted_revision") or {}
    if recorded.get("digest") != revision.digest:
        raise ValueError("the decision record names another accepted revision")
    if contract.parts != contract_parts(decision, revision) or contract.overrides:
        raise ValueError("the confirmed contract no longer describes the current decision and revision")


def plan_skill(
    decision: Decision,
    part_id: str,
    revision: AcceptedRevision,
    rules: list[Rule],
    *,
    contract: Contract | None = None,
    confirmation: Confirmation | None = None,
    description: str = "",
    procedure: str = "",
) -> dict[str, str]:
    """Relative path → content for the skill package; symbolic links are SymlinkTarget values."""
    part = decision.part(part_id)
    if part["outcome"] == "reuse":
        raise BuilderUnavailable("a reused artifact stays read-only; it is checked once installed, never built")
    if part["outcome"] not in ("harness-only", "outside-alongside") or part["chosen"] not in BUILDABLE:
        raise BuilderUnavailable(f"builder unavailable for {part['chosen'] or part['outcome']} in this release")
    _check_bindings(decision, part_id, revision, contract, confirmation)

    name = part["slug"]
    if not NAME.match(name) or len(name) > _limit(rules, "skills.name.format", "max_chars", 64):
        raise ValueError(f"{name!r} is not a valid skill name")
    description = description.strip()
    if not description or "\n" in description or len(description) > _limit(rules, "skills.description.length", "max_chars", 1024):
        raise ValueError("the description must be one non-empty line within the harness limit")
    if not procedure.strip() or PLACEHOLDER.search(procedure) or PLACEHOLDER.search(description):
        raise ValueError("the procedure must be non-empty and free of Claude Code-only placeholders")

    frontmatter = {"name": name, "description": description}
    user_only = part["chosen"] == "commands"
    if user_only:
        frontmatter["disable-model-invocation"] = True
    base = f".agents/skills/{name}"
    title = name.replace("-", " ").capitalize()
    accepted = [entry["rule"] for entry in revision.rules if entry["part_id"] == part_id]
    body = _template("body.md.tmpl", title=title, procedure=procedure.rstrip() + "\n",
                     rules="\n".join(f"- {_rule_line(rule)}." for rule in accepted) or "- None accepted.")
    items = [f"- **{rule['id']}** ({KIND_LABELS[rule['kind']].lower()}): {rule['statement']}. Sources: {', '.join(rule['sources'])}"
             for rule in accepted]
    planned = {
        f"{base}/SKILL.md": "---\n" + yaml.dump(frontmatter, Dumper=_NoAliases, sort_keys=False, width=10_000) + "---\n" + body,
        f"{base}/references/requirements.md": _template("requirements.md.tmpl", digest=revision.digest,
                                                        items="\n".join(items) or "None accepted."),
    }
    if user_only:
        planned[f"{base}/agents/openai.yaml"] = _template("openai.yaml.tmpl")
    if "claude-code" in decision.record["targets"]:
        planned[f".claude/skills/{name}"] = SymlinkTarget(f"../../.agents/skills/{name}")
    return planned


def _skill_name(planned: dict[str, str]) -> str:
    names = {m.group(1) for key in planned if (m := re.fullmatch(r"\.agents/skills/([^/]+)/SKILL\.md", key))}
    if len(names) != 1:
        raise ValueError("a plan writes exactly one skill")
    return names.pop()


def _check_key(key: str, name: str, value: str) -> None:
    parts = key.split("/")
    if key.startswith("/") or ".." in parts or "" in parts or "\\" in key:
        raise ValueError(f"{key}: not a relative path inside the repository")
    if key == f".claude/skills/{name}":
        if not isinstance(value, SymlinkTarget) or value != f"../../.agents/skills/{name}":
            raise ValueError(f"{key}: must be the relative link to the canonical skill")
    elif not key.startswith(f".agents/skills/{name}/") or isinstance(value, SymlinkTarget):
        raise ValueError(f"{key}: outside the skill's directory")


def _check_no_link_escape(root: Path, key: str) -> None:
    current = root
    for part in key.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{current.relative_to(root)} is a symbolic link; refusing to write through it")


def write_skill(root: Path, planned: dict[str, str], *, replace: list[str] | tuple = ()) -> list[Path]:
    """Write the planned skill; keep matching files; refuse conflicts unless replaced."""
    root = Path(root).resolve()
    name = _skill_name(planned)
    for key, value in planned.items():
        _check_key(key, name, value)
        _check_no_link_escape(root, key)

    conflicts, diffs = [], []
    for key, value in planned.items():
        target = root / key
        if isinstance(value, SymlinkTarget):
            if target.is_symlink():
                if str(target.readlink()) != value:
                    conflicts.append(key)
                    diffs.append(f"{key}: links to {target.readlink()}, planned {value}")
            elif target.exists():
                conflicts.append(key)
                diffs.append(f"{key}: exists and is not a symbolic link")
        elif target.is_symlink() or (target.exists() and not target.is_file()):
            conflicts.append(key)
            diffs.append(f"{key}: exists and is not a regular file")
        elif target.exists():
            current = target.read_text(encoding="utf-8")
            if current != value and key not in replace:
                conflicts.append(key)
                diffs.append("".join(difflib.unified_diff(current.splitlines(True), value.splitlines(True),
                                                          f"existing/{key}", f"planned/{key}")))
    if conflicts:
        raise ConflictError(f"existing content differs: {', '.join(conflicts)}", "\n".join(diffs))

    written = []
    for key, value in planned.items():
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, SymlinkTarget):
            if not target.is_symlink():
                target.symlink_to(value)
        elif not target.exists() or target.read_text(encoding="utf-8") != value:
            target.write_text(value, encoding="utf-8")
        written.append(target)
    return written


def mark_built(decision: Decision, part_id: str) -> Decision:
    """A successful build sets the part to built; only verification sets verified."""
    record = copy.deepcopy(decision.record)
    for part in record["parts"]:
        if part["id"] == part_id:
            part["status"] = "built"
    return Decision(record=record, body=decision.body)


def unavailable_result(decision: Decision, part_id: str) -> PartResult:
    """Result for a part whose builder is unavailable: blocked, nothing built or assessed."""
    part = decision.part(part_id)
    return PartResult(
        part_id=part_id, status="blocked", harness_result="not applicable", goal_result="not assessed",
        outside_dependencies=[c["control"] for c in part["outside_controls"] if c["required_for_goal"]],
        rule_results=[], goal_verdict=None,
    )
