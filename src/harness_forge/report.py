"""Render and persist the verification report."""

from __future__ import annotations

import re
from pathlib import Path

from harness_forge.contracts import WORKER_DISCLOSURE, WORKER_POLICY
from harness_forge.models import PartResult, VerificationRun


def render_report(run: VerificationRun, results: list[PartResult]) -> str:
    """Markdown grouped by part, then rule id; runtime tests reported separately."""
    lines = [
        "# Verification report", "",
        f"- Run: `{run.run_id}`",
        f"- Harness KB version: {run.kb_version or 'unknown'}",
        f"- Contract digest: {run.contract_digest or 'none (no live confirmation)'}",
        f"- Review scope: {run.scope}",
        "- Rule overrides: none",
        f"- Checker versions digest: {run.frozen_inputs['checkers']}",
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
                         f"{evidence['model']}, configuration {evidence['configuration_digest']}, observed tools: {inventory}, "
                         f"unknowns: {'; '.join(evidence['unknowns']) or 'none recorded'}")
    else:
        lines.append("- No worker ran in this run.")
    for violation in run.worker_violations:
        lines.append(f"- Observed out-of-scope action: {violation}. Its output was discarded.")
    lines.append("")

    for result in results:
        lines += [
            f"## Part {result.part_id}", "",
            f"- Harness result: {result.harness_result}",
            f"- Goal result: {result.goal_result}",
            f"- Status: {result.status}",
            f"- Outside dependencies: {', '.join(result.outside_dependencies) or 'none'}",
        ]
        if result.goal_verdict:
            lines.append(f"- Goal verdict: {result.goal_verdict['verdict']} ({result.goal_verdict['reason']})")
        lines += ["", "| Rule | Severity | Check | Result | Reason | Sources |", "| --- | --- | --- | --- | --- | --- |"]
        for item in sorted(result.rule_results, key=lambda r: r["rule_id"]):
            info = run.rule_index.get(item["rule_id"], {})
            reason = item.get("reason", "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| `{item['rule_id']}` | {info.get('severity', '-')} | {item['check']} | {item['result']} | "
                         f"{reason} | {', '.join(info.get('sources', [])) or '-'} |")
        contested = [rule_id for rule_id in sorted({r['rule_id'] for r in result.rule_results})
                     if run.rule_index.get(rule_id, {}).get("positions")]
        for rule_id in contested:
            lines.append(f"\nContested `{rule_id}`:")
            for position in run.rule_index[rule_id]["positions"]:
                lines.append(f"- {position['position']} ({', '.join(position['sources'])})")
        lines.append("")

    lines += ["## Runtime tests", ""]
    if run.runtime_results:
        lines += ["| Part | Case | Harness | Result | Observed | Reason |", "| --- | --- | --- | --- | --- | --- |"]
        for item in run.runtime_results:
            lines.append(f"| {item.part_id} | {item.case_id} | {item.harness} {item.harness_version} | {item.result} | "
                         f"{item.observed_activation or '-'} | {item.reason} |")
    else:
        lines.append("none run")
    lines += ["", "## Checked files", "", "```"]
    lines += [f"{digest}  {path}" for path, digest in run.frozen_inputs["files"].items()]
    lines += [f"link  {path} -> {target}" for path, target in run.frozen_inputs["symlinks"].items()]
    lines += ["```", ""]
    return "\n".join(lines)


def write_report(repository: Path, slug: str, rendered: str) -> Path:
    """Write .harness/reports/<slug>.md inside the target repository only."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError(f"invalid report slug {slug!r}")
    root = Path(repository).resolve()
    current = root
    for part in (".harness", "reports"):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{current.relative_to(root)} is a symbolic link; refusing to write through it")
    path = root / ".harness" / "reports" / f"{slug}.md"
    if path.is_symlink():
        raise ValueError(f"{path.relative_to(root)} is a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError("the report directory escapes the repository")
    path.write_text(rendered, encoding="utf-8")
    return path
