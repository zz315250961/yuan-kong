//! iOS controller microphone uplink. No server-side audio service is used.
//!
//! Swift must authorize and activate AVAudioSession before calling `start`.
//! The CPAL stream never leaves its owner thread; its real-time callback only
//! copies PCM into a bounded queue. Resampling, Opus and transport run off it.

#[cfg(target_os = "ios")]
use super::{
    voice_packetizer::{VoicePacketizer, VOICE_SAMPLE_RATE},
    Data,
};
#[cfg(target_os = "ios")]
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
#[cfg(target_os = "ios")]
use crossbeam_queue::ArrayQueue;
#[cfg(target_os = "ios")]
use hbb_common::{
    log,
    message_proto::{AudioFormat, AudioFrame, Message, Misc},
    tokio::{self, sync::mpsc::UnboundedSender, sync::oneshot},
};
#[cfg(target_os = "ios")]
use magnum_opus::{Application, Bitrate, Channels, Encoder};
use std::sync::{
    atomic::{AtomicBool, AtomicUsize, Ordering},
    mpsc::{Receiver, Sender, TryRecvError},
    Arc,
};
#[cfg(target_os = "ios")]
use std::{
    sync::mpsc,
    time::{Duration, Instant},
};

#[cfg(target_os = "ios")]
const PCM_BLOCK_SAMPLES: usize = 1_024;
#[cfg(target_os = "ios")]
const PCM_QUEUE_BLOCKS: usize = 8;
#[cfg(target_os = "ios")]
const STARTUP_TIMEOUT: Duration = Duration::from_secs(5);
#[cfg(target_os = "ios")]
const CAPTURE_IDLE_TIMEOUT: Duration = Duration::from_secs(3);
#[cfg(target_os = "ios")]
const POLL_INTERVAL: Duration = Duration::from_millis(5);
#[cfg(target_os = "ios")]
const OPUS_PACKET_BYTES: usize = 1_275;

#[cfg(target_os = "ios")]
struct PcmBlock {
    samples: [f32; PCM_BLOCK_SAMPLES],
    len: usize,
}

/// Dropping a pending start future also stops its recorder. It must not leave
/// an unowned microphone running after navigation or an async startup timeout.
struct PendingStart {
    stop: Option<Sender<()>>,
    cancelled: Arc<AtomicBool>,
}

const MAX_PENDING_VOICE_MESSAGES: usize = 12;

/// One bounded slot in the main I/O queue. Before handoff, a failed build/send
/// automatically gives its slot back. After handoff, the I/O receiver releases
/// it exactly once after sending, rejecting, or ignoring the scoped message.
struct VoiceMessageCredit {
    pending: Arc<AtomicUsize>,
    handed_off: bool,
}

impl VoiceMessageCredit {
    fn reserve(pending: &Arc<AtomicUsize>) -> Option<Self> {
        pending
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                if count < MAX_PENDING_VOICE_MESSAGES { Some(count + 1) } else { None }
            })
            .ok()?;
        Some(Self { pending: pending.clone(), handed_off: false })
    }

    fn handoff(mut self) -> Arc<AtomicUsize> {
        self.handed_off = true;
        self.pending.clone()
    }
}

impl Drop for VoiceMessageCredit {
    fn drop(&mut self) {
        if !self.handed_off {
            self.pending.fetch_sub(1, Ordering::Release);
        }
    }
}

impl Drop for PendingStart {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            self.cancelled.store(true, Ordering::Release);
            let _ = stop.send(());
        }
    }
}

#[cfg(target_os = "ios")]
pub async fn start(
    sender: UnboundedSender<Data>,
    request_timestamp: i64,
) -> Result<Sender<()>, String> {
    let (stop, stop_rx) = mpsc::channel();
    let (ready, ready_rx) = oneshot::channel();
    let cancelled = Arc::new(AtomicBool::new(false));
    let worker_cancelled = cancelled.clone();
    let worker_sender = sender.clone();
    let mut pending = PendingStart {
        stop: Some(stop.clone()),
        cancelled,
    };
    std::thread::Builder::new()
        .name("linkremote-ios-voice".to_owned())
        .spawn(move || {
            let mut ready = Some(ready);
            if let Err(reason) = record(
                &worker_sender,
                request_timestamp,
                &stop_rx,
                &worker_cancelled,
                &mut ready,
            ) {
                log::warn!("iOS microphone uplink stopped: {}", reason);
                if !stopped(&stop_rx, &worker_cancelled) {
                    let _ = worker_sender.send(Data::IosVoiceFailed(request_timestamp, reason.clone()));
                }
                if let Some(ready) = ready.take() {
                    let _ = ready.send(Err(reason));
                }
            }
        })
        .map_err(|error| {
            let reason = format!("Failed to start iOS microphone worker: {}", error);
            let _ = sender.send(Data::IosVoiceFailed(request_timestamp, reason.clone()));
            reason
        })?;

    // No blocking receive, nested runtime, or device work on the Tokio thread.
    let result = match tokio::time::timeout(STARTUP_TIMEOUT, ready_rx).await {
        Ok(Ok(Ok(()))) => {
            pending.stop.take();
            Ok(stop)
        }
        Ok(Ok(Err(reason))) => Err(reason),
        Ok(Err(_)) => Err("iOS microphone startup was cancelled".to_owned()),
        Err(_) => Err("Timed out waiting for iOS microphone audio".to_owned()),
    };
    if let Err(reason) = &result {
        let _ = sender.send(Data::IosVoiceFailed(request_timestamp, reason.clone()));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{future::Future, sync::mpsc, task::{Context, Poll, Wake, Waker}};

    #[test]
    fn pending_start_drop_sends_stop_and_sets_cancellation_flag() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let pending = PendingStart { stop: Some(stop), cancelled: cancelled.clone() };
        drop(pending);
        assert!(cancelled.load(Ordering::Acquire));
        assert_eq!(receiver.try_recv(), Ok(()));
    }

    #[test]
    fn disarmed_guard_does_not_cancel_successful_recording() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let mut pending = PendingStart { stop: Some(stop.clone()), cancelled: cancelled.clone() };
        pending.stop.take();
        drop(pending);
        assert!(!cancelled.load(Ordering::Acquire));
        assert_eq!(receiver.try_recv(), Err(TryRecvError::Empty));
        drop(stop);
    }

    #[test]
    fn explicit_stop_is_latched_after_its_channel_message_is_consumed() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = AtomicBool::new(false);
        stop.send(()).unwrap();
        assert!(stopped(&receiver, &cancelled));
        assert_eq!(receiver.try_recv(), Err(TryRecvError::Empty));
        assert!(cancelled.load(Ordering::Acquire));
        assert!(stopped(&receiver, &cancelled));
    }

    #[test]
    fn losing_the_returned_stopper_also_stops_the_recorder() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = AtomicBool::new(false);
        assert!(!stopped(&receiver, &cancelled));
        drop(stop);
        assert!(stopped(&receiver, &cancelled));
        assert!(cancelled.load(Ordering::Acquire));
    }

    #[test]
    fn empty_stop_channel_does_not_cancel_live_recording() {
        let (_stop, receiver) = mpsc::channel();
        let cancelled = AtomicBool::new(false);
        for _ in 0..3 {
            assert!(!stopped(&receiver, &cancelled));
        }
        assert!(!cancelled.load(Ordering::Acquire));
    }

    #[test]
    fn cancellation_flag_is_observed_without_waiting_for_a_stop_message() {
        let (_stop, receiver) = mpsc::channel();
        let cancelled = AtomicBool::new(true);
        assert!(stopped(&receiver, &cancelled));
        assert_eq!(receiver.try_recv(), Err(TryRecvError::Empty));
    }

    struct NoopWake;
    impl Wake for NoopWake {
        fn wake(self: Arc<Self>) {}
    }

    #[test]
    fn dropping_a_polled_pending_future_executes_the_real_start_guard() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let future_cancelled = cancelled.clone();
        let mut future = Box::pin(async move {
            let _pending = PendingStart { stop: Some(stop), cancelled: future_cancelled };
            std::future::pending::<()>().await;
        });
        let waker = Waker::from(Arc::new(NoopWake));
        let mut context = Context::from_waker(&waker);
        assert_eq!(future.as_mut().poll(&mut context), Poll::Pending);
        assert!(!cancelled.load(Ordering::Acquire));
        drop(future);
        assert!(cancelled.load(Ordering::Acquire));
        assert_eq!(receiver.try_recv(), Ok(()));
    }

    #[test]
    fn dropping_unconsumed_ready_result_stops_after_guard_handoff() {
        let (stop, receiver) = mpsc::channel();
        let cancelled = Arc::new(AtomicBool::new(false));
        let mut pending = PendingStart { stop: Some(stop.clone()), cancelled: cancelled.clone() };
        pending.stop.take();
        let result: Result<Sender<()>, String> = Ok(stop);
        drop(pending);
        assert!(!stopped(&receiver, &cancelled));
        // This is the stale Ready-event case: the session drops its result.
        drop(result);
        assert!(stopped(&receiver, &cancelled));
    }

    #[test]
    fn encoded_message_credits_have_a_strict_twelve_message_limit() {
        let pending = Arc::new(AtomicUsize::new(0));
        let mut credits = Vec::new();
        for expected in 1..=MAX_PENDING_VOICE_MESSAGES {
            credits.push(VoiceMessageCredit::reserve(&pending).unwrap());
            assert_eq!(pending.load(Ordering::Acquire), expected);
        }
        assert!(VoiceMessageCredit::reserve(&pending).is_none());
        assert_eq!(pending.load(Ordering::Acquire), 12);
        drop(credits);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn dropping_an_unsent_message_credit_returns_its_slot() {
        let pending = Arc::new(AtomicUsize::new(0));
        let credit = VoiceMessageCredit::reserve(&pending).unwrap();
        assert_eq!(pending.load(Ordering::Acquire), 1);
        drop(credit);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn handed_off_credit_is_released_by_receiver_not_arc_drop() {
        let pending = Arc::new(AtomicUsize::new(0));
        let counter = VoiceMessageCredit::reserve(&pending).unwrap().handoff();
        assert_eq!(pending.load(Ordering::Acquire), 1);
        drop(counter.clone());
        assert_eq!(pending.load(Ordering::Acquire), 1);
        counter.fetch_sub(1, Ordering::Release);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn failed_handoff_rolls_back_credit_before_trying_again() {
        let pending = Arc::new(AtomicUsize::new(0));
        let (sender, receiver) = mpsc::channel();
        drop(receiver);
        let counter = VoiceMessageCredit::reserve(&pending).unwrap().handoff();
        assert!(sender.send(counter.clone()).is_err());
        counter.fetch_sub(1, Ordering::Release);
        assert_eq!(pending.load(Ordering::Acquire), 0);
        assert!(VoiceMessageCredit::reserve(&pending).is_some());
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn simultaneous_workers_cannot_overbook_encoded_message_slots() {
        let pending = Arc::new(AtomicUsize::new(0));
        let accepted = Arc::new(AtomicUsize::new(0));
        let barrier = Arc::new(std::sync::Barrier::new(33));
        let mut workers = Vec::new();
        for _ in 0..32 {
            let pending = pending.clone();
            let accepted = accepted.clone();
            let barrier = barrier.clone();
            workers.push(std::thread::spawn(move || {
                let credit = VoiceMessageCredit::reserve(&pending);
                if credit.is_some() {
                    accepted.fetch_add(1, Ordering::AcqRel);
                }
                // Keep all successful reservations until all attempts finish.
                barrier.wait();
                drop(credit);
            }));
        }
        barrier.wait();
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(accepted.load(Ordering::Acquire), MAX_PENDING_VOICE_MESSAGES);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn corrupted_or_saturated_counter_fails_closed_without_overflow() {
        for count in [MAX_PENDING_VOICE_MESSAGES, MAX_PENDING_VOICE_MESSAGES + 1, usize::MAX] {
            let pending = Arc::new(AtomicUsize::new(count));
            assert!(VoiceMessageCredit::reserve(&pending).is_none());
            assert_eq!(pending.load(Ordering::Acquire), count);
        }
    }
}

fn stopped(stop: &Receiver<()>, cancelled: &AtomicBool) -> bool {
    if cancelled.load(Ordering::Acquire) {
        return true;
    }
    if matches!(stop.try_recv(), Ok(()) | Err(TryRecvError::Disconnected)) {
        // Remember a consumed stop so an encode-loop early exit cannot report
        // a normal user cancellation as a new failure and close a later call.
        cancelled.store(true, Ordering::Release);
        return true;
    }
    false
}

#[cfg(target_os = "ios")]
fn audio_format_message() -> Message {
    let mut misc = Misc::new();
    misc.set_audio_format(AudioFormat {
        sample_rate: VOICE_SAMPLE_RATE,
        channels: 1,
        ..Default::default()
    });
    let mut message = Message::new();
    message.set_misc(misc);
    message
}

#[cfg(target_os = "ios")]
fn queue_voice_message(
    sender: &UnboundedSender<Data>,
    request_timestamp: i64,
    message: Message,
    pending: &Arc<AtomicUsize>,
) -> Result<bool, String> {
    let Some(credit) = VoiceMessageCredit::reserve(pending) else {
        return Ok(false);
    };
    let counter = credit.handoff();
    if sender
        .send(Data::IosVoiceMessage(request_timestamp, message, counter.clone()))
        .is_err()
    {
        counter.fetch_sub(1, Ordering::Release);
        return Err("Voice session transport is closed".to_owned());
    }
    Ok(true)
}

#[cfg(target_os = "ios")]
fn record(
    sender: &UnboundedSender<Data>,
    request_timestamp: i64,
    stop: &Receiver<()>,
    cancelled: &Arc<AtomicBool>,
    ready: &mut Option<oneshot::Sender<Result<(), String>>>,
) -> Result<(), String> {
    if stopped(stop, cancelled) || sender.is_closed() {
        return Ok(());
    }
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| "No iOS microphone input device is available".to_owned())?;
    let supported = device
        .default_input_config()
        .map_err(|error| format!("Cannot read iOS microphone format: {}", error))?;
    if stopped(stop, cancelled) {
        return Ok(());
    }
    if supported.sample_format() != cpal::SampleFormat::F32 {
        return Err("Unsupported iOS microphone PCM format; expected float32".to_owned());
    }
    let mut packetizer = VoicePacketizer::new(supported.sample_rate().0, supported.channels())
        .map_err(str::to_owned)?;
    let mut encoder = Encoder::new(VOICE_SAMPLE_RATE, Channels::Mono, Application::Voip)
        .map_err(|error| format!("Cannot create voice encoder: {}", error))?;
    encoder
        .set_bitrate(Bitrate::Bits(32_000))
        .map_err(|error| format!("Cannot configure voice encoder: {}", error))?;
    encoder
        .set_vbr(true)
        .map_err(|error| format!("Cannot configure voice bitrate mode: {}", error))?;
    if stopped(stop, cancelled) {
        return Ok(());
    }

    let queue = Arc::new(ArrayQueue::<PcmBlock>::new(PCM_QUEUE_BLOCKS));
    let dropped = Arc::new(AtomicUsize::new(0));
    let stream_failed = Arc::new(AtomicBool::new(false));
    let capture_queue = queue.clone();
    let capture_dropped = dropped.clone();
    let capture_cancelled = cancelled.clone();
    let callback_failed = stream_failed.clone();
    let config = cpal::StreamConfig {
        channels: supported.channels(),
        sample_rate: supported.sample_rate(),
        buffer_size: cpal::BufferSize::Default,
    };
    let stream = device
        .build_input_stream::<f32, _, _>(
            &config,
            move |data, _| {
                // Real-time boundary: fixed stack block, copy, bounded queue.
                // No allocations, locks, Opus, logging, or network sends here.
                if capture_cancelled.load(Ordering::Acquire) {
                    return;
                }
                for chunk in data.chunks(PCM_BLOCK_SAMPLES) {
                    let mut block = PcmBlock {
                        samples: [0.0; PCM_BLOCK_SAMPLES],
                        len: chunk.len(),
                    };
                    block.samples[..chunk.len()].copy_from_slice(chunk);
                    if capture_queue.push(block).is_err() {
                        capture_dropped.fetch_add(1, Ordering::Relaxed);
                    }
                }
            },
            move |_| callback_failed.store(true, Ordering::Release),
            None,
        )
        .map_err(|error| format!("Cannot open iOS microphone stream: {}", error))?;
    // Keep this non-Send platform stream in the same worker until teardown.
    if stopped(stop, cancelled) {
        drop(stream);
        while queue.pop().is_some() {}
        packetizer.reset();
        return Ok(());
    }
    stream
        .play()
        .map_err(|error| format!("Cannot start iOS microphone stream: {}", error))?;

    let mut output = [0u8; OPUS_PACKET_BYTES];
    let pending_messages = Arc::new(AtomicUsize::new(0));
    let mut dropped_seen = 0;
    let mut last_audio = Instant::now();
    let result = (|| {
        loop {
            if stopped(stop, cancelled) || sender.is_closed() {
                return Ok(());
            }
            if stream_failed.load(Ordering::Acquire) {
                return Err("iOS microphone stream reported a device error".to_owned());
            }
            let dropped_now = dropped.load(Ordering::Acquire);
            if dropped_now != dropped_seen {
                // Never splice pre-overrun PCM or a partial stereo group into
                // fresh audio. Latency stays bounded instead of growing.
                dropped_seen = dropped_now;
                packetizer.reset();
                while queue.pop().is_some() {}
            }
            if let Some(mut block) = queue.pop() {
                last_audio = Instant::now();
                packetizer.push(&block.samples[..block.len], |frame| {
                    if stopped(stop, cancelled) || stream_failed.load(Ordering::Acquire) {
                        return Err("iOS microphone recording was interrupted".to_owned());
                    }
                    let length = encoder
                        .encode_float(frame, &mut output)
                        .map_err(|error| format!("Cannot encode microphone audio: {}", error))?;
                    if stopped(stop, cancelled) || stream_failed.load(Ordering::Acquire) {
                        return Err("iOS microphone recording was interrupted".to_owned());
                    }
                    if length == 0 || length > output.len() {
                        return Err("Voice encoder returned an invalid packet length".to_owned());
                    }
                    if let Some(ready) = ready.take() {
                        // No connected signal until actual PCM was captured and
                        // encoded. The peer receives its format before audio.
                        if !queue_voice_message(
                            sender,
                            request_timestamp,
                            audio_format_message(),
                            &pending_messages,
                        )? {
                            return Err("Voice format could not enter the bounded send queue".to_owned());
                        }
                        ready
                            .send(Ok(()))
                            .map_err(|_| "iOS microphone startup was cancelled".to_owned())?;
                    }
                    let mut message = Message::new();
                    message.set_audio_frame(AudioFrame {
                        data: output[..length].to_vec().into(),
                        ..Default::default()
                    });
                    if stopped(stop, cancelled) {
                        return Err("iOS microphone recording was cancelled".to_owned());
                    }
                    // A slow peer cannot accumulate unlimited encoded speech
                    // in the shared unbounded Data queue. Drop this audio packet
                    // at 12 pending messages; never block the capture callback.
                    queue_voice_message(sender, request_timestamp, message, &pending_messages)?;
                    Ok(())
                })?;
                block.samples.fill(0.0);
            } else {
                if last_audio.elapsed() >= CAPTURE_IDLE_TIMEOUT {
                    return Err("No microphone audio arrived; the iOS input route may be interrupted".to_owned());
                }
                std::thread::sleep(POLL_INTERVAL);
            }
        }
    })();
    // Stop callbacks first, then discard queued PCM and partial packet state.
    drop(stream);
    while queue.pop().is_some() {}
    packetizer.reset();
    result
}
