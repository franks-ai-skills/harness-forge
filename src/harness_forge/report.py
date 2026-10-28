# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Render and persist the verification report."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from harness_forge.checks.outside import FORGE_URL
from harness_forge.contracts import WORKER_DISCLOSURE, WORKER_POLICY
from harness_forge.documents import safe_path, validate
from harness_forge.models import PartResult, VerificationRun
from harness_forge.provenance import describe, verifier



# Every outcome of a declared case has its own count: passed + failed + error + unavailable + not_run = declared.
OUTCOMES = ("passed", "failed", "error", "unavailable", "not_run")
_TARGET_NAMES = {"claude-code": "Claude Code", "codex": "Codex"}


def _outcome(result) -> str:
    if result.result == "pass":
        return "passed"
    if result.result == "fail":
        return "failed"
    if result.result == "error":
        return "error"
    if result.result == "unavailable" or (result.result == "not-run" and result.reason.startswith("unavailable")):
        return "unavailable"
    return "not_run"


def runtime_evidence(run: VerificationRun, cases: list[dict], harness: str) -> dict:
    """Each built part's declared activation and behavior cases per target harness, and what became of them (#103).

    Only ``harness``, the one this verification ran from, can have results: a declared case of another target
    harness is not run, since Forge launches no other harness. Each counted case names its source, this attempt
    or the attempt a kept result came from.
    """
    from harness_forge.verify import part_kind  # verify renders reports; imported here to avoid a cycle

    evidence: dict[str, dict] = {}
    for part in run.parts:
        if part_kind(part) not in ("build", "local-reuse"):
            continue
        part_id = part["id"]
        evidence[part_id] = {}
        for target in run.targets:
            ran = target == harness
            found = {r.case_id: r for r in run.runtime_results if r.part_id == part_id and r.harness == target}
            entry = {"ran": ran, "reason": None if ran else
                     f"verification ran from {_TARGET_NAMES.get(harness, harness)}; Forge launches no other harness"}
            for kind in ("activation", "behavior"):
                declared = [case for case in cases if case["part_id"] == part_id and (
                    (kind == "behavior" and case.get("kind") == "behavior" and target in case.get("harnesses", []))
                    or (kind == "activation" and case.get("kind") != "behavior"
                        and target in case.get("permitted_tools", {})))]
                counts = {"declared": [case["id"] for case in declared], **{name: [] for name in OUTCOMES},
                          "sources": {}}
                for case in declared:
                    result = found.get(case["id"]) if ran else None
                    outcome = _outcome(result) if result else "not_run"
                    counts[outcome].append(case["id"])
                    if result:
                        counts["sources"][case["id"]] = ("kept" if "(kept from " in result.reason
                                                         else "this attempt")
                entry[kind] = counts
            evidence[part_id][target] = entry
    return evidence


def unrun_harnesses(evidence: dict) -> dict[str, list[str]]:
    """Per part, the other target harnesses whose declared cases did not run because this verification ran from
    another one. A case that did not run in this harness is a runtime result with its own reason, not listed here."""
    found = {}
    for part_id, targets in evidence.items():
        other = [target for target, entry in targets.items()
                 if not entry["ran"] and (entry["activation"]["declared"] or entry["behavior"]["declared"])]
        if other:
            found[part_id] = other
    return found


def _evidence_summary(target: str, entry: dict) -> str:
    """One harness's evidence for a part in words: counts, or no cases declared, or not run with why."""
    name = _TARGET_NAMES.get(target, target)
    declared = len(entry["activation"]["declared"]) + len(entry["behavior"]["declared"])
    if not declared:
        return f"{name}: no cases declared"
    if not entry["ran"]:
        return f"{name}: not run ({declared} declared)"
    pieces = []
    for kind in ("activation", "behavior"):
        counts = entry[kind]
        if counts["declared"]:
            shown = ", ".join(f"{len(counts[name])} {name.replace('_', ' ')}" for name in OUTCOMES if counts[name])
            pieces.append(f"{kind} {len(counts['declared'])} declared: {shown}")
    return f"{name}: " + "; ".join(pieces)


def _checks(run: VerificationRun, case_id: str) -> str:
    """What a behavior case checks, as its row shows it; a case from before #110 names nothing."""
    checks = run.case_checks.get(case_id)
    return ", ".join(f"`{item}`" for item in checks) if checks else "names no requirement"


def _one_line(value: str) -> str:
    """Text from a worker or a run kept on one line, so that it cannot start a heading or a code block."""
    return re.sub(r"\r\n|\r|\n", " ", value)


def recorded_builds(run: VerificationRun) -> dict[str, dict]:
    """Each part's recorded build, with the outputs whose files or links this run found otherwise.

    Compared with the run's frozen inputs only: a path the record names is never
    opened for this. A missing output, a file where a link was or the reverse,
    and a changed link target count as changed. Reporting only: no verdict
    depends on it. A part without a build record is absent.
    """
    frozen = run.frozen_inputs
    builds = {}
    for part in run.parts:
        build = part.get("build")
        if not build:
            continue
        changed = []
        for path, value in sorted(build["outputs"].items()):
            kind, _, expected = value.partition(":")
            found = frozen["files"].get(path) if kind == "sha256" else frozen["symlinks"].get(path)
            other = frozen["symlinks"] if kind == "sha256" else frozen["files"]
            if found != expected or path in other:
                changed.append(path)
        builds[part["id"]] = {"forge": dict(build["forge"]), "outputs_changed": changed}
    return builds


def report_data(run: VerificationRun, results: list[PartResult]) -> dict:
    """The report's complete, schema-validated record of results and bound inputs."""
    frozen = run.frozen_inputs
    data = {
        "run_id": run.run_id, **({"attempt_id": run.attempt_id} if run.attempt_id else {}),
        "decision_digest": run.decision_digest, "contract_digest": run.contract_digest or None,
        "kb_version": run.kb_version or "unknown", "idea_digest": frozen["idea"], "rules_digest": frozen["rules"],
        "revision_digest": frozen["revision"], "confirmed_parts": list(run.confirmed_parts), "whole_idea": run.whole_idea,
        "answers": {part["id"]: part["answers"] for part in run.parts}, "scope": run.scope, "overrides": list(run.overrides),
        "parts": [result.to_dict() for result in results],
        "runtime_results": [asdict(result) for result in run.runtime_results],
        "worker_evidence": list(run.worker_evidence), "worker_violations": list(run.worker_violations),
        "tool_versions": dict(run.tool_versions), "file_hashes": dict(frozen["files"]), "symlinks": dict(frozen["symlinks"]),
        # What a later Review run compares with to find the parts whose inputs changed.
        "bound_inputs": dict(frozen), "scope_parts": run.scope_parts,
        "part_map": {"files": dict(run.part_files), "dirs": dict(run.part_dirs), "links": dict(run.part_links),
                     "requirement_parts": list(run.requirement_parts)},
    }
    builds = recorded_builds(run)
    if builds:
        data["builds"] = builds
    # Only a run with declared dependencies records them, so earlier reports keep their exact form.
    if run.part_dependencies:
        data["part_map"]["dependencies"] = dict(run.part_dependencies)
    if run.part_dependency_roots:
        data["part_map"]["dependency_roots"] = dict(run.part_dependency_roots)
    # Only a run with behavior cases records what they check, so earlier reports keep their exact form.
    if run.case_checks:
        data["case_checks"] = dict(run.case_checks)
    # Only a run that computed runtime evidence per harness records it, so earlier reports keep their exact form.
    if run.runtime_evidence:
        data["runtime_evidence"] = run.runtime_evidence
    # Only a run that found a changed judgment records them, so earlier reports keep their exact form.
    if run.changed_judgments:
        data["changed_judgments"] = [dict(item) for item in run.changed_judgments]
    # Only a run whose first review reply was rejected records its attempts.
    if run.review_attempts:
        data["review_attempts"] = [dict(attempt) for attempt in run.review_attempts]
    validate(data, "report")
    return data


def render_report(run: VerificationRun, results: list[PartResult]) -> str:
    """Markdown grouped by part, then rule id; runtime tests reported separately."""
    frozen = run.frozen_inputs
    lines = [
        "# Verification report", "",
        f"- Run: `{run.run_id}`",
        *([f"- Verification attempt: `{run.attempt_id}`"] if run.attempt_id else []),
        f"- Harness KB version: {run.kb_version or 'unknown'}",
        f"- [Harness Forge]({FORGE_URL}) verifier: {describe(verifier(run.tool_versions))}",
        f"- Confirmed configuration digest: {run.contract_digest or 'none (' + (run.unconfirmed_reason or 'no live confirmation') + ')'}",
        f"- Confirmed parts: {', '.join(run.confirmed_parts) or 'none'}",
        f"- Decision record digest: {run.decision_digest}",
        f"- Whole-idea answers and targets: `{json.dumps(run.whole_idea, sort_keys=True)}` (digest {frozen['idea']})",
        f"- Rule content digest: {frozen['rules']}",
        f"- Accepted Idea KB revision: {frozen['revision'] or 'none'}",
        f"- Review scope: {run.scope}",
        "- Rule overrides: " + (f"{len(run.overrides)} adopted: " + ", ".join(
            f"`{item['rule_id']}` (sha256 {item['digest']})" for item in run.overrides) if run.overrides else "none"),
        f"- Checker versions digest: {frozen['checkers']}",
        "",
        "## Worker isolation", "",
        f"Workers ran under `{WORKER_POLICY}` with isolation status `not-certified`. {WORKER_DISCLOSURE}",
        "",
    ]
    if run.worker_evidence:
        for evidence in run.worker_evidence:
            tools = evidence["observed_tools"]
            inventory = ", ".join(tools["tools"]) or "none" if tools["status"] == "observed" else "unknown"
            lines.append(f"- {evidence['role']} worker: {evidence['harness']} {evidence['harness_version']}, model "
                         f"{evidence['model']}, launcher {evidence['launcher']}, configuration {evidence['configuration_digest']}, "
                         f"controls: {'; '.join(evidence['configured_controls']) or 'none recorded'}, observed tools: {inventory}, "
                         f"unknowns: {'; '.join(evidence['unknowns']) or 'none recorded'}")
    else:
        lines.append("- No worker ran in this run.")
    for attempt in run.review_attempts:
        if attempt["outcome"] == "rejected":
            lines.append(f"- Review reply {attempt['attempt']} was rejected"
                         + (" and the review ran once more" if attempt["attempt"] == 1 else "")
                         + f": {_one_line(attempt['error'])}. The rejected reply is kept with the run's diagnostics, "
                         "not in this report.")
    for violation in run.worker_violations:
        lines.append(f"- Observed out-of-scope action: {violation}. Its output was discarded.")
    lines.append("")

    builds = recorded_builds(run)
    for result in results:
        build = builds.get(result.part_id)
        if build is None:
            builder = outputs = "unknown (no build record)"
        else:
            builder = f"Harness Forge {describe(build['forge'])}"
            outputs = ("changed since that build: " + ", ".join(build["outputs_changed"])
                       if build["outputs_changed"] else "match that build")
        evidence = run.runtime_evidence.get(result.part_id, {})
        # "verified" holds for the harness that ran; a target harness without runtime evidence is named (#103).
        unrun = [_TARGET_NAMES.get(target, target) for target, entry in evidence.items() if not entry["ran"]]
        ran = [_TARGET_NAMES.get(target, target) for target, entry in evidence.items() if entry["ran"]]
        scope = (f" in {', '.join(ran)}; no runtime evidence for {', '.join(unrun)}"
                 if result.harness_result == "verified" and ran and unrun else "")
        lines += [
            f"## Part {result.part_id}", "",
            f"- Harness result: {result.harness_result}" + (
                f" with {len(run.overrides)} override{'s' if len(run.overrides) > 1 else ''} "
                f"({', '.join(item['rule_id'] for item in run.overrides)})"
                if run.overrides and result.harness_result == "verified" else "") + scope,
            f"- Goal result: {result.goal_result}",
            f"- Status: {result.status}",
            f"- Outside dependencies: {', '.join(result.outside_dependencies) or 'none'}",
            f"- Recorded builder: {builder}",
            f"- Built outputs: {outputs}",
        ]
        if evidence:
            lines.append("- Runtime evidence: " + "; ".join(_evidence_summary(target, entry)
                                                             for target, entry in evidence.items()))
        answers = next((part["answers"] for part in run.parts if part["id"] == result.part_id), {})
        lines.append(f"- Confirmed answers: `{json.dumps(answers, sort_keys=True)}`")
        if result.goal_verdict:
            # A reviewer's text stays on its line, so that it cannot start a heading or a code block.
            reason = _one_line(result.goal_verdict["reason"])
            lines.append(f"- Goal verdict: {result.goal_verdict['verdict']} ({reason})")
        lines += ["", "| Rule | Severity | Check | Result | Reason | Citation | Sources |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for item in sorted(result.rule_results, key=lambda r: r["rule_id"]):
            info = run.rule_index.get(item["rule_id"], {})
            reason = _one_line(item.get("reason", "")).replace("|", "\\|")
            citation = _one_line(item.get("citation", "-")).replace("|", "\\|")
            lines.append(f"| `{item['rule_id']}` | {info.get('severity', '-')} | {item['check']} | {item['result']} | "
                         f"{reason} | {citation} | {', '.join(info.get('sources', [])) or '-'} |")
        contested = [rule_id for rule_id in sorted({r['rule_id'] for r in result.rule_results})
                     if run.rule_index.get(rule_id, {}).get("positions")]
        for rule_id in contested:
            lines.append(f"\nContested `{rule_id}`:")
            for position in run.rule_index[rule_id]["positions"]:
                lines.append(f"- {position['position']} ({', '.join(position['sources'])})")
        lines.append("")

    activation = [item for item in run.runtime_results if item.kind != "behavior"]
    behavior = [item for item in run.runtime_results if item.kind == "behavior"]
    if run.changed_judgments:
        # A fresh judgment can find what an earlier one missed: it counts as usual; the change is only shown (#108).
        lines += ["## Changed judgments on unchanged inputs", "",
                  "The same reviewed inputs got another verdict. The current one counts.", ""]
        for item in run.changed_judgments:
            earlier, current = item["earlier"], item["current"]
            where = (f"attempt `{earlier['attempt_id']}` of run `{earlier['run_id']}`" if earlier["attempt_id"]
                     else f"run `{earlier['run_id']}`")
            lines.append(f"- Part {item['part_id']}, `{item['rule_id']}`: {earlier['result']} in {where}, now "
                         f"{current['result']} ({_one_line(current['reason'])})")
        lines.append("")
    if run.runtime_evidence:
        lines += ["## Runtime evidence by harness", "",
                  "Verifying from another target harness attempts to run its declared cases; its environment or "
                  "plugin set can still leave them unavailable. Forge launches no other harness itself.", "",
                  "| Part | Harness | Ran | Activation | Behavior | Note |", "| --- | --- | --- | --- | --- | --- |"]
        for part_id, targets in run.runtime_evidence.items():
            for target, entry in targets.items():
                cells = []
                for kind in ("activation", "behavior"):
                    counts = entry[kind]
                    cells.append("none declared" if not counts["declared"] else
                                 f"{len(counts['declared'])} declared: " + ", ".join(
                                     f"{len(counts[name])} {name.replace('_', ' ')}" for name in OUTCOMES
                                     if counts[name]))
                lines.append(f"| {part_id} | {_TARGET_NAMES.get(target, target)} | {'yes' if entry['ran'] else 'no'} "
                             f"| {cells[0]} | {cells[1]} | {_one_line(entry['reason'] or '-')} |")
        lines.append("")
    # Declared cases of a target harness this verification did not run from, so that none is left out.
    unrun_cases = [(part_id, target, case_id, kind, entry["reason"])
                   for part_id, targets in run.runtime_evidence.items() for target, entry in targets.items()
                   if not entry["ran"] for kind in ("activation", "behavior") for case_id in entry[kind]["declared"]]
    lines += ["## Runtime tests", ""]
    if activation or any(kind == "activation" for *_, kind, _ in unrun_cases):
        lines += ["| Part | Case | Harness | Result | Observed | Reason |", "| --- | --- | --- | --- | --- | --- |"]
        for item in activation:
            lines.append(f"| {item.part_id} | {item.case_id} | {item.harness} {item.harness_version} | {item.result} | "
                         f"{_one_line(item.observed_activation or '-')} | {_one_line(item.reason)} |")
        for part_id, target, case_id, kind, reason in unrun_cases:
            if kind == "activation":
                lines.append(f"| {part_id} | {case_id} | {target} | not run | - | {_one_line(reason)} |")
        for harness in sorted({item.harness for item in activation if item.inventory != "observed"}):
            lines += ["", f"Inventory gap: {harness} does not report the skills, plugins and MCP servers it loaded, "
                          "so a user-level one that stayed loaded cannot be ruled out; its results are best-effort."]
    else:
        lines.append("none run")
    if behavior or any(kind == "behavior" for *_, kind, _ in unrun_cases):
        # What a built skill did once it ran: its writes, its records and what the harness could not show.
        lines += ["", "## Behavior tests", "",
                  "| Part | Case | Harness | Checks | Result | Reason | Changed | Failed assertions |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for item in behavior:
            details = item.details or {}
            cells = [_one_line("; ".join(details.get(key) or []) or "none").replace("|", "\\|")
                     for key in ("changes", "failed")]
            lines.append(f"| {item.part_id} | {item.case_id} | {item.harness} {item.harness_version} "
                         f"| {_checks(run, item.case_id)} | {item.result} "
                         f"| {_one_line(item.reason).replace('|', chr(92) + '|')} | {cells[0]} | {cells[1]} |")
        for part_id, target, case_id, kind, reason in unrun_cases:
            if kind == "behavior":
                lines.append(f"| {part_id} | {case_id} | {target} | {_checks(run, case_id)} | not run "
                             f"| {_one_line(reason)} | - | - |")
        for harness in sorted({item.harness for item in behavior}):
            gaps = sorted({gap for item in behavior if item.harness == harness for gap in (item.details or {})
                           .get("gaps") or []})
            if gaps:
                lines += ["", f"Observation gaps in {harness}: " + "; ".join(gaps) + "."]
        lines += ["", "An unavailable behavior case leaves the behavior unverified; it does not count as passed."]
    lines += ["", "## Checked files", "", "```"]
    lines += [f"{digest}  {_one_line(path)}" for path, digest in run.frozen_inputs["files"].items()]
    lines += [f"link  {_one_line(path)} -> {_one_line(target)}" for path, target in run.frozen_inputs["symlinks"].items()]
    lines += ["```", ""]
    # Every rendered line holds one line: text from a worker, the repository or a run, wherever it sits,
    # cannot start a heading, a code block or an HTML block. The report data keeps the originals.
    lines = [line[: len(line) - len(line.lstrip("\n"))] + _one_line(line.lstrip("\n")) for line in lines]
    lines += ["## Report data", "", "```json", json.dumps(report_data(run, results), indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)


def write_report(repository: Path, slug: str, rendered: str) -> Path:
    """Write .harness/reports/<slug>.md inside the target repository only."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError(f"invalid report slug {slug!r}")
    path = safe_path(repository, f".harness/reports/{slug}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return path
