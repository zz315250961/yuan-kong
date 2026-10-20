//! Bound encoded iOS downlink frames before they enter the decoder FIFO.
//! Format and playback handoff messages keep their existing FIFO ordering.
//! A credit is held through decoding and returned even if send/decoding fails.

use std::sync::{atomic::{AtomicUsize, Ordering}, Arc};

pub const MAX_PENDING_AUDIO_FRAMES: usize = 6;

pub struct AudioFrameCredit(Arc<AtomicUsize>);

impl AudioFrameCredit {
    pub fn reserve(pending: &Arc<AtomicUsize>) -> Option<Self> {
        pending.fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
            (count < MAX_PENDING_AUDIO_FRAMES).then(|| count + 1)
        }).ok()?;
        Some(Self(pending.clone()))
    }
}

impl Drop for AudioFrameCredit {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Release);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn six_pending_frames_bound_the_real_decoder_fifo() {
        let pending = Arc::new(AtomicUsize::new(0));
        let (sender, receiver) = std::sync::mpsc::channel();
        for _ in 0..MAX_PENDING_AUDIO_FRAMES {
            sender.send(AudioFrameCredit::reserve(&pending).unwrap()).unwrap();
        }
        for _ in 0..10_000 { assert!(AudioFrameCredit::reserve(&pending).is_none()); }
        let decoding = receiver.recv().unwrap();
        assert!(AudioFrameCredit::reserve(&pending).is_none());
        drop(decoding);
        assert!(AudioFrameCredit::reserve(&pending).is_some());
        drop(receiver);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn failed_send_returns_credit_without_waiting_for_decoder() {
        let pending = Arc::new(AtomicUsize::new(0));
        let (sender, receiver) = std::sync::mpsc::channel();
        drop(receiver);
        drop(sender.send(AudioFrameCredit::reserve(&pending).unwrap()));
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn simultaneous_producers_cannot_overbook_decoder_credits() {
        let pending = Arc::new(AtomicUsize::new(0));
        let accepted = Arc::new(AtomicUsize::new(0));
        let barrier = Arc::new(std::sync::Barrier::new(33));
        let mut workers = Vec::new();
        for _ in 0..32 {
            let (pending, accepted, barrier) = (pending.clone(), accepted.clone(), barrier.clone());
            workers.push(std::thread::spawn(move || {
                let credit = AudioFrameCredit::reserve(&pending);
                if credit.is_some() { accepted.fetch_add(1, Ordering::AcqRel); }
                barrier.wait();
                drop(credit);
            }));
        }
        barrier.wait();
        for worker in workers { worker.join().unwrap(); }
        assert_eq!(accepted.load(Ordering::Acquire), MAX_PENDING_AUDIO_FRAMES);
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }

    #[test]
    fn invalid_saturated_counter_fails_closed_without_wrap() {
        for count in [MAX_PENDING_AUDIO_FRAMES, MAX_PENDING_AUDIO_FRAMES + 1, usize::MAX] {
            let pending = Arc::new(AtomicUsize::new(count));
            assert!(AudioFrameCredit::reserve(&pending).is_none());
            assert_eq!(pending.load(Ordering::Acquire), count);
        }
    }
}
