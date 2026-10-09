"""Link checkers: references inside a skill and the portable symlink."""

from __future__ import annotations

import posixpath

from harness_forge.checks.common import each_skill, relative_links, resolve, skill_dirs, text


def check_relative_references(files, facts, params):
    def problem(directory, files):
        found = []
        for path in sorted(files):
            if not (path.startswith(directory + "/") and path.endswith(".md")):
                continue
            for link in relative_links(text(files[path])):
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


REGISTRY = {
    "links.relative-references": ("1.0.0", check_relative_references),
    "links.portable-symlink": ("1.0.0", check_portable_symlink),
}
