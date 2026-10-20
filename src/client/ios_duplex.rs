//! One iOS VoiceProcessingIO owns both the far-end reference and microphone.
//!
//! The caller authorizes AVAudioSession and detaches ordinary CPAL playback
//! before `Duplex::start`. It feeds only this call's decoded mono 48 kHz PCM
//! through `Playback`; capture goes to the caller's existing bounded PCM queue.
//! No fallback to an unprocessed microphone is provided. Memory-only tests use
//! these same queue/lifetime interfaces, not a simulated echo canceller.

use crossbeam_queue::ArrayQueue;
use std::sync::{atomic::{AtomicBool, AtomicU8, Ordering}, Arc};

pub const SAMPLE_RATE: u32 = 48_000;
pub const PLAYBACK_SAMPLES: usize = SAMPLE_RATE as usize * 120 / 1_000;
pub const MAX_CALLBACK_FRAMES: usize = 4_096;
const DEVICE_ACTIVE: u8 = 1;
const DEVICE_QUARANTINED: u8 = 2;
const ORDINARY_PLAYBACK: u8 = 4;
static DEVICE_STATE: AtomicU8 = AtomicU8::new(0);

pub fn playback_can_restore() -> bool {
    DEVICE_STATE.load(Ordering::Acquire) & (DEVICE_ACTIVE | DEVICE_QUARANTINED) == 0
}

pub fn is_quarantined() -> bool {
    DEVICE_STATE.load(Ordering::Acquire) & DEVICE_QUARANTINED != 0
}

// A device that may still have callbacks must never overlap a later call. The
// process quarantine is intentionally permanent; restarting is safer than
// guessing whether a failing Apple disposal left the microphone active.
struct DeviceLease<'a> { state: &'a AtomicU8, owned: bool }

impl<'a> DeviceLease<'a> {
    fn claim(state: &'a AtomicU8) -> Result<Self, String> {
        match state.compare_exchange(0, DEVICE_ACTIVE, Ordering::AcqRel, Ordering::Acquire) {
            Ok(_) => Ok(Self { state, owned: true }),
            Err(value) if value & DEVICE_QUARANTINED != 0 => Err("iOS voice audio is quarantined after a device cleanup error; restart the app".to_owned()),
            Err(value) if value >= ORDINARY_PLAYBACK => Err("iOS ordinary audio output is still active; retry after it stops".to_owned()),
            Err(_) => Err("Another iOS voice-processing device is still active".to_owned()),
        }
    }

    fn release(&mut self) {
        if self.owned {
            self.state.fetch_and(!DEVICE_ACTIVE, Ordering::AcqRel);
            self.owned = false;
        }
    }

    fn quarantine(&mut self) {
        self.state.fetch_or(DEVICE_QUARANTINED, Ordering::AcqRel);
    }
}

impl Drop for DeviceLease<'_> {
    fn drop(&mut self) {
        // A failed native disposal still owns its callback context. Only a
        // later successful disposal may explicitly release that active bit.
        if self.state.load(Ordering::Acquire) & DEVICE_QUARANTINED == 0 { self.release(); }
    }
}

/// Ordinary outputs may coexist, but neither their configuration nor their
/// live streams may overlap VoiceProcessingIO. The same atomic state closes
/// the check-then-create race between an old decoder's End and a new call.
pub struct OrdinaryPlaybackLease<'a> { state: &'a AtomicU8 }

impl OrdinaryPlaybackLease<'static> {
    pub fn acquire() -> Result<Self, String> { Self::claim(&DEVICE_STATE) }
}

impl<'a> OrdinaryPlaybackLease<'a> {
    fn claim(state: &'a AtomicU8) -> Result<Self, String> {
        state.fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
            if value & (DEVICE_ACTIVE | DEVICE_QUARANTINED) == 0 {
                value.checked_add(ORDINARY_PLAYBACK)
            } else { None }
        }).map_err(|value| {
            if value & DEVICE_QUARANTINED != 0 {
                "iOS ordinary audio is quarantined after a cleanup error; restart the app".to_owned()
            } else {
                "iOS ordinary audio cannot start while voice IO is active or output capacity is exhausted".to_owned()
            }
        })?;
        Ok(Self { state })
    }
}

impl Drop for OrdinaryPlaybackLease<'_> {
    fn drop(&mut self) { self.state.fetch_sub(ORDINARY_PLAYBACK, Ordering::Release); }
}

/// Rust drops fields in declaration order: the actual CPAL stream must finish
/// disposal before another thread can acquire exclusive voice ownership.
pub struct OrdinaryPlayback<'a, S> {
    stream: S,
    _lease: OrdinaryPlaybackLease<'a>,
}

impl<'a, S> OrdinaryPlayback<'a, S> {
    pub fn new(stream: S, lease: OrdinaryPlaybackLease<'a>) -> Self {
        Self { stream, _lease: lease }
    }

    pub fn stream(&self) -> &S { &self.stream }
}

/// A bounded, lock-free far-end reference. It is never shared between calls.
pub struct Playback {
    samples: ArrayQueue<f32>,
    cancelled: Arc<AtomicBool>,
    closed: AtomicBool,
    failed: AtomicBool,
    native_released: AtomicBool,
}

impl Playback {
    pub fn new(cancelled: Arc<AtomicBool>) -> Arc<Self> {
        Arc::new(Self {
            samples: ArrayQueue::new(PLAYBACK_SAMPLES),
            cancelled,
            closed: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            native_released: AtomicBool::new(true),
        })
    }

    /// Keep at most the latest 120 ms. Called by the decoder, never by a network
    /// or microphone callback. Non-finite/clipped PCM cannot poison the device.
    pub fn push(&self, samples: &[f32]) -> bool {
        if self.is_cancelled() { return false; }
        let samples = &samples[samples.len().saturating_sub(PLAYBACK_SAMPLES)..];
        for sample in samples {
            if self.is_cancelled() { self.clear(); return false; }
            let sample = if sample.is_finite() { sample.clamp(-1.0, 1.0) } else { 0.0 };
            let _discarded_oldest = self.samples.force_push(sample);
        }
        if self.is_cancelled() { self.clear(); return false; }
        true
    }

    pub fn is_cancelled(&self) -> bool {
        self.closed.load(Ordering::Acquire) || self.cancelled.load(Ordering::Acquire)
    }

    pub fn is_failed(&self) -> bool { self.failed.load(Ordering::Acquire) }

    /// This endpoint's native device was never created, or disposal succeeded.
    /// Restore ordinary playback only when `playback_can_restore()` also holds.
    pub fn is_native_released(&self) -> bool { self.native_released.load(Ordering::Acquire) }

    /// Cancellation is observable before PCM is discarded. Closing an endpoint
    /// never cancels a different endpoint that happens to share an owner flag.
    pub fn close(&self) {
        self.closed.store(true, Ordering::Release);
        self.clear();
    }

    fn clear(&self) {
        // A producer checks cancellation for every sample. Keep cleanup bounded
        // even if that producer was preempted immediately before its last push.
        for _ in 0..PLAYBACK_SAMPLES {
            if self.samples.pop().is_none() { break; }
        }
    }

    fn fail(&self) {
        self.failed.store(true, Ordering::Release);
        self.close();
    }

    /// The native render callback crosses this same memory-only interface.
    /// Underrun, cancellation and errors produce silence, never stale PCM.
    pub(crate) fn render(&self, output: &mut [f32]) -> bool {
        output.fill(0.0);
        if output.len() > MAX_CALLBACK_FRAMES { self.fail(); return false; }
        if self.is_cancelled() { return false; }
        for index in 0..output.len() {
            if self.is_cancelled() { output.fill(0.0); return false; }
            output[index] = self.samples.pop().unwrap_or(0.0);
        }
        if self.is_cancelled() { output.fill(0.0); return false; }
        true
    }

    fn capture(&self, samples: &[f32], capture: &mut impl FnMut(&[f32])) -> bool {
        if samples.len() > MAX_CALLBACK_FRAMES { self.fail(); return false; }
        if self.is_cancelled() { return false; }
        capture(samples);
        !self.is_cancelled()
    }
}

/// Per-session routing with persistent epoch tombstones. The decoder and tests
/// cross the same interface; an old End cannot dismantle a newer call's sink.
#[derive(Default)]
pub struct PlaybackRoute {
    latest: u64,
    current: Option<(u64, Arc<Playback>)>,
}

impl PlaybackRoute {
    pub fn bind(&mut self, epoch: u64, playback: Arc<Playback>) -> bool {
        if epoch == 0 || epoch <= self.latest { return false; }
        self.latest = epoch;
        if playback.is_cancelled() { return false; }
        if let Some((_, previous)) = self.current.replace((epoch, playback)) {
            previous.close();
        }
        true
    }

    pub fn end(&mut self, epoch: u64) -> bool {
        self.latest = self.latest.max(epoch);
        if self.current.as_ref().map(|entry| entry.0) != Some(epoch) { return false; }
        if let Some((_, playback)) = self.current.take() { playback.close(); }
        true
    }

    pub fn current(&self) -> Option<&Arc<Playback>> {
        self.current.as_ref().map(|entry| &entry.1)
    }
}

impl Drop for PlaybackRoute {
    fn drop(&mut self) {
        if let Some((_, playback)) = self.current.take() { playback.close(); }
    }
}

/// Keep this handle on its creating worker. Permission, session activation and
/// exclusive far-end routing must already be established. A successful start
/// means the Apple processing mode was accepted, not that acoustic quality was
/// measured. The first real PCM/Opus packet still gates the caller's readiness.
pub struct Duplex {
    playback: Arc<Playback>,
    #[cfg(target_os = "ios")]
    native: native::NativeDuplex,
    _owner_thread: std::marker::PhantomData<std::rc::Rc<()>>,
}

impl Duplex {
    pub fn start(
        playback: Arc<Playback>,
        capture: impl FnMut(&[f32]) + Send + 'static,
    ) -> Result<Self, String> {
        if playback.is_cancelled() {
            return Err("iOS voice audio was cancelled before device startup".to_owned());
        }
        #[cfg(target_os = "ios")]
        {
            match native::NativeDuplex::start(playback.clone(), capture) {
                Ok(native) => Ok(Self { playback, native, _owner_thread: Default::default() }),
                Err(error) => { playback.fail(); Err(error) }
            }
        }
        #[cfg(not(target_os = "ios"))]
        {
            let _ = capture;
            playback.close();
            Err("Apple voice-processing audio requires an iOS device or Simulator".to_owned())
        }
    }

    pub fn stop(&mut self) -> Result<(), String> {
        self.playback.close();
        #[cfg(target_os = "ios")]
        { self.native.stop() }
        #[cfg(not(target_os = "ios"))]
        { Ok(()) }
    }
}

impl Drop for Duplex {
    fn drop(&mut self) { self.playback.close(); }
}

#[cfg(target_os = "ios")]
mod native {
    use super::*;
    use coreaudio::sys;
    use std::{ffi::c_void, mem, ptr, slice};

    // coreaudio-rs 0.11.3's set_input_callback resizes a Vec when the frame
    // count changes. Use its raw bindings with one startup allocation instead.
    struct CaptureContext {
        unit: sys::AudioUnit,
        playback: Arc<Playback>,
        capture: Box<dyn FnMut(&[f32]) + Send>,
        scratch: [f32; MAX_CALLBACK_FRAMES],
    }

    impl Drop for CaptureContext {
        fn drop(&mut self) { self.scratch.fill(0.0); }
    }

    pub(super) struct NativeDuplex {
        unit: sys::AudioUnit,
        playback: Arc<Playback>,
        capture: Option<Box<CaptureContext>>,
        render: Option<Box<Arc<Playback>>>,
        initialized: bool,
        started: bool,
        lease: DeviceLease<'static>,
    }

    fn status(value: sys::OSStatus, operation: &str) -> Result<(), String> {
        if value == 0 { Ok(()) }
        else { Err(format!("iOS voice {} failed ({})", operation, value)) }
    }

    fn format() -> sys::AudioStreamBasicDescription {
        sys::AudioStreamBasicDescription {
            mSampleRate: SAMPLE_RATE as f64,
            mFormatID: sys::kAudioFormatLinearPCM,
            mFormatFlags: sys::kAudioFormatFlagIsFloat | sys::kAudioFormatFlagIsPacked,
            mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4,
            mChannelsPerFrame: 1, mBitsPerChannel: 32, mReserved: 0,
        }
    }

    impl NativeDuplex {
        pub(super) fn start(
            playback: Arc<Playback>,
            capture: impl FnMut(&[f32]) + Send + 'static,
        ) -> Result<Self, String> {
            let lease = DeviceLease::claim(&DEVICE_STATE)?;
            let mut native = Self {
                unit: ptr::null_mut(), playback, capture: None, render: None,
                initialized: false, started: false, lease,
            };
            let description = sys::AudioComponentDescription {
                componentType: sys::kAudioUnitType_Output,
                componentSubType: sys::kAudioUnitSubType_VoiceProcessingIO,
                componentManufacturer: sys::kAudioUnitManufacturer_Apple,
                componentFlags: 0, componentFlagsMask: 0,
            };
            let component = unsafe { sys::AudioComponentFindNext(ptr::null_mut(), &description) };
            if component.is_null() { return Err("Apple VoiceProcessingIO is unavailable".to_owned()); }
            let created = unsafe { sys::AudioComponentInstanceNew(component, &mut native.unit) };
            if !native.unit.is_null() { native.playback.native_released.store(false, Ordering::Release); }
            status(created, "creation")?;
            if native.unit.is_null() { return Err("Apple VoiceProcessingIO returned no device".to_owned()); }
            native.set(sys::kAudioOutputUnitProperty_EnableIO, sys::kAudioUnitScope_Input, 1, &1u32)?;
            native.set(sys::kAudioOutputUnitProperty_EnableIO, sys::kAudioUnitScope_Output, 0, &1u32)?;
            let pcm = format();
            native.set(sys::kAudioUnitProperty_StreamFormat, sys::kAudioUnitScope_Input, 0, &pcm)?;
            native.set(sys::kAudioUnitProperty_StreamFormat, sys::kAudioUnitScope_Output, 1, &pcm)?;
            native.set(sys::kAudioUnitProperty_MaximumFramesPerSlice, sys::kAudioUnitScope_Global, 0,
                       &(MAX_CALLBACK_FRAMES as u32))?;
            native.set(sys::kAudioUnitProperty_ShouldAllocateBuffer, sys::kAudioUnitScope_Output, 1, &0u32)?;
            native.set(sys::kAUVoiceIOProperty_BypassVoiceProcessing, sys::kAudioUnitScope_Global, 0, &0u32)?;
            native.set(sys::kAUVoiceIOProperty_VoiceProcessingEnableAGC, sys::kAudioUnitScope_Global, 0, &1u32)?;
            native.set(sys::kAUVoiceIOProperty_MuteOutput, sys::kAudioUnitScope_Global, 0, &0u32)?;
            native.verify_processing()?;

            native.capture = Some(Box::new(CaptureContext {
                unit: native.unit, playback: native.playback.clone(),
                capture: Box::new(capture), scratch: [0.0; MAX_CALLBACK_FRAMES],
            }));
            native.render = Some(Box::new(native.playback.clone()));
            let input_context = native.capture.as_mut().ok_or("Missing voice capture context")?;
            let input = sys::AURenderCallbackStruct {
                inputProc: Some(capture_callback),
                inputProcRefCon: (&mut **input_context as *mut CaptureContext).cast(),
            };
            let output_context = native.render.as_mut().ok_or("Missing voice render context")?;
            let output = sys::AURenderCallbackStruct {
                inputProc: Some(render_callback),
                inputProcRefCon: (&mut **output_context as *mut Arc<Playback>).cast(),
            };
            native.set(sys::kAudioOutputUnitProperty_SetInputCallback, sys::kAudioUnitScope_Global, 0, &input)?;
            native.set(sys::kAudioUnitProperty_SetRenderCallback, sys::kAudioUnitScope_Input, 0, &output)?;
            if native.playback.is_cancelled() { return Err("iOS voice audio was cancelled during setup".to_owned()); }
            native.initialized = true;
            status(unsafe { sys::AudioUnitInitialize(native.unit) }, "initialization")?;
            native.verify_processing()?;
            if native.playback.is_cancelled() { return Err("iOS voice audio was cancelled before starting".to_owned()); }
            native.started = true;
            status(unsafe { sys::AudioOutputUnitStart(native.unit) }, "start")?;
            if native.playback.is_cancelled() { return Err("iOS voice audio was cancelled while starting".to_owned()); }
            Ok(native)
        }

        fn set<T>(&self, property: u32, scope: u32, bus: u32, value: &T) -> Result<(), String> {
            status(unsafe {
                sys::AudioUnitSetProperty(self.unit, property, scope, bus,
                    (value as *const T).cast(), mem::size_of::<T>() as u32)
            }, "configuration")
        }

        fn get<T: Copy>(&self, property: u32, scope: u32, bus: u32) -> Result<T, String> {
            let mut value = mem::MaybeUninit::<T>::uninit();
            let mut size = mem::size_of::<T>() as u32;
            status(unsafe {
                sys::AudioUnitGetProperty(self.unit, property, scope, bus,
                    value.as_mut_ptr().cast(), &mut size)
            }, "configuration readback")?;
            if size != mem::size_of::<T>() as u32 {
                return Err("Apple voice-processing configuration has an unexpected size".to_owned());
            }
            // The successful system call initialized exactly size_of::<T>().
            Ok(unsafe { value.assume_init() })
        }

        fn verify_processing(&self) -> Result<(), String> {
            let bypass: u32 = self.get(sys::kAUVoiceIOProperty_BypassVoiceProcessing,
                sys::kAudioUnitScope_Global, 0)?;
            let agc: u32 = self.get(sys::kAUVoiceIOProperty_VoiceProcessingEnableAGC,
                sys::kAudioUnitScope_Global, 0)?;
            let max_frames: u32 = self.get(sys::kAudioUnitProperty_MaximumFramesPerSlice,
                sys::kAudioUnitScope_Global, 0)?;
            if bypass != 0 || agc != 1 || max_frames == 0 || max_frames as usize > MAX_CALLBACK_FRAMES {
                return Err("Apple voice processing was not enabled with a bounded callback size".to_owned());
            }
            for (scope, bus) in [(sys::kAudioUnitScope_Input, 0), (sys::kAudioUnitScope_Output, 1)] {
                let actual: sys::AudioStreamBasicDescription = self.get(sys::kAudioUnitProperty_StreamFormat, scope, bus)?;
                let expected = format();
                if actual.mSampleRate != expected.mSampleRate || actual.mFormatID != expected.mFormatID
                    || actual.mFormatFlags != expected.mFormatFlags || actual.mBytesPerFrame != 4
                    || actual.mChannelsPerFrame != 1 || actual.mBitsPerChannel != 32
                    || actual.mFramesPerPacket != 1 || actual.mBytesPerPacket != 4 {
                    return Err("Apple voice processing did not accept mono 48 kHz float PCM".to_owned());
                }
            }
            Ok(())
        }

        pub(super) fn stop(&mut self) -> Result<(), String> {
            self.playback.close();
            if self.unit.is_null() {
                self.playback.native_released.store(true, Ordering::Release);
                self.lease.release(); return Ok(());
            }
            let mut errors = Vec::new();
            if self.started {
                let result = unsafe { sys::AudioOutputUnitStop(self.unit) };
                if result == 0 { self.started = false; }
                else { errors.push(format!("stop {}", result)); }
            }
            if self.initialized {
                let result = unsafe { sys::AudioUnitUninitialize(self.unit) };
                if result == 0 { self.initialized = false; }
                else { errors.push(format!("uninitialize {}", result)); }
            }
            let disposed = unsafe { sys::AudioComponentInstanceDispose(self.unit) };
            if disposed == 0 {
                self.unit = ptr::null_mut();
                self.started = false;
                self.initialized = false;
                self.capture.take();
                self.render.take();
                self.playback.native_released.store(true, Ordering::Release);
                self.lease.release();
            } else {
                self.lease.quarantine();
                errors.push(format!("dispose {}; device quarantined until app restart", disposed));
            }
            if errors.is_empty() { Ok(()) }
            else { Err(format!("iOS voice audio cleanup failed: {}", errors.join(", "))) }
        }
    }

    impl Drop for NativeDuplex {
        fn drop(&mut self) {
            if let Err(error) = self.stop() {
                eprintln!("{}", error);
                // If Apple refuses disposal, a callback could still hold these
                // pointers. Keep the closed (silence/no-capture) contexts alive
                // rather than freeing memory still referenced by CoreAudio.
                if !self.unit.is_null() {
                    if let Some(context) = self.capture.take() { Box::leak(context); }
                    if let Some(context) = self.render.take() { Box::leak(context); }
                }
            }
        }
    }

    unsafe extern "C" fn capture_callback(
        context: *mut c_void, flags: *mut sys::AudioUnitRenderActionFlags,
        timestamp: *const sys::AudioTimeStamp, _bus: u32, frames: u32,
        _data: *mut sys::AudioBufferList,
    ) -> sys::OSStatus {
        if context.is_null() { return -50; }
        // CoreAudio serializes a unit's input callback. Only that callback
        // accesses this mutable scratch/closure; disposal waits for callbacks.
        let context = &mut *context.cast::<CaptureContext>();
        if frames as usize > MAX_CALLBACK_FRAMES { context.playback.fail(); return sys::kAudioUnitErr_TooManyFramesToProcess; }
        if context.playback.is_cancelled() || frames == 0 { return 0; }
        if timestamp.is_null() || flags.is_null() { context.playback.fail(); return -50; }
        let mut buffers = sys::AudioBufferList {
            mNumberBuffers: 1,
            mBuffers: [sys::AudioBuffer {
                mNumberChannels: 1, mDataByteSize: frames * 4,
                mData: context.scratch.as_mut_ptr().cast(),
            }],
        };
        let result = sys::AudioUnitRender(context.unit, flags, timestamp, 1, frames, &mut buffers);
        if result != 0 {
            context.scratch[..frames as usize].fill(0.0);
            context.playback.fail(); return result;
        }
        if buffers.mNumberBuffers != 1 || buffers.mBuffers[0].mDataByteSize != frames * 4
            || buffers.mBuffers[0].mNumberChannels != 1
            || buffers.mBuffers[0].mData != context.scratch.as_mut_ptr().cast() {
            context.scratch[..frames as usize].fill(0.0);
            context.playback.fail(); return -50;
        }
        let samples = &mut context.scratch[..frames as usize];
        for sample in samples.iter_mut() { if !sample.is_finite() { *sample = 0.0; } }
        context.playback.capture(samples, &mut context.capture);
        samples.fill(0.0);
        0
    }

    unsafe extern "C" fn render_callback(
        context: *mut c_void, _flags: *mut sys::AudioUnitRenderActionFlags,
        _timestamp: *const sys::AudioTimeStamp, _bus: u32, frames: u32,
        data: *mut sys::AudioBufferList,
    ) -> sys::OSStatus {
        if context.is_null() { return -50; }
        let playback = &*context.cast::<Arc<Playback>>();
        if frames == 0 { return 0; }
        if data.is_null() { playback.fail(); return -50; }
        if (*data).mNumberBuffers != 1 { playback.fail(); return -50; }
        let buffer = &mut (*data).mBuffers[0];
        if buffer.mData.is_null() || buffer.mNumberChannels != 1
            || (buffer.mData as usize) % mem::align_of::<f32>() != 0
            || buffer.mDataByteSize < frames.saturating_mul(4)
            || buffer.mDataByteSize % 4 != 0 {
            playback.fail(); return -50;
        }
        if frames as usize > MAX_CALLBACK_FRAMES {
            playback.fail();
            // Do not construct an unbounded slice from a malformed callback.
            let count = (buffer.mDataByteSize as usize / 4).min(MAX_CALLBACK_FRAMES);
            slice::from_raw_parts_mut(buffer.mData.cast::<f32>(), count).fill(0.0);
            return sys::kAudioUnitErr_TooManyFramesToProcess;
        }
        let output = slice::from_raw_parts_mut(buffer.mData.cast::<f32>(), frames as usize);
        playback.render(output);
        buffer.mDataByteSize = frames * 4;
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn playback() -> Arc<Playback> { Playback::new(Arc::new(AtomicBool::new(false))) }

    #[test]
    fn fresh_endpoint_underflow_is_silence() {
        let endpoint = playback();
        let mut output = [0.7; 128];
        assert!(endpoint.render(&mut output));
        assert_eq!(output, [0.0; 128]);
    }

    #[test]
    fn rendered_reference_is_exactly_the_fifo_far_end_pcm() {
        let endpoint = playback();
        assert!(endpoint.push(&[0.1, -0.2, 0.3]));
        let mut output = [9.0; 5];
        assert!(endpoint.render(&mut output));
        assert_eq!(output, [0.1, -0.2, 0.3, 0.0, 0.0]);
        assert!(endpoint.render(&mut output));
        assert_eq!(output, [0.0; 5]);
    }

    #[test]
    fn overflow_keeps_only_the_latest_120_ms() {
        let endpoint = playback();
        assert!(endpoint.push(&vec![-0.5; PLAYBACK_SAMPLES]));
        assert!(endpoint.push(&vec![0.5; 960]));
        assert_eq!(endpoint.samples.len(), PLAYBACK_SAMPLES);
        let mut first = [0.0; MAX_CALLBACK_FRAMES];
        endpoint.render(&mut first);
        assert_eq!(first, [-0.5; MAX_CALLBACK_FRAMES]);
        let mut tail = [0.0; PLAYBACK_SAMPLES - MAX_CALLBACK_FRAMES];
        endpoint.render(&mut tail);
        assert!(tail[..tail.len() - 960].iter().all(|sample| *sample == -0.5));
        assert!(tail[tail.len() - 960..].iter().all(|sample| *sample == 0.5));
    }

    #[test]
    fn one_oversized_push_also_keeps_latest_not_earliest_audio() {
        let endpoint = playback();
        let mut input = vec![-0.2; PLAYBACK_SAMPLES * 4];
        input[PLAYBACK_SAMPLES * 3..].fill(0.8);
        endpoint.push(&input);
        let mut output = [0.0; MAX_CALLBACK_FRAMES];
        endpoint.render(&mut output);
        assert_eq!(output, [0.8; MAX_CALLBACK_FRAMES]);
        assert_eq!(endpoint.samples.len(), PLAYBACK_SAMPLES - MAX_CALLBACK_FRAMES);
    }

    #[test]
    fn non_finite_and_out_of_range_far_end_pcm_is_sanitized() {
        let endpoint = playback();
        endpoint.push(&[f32::NAN, f32::INFINITY, f32::NEG_INFINITY, -2.0, 2.0, 0.4]);
        let mut output = [0.0; 6];
        endpoint.render(&mut output);
        assert_eq!(output, [0.0, 0.0, 0.0, -1.0, 1.0, 0.4]);
    }

    #[test]
    fn external_cancel_silences_both_directions_and_rejects_push() {
        let cancelled = Arc::new(AtomicBool::new(false));
        let endpoint = Playback::new(cancelled.clone());
        endpoint.push(&[0.4; 20]);
        cancelled.store(true, Ordering::Release);
        assert!(!endpoint.push(&[0.6]));
        let mut output = [0.9; 20];
        assert!(!endpoint.render(&mut output));
        assert_eq!(output, [0.0; 20]);
        assert!(!endpoint.capture(&[0.5], &mut |_| panic!("cancelled capture was delivered")));
    }

    #[test]
    fn close_is_idempotent_and_does_not_cancel_a_different_endpoint() {
        let flag = Arc::new(AtomicBool::new(false));
        let first = Playback::new(flag.clone());
        let second = Playback::new(flag.clone());
        first.push(&[0.3]);
        first.close(); first.close();
        assert_eq!(first.samples.len(), 0);
        assert!(first.is_cancelled());
        assert!(!flag.load(Ordering::Acquire));
        assert!(!second.is_cancelled());
        assert!(second.push(&[0.3]));
        assert!(first.is_native_released());
        assert!(second.is_native_released());
    }

    #[test]
    fn callback_shape_overrun_fails_closed_instead_of_reallocating() {
        let endpoint = playback();
        let mut output = vec![0.4; MAX_CALLBACK_FRAMES + 1];
        assert!(!endpoint.render(&mut output));
        assert!(endpoint.is_failed());
        assert!(endpoint.is_cancelled());
        assert!(output.iter().all(|sample| *sample == 0.0));
        assert!(!endpoint.push(&[0.3]));
    }

    #[test]
    fn closing_pcm_gate_does_not_forge_a_native_disposal_proof() {
        let endpoint = playback();
        endpoint.native_released.store(false, Ordering::Release);
        endpoint.close();
        assert!(endpoint.is_cancelled());
        assert!(!endpoint.is_native_released());
    }

    #[test]
    fn oversized_capture_fails_without_delivering_partial_microphone_pcm() {
        let endpoint = playback();
        assert!(!endpoint.capture(&vec![0.7; MAX_CALLBACK_FRAMES + 1], &mut |_| panic!("oversized capture")));
        assert!(endpoint.is_failed());
    }

    #[test]
    fn processed_capture_preserves_chunk_and_rechecks_cancel_after_delivery() {
        let endpoint = playback();
        let mut captured = Vec::new();
        assert!(endpoint.capture(&[0.1, 0.2], &mut |samples| captured.extend_from_slice(samples)));
        assert_eq!(captured, [0.1, 0.2]);
        assert!(!endpoint.capture(&[0.3], &mut |_| endpoint.close()));
    }

    #[test]
    fn concurrent_overflow_and_render_stay_bounded_and_finite() {
        let endpoint = playback();
        let producer = endpoint.clone();
        let worker = std::thread::spawn(move || {
            for _ in 0..2_000 { producer.push(&[0.2; 97]); }
        });
        let mut output = [0.0; 137];
        for _ in 0..2_000 {
            assert!(endpoint.render(&mut output));
            assert!(output.iter().all(|sample| *sample == 0.0 || *sample == 0.2));
            assert!(endpoint.samples.len() <= PLAYBACK_SAMPLES);
        }
        worker.join().unwrap();
        endpoint.close();
        assert_eq!(endpoint.samples.len(), 0);
    }

    #[test]
    fn concurrent_close_cannot_leave_a_live_playback_or_capture() {
        let endpoint = playback();
        let producer = endpoint.clone();
        let worker = std::thread::spawn(move || {
            while producer.push(&[0.4; 960]) {}
        });
        endpoint.close();
        worker.join().unwrap();
        assert_eq!(endpoint.samples.len(), 0);
        let mut output = [0.9; 480];
        assert!(!endpoint.render(&mut output));
        assert_eq!(output, [0.0; 480]);
    }

    #[test]
    fn route_rejects_zero_duplicate_and_stale_bind_without_closing_current() {
        let mut route = PlaybackRoute::default();
        let current = playback();
        assert!(!route.bind(0, playback()));
        assert!(route.bind(10, current.clone()));
        assert!(!route.bind(10, current.clone()));
        assert!(!route.bind(9, playback()));
        assert!(Arc::ptr_eq(route.current().unwrap(), &current));
        assert!(!current.is_cancelled());
    }

    #[test]
    fn new_bind_closes_the_previous_reference() {
        let mut route = PlaybackRoute::default();
        let old = playback(); let new = playback();
        assert!(route.bind(1, old.clone()));
        assert!(route.bind(2, new.clone()));
        assert!(old.is_cancelled());
        assert!(!new.is_cancelled());
        assert!(Arc::ptr_eq(route.current().unwrap(), &new));
    }

    #[test]
    fn late_end_cannot_stop_a_new_call() {
        let mut route = PlaybackRoute::default();
        assert!(route.bind(1, playback()));
        let current = playback();
        assert!(route.bind(2, current.clone()));
        assert!(!route.end(1));
        assert!(!current.is_cancelled());
        assert!(route.end(2));
        assert!(current.is_cancelled());
        assert!(route.current().is_none());
        assert!(!route.end(2));
    }

    #[test]
    fn ended_epoch_remains_a_tombstone_against_late_bind() {
        let mut route = PlaybackRoute::default();
        route.bind(5, playback()); route.end(5);
        assert!(!route.bind(5, playback()));
        assert!(!route.bind(4, playback()));
        assert!(route.bind(6, playback()));
    }

    #[test]
    fn end_before_bind_also_fences_the_late_start() {
        let mut route = PlaybackRoute::default();
        assert!(!route.end(8));
        assert!(!route.bind(8, playback()));
        assert!(!route.bind(7, playback()));
        assert!(route.bind(9, playback()));
    }

    #[test]
    fn cancelled_new_bind_advances_tombstone_but_preserves_current() {
        let mut route = PlaybackRoute::default();
        let current = playback(); route.bind(1, current.clone());
        let cancelled = playback(); cancelled.close();
        assert!(!route.bind(3, cancelled));
        assert!(!route.bind(2, playback()));
        assert!(Arc::ptr_eq(route.current().unwrap(), &current));
        assert!(!current.is_cancelled());
        assert!(route.end(1));
        assert!(route.bind(4, playback()));
    }

    #[test]
    fn dropping_a_route_closes_its_endpoint() {
        let endpoint = playback();
        { let mut route = PlaybackRoute::default(); route.bind(1, endpoint.clone()); }
        assert!(endpoint.is_cancelled());
    }

    #[test]
    fn cancelled_start_never_invokes_the_capture_closure() {
        let endpoint = playback(); endpoint.close();
        assert!(Duplex::start(endpoint, |_| panic!("cancelled start captured microphone")).is_err());
    }

    #[test]
    fn device_lease_excludes_overlapping_native_calls() {
        let state = AtomicU8::new(0);
        let mut first = DeviceLease::claim(&state).unwrap();
        assert!(DeviceLease::claim(&state).is_err());
        first.release();
        let second = DeviceLease::claim(&state).unwrap();
        drop(first);
        assert!(DeviceLease::claim(&state).is_err());
        drop(second);
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn failed_disposal_quarantines_native_device_until_process_restart() {
        let state = AtomicU8::new(0);
        let mut lease = DeviceLease::claim(&state).unwrap();
        lease.quarantine();
        lease.release();
        drop(lease);
        let error = match DeviceLease::claim(&state) {
            Ok(_) => panic!("quarantined device was reacquired"),
            Err(error) => error,
        };
        assert!(error.contains("quarantined"));
        assert!(error.contains("restart"));
        assert_eq!(state.load(Ordering::Acquire), DEVICE_QUARANTINED);
    }

    #[test]
    fn dropping_failed_disposal_cannot_clear_its_active_device_bit() {
        let state = AtomicU8::new(0);
        {
            let mut lease = DeviceLease::claim(&state).unwrap();
            lease.quarantine();
        }
        assert_eq!(state.load(Ordering::Acquire), DEVICE_ACTIVE | DEVICE_QUARANTINED);
        assert!(DeviceLease::claim(&state).is_err());
    }

    #[test]
    fn abandoned_setup_without_a_native_device_releases_the_lease() {
        let state = AtomicU8::new(0);
        { let _lease = DeviceLease::claim(&state).unwrap(); }
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn ordinary_outputs_share_their_permits_but_exclude_native_voice() {
        let state = AtomicU8::new(0);
        let first = OrdinaryPlaybackLease::claim(&state).unwrap();
        let second = OrdinaryPlaybackLease::claim(&state).unwrap();
        assert_eq!(state.load(Ordering::Acquire), ORDINARY_PLAYBACK * 2);
        assert!(DeviceLease::claim(&state).is_err());
        drop(first);
        assert!(DeviceLease::claim(&state).is_err());
        drop(second);
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn native_voice_excludes_a_late_decoder_end_restoring_ordinary_output() {
        let state = AtomicU8::new(0);
        let mut old_voice = DeviceLease::claim(&state).unwrap();
        old_voice.release();
        let next_voice = DeviceLease::claim(&state).unwrap();
        assert!(OrdinaryPlaybackLease::claim(&state).is_err());
        drop(next_voice);
        assert!(OrdinaryPlaybackLease::claim(&state).is_ok());
    }

    #[test]
    fn old_decoder_end_restoration_excludes_voice_claim_after_another_bind_ack() {
        let state = AtomicU8::new(0);
        let mut old_voice = DeviceLease::claim(&state).unwrap();
        old_voice.release();
        // Different decoders have independent epoch routes. Its late End can
        // restore output, but a new native unit must then fail before creation.
        let old_output = OrdinaryPlaybackLease::claim(&state).unwrap();
        assert!(DeviceLease::claim(&state).is_err());
        drop(old_output);
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn failed_native_cleanup_blocks_ordinary_output_too() {
        let state = AtomicU8::new(0);
        let mut voice = DeviceLease::claim(&state).unwrap();
        voice.quarantine();
        voice.release();
        assert!(OrdinaryPlaybackLease::claim(&state).is_err());
        assert_eq!(state.load(Ordering::Acquire), DEVICE_QUARANTINED);
    }

    #[test]
    fn failed_ordinary_setup_releases_its_permit_without_touching_other_streams() {
        let state = AtomicU8::new(0);
        let existing = OrdinaryPlaybackLease::claim(&state).unwrap();
        { let _failed_setup = OrdinaryPlaybackLease::claim(&state).unwrap(); }
        assert_eq!(state.load(Ordering::Acquire), ORDINARY_PLAYBACK);
        assert!(DeviceLease::claim(&state).is_err());
        drop(existing);
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn ordinary_permit_counter_saturation_cannot_wrap_to_unowned() {
        let state = AtomicU8::new(0);
        let mut outputs = Vec::new();
        for _ in 0..(u8::MAX / ORDINARY_PLAYBACK) {
            outputs.push(OrdinaryPlaybackLease::claim(&state).unwrap());
        }
        assert!(OrdinaryPlaybackLease::claim(&state).is_err());
        assert!(DeviceLease::claim(&state).is_err());
        drop(outputs);
        assert_eq!(state.load(Ordering::Acquire), 0);
    }

    #[test]
    fn real_stream_owner_drops_stream_before_releasing_its_ordinary_permit() {
        struct StreamDrop<'a> { state: &'a AtomicU8, dropped: &'a AtomicBool }
        impl Drop for StreamDrop<'_> {
            fn drop(&mut self) {
                assert!(DeviceLease::claim(self.state).is_err());
                self.dropped.store(true, Ordering::Release);
            }
        }
        let state = AtomicU8::new(0);
        let dropped = AtomicBool::new(false);
        let output = OrdinaryPlayback::new(
            StreamDrop { state: &state, dropped: &dropped },
            OrdinaryPlaybackLease::claim(&state).unwrap(),
        );
        drop(output);
        assert!(dropped.load(Ordering::Acquire));
        assert!(DeviceLease::claim(&state).is_ok());
    }

    #[test]
    fn simultaneous_voice_and_ordinary_claims_never_overlap() {
        use std::sync::{atomic::AtomicUsize, Barrier};
        let state = Arc::new(AtomicU8::new(0));
        let ordinary = Arc::new(AtomicUsize::new(0));
        let voice = Arc::new(AtomicUsize::new(0));
        let barrier = Arc::new(Barrier::new(33));
        let mut workers = Vec::new();
        for index in 0..32 {
            let (state, ordinary, voice, barrier) =
                (state.clone(), ordinary.clone(), voice.clone(), barrier.clone());
            workers.push(std::thread::spawn(move || {
                let native = if index % 2 == 0 { DeviceLease::claim(&state).ok() } else { None };
                let output = if index % 2 == 1 { OrdinaryPlaybackLease::claim(&state).ok() } else { None };
                if native.is_some() { voice.fetch_add(1, Ordering::AcqRel); }
                if output.is_some() { ordinary.fetch_add(1, Ordering::AcqRel); }
                barrier.wait();
                drop(output); drop(native);
            }));
        }
        barrier.wait();
        for worker in workers { worker.join().unwrap(); }
        let voices = voice.load(Ordering::Acquire);
        let outputs = ordinary.load(Ordering::Acquire);
        assert!(voices <= 1);
        assert!(voices == 0 || outputs == 0);
        assert!(voices > 0 || outputs > 0);
        assert_eq!(state.load(Ordering::Acquire), 0);
    }

    #[cfg(not(target_os = "ios"))]
    #[test]
    fn host_native_start_reports_unsupported_without_a_mock_aec_fallback() {
        let endpoint = playback();
        assert!(Duplex::start(endpoint.clone(), |_| panic!("non-iOS capture")).is_err());
        assert!(endpoint.is_cancelled());
        assert!(!endpoint.is_failed());
    }
}
