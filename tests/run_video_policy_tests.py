#!/usr/bin/env python3
"""Run production video-policy tests without native codecs or Cargo dependencies.

Usage: python tests/run_video_policy_tests.py
       python tests/run_video_policy_tests.py --source-only

The harnesses include the real policy/QoS modules. Minimal dependency stand-ins
do not exercise hardware encoders, capture, transport, rendering or device FPS.
Run on both Windows and Linux to cover the platform-specific 120/60 FPS ceiling.
"""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = (
    "video_policy_harness.rs",
    "bitrate_policy_harness.rs",
    "video_qos_harness.rs",
)


def function_body(source, name):
    match = re.search(r"\bfn\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{", source)
    if not match:
        raise ValueError(f"Missing function: {name}")
    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            body = re.sub(r"//[^\n]*|/\*.*?\*/", "", source[start:index], flags=re.S)
            return re.sub(r"\s+", "", body)
    raise ValueError(f"Unclosed function: {name}")


def constant(source, name):
    match = re.search(r"\bconst\s+" + re.escape(name) + r"\s*:\s*f32\s*=\s*([\d._]+)\s*;", source)
    if not match:
        raise ValueError(f"Missing f32 constant: {name}")
    return float(match[1].replace("_", ""))


def check_sources(root=ROOT):
    def read(relative):
        return (root / relative).read_text(encoding="utf-8")

    harness = read("tests/video_qos_harness.rs")
    client = read("src/client.rs")
    if function_body(harness, "normalize_custom_fps") != function_body(client, "normalize_custom_fps"):
        raise ValueError("QoS harness normalize_custom_fps differs from production src/client.rs")
    codec = read("libs/scrap/src/common/codec.rs")
    for name in ("BR_BEST", "BR_BALANCED", "BR_SPEED"):
        if constant(harness, name) != constant(codec, name):
            raise ValueError(f"QoS harness {name} differs from production codec.rs")

    # The production bitrate function is included, never reimplemented here.
    if re.search(r"\bfn\s+fps_bitrate_scale\s*\(", harness):
        raise ValueError("QoS harness must reuse production bitrate_policy.rs")
    include = re.search(r'#\[path\s*=\s*"([^"]*bitrate_policy\.rs)"\]\s*mod\s+bitrate_policy\s*;', harness)
    expected = (root / "libs/scrap/src/common/bitrate_policy.rs").resolve()
    if not include or (root / "tests" / include[1]).resolve() != expected:
        raise ValueError("QoS harness must include production bitrate_policy.rs")
    if not re.search(r"pub\s+use\s+(?:super|crate)::bitrate_policy::fps_bitrate_scale\s*;", harness):
        raise ValueError("QoS harness must re-export production fps_bitrate_scale")

    # Numeric protobuf values matter: a drift would test the wrong quality mode.
    proto = read("libs/hbb_common/protos/message.proto")
    enum = re.search(r"enum\s+ImageQuality\s*\{([^}]+)\}", proto, re.S)
    if not enum:
        raise ValueError("Missing production ImageQuality protobuf enum")
    for name in ("Balanced", "Low", "Best"):
        actual = re.search(r"\b" + name + r"\s*=\s*(\d+)\s*;", enum[1])
        stub = re.search(r"Self::" + name + r"\s*=>\s*(\d+)\b", harness)
        if not actual or not stub or actual[1] != stub[1]:
            raise ValueError(f"QoS harness ImageQuality::{name} differs from production protobuf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true", help="Check harness dependency contracts without claiming Rust tests ran")
    parser.add_argument("--rustc", default="rustc", help="Rust compiler executable (default: rustc on PATH)")
    args = parser.parse_args()
    try:
        check_sources()
        print("PASS: harness dependency contracts match production sources", flush=True)
        if args.source_only:
            print("SOURCE ONLY: Rust policy tests were not run")
            return 0
        rustc = shutil.which(args.rustc)
        if not rustc:
            print("Rust compiler unavailable; no Rust tests ran. Install Rust or use --source-only for limited source checks.", file=sys.stderr)
            return 2
        subprocess.run([rustc, "--version"], check=True)
        with tempfile.TemporaryDirectory(prefix="linkremote-video-policy-") as temp:
            for harness in HARNESSES:
                output = Path(temp) / (Path(harness).stem + (".exe" if sys.platform == "win32" else ""))
                print(f"\nTesting {harness} on {sys.platform}", flush=True)
                subprocess.run([rustc, "--edition=2021", "--test", str(ROOT / "tests" / harness), "-o", str(output)], check=True)
                subprocess.run([str(output), "--nocapture"], check=True)
        print("PASS: production policy/QoS tests; actual device/codec throughput remains unmeasured")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
