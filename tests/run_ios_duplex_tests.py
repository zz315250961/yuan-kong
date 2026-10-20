#!/usr/bin/env python3
"""Exercise the production iOS duplex queue/routing interface, without faking AEC.

Host mode runs the real Rust memory/lifetime tests. --check-native TARGET
type-checks the same production AudioUnit callbacks against Apple's selected
SDK, but does not start a microphone or measure acoustic echo cancellation.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODULE = "src/client/ios_duplex.rs"
HARNESS = "tests/ios_duplex_harness.rs"


def body(source, name):
    match = re.search(r"\bfn\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{", source)
    if not match:
        raise ValueError(f"Missing duplex function {name}")
    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            return re.sub(r"//[^\n]*", "", source[start:index])
    raise ValueError(f"Unclosed duplex function {name}")


def check_sources(root=ROOT):
    harness = (root / HARNESS).read_text(encoding="utf-8")
    include = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*mod\s+ios_duplex\s*;', harness)
    if not include or (root / "tests" / include[1]).resolve() != (root / MODULE).resolve():
        raise ValueError("Duplex harness must include the production module directly")
    if re.search(r"\b(?:struct\s+(?:Playback|Duplex)|fn\s+(?:render|capture|push))\b", harness):
        raise ValueError("Duplex harness must not copy or mock the production implementation")
    source = (root / MODULE).read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", source)
    if re.search(r"\bRemoteIO\b|\.set_input_callback\(", code):
        raise ValueError("Voice processing must not fall back to RemoteIO or a reallocating capture wrapper")
    if code.count("sys::AudioComponentInstanceNew(") != 1:
        raise ValueError("Duplex capture and playback must share one AudioUnit instance")
    for name in ("capture_callback", "render_callback"):
        callback = body(source, name)
        if re.search(r"\b(?:Vec|Mutex|Encoder)\b|Box::|format!|log::|println!|\.send\(|\.recv\(|\.sleep\(|thread::sleep", callback):
            raise ValueError("Native audio callbacks must not allocate, lock, log, encode or send")
    for name in ("render", "capture"):
        callback = body(source, name)
        if re.search(r"\b(?:Vec|Mutex|Encoder)\b|Box::|format!|log::|println!|\.send\(|\.recv\(|thread::sleep", callback):
            raise ValueError("Memory callback helpers must not allocate, lock, log, encode or send")
    compact = re.sub(r"\s+", "", code)
    required = (
        "componentSubType:sys::kAudioUnitSubType_VoiceProcessingIO,",
        "native.set(sys::kAudioOutputUnitProperty_EnableIO,sys::kAudioUnitScope_Input,1,&1u32)?;",
        "native.set(sys::kAudioOutputUnitProperty_EnableIO,sys::kAudioUnitScope_Output,0,&1u32)?;",
        "native.set(sys::kAUVoiceIOProperty_BypassVoiceProcessing,sys::kAudioUnitScope_Global,0,&0u32)?;",
        "native.set(sys::kAUVoiceIOProperty_VoiceProcessingEnableAGC,sys::kAudioUnitScope_Global,0,&1u32)?;",
        "sys::AudioUnitRender(context.unit,flags,timestamp,1,frames,&mutbuffers)",
        "context.playback.capture(samples,&mutcontext.capture);",
        "playback.render(output);",
        "self.closed.store(true,Ordering::Release);self.clear();",
        "pubconstPLAYBACK_SAMPLES:usize=SAMPLE_RATEasusize*120/1_000;",
    )
    for requirement in required:
        if requirement not in compact:
            raise ValueError("Missing one-device processed duplex/bounded cancellation contract: " + requirement)
    native = source[source.index("mod native {"):]
    start = body(native, "start")
    if start.count("native.verify_processing()?;") != 2:
        raise ValueError("Voice processing/format must be read back before and after initialization")
    if start.index("sys::AudioUnitInitialize(") > start.index("sys::AudioOutputUnitStart("):
        raise ValueError("AudioUnit must initialize before capture can start")
    if "context.scratch[..frames as usize].fill(0.0);" not in body(source, "capture_callback"):
        raise ValueError("Microphone callback errors must discard temporary captured PCM")
    if "self.latest = self.latest.max(epoch);" not in body(source, "end"):
        raise ValueError("End-before-Bind must retain an epoch tombstone")


def manifest(root=ROOT):
    harness = (root / HARNESS).resolve().as_posix()
    return "\n".join((
        "[package]", 'name = "linkremote-ios-duplex-check"', 'version = "0.0.0"',
        'edition = "2021"', 'rust-version = "1.75"', "[workspace]", "[lib]",
        "path = " + json.dumps(harness, ensure_ascii=False),
        "[dependencies]", 'crossbeam-queue = "=0.3.11"',
        "[target.'cfg(target_os = \"ios\")'.dependencies]",
        'coreaudio-rs = { version = "=0.11.3", default-features = false, features = ["audio_unit", "core_audio", "audio_toolbox"] }', "",
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--check-native", choices=("aarch64-apple-ios", "x86_64-apple-ios", "aarch64-apple-ios-sim"))
    args = parser.parse_args(argv)
    try:
        check_sources()
        print("PASS: production duplex harness and native callback source contracts", flush=True)
        if args.source_only:
            print("SOURCE ONLY: no Rust interface test, native SDK check or acoustic test ran")
            return 0
        cargo = shutil.which(args.cargo)
        if not cargo:
            print("Cargo unavailable; no Rust duplex tests ran", file=sys.stderr)
            return 2
        subprocess.run([cargo, "--version"], check=True)
        with tempfile.TemporaryDirectory(prefix="linkremote-ios-duplex-") as directory:
            temporary = Path(directory)
            # Seed the existing project lock: keep its Rust 1.75-compatible
            # dependency graph instead of resolving newer transitive packages.
            shutil.copy2(ROOT / "Cargo.lock", temporary / "Cargo.lock")
            target_manifest = temporary / "Cargo.toml"
            target_manifest.write_text(manifest(), encoding="utf-8")
            if args.check_native:
                command = [cargo, "check", "--manifest-path", str(target_manifest), "--target", args.check_native]
            else:
                command = [cargo, "test", "--manifest-path", str(target_manifest), "--", "--nocapture"]
            subprocess.run(command, check=True)
        if args.check_native:
            print("PASS: production duplex callbacks compile against selected Apple SDK; no live microphone or acoustic AEC test ran")
        else:
            print("PASS: real production queue/routing/lifetime Rust tests; no Apple AudioUnit or acoustic AEC test ran")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
