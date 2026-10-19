"""Validate immutable candidate sources and record build artifact hashes.

This does not publish, create tags, install packages, or certify runtime behavior.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def validate_source(source_sha, expected_sha, version, tag, publish, tag_sha=None):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha or ""):
        raise ValueError("Expected source must be an exact 40-character lowercase commit SHA")
    if source_sha != expected_sha:
        raise ValueError("Workflow source does not match the explicitly requested source SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version or ""):
        raise ValueError("Candidate version must be a three-part numeric version")
    if publish:
        if tag != f"v{version}":
            raise ValueError("Publication tag must match the candidate source version")
        if tag_sha != source_sha:
            raise ValueError("Publication requires an existing immutable tag at the exact source SHA")


def candidate_versions(root, expected):
    cargo = (root / "Cargo.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', cargo)
    if match is None or match[1] != expected:
        raise ValueError("Cargo version differs from the candidate workflow version")
    flutter = (root / "flutter/pubspec.yaml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^version: ([0-9]+\.[0-9]+\.[0-9]+)\+([0-9]+)$", flutter)
    if match is None or match[1] != expected:
        raise ValueError("Flutter version differs from the candidate workflow version")
    build = int(match[2])
    for name, package in (
        ("Cargo.lock", "linkremote"),
        ("Cargo.lock", "linkremote-portable-packer"),
        ("libs/portable/Cargo.toml", "linkremote-portable-packer"),
        ("libs/portable/Cargo.lock", "linkremote-portable-packer"),
    ):
        content = (root / name).read_text(encoding="utf-8")
        pattern = rf'(?m)^name = "{re.escape(package)}"\nversion = "{re.escape(expected)}"$'
        if not re.search(pattern, content):
            raise ValueError(f"{name} {package} version differs from the candidate workflow version")
    return {"version": expected, "flutter_build": build}


def artifact_record(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Missing candidate artifact: {path.name}")
    if path.stat().st_size == 0:
        raise ValueError(f"Empty candidate artifact: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def candidate_manifest(root, platform, paths, source_sha, metadata, environment):
    version = metadata["version"]
    expected = {
        "windows": {f"LinkRemote-{version}-x86_64.exe", f"LinkRemote-{version}-x86_64.msi"},
        "android": {f"LinkRemote-{version}-android-signed.apk"},
        "web": {f"LinkRemote-{version}-web-management.zip"},
    }[platform]
    names = [Path(path).name for path in paths]
    if len(names) != len(expected) or set(names) != expected:
        raise ValueError("Candidate artifact names must match the platform and source version exactly")
    return {
        "schema": 1,
        "source_commit": source_sha,
        **metadata,
        "platform": platform,
        "repository": environment.get("GITHUB_REPOSITORY", ""),
        "run_id": environment.get("GITHUB_RUN_ID", ""),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
        "candidate_only": environment.get("PUBLISH_RELEASE") != "true",
        "artifacts": [artifact_record(path) for path in paths],
        "notices": [artifact_record(root / name) for name in ("LICENCE", "NOTICE.md")],
        "scope": "Source/version/filename/size/SHA256 provenance only; not installation, runtime, signature identity, or release approval",
    }


def git_output(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-source", action="store_true")
    parser.add_argument("--platform", choices=("windows", "android", "web"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("artifacts", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if not args.check_source and (args.platform is None or args.output is None):
        parser.error("Use --check-source or provide --platform and --output")
    source_sha = git_output("rev-parse", "HEAD")
    version = os.environ.get("VERSION", "")
    publish = os.environ.get("PUBLISH_RELEASE") == "true"
    tag = os.environ.get("TAG_NAME", "")
    tag_sha = None
    if publish and tag == f"v{version}":
        try:
            tag_sha = git_output("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
        except subprocess.CalledProcessError as error:
            raise ValueError("Publication tag does not exist in the source checkout") from error
    validate_source(source_sha, os.environ.get("EXPECTED_SOURCE_SHA", ""), version, tag, publish, tag_sha)
    metadata = candidate_versions(ROOT, version)
    if args.check_source:
        print(json.dumps({"source_commit": source_sha, **metadata, "publish_release": publish}, indent=2))
        return 0
    manifest = candidate_manifest(ROOT, args.platform, args.artifacts, source_sha, metadata, os.environ)
    # Avoid overwriting evidence from another invocation in this checkout.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(f"Recorded {args.platform} candidate provenance: {args.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
