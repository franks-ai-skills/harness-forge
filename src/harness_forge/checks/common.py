"""Helpers shared by checkers: skill discovery, frontmatter and links."""

from __future__ import annotations

import posixpath
import re

from harness_forge.documents import markdown_prose, parse_yaml, split_frontmatter

_DESTINATION = r"(?:<([^<>\n]*)>|((?:[^\s<>()]|\([^\s<>()]*\))+))"
_TITLE = r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^()]*\)))?"""
# Inline links and images, with an optional title, and reference definitions;
# a footnote definition ([^1]: text) is not a link.
LINK = re.compile(r"\]\(\s*" + _DESTINATION + _TITLE + r"\s*\)")
DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*\n?[ \t]*" + _DESTINATION, re.M)


def skill_dirs(files: dict[str, bytes]) -> list[str]:
    return sorted(path[: -len("/SKILL.md")] for path in files if path.endswith("/SKILL.md"))


def text(data: bytes) -> str:
    return data.decode("utf-8")


def frontmatter(data: bytes) -> tuple[dict, str]:
    """Return (frontmatter, body); raise ValueError when malformed."""
    head, body = split_frontmatter(text(data))
    value = parse_yaml(head)
    if not isinstance(value, dict):
        raise ValueError("frontmatter is not a mapping")
    return value, body


def without_code(markdown: str) -> str:
    return markdown_prose(markdown)


def relative_links(markdown: str, *, frontmatter: bool = False) -> list[str]:
    """Relative link targets outside code; ``frontmatter`` for SKILL.md, the one file that starts with YAML."""
    links = []
    if frontmatter and markdown.startswith("---"):
        # Frontmatter is YAML, not Markdown: a fence in a scalar opens no code block in the body.
        try:
            markdown = split_frontmatter(markdown)[1]
        except ValueError:
            pass
    markdown = without_code(markdown)
    for angled, bare in LINK.findall(markdown) + DEFINITION.findall(markdown):
        target = angled or bare
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
            continue
        links.append(target.split("#")[0])
    return [link for link in links if link]


def resolve(directory: str, link: str) -> str:
    if link.startswith("/"):
        return link
    return posixpath.normpath(posixpath.join(directory, link))


def each_skill(files: dict[str, bytes], check) -> tuple[str, str]:
    """Run ``check(skill_dir, files)`` per skill; fail if any fails."""
    directories = skill_dirs(files)
    if not directories:
        return "fail", "no SKILL.md in the checked files"
    problems = []
    for directory in directories:
        problem = check(directory, files)
        if problem:
            problems.append(f"{directory}: {problem}")
    return ("fail", "; ".join(problems)) if problems else ("pass", f"{len(directories)} skill(s) checked")
