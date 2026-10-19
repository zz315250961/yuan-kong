//! Bounded, streaming mono PCM packetizer shared with the iOS microphone worker.
//!
//! No device, codec, network, allocation, or platform dependency lives here.
//! The causal linear interpolator carries its integer phase across callbacks.

pub const VOICE_SAMPLE_RATE: u32 = 48_000;
pub const VOICE_FRAME_SAMPLES: usize = 960; // 20 ms of mono 48 kHz PCM.

pub struct VoicePacketizer {
    input_rate: u32,
    channels: usize,
    channel_sum: f32,
    channel_count: usize,
    previous: Option<f32>,
    phase: u32,
    frame: [f32; VOICE_FRAME_SAMPLES],
    frame_len: usize,
}

impl VoicePacketizer {
    pub fn new(input_rate: u32, channels: u16) -> Result<Self, &'static str> {
        // Voice routes commonly expose 8/16/24/32/44.1/48 kHz. Upsampling only:
        // a device above 48 kHz needs a low-pass downsampler, not silent aliasing.
        if !(8_000..=VOICE_SAMPLE_RATE).contains(&input_rate) {
            return Err("Unsupported microphone sample rate; expected 8 to 48 kHz");
        }
        if channels != 1 && channels != 2 {
            return Err("Unsupported microphone channel count; expected mono or stereo");
        }
        Ok(Self {
            input_rate,
            channels: channels as usize,
            channel_sum: 0.0,
            channel_count: 0,
            previous: None,
            phase: 0,
            frame: [0.0; VOICE_FRAME_SAMPLES],
            frame_len: 0,
        })
    }

    /// Complete frames are emitted immediately; at most one partial frame and
    /// one partial interleaved channel group remain in this fixed-size state.
    /// If the consumer fails, the caller must reset before resuming the stream.
    pub fn push<E>(
        &mut self,
        interleaved: &[f32],
        mut emit: impl FnMut(&[f32; VOICE_FRAME_SAMPLES]) -> Result<(), E>,
    ) -> Result<(), E> {
        for &sample in interleaved {
            // Corrupt device samples must not poison later interpolation/Opus.
            self.channel_sum += if sample.is_finite() {
                sample.clamp(-1.0, 1.0)
            } else {
                0.0
            };
            self.channel_count += 1;
            if self.channel_count == self.channels {
                let mono = self.channel_sum / self.channels as f32;
                self.channel_sum = 0.0;
                self.channel_count = 0;
                self.push_mono(mono, &mut emit)?;
            }
        }
        Ok(())
    }

    fn push_mono<E>(
        &mut self,
        sample: f32,
        emit: &mut impl FnMut(&[f32; VOICE_FRAME_SAMPLES]) -> Result<(), E>,
    ) -> Result<(), E> {
        if self.input_rate == VOICE_SAMPLE_RATE {
            return self.push_output(sample, emit);
        }
        let previous = self.previous.unwrap_or(sample);
        // Each input sample advances one interval. The first interval is held
        // constant; later intervals interpolate previous -> current, so no
        // look-ahead, callback-boundary duplication, or floating-point drift.
        while self.phase < VOICE_SAMPLE_RATE {
            let fraction = self.phase as f32 / VOICE_SAMPLE_RATE as f32;
            self.push_output(previous + (sample - previous) * fraction, emit)?;
            self.phase += self.input_rate;
        }
        self.phase -= VOICE_SAMPLE_RATE;
        self.previous = Some(sample);
        Ok(())
    }

    fn push_output<E>(
        &mut self,
        sample: f32,
        emit: &mut impl FnMut(&[f32; VOICE_FRAME_SAMPLES]) -> Result<(), E>,
    ) -> Result<(), E> {
        self.frame[self.frame_len] = sample;
        self.frame_len += 1;
        if self.frame_len == VOICE_FRAME_SAMPLES {
            // Restore the indexing invariant even if the consumer fails.
            self.frame_len = 0;
            emit(&self.frame)?;
        }
        Ok(())
    }

    /// Discard stale audio after a queue overrun, stop, or device interruption.
    /// Never pad or transmit an incomplete frame on shutdown.
    pub fn reset(&mut self) {
        self.channel_sum = 0.0;
        self.channel_count = 0;
        self.previous = None;
        self.phase = 0;
        self.frame.fill(0.0);
        self.frame_len = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect(packetizer: &mut VoicePacketizer, data: &[f32]) -> Vec<f32> {
        let mut output = Vec::new();
        packetizer
            .push(data, |frame| {
                output.extend_from_slice(frame);
                Ok::<(), ()>(())
            })
            .unwrap();
        output
    }

    #[test]
    fn invalid_device_configuration_is_rejected() {
        for rate in [0, 1, 7_999, 48_001, 96_000, u32::MAX] {
            assert!(VoicePacketizer::new(rate, 1).is_err());
        }
        for channels in [0, 3, 8, u16::MAX] {
            assert!(VoicePacketizer::new(48_000, channels).is_err());
        }
    }

    #[test]
    fn mono_48k_is_passed_without_resampling_or_padding() {
        let mut packetizer = VoicePacketizer::new(48_000, 1).unwrap();
        let input: Vec<f32> = (0..960).map(|index| index as f32 / 960.0).collect();
        assert_eq!(collect(&mut packetizer, &input), input);
        assert_eq!(packetizer.frame_len, 0);
    }

    #[test]
    fn callbacks_smaller_than_20ms_only_emit_complete_frames() {
        let mut packetizer = VoicePacketizer::new(48_000, 1).unwrap();
        assert!(collect(&mut packetizer, &[0.2; 959]).is_empty());
        assert_eq!(packetizer.frame_len, 959);
        let output = collect(&mut packetizer, &[0.4; 2]);
        assert_eq!(output.len(), 960);
        assert_eq!(&output[..959], &[0.2; 959]);
        assert_eq!(output[959], 0.4);
        assert_eq!(packetizer.frame_len, 1);
    }

    #[test]
    fn empty_callback_does_not_emit_or_reset_partial_audio() {
        let mut packetizer = VoicePacketizer::new(48_000, 1).unwrap();
        collect(&mut packetizer, &[0.2; 3]);
        assert!(collect(&mut packetizer, &[]).is_empty());
        assert_eq!(packetizer.frame_len, 3);
    }

    #[test]
    fn stereo_is_averaged_and_interleaved_remainder_crosses_callbacks() {
        let mut packetizer = VoicePacketizer::new(48_000, 2).unwrap();
        assert!(collect(&mut packetizer, &[0.2]).is_empty());
        assert_eq!(packetizer.channel_count, 1);
        let mut remaining = vec![0.6];
        remaining.extend([0.2, 0.6].repeat(959));
        let output = collect(&mut packetizer, &remaining);
        assert_eq!(output, vec![0.4; 960]);
        assert_eq!(packetizer.channel_count, 0);
    }

    #[test]
    fn stereo_antiphase_cancels_without_channel_gain() {
        let mut packetizer = VoicePacketizer::new(48_000, 2).unwrap();
        assert_eq!(collect(&mut packetizer, &[1.0, -1.0].repeat(960)), vec![0.0; 960]);
    }

    #[test]
    fn non_finite_and_out_of_range_samples_are_sanitized_before_downmix() {
        let mut packetizer = VoicePacketizer::new(48_000, 2).unwrap();
        let input = [f32::NAN, 1.0, f32::INFINITY, f32::NEG_INFINITY, 3.0, -2.0].repeat(320);
        let output = collect(&mut packetizer, &input);
        assert_eq!(output, [0.5, 0.0, 0.0].repeat(320));
        assert!(output.iter().all(|sample| sample.is_finite() && sample.abs() <= 1.0));
    }

    #[test]
    fn exactly_882_mono_44100_samples_produce_one_960_sample_frame() {
        let mut packetizer = VoicePacketizer::new(44_100, 1).unwrap();
        let output = collect(&mut packetizer, &[0.3; 882]);
        assert_eq!(output, vec![0.3; 960]);
        assert_eq!(packetizer.frame_len, 0);
        assert_eq!(packetizer.phase, 0);
    }

    #[test]
    fn all_supported_voice_route_rates_have_exact_one_second_counts() {
        for rate in [8_000, 16_000, 24_000, 32_000, 44_100, 48_000] {
            let mut packetizer = VoicePacketizer::new(rate, 1).unwrap();
            assert_eq!(collect(&mut packetizer, &vec![0.25; rate as usize]), vec![0.25; 48_000]);
            assert_eq!(packetizer.frame_len, 0);
            assert_eq!(packetizer.phase, 0);
        }
    }

    #[test]
    fn arbitrary_callbacks_match_contiguous_44100_stereo_exactly() {
        let input: Vec<f32> = (0..88_201).map(|index| (index as f32 / 199.0).sin()).collect();
        let mut whole = VoicePacketizer::new(44_100, 2).unwrap();
        let expected = collect(&mut whole, &input);
        let mut chunked = VoicePacketizer::new(44_100, 2).unwrap();
        let mut output = Vec::new();
        let sizes = [1, 3, 511, 2, 1024, 881, 17];
        let mut offset = 0;
        let mut index = 0;
        while offset < input.len() {
            let end = (offset + sizes[index % sizes.len()]).min(input.len());
            output.extend(collect(&mut chunked, &input[offset..end]));
            offset = end;
            index += 1;
        }
        assert_eq!(output, expected);
        assert_eq!(chunked.frame_len, whole.frame_len);
        assert_eq!(chunked.phase, whole.phase);
        assert_eq!(chunked.channel_count, whole.channel_count);
        assert_eq!(chunked.channel_sum, whole.channel_sum);
    }

    #[test]
    fn interpolation_is_causal_and_stays_between_adjacent_samples() {
        let mut packetizer = VoicePacketizer::new(24_000, 1).unwrap();
        let input = [-1.0, 1.0].repeat(240);
        let output = collect(&mut packetizer, &input);
        assert_eq!(&output[..8], &[-1.0, -1.0, -1.0, 0.0, 1.0, 0.0, -1.0, 0.0]);
        assert!(output.iter().all(|sample| sample.abs() <= 1.0));
    }

    #[test]
    fn long_non_frame_multiple_stream_keeps_only_bounded_remainders() {
        let mut packetizer = VoicePacketizer::new(44_100, 2).unwrap();
        let mut frames = 0;
        for _ in 0..1_000 {
            packetizer
                .push(&[0.5; 1_023], |_| {
                    frames += 1;
                    Ok::<(), ()>(())
                })
                .unwrap();
            assert!(packetizer.frame_len < VOICE_FRAME_SAMPLES);
            assert!(packetizer.channel_count < 2);
            assert!(packetizer.phase < packetizer.input_rate);
        }
        assert_eq!(frames, (511_500usize * 48_000).div_ceil(44_100) / 960);
        assert!(std::mem::size_of::<VoicePacketizer>() < 4_096);
    }

    #[test]
    fn stop_discards_partial_frame_and_channel_remainder() {
        let mut packetizer = VoicePacketizer::new(44_100, 2).unwrap();
        collect(&mut packetizer, &[0.6; 511]);
        packetizer.reset();
        assert_eq!(packetizer.frame_len, 0);
        assert_eq!(packetizer.channel_count, 0);
        assert_eq!(packetizer.phase, 0);
        assert_eq!(packetizer.previous, None);
        assert!(packetizer.frame.iter().all(|sample| *sample == 0.0));
        assert_eq!(collect(&mut packetizer, &[0.0; 1_764]), vec![0.0; 960]);
    }

    #[test]
    fn consumer_error_does_not_overflow_frame_and_reset_starts_cleanly() {
        let mut packetizer = VoicePacketizer::new(48_000, 1).unwrap();
        let error = packetizer.push(&[0.7; 2_000], |_| Err("transport failed"));
        assert_eq!(error, Err("transport failed"));
        assert_eq!(packetizer.frame_len, 0);
        packetizer.reset();
        assert_eq!(collect(&mut packetizer, &[0.0; 960]), vec![0.0; 960]);
    }
}
