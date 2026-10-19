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


def method_body(source, name):
    match = re.search(r"\bfn\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{", source)
    if not match:
        raise ValueError(f"Missing session method: {name}")
    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            return re.sub(r"\s+", "", source[start:index])
    raise ValueError(f"Unclosed session method: {name}")


def event_body(source, marker):
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"Missing voice event: {marker}")
    start += len(marker)
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            return re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", source[start:index]))
    raise ValueError(f"Unclosed voice event: {marker}")


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
    if not re.search(r"pub\s+fn\s+start\(\s*sender:\s*UnboundedSender<Data>,\s*request_timestamp:\s*i64,\s*owner:\s*usize,\s*epoch:\s*u64", source):
        raise ValueError("Voice start must eagerly receive the scoped request, owner and epoch")
    if "if stopped(stop, cancelled)" not in source[encode:format_send]:
        raise ValueError("Cancelled voice startup must be checked again after encoding before readiness")
    if "constMAX_PENDING_VOICE_MESSAGES:usize=12;" not in compact:
        raise ValueError("Encoded voice messages must be bounded to twelve pending messages")
    if "Data::IosVoiceMessage(request_timestamp,message,counter.clone())" not in compact:
        raise ValueError("Each encoded voice message must carry a pending-credit counter")
    if "letcounter=credit.handoff();" not in compact or "counter.fetch_sub(1,Ordering::Release);" not in compact:
        raise ValueError("Encoded message send failures must return their pending credit")
    register = source.find("let registration = register_owner(owner, epoch, &cancelled);")
    future = source.find("async move {", register)
    thread = source.find("std::thread::Builder::new()", register)
    if not (0 <= register < future < thread):
        raise ValueError("Owner cancellation must register eagerly before future polling and thread creation")
    if "state.epoch!=epoch" not in compact or "state.epoch=state.epoch.saturating_add(1);" not in compact:
        raise ValueError("Cancelled owner epochs must invalidate late remote accepts")
    if "state.epoch==self.epoch&&Weak::ptr_eq(&state.cancelled,&self.cancelled)" not in compact:
        raise ValueError("An old worker may clear only its own owner epoch and weak flag")
    session = (root / "src/ui_session_interface.rs").read_text(encoding="utf-8")
    cancel = "crate::client::ios_voice::cancel_owner(Arc::as_ptr(&self.sender)asusize);"
    for name, event in (("close", "Close"), ("close_voice_call", "CloseVoiceCall")):
        body = method_body(session, name)
        cancel_at = body.find(cancel)
        send_at = body.find(f"self.send(Data::{event});")
        if not (0 <= cancel_at < send_at):
            raise ValueError("Session close must cancel microphone synchronously before queued network close")
    request = method_body(session, "request_voice_call")
    if "arm_owner(Arc::as_ptr(&self.sender)asusize)" not in request or "Data::IosNewVoiceCall(epoch)" not in request:
        raise ValueError("iOS voice UI must arm an owner epoch before queuing its request")
    io_loop = (root / "src/client/io_loop.rs").read_text(encoding="utf-8")
    current = method_body(io_loop, "ios_voice_event_is_current")
    if not all(part in current for part in (
        "self.ios_voice_timestamp==Some(timestamp)&&self.ios_voice_epoch.map_or(false,|epoch|{",
        "Arc::as_ptr(&self.handler.sender)asusize",
        "super::ios_voice::is_owner_current(owner,epoch)",
    )):
        raise ValueError("Live microphone events must match timestamp and current owner epoch")
    ready = event_body(io_loop, "Data::IosVoiceReady(timestamp, result) => {")
    success = event_body(ready, "Ok(stopper)=>{")
    if not success.startswith("if!self.ios_voice_event_is_current(timestamp){let_=stopper.send(());returntrue;}"):
        raise ValueError("A cancelled Ready success must stop its own recorder before attaching it")
    failure = event_body(ready, "Err(reason)=>{")
    if not failure.startswith("self.stop_voice_call();self.handler.on_voice_call_closed(&reason);"):
        raise ValueError("Ready errors must clear voice state without checking the exited worker's armed flag")
    failed = event_body(io_loop, "Data::IosVoiceFailed(timestamp, reason) => {")
    if not failed.startswith("ifself.ios_voice_timestamp==Some(timestamp){self.stop_voice_call();self.handler.on_voice_call_closed(&reason);"):
        raise ValueError("Worker failure cleanup must match its timestamp, not the exited owner's armed flag")
    message = event_body(io_loop, "Data::IosVoiceMessage(timestamp, message, pending) => {")
    if message != "ifself.ios_voice_event_is_current(timestamp){allow_err!(peer.send(&message).await);}pending.fetch_sub(1,Ordering::Release);":
        raise ValueError("Voice messages must reject cancelled epochs and always return their pending credit")
    stopped = method_body(io_loop, "stop_voice_call")
    if not all(part in stopped for part in (
        "lethad_voice_call=self.voice_call_request_timestamp.is_some()||self.stop_voice_call_sender.is_some();",
        "||self.ios_voice_timestamp.is_some()||self.ios_voice_epoch.is_some()||self.ios_voice_start_task.is_some();",
        "self.voice_call_request_timestamp=None;", "self.ios_voice_timestamp=None;",
        "self.ios_voice_epoch=None;", "task.abort();",
    )) or not stopped.endswith("had_voice_call"):
        raise ValueError("Stopping voice must report and clear pending, starting and active state once")
    closed = event_body(io_loop, "Data::CloseVoiceCall => {")
    if "ifhad_voice_call{self.handler.on_voice_call_closed(\"Closedmanuallybythepeer\");}" not in closed or not closed.startswith("lethad_voice_call=self.stop_voice_call();"):
        raise ValueError("An idle queued close must not re-notify Dart; protocol close must still be sent")
    ack = 'ifhad_voice_call{self.handler.on_voice_call_closed("Closedmanuallybythepeer");}#[cfg(target_os="ios")]self.handler.on_voice_call_close_ack();allow_err!(peer.send(&msg).await);'
    if ack not in closed:
        raise ValueError("iOS close ack must always follow local stop/optional closed and precede network await")
    if '#[cfg(target_os = "ios")]\n    fn on_voice_call_close_ack(&self) {}' not in session:
        raise ValueError("Close ack must have an iOS-only default UI interface method")
    flutter = (root / "src/flutter.rs").read_text(encoding="utf-8")
    ack_event = method_body(flutter, "on_voice_call_close_ack")
    if ack_event != 'self.push_event::<&str>("on_voice_call_close_ack",&[],&[]);':
        raise ValueError("Close ack must use the same Flutter session stream as voice closed events")
    remote_close = event_body(io_loop, "Some(message::Union::VoiceCallRequest(request)) => {")
    if "ifself.stop_voice_call(){self.handler.on_voice_call_closed(\"\");}" not in remote_close:
        raise ValueError("An idle repeated remote close must not re-notify Dart")
    if "// The peer refused the voice call.\n                                self.stop_voice_call();" not in io_loop:
        raise ValueError("Refused requests must clear their owner epoch before notifying Dart")
    model = (root / "flutter/lib/models/model.dart").read_text(encoding="utf-8")
    dispatch = event_body(model, "else if (name == 'on_voice_call_close_ack') {")
    if dispatch != "parent.target?.chatModel.onVoiceCallCloseAck();":
        raise ValueError("Flutter close ack dispatch must synchronously complete ChatModel's barrier")
    chat = (root / "flutter/lib/models/chat_model.dart").read_text(encoding="utf-8")
    close_call = event_body(chat, "Future<void> _closeIosVoiceCall() async {")
    register_ack = close_call.find("_iosVoiceCloseAck=pending;")
    enqueue_close = close_call.find("awaitbind.sessionCloseVoiceCall(sessionId:sessionId);")
    await_ack = close_call.find("if(pending!=null&&!_voiceDisposed)awaitpending.future;")
    if not (0 <= register_ack < enqueue_close < await_ack) or ".timeout(" in close_call:
        raise ValueError("ChatModel must register its close barrier before FFI and await the real ack without a timeout")
    if "closeCall:_closeIosVoiceCall," not in re.sub(r"\s+", "", chat):
        raise ValueError("The iOS coordinator must await ChatModel's acknowledged close callback")
    ack_handler = event_body(chat, "void onVoiceCallCloseAck() {")
    if "_iosVoiceCloseAck=null;" not in ack_handler or "pending.complete();" not in ack_handler:
        raise ValueError("ChatModel must release and clear the pending close barrier on its ack")
    coordinator = (root / "flutter/lib/utils/ios_voice_call.dart").read_text(encoding="utf-8")
    request_call = event_body(coordinator, "Future<bool> request() async {")
    if not request_call.startswith("if(_disposed||_requesting||_active||_stopping)returnfalse;"):
        raise ValueError("iOS redial must remain fenced throughout acknowledged closing")
    stop = event_body(coordinator, "Future<void> stop({bool sendClose = true}) async {")
    native_end = stop.find("if(key!=null)await_endNative(key);")
    await_close = stop.find("awaitclosing;")
    release_barrier = stop.find("_stopping=false;")
    if not (0 <= native_end < await_close < release_barrier) or "awaitrequesting;" not in stop[:native_end]:
        raise ValueError("Native audio end must not wait for close ack, but redial must wait for it and request enqueue")


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
