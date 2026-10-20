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
    marker = "move |data| {"
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
            return re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", source[start:index]))
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
    credit_include = re.search(r'#\[path\s*=\s*"([^"]+)"\]\s*mod\s+ios_audio_credit\s*;', harness)
    if not credit_include or (root / "tests" / credit_include[1]).resolve() != (root / "src/client/ios_audio_credit.rs").resolve():
        raise ValueError("Voice harness must include the production downlink frame credits")
    if re.search(r"\bstruct\s+AudioFrameCredit\b", harness):
        raise ValueError("Voice harness must not copy the downlink credit implementation")
    credit_source = (root / "src/client/ios_audio_credit.rs").read_text(encoding="utf-8")
    credit_compact = re.sub(r"\s+", "", credit_source)
    if "constMAX_PENDING_AUDIO_FRAMES:usize=6;" not in credit_compact or "count<MAX_PENDING_AUDIO_FRAMES" not in credit_compact or "self.0.fetch_sub(1,Ordering::Release);" not in credit_compact:
        raise ValueError("Encoded downlink credits must bound six frames and return on Drop")
    source = (root / "src/client/ios_voice.rs").read_text(encoding="utf-8")
    if re.search(r"\b(?:CLIENT_SERVER|audio_service|unbounded_channel)\b", source):
        raise ValueError("iOS voice must not depend on a server, unbounded PCM, or blocking async startup")
    async_start = source[source.index("pub fn start("):source.index("#[cfg(test)]")]
    if re.search(r"\b(?:recv_timeout|thread::sleep)\b", async_start):
        raise ValueError("iOS voice must not depend on a server, unbounded PCM, or blocking async startup")
    if 'if super::ios_duplex::is_quarantined() {' not in async_start:
        raise ValueError("Quarantined native audio must reject another call before device startup")
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
    if "cpal::" in source or "build_input_stream" in source:
        raise ValueError("iOS call capture must use the shared VoiceProcessingIO, not a separate RemoteIO")
    binding = method_body(source, "bind_playback")
    guard_at = binding.find("letbinding=PlaybackBinding{")
    bind_at = binding.find("sender.send(MediaData::IosVoicePlaybackBind{")
    if not (0 <= guard_at < bind_at) or "acknowledged.recv_timeout(POLL_INTERVAL)" not in binding or "started.elapsed()>=PLAYBACK_BIND_TIMEOUT" not in binding:
        raise ValueError("Playback binding must guard before enqueue and wait for a bounded decoder acknowledgement on its worker")
    record = method_body(source, "record")
    bound_at = record.find("letmutbinding=bind_playback(")
    capture_at = record.find("letstarting=Duplex::start(")
    if not (0 <= bound_at < capture_at) or "binding.restore=false;" not in record[bound_at:capture_at]:
        raise ValueError("VoiceProcessingIO cannot start before exclusive playback binding or restore on uncertain startup")
    if "VoicePacketizer::new(VOICE_SAMPLE_RATE,1)" not in record or "playback.is_failed()" not in record or "playback.is_cancelled()" not in record:
        raise ValueError("Duplex capture must use fixed mono 48k PCM and observe native route failure/cancellation")
    if record.count("letcleanup=stream.stop();binding.restore=playback.is_native_released()&&super::ios_duplex::playback_can_restore();drop(stream);") != 2:
        raise ValueError("Normal audio playback cannot restore before confirmed native duplex teardown")
    if "match(result,cleanup){(Err(reason),_)=>Err(reason),(Ok(()),Err(reason))=>Err(reason),(Ok(()),Ok(()))=>Ok(()),}" not in record:
        raise ValueError("A safely disposed native unit must still report its stop/uninitialize error")
    if 'ifself.restore{let_=self.sender.send(MediaData::IosVoicePlaybackEnd{epoch:self.epoch});}' not in re.sub(r"\s+", "", source):
        raise ValueError("An uncertain native teardown must quarantine playback instead of sending End")
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
    # The non-iOS microphone forwarding code has an identically named arm.
    # Scope this contract to actual inbound peer handling, not that recorder.
    peer_handler = io_loop[io_loop.index("async fn handle_msg_from_peer("):]
    downlink = event_body(peer_handler, "Some(message::Union::AudioFrame(frame)) => {")
    if 'if!self.handler.lc.read().unwrap().disable_audio.v{#[cfg(target_os="ios")]ifletSome(credit)=super::ios_audio_credit::AudioFrameCredit::reserve(&self.ios_audio_pending){let_=self.audio_sender.send(MediaData::IosAudioFrame{frame:Box::new(frame),credit,});}#[cfg(not(target_os="ios"))]self.audio_sender.send(MediaData::AudioFrame(Box::new(frame))).ok();}' != downlink:
        raise ValueError("iOS downlink must reserve credit before enqueue and preserve the non-iOS frame path")
    client = (root / "src/client.rs").read_text(encoding="utf-8")
    decoded = event_body(client, "MediaData::IosAudioFrame { frame, credit: _credit } => {")
    if decoded != "audio_handler.handle_frame(*frame);":
        raise ValueError("Decoder must retain the downlink credit through handle_frame completion")
    audio_format = method_body(client, "handle_format")
    valid_format = event_body(audio_format, "Ok(d)=>{")
    remembered_format = "self.ios_last_audio_format=Some(f.clone());"
    if audio_format.count(remembered_format) != 1 or valid_format.count(remembered_format) != 1:
        raise ValueError("iOS playback restore format must be saved only after AudioDecoder validates it")
    ordinary_audio = method_body(client[client.index('#[cfg(not(target_os = "linux"))]\n    fn start_audio'):], "start_audio")
    guard = '#[cfg(target_os="ios")]if!ios_duplex::playback_can_restore(){bail!("iOSordinaryaudioisunavailablewhilevoicedeviceownershipisunresolved");}'
    if guard not in ordinary_audio or ordinary_audio.index(guard) > ordinary_audio.index("letdevice=AUDIO_HOST"):
        raise ValueError("Ordinary playback must not bypass unresolved VoiceProcessingIO ownership")
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
    if not failure.startswith("self.stop_voice_call();self.notify_voice_call_failed(&reason);"):
        raise ValueError("Ready errors must clear voice state without checking the exited worker's armed flag")
    failed = event_body(io_loop, "Data::IosVoiceFailed(timestamp, reason) => {")
    if not failed.startswith("ifself.ios_voice_timestamp==Some(timestamp){self.stop_voice_call();self.notify_voice_call_failed(&reason);"):
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
    if "ifhad_voice_call{self.notify_voice_call_closed(\"Closedmanuallybythepeer\");}" not in closed or not closed.startswith("lethad_voice_call=self.stop_voice_call();"):
        raise ValueError("An idle queued close must not re-notify Dart; protocol close must still be sent")
    ack = 'ifhad_voice_call{self.notify_voice_call_closed("Closedmanuallybythepeer");}#[cfg(target_os="ios")]self.ios_voice_event("close_ack","");allow_err!(peer.send(&msg).await);'
    if ack not in closed:
        raise ValueError("iOS close ack must always follow local stop/optional closed and precede network await")
    if '#[cfg(target_os = "ios")]\n    fn on_ios_voice_call_session_event(&self, _round: u32, _state: &str, _reason: &str) {}' not in session:
        raise ValueError("Scoped voice events must have an iOS-only default UI interface method")
    flutter = (root / "src/flutter.rs").read_text(encoding="utf-8")
    scoped_event = method_body(flutter, "on_ios_voice_call_session_event")
    if scoped_event != 'self.push_event("on_ios_voice_call_session_event",&[("round",round.to_string().as_str()),("state",state),("reason",reason)],&[],);':
        raise ValueError("Scoped voice events must carry round/state/reason on the same Flutter session stream")
    remote_close = event_body(io_loop, "Some(message::Union::VoiceCallRequest(request)) => {")
    if "ifself.stop_voice_call(){self.notify_voice_call_closed(\"\");}" not in remote_close:
        raise ValueError("An idle repeated remote close must not re-notify Dart")
    if "// The peer refused the voice call.\n                                self.stop_voice_call();" not in io_loop:
        raise ValueError("Refused requests must clear their owner epoch before notifying Dart")
    model = (root / "flutter/lib/models/model.dart").read_text(encoding="utf-8")
    dispatch = event_body(model, "else if (name == 'on_ios_voice_call_session_event') {")
    if dispatch != "finalround=int.tryParse(evt['round']??'');if(round!=null){parent.target?.chatModel.onIosVoiceCallSessionEvent(round,evt['state']??'',evt['reason']??'');}":
        raise ValueError("Flutter scoped event dispatch must validate the round before reaching ChatModel")
    chat = (root / "flutter/lib/models/chat_model.dart").read_text(encoding="utf-8")
    coordinator = (root / "flutter/lib/utils/ios_voice_call.dart").read_text(encoding="utf-8")
    chat_event = event_body(chat, "void onIosVoiceCallSessionEvent(int round, String state, String reason) {")
    if "if(!_iosVoiceSession!.event(round,state,reason))return;" not in chat_event:
        raise ValueError("ChatModel must reject stale voice rounds before changing UI state")
    close_call = event_body(coordinator, "Future<void> _closeRound(int round) async {")
    register_ack = close_call.find("_closeAck=pending;")
    enqueue_close = close_call.find("awaitcloseCall();")
    await_ack = close_call.find("if(!_disposed&&!_ended&&round==_round)awaitpending.future;")
    if not (0 <= register_ack < enqueue_close < await_ack) or ".timeout(" in close_call:
        raise ValueError("Session must register its close barrier before FFI and await the real ack without a timeout")
    retire = event_body(coordinator, "void _retire() {")
    if not retire.startswith("_acknowledgeClose();") or "call.disposeAsync(sendClose:false)" not in retire or "_retiring=retired;" not in retire:
        raise ValueError("Terminal round must release its dead-loop ack and drain the retired coordinator")
    session_request = event_body(coordinator, "Future<bool> request() async {")
    if not session_request.startswith("if(_disposed||_ended||_retiring!=null)returnfalse;"):
        raise ValueError("A new round must wait for retired FFI operations to drain")
    scoped = method_body(io_loop, "ios_voice_event")
    terminal = method_body(io_loop, "end_ios_voice_session")
    if scoped != "ifletSome(round)=self.ios_voice_round{self.handler.on_ios_voice_call_session_event(round,state,reason);}":
        raise ValueError("Every iOS voice event must carry its owning connection round")
    if terminal != 'ifletSome(round)=self.ios_voice_round.take(){self.handler.on_ios_voice_call_session_event(round,"ended","");}':
        raise ValueError("Terminal event must take its round exactly once")
    for name, state in (("notify_voice_call_waiting", "waiting"), ("notify_voice_call_started", "connected"), ("notify_voice_call_closed", "closed")):
        notify = method_body(io_loop, name)
        argument = "reason" if state == "closed" else '""'
        if f'#[cfg(target_os="ios")]self.ios_voice_event("{state}",{argument});' not in notify or '#[cfg(not(target_os="ios"))]self.handler.on_voice_call_' not in notify:
            raise ValueError("Voice notifications must scope iOS while preserving non-iOS behavior")
    disconnected = method_body(io_loop, "handle_disconnected")
    if not disconnected.startswith('#[cfg(target_os="ios")]{self.stop_voice_call();self.end_ios_voice_session();}'):
        raise ValueError("Disconnect must stop capture then emit the terminal round before other cleanup")
    remote_drop = re.search(r"impl<T: InvokeUiSession> Drop for Remote<T>\s*\{(.*?)\n\}", io_loop, re.S)
    if not remote_drop or "self.stop_voice_call();" not in remote_drop[1] or "self.end_ios_voice_session();" not in remote_drop[1]:
        raise ValueError("Aborting a remote must stop capture and emit its terminal round")
    session_loop = method_body(io_loop, "io_loop")
    if 'if!self.handler.connection_round_state.lock().unwrap().is_round_gt(round){super::ios_voice::cancel_owner(Arc::as_ptr(&self.handler.sender)asusize);self.ios_voice_round=Some(round);self.ios_voice_event("started","");}' not in session_loop:
        raise ValueError("A current reconnect must synchronously cancel old capture before its started event")
    voice_request = method_body(io_loop, "send_voice_call_request")
    muted = 'ifself.handler.lc.read().unwrap().disable_audio.v{super::ios_voice::cancel_owner(owner);self.stop_voice_call();self.ios_voice_event("failed","请先启用远程声音再发起语音。");return;}'
    if muted not in voice_request or voice_request.index(muted) > voice_request.index("new_voice_call_request(true)"):
        raise ValueError("Muted iOS downlink must fail closed before requesting or recording voice")
    if "if(closed||streamGeneration!=_sessionStreamGeneration)return;" not in re.sub(r"\s+", "", model):
        raise ValueError("A reused mobile FFI must ignore events from its previous UI stream")
    ui_close = event_body(model, "Future<void> close({bool closeSession = true}) async {")
    if not ui_close.startswith("chatModel.onRemoteSessionClosed();closed=true;"):
        raise ValueError("Closing the UI must end voice before terminal stream events are ignored")
    # The file also has IosVoiceCallSession.request; select the coordinator's
    # request in its own class rather than matching the first same-named method.
    coordinator_class = coordinator[coordinator.index("class IosVoiceCallCoordinator"):]
    request_call = event_body(coordinator_class, "Future<bool> request() async {")
    if not request_call.startswith("if(_disposed||_requesting||_active||_stopping)returnfalse;"):
        raise ValueError("iOS redial must remain fenced throughout acknowledged closing")
    stop = event_body(coordinator_class, "Future<void> _stop({required bool sendClose}) async {")
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
