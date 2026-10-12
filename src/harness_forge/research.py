"""Idea research: approved briefs, source filtering, support and acceptance.

Fetched pages are untrusted data. A finding reaches the user only when
its source passes the source policy, its quote appears verbatim in the
fetched text and inside its stated context, and a separate support
worker judged that the quote supports the claim. Only requirements the
user accepts enter the hashed Idea KB revision.
"""

from __future__ import annotations

import copy

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from harness_forge.checks import REGISTRY
from harness_forge.documents import canonical_digest, load_document, safe_path, schema_errors, validate
from harness_forge.models import AcceptedRevision, Decision, Finding, ResearchBrief, Rule

SNAPSHOT = Path(__file__).resolve().parents[2] / "snapshot"
X_HOSTS = ("x.com", "twitter.com")
X_MIRRORS = ("xcancel.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com")
_BLOCKING_DRIFT = ("security", "compatibility")


class _NoAliases(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


# --- Briefs -----------------------------------------------------------------


def make_brief(decision: Decision, pass_name: str, part_id: str | None) -> ResearchBrief:
    """The approved inputs for one research pass, bound by their digest."""
    if pass_name not in ("survey", "deep") or (pass_name == "deep") != (part_id is not None):
        raise ValueError("survey briefs have no part; deep briefs name one part")
    record = decision.record
    parts = [
        {"id": part["id"], "goal": part["goal"], "answers": part["answers"], "outcome": part["outcome"], "chosen": part["chosen"]}
        for part in record["parts"]
        if part_id is None or part["id"] == part_id
    ]
    if part_id is not None and not parts:
        raise ValueError(f"unknown part {part_id!r}")
    content = {
        "pass": pass_name,
        "goals": record["goals"],
        "picture": record["picture"],
        "targets": record["targets"],
        "reach": record["reach"],
        "parts": parts,
    }
    if part_id is not None:
        content["part_id"] = part_id
    return ResearchBrief(pass_name=pass_name, part_id=part_id, content=content, digest=canonical_digest(content))


def research_request(brief: ResearchBrief, run_id: str, request_id: str) -> dict:
    """The research worker's request: the approved brief and nothing else."""
    inputs = {"pass": brief.pass_name, "part_id": brief.part_id, "brief": brief.content, "brief_digest": brief.digest}
    request = {"role": "research", "run_id": run_id, "request_id": request_id,
               "input_digest": canonical_digest(inputs), "inputs": inputs}
    validate(request, "worker-request")
    return request


def worker_finding(item: dict, brief: ResearchBrief, finding_id: str) -> Finding:
    """A research worker's finding with the id, pass, part and brief digest Forge sent it."""
    return Finding.from_dict({**item, "id": finding_id, "pass": brief.pass_name, "part_id": brief.part_id,
                              "brief_digest": brief.digest})


def support_request(finding: Finding, run_id: str, request_id: str) -> dict:
    """The source-support worker's request: one finding's claim, quote and context, not its source or reasoning."""
    inputs = {"finding_id": finding.id, "claim": finding.claim, "quote": finding.quote, "context": finding.context}
    request = {"role": "source-support", "run_id": run_id, "request_id": request_id,
               "input_digest": canonical_digest(inputs), "inputs": inputs}
    validate(request, "worker-request")
    return request


def research_matches_brief(record: dict, brief: ResearchBrief) -> bool:
    """Existing research is reused only for exactly the same brief."""
    return any(
        entry["pass"] == brief.pass_name and entry["part_id"] == brief.part_id and entry["digest"] == brief.digest
        for entry in record.get("briefs", [])
    )


# --- Sources and findings -----------------------------------------------------


@lru_cache(maxsize=4)
def rejected_domains(snapshot: Path = SNAPSHOT) -> dict[str, frozenset[str]]:
    """Hosts and GitHub repositories the pinned source policy rejects."""
    text = (snapshot / "docs" / "sources.md").read_text(encoding="utf-8")
    section = text.split("## Rejected domains", 1)[1]
    hosts, github = set(X_HOSTS), set()
    for line in section.splitlines():
        if not line.startswith("| `") and not line.startswith("| GitHub"):
            continue
        first_cell = line.split("|")[1]
        for token in re.findall(r"`([^`]+)`", first_cell):
            (github if "/" in token else hosts).add(token.lower())
    return {"hosts": frozenset(hosts), "github": frozenset(github)}


def source_rejection(url: str) -> str | None:
    """Why the source policy rejects this URL, or None."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "sources must be fetched over https"
    host = parts.hostname.lower()
    policy = rejected_domains()

    def matches(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if any(matches(domain) for domain in X_HOSTS) or host.startswith("nitter.") or any(matches(m) for m in X_MIRRORS):
        return "the X platform and its mirrors are not sources"
    if any(matches(domain) for domain in policy["hosts"]):
        return f"{host} is a rejected domain"
    # Every GitHub host that serves a repository's content names it by its first two path segments.
    if matches("github.com") or host in ("raw.githubusercontent.com", "codeload.github.com"):
        repository = "/".join(parts.path.strip("/").split("/")[:2]).lower()
        if repository in policy["github"]:
            return f"GitHub {repository} is a rejected source"
    return None


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def finding_problem(finding: Finding, fetched_text: str | None, support: dict | None) -> str | None:
    """Why a finding must be dropped, or None when it may be shown."""
    rejection = source_rejection(finding.source)
    if rejection:
        return rejection
    if fetched_text is None:
        return "the source could not be fetched"
    quote, context, page = _normalized(finding.quote), _normalized(finding.context), _normalized(fetched_text)
    if not quote or quote not in page:
        return "the quote does not appear in the fetched text"
    if quote not in context or context not in page:
        return "the context does not contain the quote or does not appear in the fetched text"
    try:
        validate({"verdicts": [support]}, "source-support")
    except (ValueError, TypeError):
        return "no valid source-support verdict"
    if support["finding_id"] != finding.id:
        return "the support verdict names another finding"
    if support["verdict"] != "supported":
        return "the support worker judged that the quote does not support the claim"
    return None


def validate_finding(finding: Finding, fetched_text: str | None, support: dict | None) -> Finding | None:
    """The finding when it passes every check, else None (dropped)."""
    return None if finding_problem(finding, fetched_text, support) else finding


def match_support(findings: list[Finding], response: dict) -> dict[str, dict]:
    """Map a support worker's verdicts to findings: exactly one per finding."""
    validate(response, "source-support")
    expected = {finding.id for finding in findings}
    verdicts: dict[str, dict] = {}
    for verdict in response["verdicts"]:
        finding_id = verdict["finding_id"]
        if finding_id in verdicts:
            raise ValueError(f"duplicate verdict for {finding_id}")
        if finding_id not in expected:
            raise ValueError(f"verdict for unknown finding {finding_id}")
        verdicts[finding_id] = verdict
    missing = expected - set(verdicts)
    if missing:
        raise ValueError(f"no verdict for {', '.join(sorted(missing))}")
    return verdicts


# --- Acceptance -----------------------------------------------------------------


@lru_cache(maxsize=4)
def _catalog(snapshot: Path = SNAPSHOT) -> dict[str, dict]:
    catalog = load_document(snapshot / "checks" / "catalog.yaml", "catalog")
    return {checker["id"]: checker for checker in catalog["checkers"]}


def _idea_part(rule: Rule) -> str:
    segments = rule.id.split(".")
    if rule.concept != "idea" or len(segments) < 4 or segments[0] != "idea":
        raise ValueError(f"{rule.id}: idea rules are named idea.<slug>.<part>.<name> with concept idea")
    part_id = segments[2].upper()
    if not re.fullmatch(r"[A-Z][0-9]*", part_id):
        raise ValueError(f"{rule.id}: {segments[2]!r} is not a part id")
    return part_id


def _check_idea_rule(rule: Rule) -> None:
    if rule.check == "script":
        checker = _catalog().get(rule.checker)
        if checker is None or rule.checker not in REGISTRY or rule.checker == "selection.match":
            raise ValueError(f"{rule.id}: {rule.checker} is not a catalog checker idea rules may use")
        errors = schema_errors(rule.params or {}, checker["params"])
        if errors:
            raise ValueError(f"{rule.id}: invalid params for {rule.checker}: {errors[0]}")


def _require_unique(rule_ids: list[str], where: str) -> None:
    duplicates = sorted({rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1})
    if duplicates:
        raise ValueError(f"{where} use an id more than once: {', '.join(duplicates)}")


def _check_entry(part_id: str, rule: Rule) -> None:
    """An accepted requirement names its own part and uses only checkers idea rules may use."""
    owner = _idea_part(rule)
    if owner != part_id:
        raise ValueError(f"{rule.id} belongs to part {owner}, not {part_id}")
    _check_idea_rule(rule)


def revision_digest(brief_digests: dict[str, str], rules: list[dict]) -> str:
    return canonical_digest({"briefs": dict(sorted(brief_digests.items())), "rules": rules})


def accept_requirements(proposed: list[Rule], choices: dict, brief_digest: str,
                        part_id: str | None = None) -> AcceptedRevision:
    """One part's accepted requirements from the user's accept, edit or reject choices.

    A proposed requirement without a choice stays pending and is excluded.
    With ``part_id``, every accepted requirement must belong to that part;
    without it, they name one part. ``brief_digest`` is that part's deep
    brief; merge_revision adds the part to the repository's revision.
    """
    _require_unique([rule.id for rule in proposed], "proposed requirements")
    by_id = {rule.id: rule for rule in proposed}
    unknown = set(choices) - set(by_id)
    if unknown:
        raise ValueError(f"choices for unknown requirements: {', '.join(sorted(unknown))}")
    accepted = []
    for rule in proposed:
        choice = choices.get(rule.id)
        if choice is None or choice == "reject":
            continue
        if choice == "accept":
            final = rule
        elif isinstance(choice, dict) and set(choice) == {"edit"}:
            final = Rule.from_dict(choice["edit"])
            if final.id != rule.id:
                raise ValueError(f"an edit of {rule.id} must keep its id")
        else:
            raise ValueError(f"{rule.id}: choice must be accept, reject or an edit")
        owner = _idea_part(final)
        _check_entry(part_id or owner, final)
        accepted.append({"part_id": owner, "rule": final.to_dict()})
    if part_id is None:
        owners = {entry["part_id"] for entry in accepted}
        if len(owners) != 1:
            raise ValueError("name the part whose requirements these are")
        part_id = owners.pop()
    briefs = {part_id: brief_digest}
    return AcceptedRevision(rule_ids=[entry["rule"]["id"] for entry in accepted], rules=accepted,
                            brief_digests=briefs, digest=revision_digest(briefs, accepted))


def merge_revision(previous: AcceptedRevision | None, accepted: AcceptedRevision,
                   parts: set[str] | None = None) -> AcceptedRevision:
    """The repository's revision with one part's accepted requirements replaced by ``accepted``'s.

    With ``parts``, the decision's current parts, requirements of parts it no
    longer has (combined or split away) are dropped.
    """
    if previous is None:
        return accepted
    (part_id,) = accepted.brief_digests
    def kept(key: str) -> bool:
        return key != part_id and (parts is None or key in parts)
    rules = [entry for entry in previous.rules if kept(entry["part_id"])] + list(accepted.rules)
    briefs = {**{key: value for key, value in previous.brief_digests.items() if kept(key)},
              part_id: accepted.brief_digests[part_id]}
    return AcceptedRevision(rule_ids=[entry["rule"]["id"] for entry in rules], rules=rules, brief_digests=briefs,
                            digest=revision_digest(briefs, rules))


# --- Drift ----------------------------------------------------------------------


def drift_category(drift: dict) -> str:
    """Security rules always count as security drift, whatever research says."""
    rule_id = drift["rule_id"]
    if rule_id.startswith(("security.", "outside.")) or ".security." in rule_id:
        return "security"
    return drift.get("category", "ordinary")


def blocked_parts(drift: list[dict], applicable: dict[str, list[str]]) -> set[str]:
    """Parts blocked by unresolved security or compatibility drift."""
    blocking = {entry["rule_id"] for entry in drift if drift_category(entry) in _BLOCKING_DRIFT}
    return {part for part, rule_ids in applicable.items() if blocking & set(rule_ids)}


def record_drift(record: dict, *, finding_id: str, rule_id: str, category: str, summary: str,
                 pinned_ids: set[str]) -> dict:
    """The research record with one more drift entry: a kept, supported finding that contradicts a pinned rule.

    A security rule's drift is always security drift. Recording the same
    rule and finding again replaces the entry.
    """
    finding = next((item for item in record["findings"] if item["id"] == finding_id), None)
    if finding is None:
        raise ValueError(f"finding {finding_id} is not a kept finding of research record {record['slug']}")
    if finding_id not in supported_findings(record):
        raise ValueError(f"finding {finding_id} did not pass both checks: its quote and a supported verdict")
    if rule_id not in pinned_ids:
        raise ValueError(f"{rule_id} is not a pinned Harness KB rule")
    if category not in ("security", "compatibility", "ordinary"):
        raise ValueError(f"category must be security, compatibility or ordinary, not {category!r}")
    entry = {"rule_id": rule_id, "finding_id": finding_id, "summary": summary,
             "category": drift_category({"rule_id": rule_id, "category": category})}
    updated = copy.deepcopy(record)
    updated["drift"] = [item for item in updated["drift"]
                        if (item["rule_id"], item["finding_id"]) != (rule_id, finding_id)] + [entry]
    return updated


def drift_digest(drift: dict) -> str:
    """What a keep decision is given for: the drift entry, its finding and the research record holding it."""
    return canonical_digest({key: drift.get(key) for key in
                             ("rule_id", "finding_id", "summary", "category", "finding", "slug")})


def drift_state(drift: dict, decision_record: dict, overrides: list[dict]) -> str:
    """``kept``, ``adopted`` or ``open``: what the user decided about one drift entry.

    ``drift`` carries its ``finding`` and ``slug``. Keeping the pinned rule
    is a decision in the decision record, bound to the drift's content by
    drift_digest: a changed drift is open again. Adopting is an override of
    that rule from that finding. Both count only under a confirmation of the contract that shows
    them, which the caller holds. A filed or pending issue decides nothing.
    """
    finding = drift.get("finding") or {}
    shown = {"rule_id": drift.get("rule_id"), "finding_id": drift.get("finding_id"), "slug": drift.get("slug"),
             "summary": drift.get("summary"), "source": finding.get("source"), "quote": finding.get("quote")}
    for entry in decision_record.get("drift_decisions", []):
        # The contract shows the decision's copy of the drift: it must be the drift the digest names.
        if (entry["choice"] == "keep" and entry.get("drift_digest") == drift_digest(drift)
                and {key: entry.get(key) for key in shown} == shown):
            return "kept"
    for override in overrides:
        if override["rule_id"] == drift["rule_id"] and override["drift"]["finding"] == drift.get("finding"):
            return "adopted"
    return "open"


def can_adopt(drift: dict, support: dict | None) -> bool:
    """A drift correction may be adopted only after both source checks passed."""
    return (
        support is not None
        and support.get("finding_id") == drift["finding_id"]
        and support.get("quote_check") == "pass"
        and support.get("verdict") == "supported"
    )


# --- Records --------------------------------------------------------------------


def supported_findings(record: dict) -> dict[str, dict]:
    """The record's findings that passed both checks, by id: a passed quote check and a supported verdict,
    given for exactly the finding's current content."""
    findings = {finding["id"]: finding for finding in record["findings"]}
    return {entry["finding_id"]: findings[entry["finding_id"]] for entry in record["support"]
            if entry["quote_check"] == "pass" and entry["verdict"] == "supported" and entry["finding_id"] in findings
            and entry.get("finding_digest") == canonical_digest(findings[entry["finding_id"]])}


def _check_record(record: dict, where: str) -> None:
    _require_unique([finding["id"] for finding in record["findings"]], f"{where}research findings")
    _require_unique([entry["finding_id"] for entry in record["support"]], f"{where}research support entries")


def load_research(path: Path, *, root: Path | None = None) -> dict:
    """A research record, with one support entry per finding at most."""
    record = load_document(path, "research", root=root)
    _check_record(record, f"{path}: ")
    return record


def write_research(root: Path, record: dict, body: str) -> Path:
    validate(record, "research")
    _check_record(record, "")
    path = safe_path(root, f".harness/research/{record['slug']}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = yaml.dump(record, Dumper=_NoAliases, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(f"---\n{frontmatter}---\n{body.rstrip()}\n", encoding="utf-8")
    return path


def write_revision(root: Path, slug: str, revision: AcceptedRevision) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError(f"invalid slug {slug!r}")
    value = {"briefs": dict(sorted(revision.brief_digests.items())), "digest": revision.digest, "rules": revision.rules}
    validate(value, "revision")
    path = safe_path(root, f".harness/research/{slug}.rules.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(value, Dumper=_NoAliases, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return path


def load_revision(path: Path, *, root: Path | None = None) -> AcceptedRevision:
    """Load a revision and recompute its digest; a mismatch is an edit.

    A revision accepted before per-part briefs has one ``brief_digest``. It
    loads as its one part's, keeping the digest its decision records; the
    next acceptance writes the current format.
    """
    value = load_document(path, "revision", root=root)
    if "brief_digest" in value:
        digest = canonical_digest({"brief_digest": value["brief_digest"], "rules": value["rules"]})
        owners = {entry["part_id"] for entry in value["rules"]}
        if digest == value["digest"] and len(owners) != 1:
            raise ValueError(f"{path}: an older revision names no single part; accept its part's requirements again")
        value = {**value, "briefs": {owner: value["brief_digest"] for owner in owners}}
    else:
        digest = revision_digest(value["briefs"], value["rules"])
    if digest != value["digest"]:
        raise ValueError(f"{path}: accepted requirements changed after acceptance")
    unbriefed = sorted({entry["part_id"] for entry in value["rules"]} - set(value["briefs"]))
    if unbriefed:
        raise ValueError(f"{path}: requirements of part {', '.join(unbriefed)} without the brief they were accepted for")
    _require_unique([entry["rule"]["id"] for entry in value["rules"]], f"{path}: accepted requirements")
    for entry in value["rules"]:
        try:
            _check_entry(entry["part_id"], Rule.from_dict(entry["rule"]))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"{path}: {error}") from error
    return AcceptedRevision(rule_ids=[entry["rule"]["id"] for entry in value["rules"]], rules=value["rules"],
                            brief_digests=dict(value["briefs"]), digest=digest)
