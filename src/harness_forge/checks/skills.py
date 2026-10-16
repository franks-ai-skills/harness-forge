# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Skill package and frontmatter checkers."""

from __future__ import annotations

import posixpath
import re

from harness_forge.checks.common import each_skill, frontmatter, is_markdown, relative_links, resolve, text, without_code
from harness_forge.documents import parse_yaml

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\$ARGUMENTS|\$[0-9]\b|\$\{CLAUDE_[A-Z_]+\}|!`")


def _frontmatter_problem(directory, files):
    try:
        fields, _ = frontmatter(files[f"{directory}/SKILL.md"])
    except (ValueError, UnicodeDecodeError) as error:
        return f"frontmatter: {error}"
    for key in ("name", "description"):
        if not isinstance(fields.get(key), str) or not fields[key].strip():
            return f"{key} must be a non-empty string"
    for key in ("license", "compatibility"):
        if key in fields and not isinstance(fields[key], str):
            return f"{key} must be a string"
    if len(fields.get("compatibility", "")) > 500:
        return "compatibility is longer than 500 characters"
    metadata = fields.get("metadata", {})
    if not isinstance(metadata, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()):
        return "metadata must map strings to strings"
    return None


def check_frontmatter(files, facts, params):
    return each_skill(files, _frontmatter_problem)


def _field(directory, files, key):
    fields, _ = frontmatter(files[f"{directory}/SKILL.md"])
    value = fields.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} is not a string")
    return value


def check_name_format(files, facts, params):
    def problem(directory, files):
        try:
            name = _field(directory, files, "name")
        except (ValueError, UnicodeDecodeError) as error:
            return str(error)
        if len(name) > params["max_chars"]:
            return f"name has {len(name)} characters, more than {params['max_chars']}"
        if not NAME.match(name):
            return f"name {name!r} is not lowercase letters, digits and single hyphens"
        if name != posixpath.basename(directory):
            return f"name {name!r} differs from the directory {posixpath.basename(directory)!r}"
        return None

    return each_skill(files, problem)


def check_description_length(files, facts, params):
    def problem(directory, files):
        try:
            description = _field(directory, files, "description")
        except (ValueError, UnicodeDecodeError) as error:
            return str(error)
        if not description.strip():
            return "description is empty"
        if len(description) > params["max_chars"]:
            return f"description has {len(description)} characters, more than {params['max_chars']}"
        return None

    return each_skill(files, problem)


def check_body_lines(files, facts, params):
    def problem(directory, files):
        lines = len(text(files[f"{directory}/SKILL.md"]).splitlines())
        return f"SKILL.md has {lines} lines, more than {params['max_lines']}" if lines > params["max_lines"] else None

    return each_skill(files, problem)


def check_reference_depth(files, facts, params):
    def problem(directory, files):
        found = []
        for path in sorted(files):
            if path.startswith(directory + "/") and is_markdown(path) and path != f"{directory}/SKILL.md":
                for link in relative_links(text(files[path])):
                    target = resolve(posixpath.dirname(path), link)
                    if is_markdown(target) and target.startswith(directory + "/") and target != f"{directory}/SKILL.md":
                        found.append(f"{path} links to {target}")
        return "; ".join(found) or None

    return each_skill(files, problem)


def check_no_auxiliary_docs(files, facts, params):
    names = set(params["names"])

    def problem(directory, files):
        found = [path for path in sorted(files) if path.startswith(directory + "/") and posixpath.basename(path) in names]
        return f"auxiliary files: {', '.join(found)}" if found else None

    return each_skill(files, problem)


def check_portable_frontmatter(files, facts, params):
    allowed = set(params["allowed_keys"])

    def problem(directory, files):
        try:
            fields, _ = frontmatter(files[f"{directory}/SKILL.md"])
        except (ValueError, UnicodeDecodeError) as error:
            return f"frontmatter: {error}"
        extra = sorted(set(fields) - allowed)
        return f"harness-specific keys: {', '.join(extra)}" if extra else None

    return each_skill(files, problem)


def check_portable_body(files, facts, params):
    def problem(directory, files):
        try:
            _, body = frontmatter(files[f"{directory}/SKILL.md"])
        except (ValueError, UnicodeDecodeError) as error:
            return f"frontmatter: {error}"
        found = sorted(set(PLACEHOLDER.findall(without_code(body))))
        return f"Claude Code-only placeholders: {', '.join(found)}" if found else None

    return each_skill(files, problem)


def check_invocation_flags(files, facts, params):
    def problem(directory, files):
        try:
            fields, _ = frontmatter(files[f"{directory}/SKILL.md"])
        except (ValueError, UnicodeDecodeError) as error:
            return f"frontmatter: {error}"
        claude_off = fields.get("disable-model-invocation") is True
        codex_off = False
        sidecar = files.get(f"{directory}/agents/openai.yaml")
        if sidecar is not None:
            try:
                config = parse_yaml(text(sidecar)) or {}
            except (ValueError, UnicodeDecodeError) as error:
                return f"agents/openai.yaml: {error}"
            policy = config.get("policy", {}) if isinstance(config, dict) else {}
            codex_off = isinstance(policy, dict) and policy.get("allow_implicit_invocation") is False
        if claude_off != codex_off:
            return (
                "model invocation is off in "
                + ("Claude Code only" if claude_off else "Codex only")
                + "; set disable-model-invocation and policy.allow_implicit_invocation together"
            )
        return None

    return each_skill(files, problem)


REGISTRY = {
    "skills.frontmatter": ("1.0.0", check_frontmatter),
    "skills.name-format": ("1.0.0", check_name_format),
    "skills.description-length": ("1.0.0", check_description_length),
    "skills.body-lines": ("1.0.0", check_body_lines),
    "skills.reference-depth": ("1.3.0", check_reference_depth),
    "skills.no-auxiliary-docs": ("1.0.0", check_no_auxiliary_docs),
    "skills.portable-frontmatter": ("1.0.0", check_portable_frontmatter),
    "skills.portable-body": ("1.1.0", check_portable_body),
    "skills.invocation-flags": ("1.0.0", check_invocation_flags),
}
