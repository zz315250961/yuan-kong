use super::video_policy::{
    classify_delay, next_fps, ratio_multiplier, NetworkTier, DEFAULT_FPS as FPS,
    INITIAL_FPS as INIT_FPS, MAX_FPS, MIN_FPS, RECOVERY_SAMPLES,
};
use super::*;
use scrap::codec::{Quality, BR_BALANCED, BR_BEST, BR_SPEED};
use std::{
    collections::VecDeque,
    time::{Duration, Instant},
};

/*
FPS and ratio adjustment use the shared four-tier network policy. Good samples recover
slowly after a hysteresis window; congested samples reduce FPS and bitrate immediately.
The effective FPS always remains within the user's 10-120 FPS cap on Windows,
or 10-60 FPS on other hosts.

delay:
    use delay minus RTT as the actual network delay
*/

// Bitrate ratio constants for different quality levels
const BR_MAX: f32 = 40.0; // 2000 * 2 / 100
const BR_MIN: f32 = 0.2;
const BR_MIN_HIGH_RESOLUTION: f32 = 0.1; // For high resolution, BR_MIN is still too high, so we set a lower limit
const MAX_BR_MULTIPLE: f32 = 1.0;

const HISTORY_DELAY_LEN: usize = 2;
const ADJUST_RATIO_INTERVAL: usize = 3; // Adjust quality ratio every 3 seconds
const DYNAMIC_SCREEN_THRESHOLD: usize = 2; // Allow increase quality ratio if encode more than 2 times in one second
const DEFAULT_DELAY_MS: u32 = 100;

#[derive(Default, Debug, Clone)]
struct UserDelay {
    response_delayed: bool,
    delay_history: VecDeque<u32>,
    fps: Option<u32>,
    rtt_calculator: RttCalculator,
    increase_fps_count: usize,
}

impl UserDelay {
    fn add_delay(&mut self, delay: u32) {
        self.rtt_calculator.update(delay);
        if self.delay_history.len() >= HISTORY_DELAY_LEN {
            self.delay_history.pop_front();
        }
        self.delay_history.push_back(delay);
    }

    // Average delay minus RTT
    fn avg_delay(&self) -> u32 {
        let len = self.delay_history.len();
        if len > 0 {
            let avg_delay = self.delay_history.iter().sum::<u32>() / len as u32;

            // If RTT is available, subtract it from average delay to get actual network latency
            if let Some(rtt) = self.rtt_calculator.get_rtt() {
                if avg_delay > rtt {
                    avg_delay - rtt
                } else {
                    avg_delay
                }
            } else {
                avg_delay
            }
        } else {
            DEFAULT_DELAY_MS
        }
    }
}

// User session data structure
#[derive(Default, Debug, Clone)]
struct UserData {
    auto_adjust_fps: Option<u32>, // reserve for compatibility
    custom_fps: Option<u32>,
    quality: Option<(u64, Quality)>, // (monotonic sequence, quality)
    delay: UserDelay,
    record: bool,
}

#[derive(Default, Debug, Clone)]
struct DisplayData {
    send_counter: usize, // Number of times encode during period
    support_changing_quality: bool,
}

// Main QoS controller structure
pub struct VideoQoS {
    fps: u32,
    ratio: f32,
    users: HashMap<i32, UserData>,
    displays: HashMap<String, DisplayData>,
    bitrate_store: u32,
    adjust_ratio_instant: Instant,
    abr_config: bool,
    new_user_instant: Instant,
    quality_sequence: u64,
}

pub struct VideoQosSnapshot {
    pub target_bitrate: u32,
    pub target_fps: u32,
    pub qos_tier: &'static str,
    pub capture_scale: &'static str,
}

impl Default for VideoQoS {
    fn default() -> Self {
        VideoQoS {
            fps: FPS,
            ratio: BR_BALANCED,
            users: Default::default(),
            displays: Default::default(),
            bitrate_store: 0,
            adjust_ratio_instant: Instant::now(),
            abr_config: true,
            new_user_instant: Instant::now(),
            quality_sequence: 0,
        }
    }
}

// Basic functionality
impl VideoQoS {
    // Calculate seconds per frame based on current FPS
    pub fn spf(&self) -> Duration {
        Duration::from_secs_f32(1. / (self.fps() as f32))
    }

    // Get current FPS within valid range
    pub fn fps(&self) -> u32 {
        let fps = self.fps;
        if fps >= MIN_FPS && fps <= MAX_FPS {
            fps
        } else {
            FPS
        }
    }

    // Configure the encoder for the user's ceiling while pacing starts conservatively.
    pub fn encoder_fps(&self) -> u32 {
        self.highest_fps()
    }

    // Store bitrate for later use
    pub fn store_bitrate(&mut self, bitrate: u32) {
        self.bitrate_store = bitrate;
    }

    // Get stored bitrate
    pub fn bitrate(&self) -> u32 {
        self.bitrate_store
    }

    // Get current bitrate ratio with bounds checking
    pub fn ratio(&mut self) -> f32 {
        if self.ratio < BR_MIN_HIGH_RESOLUTION || self.ratio > BR_MAX {
            self.ratio = BR_BALANCED;
        }
        self.ratio
    }

    pub fn encoder_ratio(&mut self) -> f32 {
        self.ratio() * scrap::codec::fps_bitrate_scale(self.fps())
    }

    // Check if any user is in recording mode
    pub fn record(&self) -> bool {
        self.users.iter().any(|u| u.1.record)
    }

    pub fn set_support_changing_quality(&mut self, video_service_name: &str, support: bool) {
        if let Some(display) = self.displays.get_mut(video_service_name) {
            display.support_changing_quality = support;
        }
    }

    // Check if variable bitrate encoding is supported and enabled
    pub fn in_vbr_state(&self) -> bool {
        self.abr_config && self.displays.iter().all(|e| e.1.support_changing_quality)
    }
}

// User session management
impl VideoQoS {
    // Initialize new user session
    pub fn on_connection_open(&mut self, id: i32) {
        self.users.insert(id, UserData::default());
        self.fps = self.fps.min(INIT_FPS);
        self.abr_config = Config::get_option("enable-abr") != "N";
        self.new_user_instant = Instant::now();
    }

    // Clean up user session
    pub fn on_connection_close(&mut self, id: i32) -> bool {
        let before = self.capture_half_scale();
        self.users.remove(&id);
        if self.users.is_empty() {
            *self = Default::default();
        }
        before != self.capture_half_scale()
    }

    pub fn user_custom_fps(&mut self, id: i32, fps: u32) {
        if fps < MIN_FPS || fps > MAX_FPS {
            return;
        }
        if let Some(user) = self.users.get_mut(&id) {
            user.custom_fps = Some(fps);
        }
    }

    pub fn user_auto_adjust_fps(&mut self, id: i32, fps: u32) {
        if fps < MIN_FPS || fps > MAX_FPS {
            return;
        }
        if let Some(user) = self.users.get_mut(&id) {
            user.auto_adjust_fps = Some(fps);
        }
    }

    pub fn user_image_quality(&mut self, id: i32, image_quality: i32) -> bool {
        let before = self.capture_half_scale();
        let convert_quality = |q: i32| -> Quality {
            if q == ImageQuality::Balanced.value() {
                Quality::Balanced
            } else if q == ImageQuality::Low.value() {
                Quality::Low
            } else if q == ImageQuality::Best.value() {
                Quality::Best
            } else {
                let b = ((q >> 8 & 0xFFF) * 2) as f32 / 100.0;
                Quality::Custom(b.clamp(BR_MIN, BR_MAX))
            }
        };

        self.quality_sequence = self.quality_sequence.saturating_add(1);
        let quality = Some((self.quality_sequence, convert_quality(image_quality)));
        if let Some(user) = self.users.get_mut(&id) {
            user.quality = quality;
            // update ratio directly
            self.ratio = self.latest_quality().ratio();
        }
        before != self.capture_half_scale()
    }

    pub fn user_record(&mut self, id: i32, v: bool) {
        if let Some(user) = self.users.get_mut(&id) {
            user.record = v;
        }
    }

    pub fn user_network_delay(&mut self, id: i32, delay: u32) {
        let highest_fps = self.highest_fps();

        let mut adjust_ratio = false;
        if let Some(user) = self.users.get_mut(&id) {
            let delay = delay.max(10);
            user.delay.add_delay(delay);
            let tier = classify_delay(user.delay.avg_delay().max(10));
            if tier == NetworkTier::Good {
                user.delay.increase_fps_count += 1;
            } else {
                user.delay.increase_fps_count = 0;
            }
            let recovery_ready = user.delay.increase_fps_count >= RECOVERY_SAMPLES;
            if recovery_ready {
                user.delay.increase_fps_count = 0;
            }
            let fps = next_fps(self.fps, highest_fps, tier, recovery_ready);
            // first network delay message
            adjust_ratio = user.delay.fps.is_none();
            user.delay.fps = Some(fps);
        }
        self.adjust_fps();
        if adjust_ratio && !cfg!(target_os = "linux") {
            //Reduce the possibility of vaapi being created twice
            self.adjust_ratio(false);
        }
    }

    pub fn user_delay_response_elapsed(&mut self, id: i32, elapsed: u128) {
        if let Some(user) = self.users.get_mut(&id) {
            user.delay.response_delayed = elapsed > 2000;
            if user.delay.response_delayed {
                user.delay.add_delay(elapsed as u32);
                self.adjust_fps();
            }
        }
    }
}

// Common adjust functions
impl VideoQoS {
    pub fn new_display(&mut self, video_service_name: String) {
        self.displays
            .insert(video_service_name, DisplayData::default());
    }

    pub fn remove_display(&mut self, video_service_name: &str) {
        self.displays.remove(video_service_name);
    }

    pub fn update_display_data(&mut self, video_service_name: &str, send_counter: usize) {
        if let Some(display) = self.displays.get_mut(video_service_name) {
            display.send_counter += send_counter;
        }
        self.adjust_fps();
        let abr_enabled = self.in_vbr_state();
        if abr_enabled {
            if self.adjust_ratio_instant.elapsed().as_secs() >= ADJUST_RATIO_INTERVAL as u64 {
                let dynamic_screen = self
                    .displays
                    .iter()
                    .any(|d| d.1.send_counter >= ADJUST_RATIO_INTERVAL * DYNAMIC_SCREEN_THRESHOLD);
                self.displays.iter_mut().for_each(|d| {
                    d.1.send_counter = 0;
                });
                self.adjust_ratio(dynamic_screen);
            }
        } else {
            self.ratio = self.latest_quality().ratio();
        }
    }

    pub(crate) fn network_tier(&self) -> NetworkTier {
        self.users
            .values()
            .map(|user| classify_delay(user.delay.avg_delay()))
            .max_by_key(|tier| match tier {
                NetworkTier::Good => 0,
                NetworkTier::Stable => 1,
                NetworkTier::Congested => 2,
                NetworkTier::Severe => 3,
            })
            .unwrap_or(NetworkTier::Stable)
    }

    pub fn capture_half_scale(&self) -> bool {
        super::video_policy::capture_half_scale(self.latest_quality())
    }

    pub fn snapshot(&self) -> VideoQosSnapshot {
        VideoQosSnapshot {
            target_bitrate: self.bitrate(),
            target_fps: self.fps(),
            qos_tier: self.network_tier().as_str(),
            capture_scale: if self.capture_half_scale() {
                "half"
            } else {
                "full"
            },
        }
    }

    #[inline]
    fn highest_fps(&self) -> u32 {
        let user_fps = |u: &UserData| {
            let mut fps = u.custom_fps.unwrap_or(FPS);
            if let Some(auto_adjust_fps) = u.auto_adjust_fps {
                if fps == 0 || auto_adjust_fps < fps {
                    fps = auto_adjust_fps;
                }
            }
            fps
        };

        let fps = self
            .users
            .iter()
            .map(|(_, u)| user_fps(u))
            .filter(|u| *u >= MIN_FPS)
            .min()
            .unwrap_or(FPS);

        fps.clamp(MIN_FPS, MAX_FPS)
    }

    // Get latest quality settings from all users
    pub fn latest_quality(&self) -> Quality {
        self.users
            .values()
            .filter_map(|user| user.quality)
            .max_by_key(|(sequence, _)| *sequence)
            .unwrap_or((0, Quality::Balanced))
            .1
    }

    // Adjust quality ratio based on network delay and screen changes
    fn adjust_ratio(&mut self, dynamic_screen: bool) {
        if !self.in_vbr_state() {
            return;
        }
        // Get maximum delay from all users
        let max_delay = self.users.iter().map(|u| u.1.delay.avg_delay()).max();
        let Some(max_delay) = max_delay else {
            return;
        };

        let target_quality = self.latest_quality();
        let target_ratio = target_quality.ratio();
        let current_ratio = self.ratio;
        let current_bitrate = self.bitrate();

        // Calculate minimum ratio for high resolution (1Mbps baseline)
        let ratio_1mbps = if current_bitrate > 0 {
            Some((current_ratio * 1000.0 / current_bitrate as f32).max(BR_MIN_HIGH_RESOLUTION))
        } else {
            None
        };

        // Calculate ratio for adding 150kbps bandwidth
        let ratio_add_150kbps = if current_bitrate > 0 {
            Some((current_bitrate + 150) as f32 * current_ratio / current_bitrate as f32)
        } else {
            None
        };

        // Set minimum ratio based on quality mode
        let min = match target_quality {
            Quality::Best => {
                // For Best quality, ensure minimum 1Mbps for high resolution
                let mut min = BR_BEST / 2.5;
                if let Some(ratio_1mbps) = ratio_1mbps {
                    if min > ratio_1mbps {
                        min = ratio_1mbps;
                    }
                }
                min.max(BR_MIN)
            }
            Quality::Balanced => {
                let mut min = (BR_BALANCED / 2.0).min(0.4);
                if let Some(ratio_1mbps) = ratio_1mbps {
                    if min > ratio_1mbps {
                        min = ratio_1mbps;
                    }
                }
                min.max(BR_MIN_HIGH_RESOLUTION)
            }
            Quality::Low => BR_MIN_HIGH_RESOLUTION,
            Quality::Custom(_) => BR_MIN_HIGH_RESOLUTION,
        };
        let max = target_ratio * MAX_BR_MULTIPLE;

        let tier = classify_delay(max_delay);
        let mut v = current_ratio * ratio_multiplier(tier, dynamic_screen);

        // Limit quality increase rate for better stability
        if let Some(ratio_add_150kbps) = ratio_add_150kbps {
            if v > ratio_add_150kbps
                && ratio_add_150kbps > current_ratio
                && current_ratio >= BR_SPEED
            {
                v = ratio_add_150kbps;
            }
        }

        self.ratio = v.clamp(min, max);
        self.adjust_ratio_instant = Instant::now();
    }

    // Adjust fps based on network delay and user response time
    fn adjust_fps(&mut self) {
        let highest_fps = self.highest_fps();
        // Get minimum fps from all users
        let mut fps = self
            .users
            .iter()
            .map(|u| u.1.delay.fps.unwrap_or(INIT_FPS))
            .min()
            .unwrap_or(INIT_FPS);

        if self.users.iter().any(|u| u.1.delay.response_delayed) {
            if fps > MIN_FPS + 1 {
                fps = MIN_FPS + 1;
            }
        }

        // For new connections (within 1 second), cap fps to INIT_FPS to ensure stability
        if self.new_user_instant.elapsed().as_secs() < 1 {
            if fps > INIT_FPS {
                fps = INIT_FPS;
            }
        }

        // Ensure fps stays within valid range
        self.fps = fps.clamp(MIN_FPS, highest_fps);
    }
}

#[derive(Default, Debug, Clone)]
struct RttCalculator {
    min_rtt: Option<u32>,        // Historical minimum RTT ever observed
    window_min_rtt: Option<u32>, // Minimum RTT within last 60 samples
    smoothed_rtt: Option<u32>,   // Smoothed RTT estimation
    samples: VecDeque<u32>,      // Last 60 RTT samples
}

impl RttCalculator {
    const WINDOW_SAMPLES: usize = 60; // Keep last 60 samples
    const MIN_SAMPLES: usize = 10; // Require at least 10 samples
    const ALPHA: f32 = 0.5; // Smoothing factor for weighted average

    /// Update RTT estimates with a new sample
    pub fn update(&mut self, delay: u32) {
        // 1. Update historical minimum RTT
        match self.min_rtt {
            Some(min_rtt) if delay < min_rtt => self.min_rtt = Some(delay),
            None => self.min_rtt = Some(delay),
            _ => {}
        }

        // 2. Update sample window
        if self.samples.len() >= Self::WINDOW_SAMPLES {
            self.samples.pop_front();
        }
        self.samples.push_back(delay);

        // 3. Calculate minimum RTT within the window
        self.window_min_rtt = self.samples.iter().min().copied();

        // 4. Calculate smoothed RTT
        // Use weighted average if we have enough samples
        if self.samples.len() >= Self::WINDOW_SAMPLES {
            if let (Some(min), Some(window_min)) = (self.min_rtt, self.window_min_rtt) {
                // Weighted average of historical minimum and window minimum
                let new_srtt =
                    ((1.0 - Self::ALPHA) * min as f32 + Self::ALPHA * window_min as f32) as u32;
                self.smoothed_rtt = Some(new_srtt);
            }
        }
    }

    /// Get current RTT estimate
    /// Returns None if no valid estimation is available
    pub fn get_rtt(&self) -> Option<u32> {
        if let Some(rtt) = self.smoothed_rtt {
            return Some(rtt);
        }
        if self.samples.len() >= Self::MIN_SAMPLES {
            if let Some(rtt) = self.min_rtt {
                return Some(rtt);
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn connected_qos() -> VideoQoS {
        let mut qos = VideoQoS::default();
        qos.on_connection_open(7);
        qos.new_user_instant = Instant::now() - Duration::from_secs(2);
        qos
    }

    #[test]
    fn custom_fps_rejects_values_outside_product_range() {
        let mut qos = connected_qos();
        qos.user_custom_fps(7, 9);
        assert_eq!(qos.highest_fps(), 60);
        qos.user_custom_fps(7, 45);
        assert_eq!(qos.highest_fps(), 45);
        qos.user_custom_fps(7, MAX_FPS + 1);
        assert_eq!(qos.highest_fps(), 45);
    }

    #[test]
    fn a_new_connection_starts_at_the_safe_initial_fps() {
        let qos = connected_qos();
        assert_eq!(qos.fps(), INIT_FPS);
        assert_eq!(qos.encoder_fps(), FPS);
    }

    #[test]
    fn adaptive_fps_never_raises_a_user_cap() {
        let mut qos = connected_qos();
        qos.user_custom_fps(7, 30);
        for _ in 0..3 {
            qos.user_network_delay(7, 20);
        }
        assert!(qos.fps() <= 30);
    }

    #[test]
    fn congested_and_severe_samples_reduce_ratio() {
        let mut qos = connected_qos();
        qos.ratio = BR_BALANCED;
        qos.store_bitrate(2_000);
        if let Some(user) = qos.users.get_mut(&7) {
            user.delay.delay_history = VecDeque::from([300, 300]);
        }
        qos.adjust_ratio(true);
        assert!(qos.ratio() < BR_BALANCED);

        let congested = qos.ratio();
        if let Some(user) = qos.users.get_mut(&7) {
            user.delay.delay_history = VecDeque::from([600, 600]);
        }
        qos.adjust_ratio(true);
        assert!(qos.ratio() < congested);
    }

    #[test]
    fn client_fps_normalization_matches_the_server_contract() {
        assert_eq!(crate::client::normalize_custom_fps(5), 10);
        assert_eq!(crate::client::normalize_custom_fps(45), 45);
        assert_eq!(crate::client::normalize_custom_fps(120), 120);
        assert_eq!(crate::client::normalize_custom_fps(200), 120);
    }

    #[test]
    fn best_quality_changes_capture_to_full_resolution() {
        let mut qos = connected_qos();
        assert!(qos.capture_half_scale());
        assert!(qos.user_image_quality(7, ImageQuality::Best.value()));
        assert!(!qos.capture_half_scale());
        assert!(!qos.user_image_quality(7, ImageQuality::Best.value()));
    }

    #[test]
    fn removing_latest_best_user_restores_balanced_scale() {
        let mut qos = connected_qos();
        qos.on_connection_open(8);
        qos.user_image_quality(7, ImageQuality::Balanced.value());
        qos.user_image_quality(8, ImageQuality::Best.value());
        assert!(!qos.capture_half_scale());
        assert!(qos.on_connection_close(8));
        assert!(qos.capture_half_scale());
    }
}
