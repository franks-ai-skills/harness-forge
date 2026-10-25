# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Reporting Harness KB drift upstream: an issue or a comment, filed only after the user approved its text.

Forge searches the KB's open issues for one that names the rule. It
proposes a comment on that issue, or a new issue, built from the drift
finding. The text derives from fetched content, so nothing is filed until
the user approves the exact text shown, relayed with its digest like a
contract confirmation. A decline records ``not published`` and an open
question ``pending``. Adopting an override never depends on publishing.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Callable, Mapping
from urllib.parse import urlsplit

from harness_forge.adapters.base import Runner, run_command
from harness_forge.documents import canonical_digest

KB_REPOSITORY = "franks-ai-skills/agent-harness-kb"
RELAY_KEYS = {"choice", "displayed_digest", "run_id"}
GH_TIMEOUT = 60
# What gh needs to reach GitHub with the user's existing login; nothing else is passed on.
_GH_ENVIRONMENT = ("PATH", "HOME", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "GH_HOST", "LANG", "LC_ALL", "TMPDIR")


def _plain_https(url: str) -> bool:
    """Whether the URL can stand as a Markdown autolink: HTTPS with a host and nothing that ends or splits it."""
    if any(char.isspace() or char in "<>" or unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme == "https" and bool(parts.hostname)
    except ValueError:
        return False


def _fenced(label: str, text: str) -> list[str]:
    """Fetched text under a label, in a fence longer than any backtick or tilde run in it, then a blank line.

    Only Markdown's own line endings split it, so other characters stay as fetched.
    """
    longest = max((len(run) for run in re.findall(r"`+|~+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{label} (untrusted content from a fetched page, quoted as fetched):", "", fence + "text",
            *re.split(r"\r\n|\r|\n", text), fence, ""]


def find_issue(rule_id: str, search: Callable[[str], list[dict]]) -> dict | None:
    """The first open issue whose title or body names the rule."""
    for issue in search(rule_id):
        if str(issue.get("state", "")).upper() == "OPEN" and rule_id in f"{issue.get('title', '')} {issue.get('body', '')}":
            return issue
    return None


def propose(drift: dict, finding: dict, existing: dict | None, *, kb_version: str,
            repository: str = KB_REPOSITORY) -> dict:
    """The exact text to show the user: a comment on ``existing`` or a new issue, with the digest it binds."""
    source = finding["source"]
    linked = _plain_https(source)
    body = "\n".join([
        f"Rule `{drift['rule_id']}` (Harness KB {kb_version}) may be outdated.",
        "",
        drift["summary"],
        "",
        f"- Source: <{source}>" if linked else "- Source: shown below",
        "- Checks: the quote appears verbatim on the fetched page, and an independent source-support check "
        "found that it supports the claim.",
        "",
        *([] if linked else _fenced("Source", source)),
        *_fenced("Claim", finding["claim"]),
        *_fenced("Quote", finding["quote"]),
        "Proposed from a Harness Forge research run; the user approved this text before it was filed.",
    ])
    if existing:
        proposal = {"kind": "comment", "target": existing["url"], "title": None, "body": body}
    else:
        proposal = {"kind": "issue", "target": repository, "title": f"Drift: {drift['rule_id']} may be outdated",
                    "body": body}
    proposal.update(rule_id=drift["rule_id"], finding_id=drift["finding_id"])
    proposal["digest"] = canonical_digest({key: proposal[key] for key in ("kind", "target", "title", "body")})
    return proposal


def publish(proposal: dict, relay: dict | None, publisher: Callable[[dict], str], *, run_id: str) -> str:
    """File the proposal on an approval of exactly its text in this run; otherwise record the answer.

    Returns the issue or comment link, ``not published`` for a decline, or
    ``pending`` for anything else, which leaves the user's answer open.
    """
    bound = (isinstance(relay, dict) and set(relay) == RELAY_KEYS and relay["run_id"] == run_id
             and relay["displayed_digest"] == proposal["digest"])
    if not bound or relay["choice"] not in ("approve", "decline"):
        return "pending"
    if relay["choice"] == "decline":
        return "not published"
    link = str(publisher(proposal)).strip()
    if not re.fullmatch(r"https://\S+", link):
        raise ValueError(f"the publisher returned no link: {link!r}")
    return link


def _environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {name: environ[name] for name in _GH_ENVIRONMENT if name in environ}


def gh_search(runner: Runner = run_command, *, environ: Mapping[str, str]) -> Callable[[str], list[dict]]:
    """Search the KB's open issues with the GitHub CLI and the user's existing login; read-only."""
    def search(rule_id: str) -> list[dict]:
        completed = runner(["gh", "issue", "list", "--repo", KB_REPOSITORY, "--state", "open", "--search",
                            f"{rule_id} in:title,body", "--json", "number,title,url,state,body"],
                           cwd=None, env=_environment(environ), stdin=None, timeout=GH_TIMEOUT)
        if completed.returncode or completed.timed_out:
            raise ValueError(f"gh issue list failed: {completed.stderr.strip()[-200:]}")
        issues = json.loads(completed.stdout or "[]")
        if not isinstance(issues, list):
            raise ValueError("gh issue list returned no list")
        return issues
    return search


def gh_publisher(runner: Runner = run_command, *, environ: Mapping[str, str]) -> Callable[[dict], str]:
    """File an approved proposal with the GitHub CLI and the user's existing login; returns its link."""
    def publisher(proposal: dict) -> str:
        if proposal["kind"] == "comment":
            command = ["gh", "issue", "comment", proposal["target"], "--body-file", "-"]
        else:
            command = ["gh", "issue", "create", "--repo", proposal["target"], "--title", proposal["title"],
                       "--body-file", "-"]
        completed = runner(command, cwd=None, env=_environment(environ), stdin=proposal["body"], timeout=GH_TIMEOUT)
        if completed.returncode or completed.timed_out:
            raise ValueError(f"gh failed: {completed.stderr.strip()[-200:]}")
        return completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    return publisher
