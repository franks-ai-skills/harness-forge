"""Maintain Forge's bundled runtime dependencies in vendor/.

    python tools/vendor.py --check
        Offline: exit 1 unless vendor/ holds exactly the files that
        vendor/manifest.json lists, with their sha256.
    python tools/vendor.py --refresh
        Download each pinned source in the manifest, refuse it unless its
        sha256 matches, copy the selected regular files into a fresh
        vendor/ and record their sha256 in the manifest.

Maintainer tooling, never run by the plugin. Nothing downloaded is
executed: an sdist or wheel is read as an archive, only regular-file
members under the selected paths are copied, and links, absolute paths
and `..` segments are refused. To update a dependency, change its
version, URL and sha256 in the manifest's `packages` and run --refresh.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import posixpath
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness_forge.bundle import MANIFEST, VENDOR, expected_files, problems  # noqa: E402

MAX_DOWNLOAD = 16 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, expected: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"{url}: only https sources")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - https only, hash-checked below
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"{url}: larger than {MAX_DOWNLOAD} bytes")
    if sha256(data) != expected:
        raise ValueError(f"{url}: sha256 {sha256(data)} is not the pinned {expected}")
    return data


def _safe(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive member {name!r}")
    return path


def archive_members(data: bytes, kind: str) -> dict[str, bytes]:
    """Regular files of an sdist or wheel by path; links and unsafe paths are refused."""
    members = {}
    if kind == "sdist":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError(f"{member.name}: not a regular file")
                members[str(_safe(member.name))] = archive.extractfile(member).read()
    elif kind == "wheel":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"{info.filename}: a link")
                members[str(_safe(info.filename))] = archive.read(info)
    else:
        raise ValueError(f"unknown archive kind {kind!r}")
    return members


def selected(package: dict) -> dict[str, bytes]:
    """The files one manifest package contributes, by their path inside vendor/."""
    out, members = {}, {}
    source = package["source"]
    data = download(source["url"], source["sha256"])
    if source["kind"] == "file":
        out[package["files"][""]] = data
    else:
        members = archive_members(data, source["kind"])
        for prefix, target in package["files"].items():
            found = {name: content for name, content in members.items()
                     if name == prefix or name.startswith(prefix.rstrip("/") + "/")}
            if not found:
                raise ValueError(f"{package['name']}: no member under {prefix!r}")
            for name, content in found.items():
                relative = posixpath.relpath(name, prefix) if name != prefix else ""
                out[posixpath.join(target, relative) if relative and relative != "." else target] = content
    for license_file in package.get("license_files", []):
        content = download(license_file["url"], license_file["sha256"]) if "url" in license_file \
            else members[license_file["member"]]
        out[license_file["to"]] = content
    for path in out:
        _safe(path)
    return out


def refresh() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = {}
    for package in manifest["packages"]:
        for path, content in selected(package).items():
            if path in files:
                raise ValueError(f"{path}: written by two packages")
            files[path] = content
    with tempfile.TemporaryDirectory(dir=VENDOR.parent) as directory:
        staging = Path(directory) / "vendor"
        for path, content in files.items():
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        manifest["files"] = {path: sha256(content) for path, content in sorted(files.items())}
        (staging / MANIFEST.name).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(VENDOR)
        shutil.move(str(staging), VENDOR)
    return {"files": len(files), "bytes": sum(len(content) for content in files.values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--refresh", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.refresh:
        print(json.dumps(refresh()))
    found = problems(check_python=False)
    print(json.dumps({"ok": not found, "files": len(expected_files()), "problems": found}))
    return 0 if not found else 1


if __name__ == "__main__":
    raise SystemExit(main())
