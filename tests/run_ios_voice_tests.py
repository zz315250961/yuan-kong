#!/usr/bin/env python3
"""Run production PCM packetizer/cancellation tests without native libraries.

This verifies arithmetic/chunking/bounds, real guard Drop and source contracts.
Actual CPAL, Opus, iOS permissions, duplex audio and network calls require the
full native build and a signed real-device session; they are not simulated here.
"""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = "tests/ios_voice_packetizer_harness.rs"


def callback_body(source):
    marker = "move |data, _| {"
    start = source.find(marker)
    if start < 0:
        raise ValueError("Missing typed microphone PCM callback")
    start += len(marker)
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            return re.sub(r"//[^\n]*", "", source[start:index])
    raise ValueError("Unclosed microphone PCM callback")


def check_sources(root=ROOT):
    harness = (root / HARNESS).read_text(encoding="utf-8")
    include = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*mod\s+voice_packetizer\s*;', harness)
    expected = (root / "src/client/voice_packetizer.rs").resolve()
    if not include or (root / "tests" / include[1]).resolve() != expected:
        raise ValueError("Voice harness must include the production packetizer")
    if re.search(r"\b(?:struct\s+VoicePacketizer|fn\s+push)\b", harness):
        raise ValueError("Voice harness must not copy the packetizer implementation")
    control_include = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*mod\s+ios_voice\s*;', harness)
    if not control_include or (root / "tests" / control_include[1]).resolve() != (root / "src/client/ios_voice.rs").resolve():
        raise ValueError("Voice harness must include the production cancellation controls")
    source = (root / "src/client/ios_voice.rs").read_text(encoding="utf-8")
    if re.search(r"\b(?:CLIENT_SERVER|audio_service|unbounded_channel|recv_timeout)\b", source):
        raise ValueError("iOS voice must not depend on a server, unbounded PCM, or blocking async startup")
    if re.search(r"Data::(?:Message|CloseVoiceCall)\b", source):
        raise ValueError("All microphone messages and failures must be scoped to their voice request token")
    for event in re.finditer(r"Data::IosVoice(?:Message|Failed)\(\s*([^,]+),", source):
        if event[1].strip() != "request_timestamp":
            raise ValueError("Microphone event must carry its original voice request token unchanged")
    if "tokio::time::timeout(STARTUP_TIMEOUT, ready_rx).await" not in source:
        raise ValueError("iOS voice startup must asynchronously await readiness with a timeout")
    if "ArrayQueue::<PcmBlock>::new(PCM_QUEUE_BLOCKS)" not in source:
        raise ValueError("Microphone PCM queue must have a fixed bound")
    body = callback_body(source)
    if re.search(r"(?:Encoder|encode_|Data::|\.send\(|log::|Vec\b|Mutex\b)", body):
        raise ValueError("Microphone callback must only copy into its bounded PCM queue")
    if "capture_queue.push(block)" not in body or "copy_from_slice(chunk)" not in body:
        raise ValueError("Microphone callback must copy PCM through the bounded queue")
    format_match = re.search(r"queue_voice_message\(\s*sender,\s*request_timestamp,\s*audio_format_message\(\)", source)
    format_send = format_match.start() if format_match else -1
    encode = source.find(".encode_float(frame, &mut output)")
    ready = source.find(".send(Ok(()))")
    frame_send = source.find("message.set_audio_frame(")
    if not (0 <= encode < format_send < ready < frame_send):
        raise ValueError("Ready requires real encoded PCM and format must precede audio frames")
    if "Application::Voip" not in source or "Channels::Mono" not in source:
        raise ValueError("Voice encoder must use mono Opus Voip")
    compact = re.sub(r"\s+", "", source)
    if "Data::IosVoiceFailed(request_timestamp," not in compact or "drop(stream);whilequeue.pop().is_some(){}packetizer.reset();" not in compact:
        raise ValueError("Voice errors/teardown must close the call and discard queued PCM")
    if not re.search(r"pub\s+async\s+fn\s+start\(\s*sender:\s*UnboundedSender<Data>,\s*request_timestamp:\s*i64", source):
        raise ValueError("Voice start must receive the scoped voice request token")
    if "if stopped(stop, cancelled)" not in source[encode:format_send]:
        raise ValueError("Cancelled voice startup must be checked again after encoding before readiness")
    if "constMAX_PENDING_VOICE_MESSAGES:usize=12;" not in compact:
        raise ValueError("Encoded voice messages must be bounded to twelve pending messages")
    if "Data::IosVoiceMessage(request_timestamp,message,counter.clone())" not in compact:
        raise ValueError("Each encoded voice message must carry a pending-credit counter")
    if "letcounter=credit.handoff();" not in compact or "counter.fetch_sub(1,Ordering::Release);" not in compact:
        raise ValueError("Encoded message send failures must return their pending credit")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--rustc", default="rustc")
    args = parser.parse_args(argv)
    try:
        check_sources()
        print("PASS: iOS voice source/bounded callback contracts", flush=True)
        if args.source_only:
            print("SOURCE ONLY: Rust packetizer unit tests were not run")
            return 0
        rustc = shutil.which(args.rustc)
        if not rustc:
            print("Rust compiler unavailable; no Rust voice tests ran. Use --source-only for limited checks.", file=sys.stderr)
            return 2
        subprocess.run([rustc, "--version"], check=True)
        with tempfile.TemporaryDirectory(prefix="linkremote-ios-voice-") as directory:
            output = Path(directory) / ("ios_voice_tests.exe" if sys.platform == "win32" else "ios_voice_tests")
            subprocess.run([rustc, "--edition=2021", "--test", str(ROOT / HARNESS), "-o", str(output)], check=True)
            subprocess.run([str(output), "--nocapture"], check=True)
        print("PASS: production PCM packetizer/cancellation Rust tests; no device, native Opus, microphone permissions or duplex session tested")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
