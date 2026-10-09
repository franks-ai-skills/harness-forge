"""Migrate the research into the Harness KB and pin Forge's KB snapshot.

    python tools/pin_snapshot.py --migrate <PLAY> --kb <KB>
        Copy docs/ and the routing skill from playground into the KB,
        applying only the enumerated link repairs, and record the
        migration inventory in the KB's kb-manifest.json.
    python tools/pin_snapshot.py --check-migration-source <PLAY> --kb <KB>
        Read-only: exit 1 if playground's retained research differs from
        the recorded inventory.
    python tools/pin_snapshot.py --pin --kb <KB> --out snapshot
        Copy the KB's committed docs, rules, schemas, policy, catalog and
        fixtures into the snapshot with per-file hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROUTING_SKILL = ".agents/skills/agent-harness"
ROUTING_LINK = ".claude/skills/agent-harness"
SNAPSHOT_PATHS = ("docs", "rules", "schemas", "policy", "checks", "fixtures", "kb-manifest.json")
DESIGN = "2026-10-06-harness-forge-design.md"

# The only byte changes allowed when copying research into the KB: links
# from docs/ to playground's specs/ become links to the sibling checkout.
REPAIRS: dict[str, list[tuple[str, str, int]]] = {
    "docs/guide/outside-gates.md": [
        (f"](../../specs/{DESIGN})", f"](../../../playground/specs/{DESIGN})", 2),
    ],
    "docs/research-notes/outside-gates/README.md": [
        (f"](../../../specs/{DESIGN})", f"](../../../../playground/specs/{DESIGN})", 1),
    ],
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args)} in {root}: {result.stderr.strip()}")
    return result.stdout


def research_inventory(playground: Path) -> dict[str, str]:
    """sha256 of every file under playground's docs/."""
    docs = Path(playground) / "docs"
    return {
        path.relative_to(playground).as_posix(): sha256(path.read_bytes())
        for path in sorted(docs.rglob("*"))
        if path.is_file()
    }


def apply_repairs(relative: str, data: bytes) -> bytes:
    text = data.decode("utf-8") if relative in REPAIRS else None
    for old, new, count in REPAIRS.get(relative, []):
        if text.count(old) != count:
            raise ValueError(f"{relative}: expected {count} occurrence(s) of {old!r}")
        text = text.replace(old, new)
    return data if text is None else text.encode("utf-8")


def check_migration_source(playground: Path, manifest: dict) -> list[str]:
    """Compare retained research with its inventory; empty means unchanged."""
    recorded = manifest["migration"]["files"]
    current = research_inventory(playground)
    problems = [f"changed after migration: {p}" for p in sorted(recorded) if p in current and current[p] != recorded[p]]
    problems += [f"deleted after migration: {p}" for p in sorted(recorded) if p not in current]
    problems += [f"added after migration: {p}" for p in sorted(current) if p not in recorded]
    return problems


def migrate(playground: Path, kb: Path) -> dict:
    """Copy research and the routing skill; return the migration record."""
    playground, kb = Path(playground), Path(kb)
    if git(playground, "status", "--porcelain", "--", "docs", ROUTING_SKILL).strip():
        raise ValueError("playground docs/ or routing skill has uncommitted changes")
    if (kb / "docs").exists():
        raise ValueError(f"{kb / 'docs'} already exists")
    inventory = research_inventory(playground)
    for relative in inventory:
        target = kb / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(apply_repairs(relative, (playground / relative).read_bytes()))
    shutil.copytree(playground / ROUTING_SKILL, kb / ROUTING_SKILL, dirs_exist_ok=False)
    link = kb / ROUTING_LINK
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink("../../.agents/skills/agent-harness", link)
    return {
        "source_repository": "playground",
        "source_commit": git(playground, "rev-parse", "HEAD").strip(),
        "files": inventory,
        "repairs": {path: [[old, new] for old, new, _ in repairs] for path, repairs in REPAIRS.items()},
    }


def pin_snapshot(kb: Path, destination: Path) -> dict:
    """Copy the KB's committed snapshot paths and return their manifest."""
    kb, destination = Path(kb), Path(destination)
    if git(kb, "status", "--porcelain", "--", *SNAPSHOT_PATHS).strip():
        raise ValueError(f"{kb} has uncommitted changes in the snapshot paths")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"{destination} is not empty")
    source_manifest = json.loads((kb / "kb-manifest.json").read_text(encoding="utf-8"))
    files = {}
    for relative in sorted(git(kb, "ls-files", "-z", "--", *SNAPSHOT_PATHS).split("\0")):
        if not relative or relative == "kb-manifest.json":
            continue
        source = kb / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"{relative}: snapshot files must be regular files")
        data = source.read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = sha256(data)
    manifest = {
        "kb_version": source_manifest["kb_version"],
        "kb_commit": git(kb, "rev-parse", "HEAD").strip(),
        "migration": source_manifest.get("migration"),
        "files": files,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "kb-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--migrate", metavar="PLAY")
    action.add_argument("--check-migration-source", metavar="PLAY")
    action.add_argument("--pin", action="store_true")
    parser.add_argument("--kb", required=True)
    parser.add_argument("--out")
    arguments = parser.parse_args(argv)
    kb = Path(arguments.kb)
    try:
        if arguments.migrate:
            record = migrate(Path(arguments.migrate), kb)
            manifest_path = kb / "kb-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"kb_version": "0.1.0"}
            manifest["migration"] = record
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"migrated_files": len(record["files"]), "source_commit": record["source_commit"]}))
            return 0
        if arguments.check_migration_source:
            manifest = json.loads((kb / "kb-manifest.json").read_text(encoding="utf-8"))
            problems = check_migration_source(Path(arguments.check_migration_source), manifest)
            print(json.dumps({"ok": not problems, "problems": problems}))
            return 0 if not problems else 1
        if not arguments.out:
            parser.error("--pin needs --out")
        manifest = pin_snapshot(kb, Path(arguments.out))
        print(json.dumps({"kb_commit": manifest["kb_commit"], "files": len(manifest["files"])}))
        return 0
    except ValueError as error:
        print(f"pin_snapshot.py: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
