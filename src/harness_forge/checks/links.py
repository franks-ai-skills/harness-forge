# SPDX-License-Identifier: AGPL-3.0-only
# Licensed under AGPLv3 together with the Harness Forge Generated Output
# Exception; see LICENSE-OUTPUT-EXCEPTION.
"""Link checkers: references inside a skill, the portable symlink and the plugin layout."""

from __future__ import annotations

import json
import posixpath

from harness_forge.checks.common import each_skill, is_markdown, relative_links, resolve, skill_dirs, text


def check_relative_references(files, facts, params):
    def problem(directory, files):
        found = []
        for path in sorted(files):
            if not (path.startswith(directory + "/") and is_markdown(path)):
                continue
            for link in relative_links(text(files[path]), frontmatter=path == f"{directory}/SKILL.md"):
                target = resolve(posixpath.dirname(path), link)
                inside = target.startswith(directory + "/")
                exists = target in files or any(name.startswith(target.rstrip("/") + "/") for name in files)
                if not inside:
                    found.append(f"{path} links outside the skill: {link}")
                elif not exists:
                    found.append(f"{path} links to missing {link}")
        return "; ".join(found) or None

    return each_skill(files, problem)


def check_portable_symlink(files, facts, params):
    symlinks = facts.get("symlinks", {})
    directories = skill_dirs(files)
    if not directories:
        return "fail", "no SKILL.md in the checked files"
    problems = []
    for directory in directories:
        name = posixpath.basename(directory)
        if directory != f".agents/skills/{name}":
            problems.append(f"{directory}: the canonical copy must be .agents/skills/{name}")
            continue
        link = f".claude/skills/{name}"
        expected = f"../../.agents/skills/{name}"
        if symlinks.get(link) != expected:
            problems.append(f"{link} must be a symbolic link to {expected}, found {symlinks.get(link)!r}")
    return ("fail", "; ".join(problems)) if problems else ("pass", f"{len(directories)} skill(s) linked")


PLUGIN_MANIFESTS = ("plugin.json", ".codex-plugin/plugin.json")


def check_plugin_layout(files, facts, params):
    """Each skill is skills/<name>/ under a plugin root that holds a JSON manifest Codex reads."""
    directories = skill_dirs(files)
    if not directories:
        return "fail", "no SKILL.md in the checked files"
    problems = []
    for directory in directories:
        parent = posixpath.dirname(directory)
        if posixpath.basename(parent) != "skills":
            problems.append(f"{directory}: a plugin skill must be skills/<name>/ under the plugin root")
            continue
        root = posixpath.dirname(parent)
        manifests = [posixpath.join(root, name) for name in PLUGIN_MANIFESTS]
        found = [path for path in manifests if path in files]
        if not found:
            problems.append(f"{directory}: the plugin root {root or '.'} holds neither {' nor '.join(manifests)}")
            continue
        try:
            manifest = json.loads(text(files[found[0]]))
        except (UnicodeDecodeError, ValueError):
            manifest = None
        if not isinstance(manifest, dict):
            problems.append(f"{found[0]} is not a JSON object")
    return ("fail", "; ".join(problems)) if problems else ("pass", f"{len(directories)} plugin skill(s) laid out")


REGISTRY = {
    "links.relative-references": ("1.4.0", check_relative_references),
    "links.portable-symlink": ("1.0.0", check_portable_symlink),
    "links.plugin-layout": ("1.0.0", check_plugin_layout),
}
