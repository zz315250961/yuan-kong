#!/usr/bin/env python3
"""Gate a UI-only replay on an exact GitHub source artifact and original App ZIP.

This intentionally validates the explicitly selected historical version, not the
current checkout's pubspec. Replaying an old App is not validation of new code.
"""

import argparse
import hashlib
import json
import plistlib
import re
import struct
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_source(run, artifacts, run_id, commit, artifact_name):
    require(re.fullmatch(r"[0-9]+", run_id) is not None, "Invalid source run ID")
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "Invalid source commit")
    require(run.get("id") == int(run_id), "Unexpected source run ID")
    require(run.get("head_sha") == commit, "Unexpected source commit")
    selected = [artifact for artifact in artifacts if artifact.get("name") == artifact_name]
    require(len(selected) == 1, "Expected exactly one source artifact with the selected name")
    artifact = selected[0]
    require(not artifact.get("expired", True), "Selected source artifact has expired")
    source = artifact.get("workflow_run", {})
    require(source.get("id") == int(run_id) and source.get("head_sha") == commit,
            "Unexpected artifact source run or commit")
    return {"source_run_id": int(run_id), "source_commit": commit,
            "artifact_id": artifact["id"], "artifact_name": artifact_name}


def check_archive(path, expected_sha256, version, build):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
            "Expected archive SHA-256 must be 64 lowercase hexadecimal digits")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    require(digest.hexdigest() == expected_sha256, "Archive SHA-256 differs from selected source")
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "Archive CRC validation failed")
        for entry in archive.infolist():
            parts = PurePosixPath(entry.filename).parts
            require(parts and parts[0] == "Runner.app" and ".." not in parts and
                    "\\" not in entry.filename, "Unexpected archive path: " + entry.filename)
        info = plistlib.loads(archive.read("Runner.app/Info.plist"))
        require(info.get("CFBundleIdentifier") == "top.zperme.linkremote", "Wrong replay bundle ID")
        require(info.get("CFBundleSupportedPlatforms") == ["iPhoneSimulator"],
                "Replay requires an iPhoneSimulator App, not an iPhone/iPad device installation")
        require(info.get("CFBundleShortVersionString") == version and
                info.get("CFBundleVersion") == build, "Replay App version differs from selected source")
        require(info.get("CFBundleExecutable") == "Runner", "Unexpected replay executable")
        executable = archive.getinfo("Runner.app/Runner")
        require((executable.external_attr >> 16) & 0o111, "App ZIP lost executable permission")
        header = archive.read(executable)[:8]
        require(header[:4] == b"\xcf\xfa\xed\xfe" and len(header) == 8 and
                struct.unpack("<I", header[4:])[0] == 0x01000007,
                "Replay App must be an x86_64 Mach-O executable")
        require("Runner.app/Assets.car" in archive.namelist(), "Missing compiled replay assets")
    return {"archive_sha256": expected_sha256, "archive_bytes": path.stat().st_size,
            "bundle_id": info["CFBundleIdentifier"], "version": version, "build": build,
            "platform": "iPhoneSimulator", "architecture": "x86_64"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--provenance-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository) is not None,
                "Invalid source repository")
        require(re.fullmatch(r"[0-9]+", args.source_run) is not None, "Invalid source run ID")
        base = f"repos/{args.repository}/actions/runs/{args.source_run}"
        run = json.loads(subprocess.check_output(["gh", "api", base], text=True))
        pages = json.loads(subprocess.check_output(
            ["gh", "api", "--paginate", "--slurp", base + "/artifacts?per_page=100"], text=True))
        artifacts = [artifact for page in pages for artifact in page["artifacts"]]
        result = check_source(run, artifacts, args.source_run, args.source_commit, args.artifact_name)
        result.update(check_archive(args.archive, args.archive_sha256, args.version, args.build))
        result["scope"] = "Historical App UI replay only; not validation of newer checkout code"
        args.provenance_output.parent.mkdir(parents=True, exist_ok=True)
        args.provenance_output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, plistlib.InvalidFileException,
            subprocess.SubprocessError) as error:
        parser.exit(1, f"Simulator replay source validation failed: {error}\n")


if __name__ == "__main__":
    main()
