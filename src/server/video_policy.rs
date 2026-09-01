use scrap::codec::Quality;

pub const MIN_FPS: u32 = 10;
pub const MAX_FPS: u32 = 60;
pub const DEFAULT_FPS: u32 = 60;
pub const INITIAL_FPS: u32 = 30;
pub const RECOVERY_SAMPLES: usize = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NetworkTier {
    Good,
    Stable,
    Congested,
    Severe,
}

impl NetworkTier {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Good => "good",
            Self::Stable => "stable",
            Self::Congested => "congested",
            Self::Severe => "severe",
        }
    }
}

pub fn classify_delay(delay_ms: u32) -> NetworkTier {
    match delay_ms {
        0..=99 => NetworkTier::Good,
        100..=249 => NetworkTier::Stable,
        250..=499 => NetworkTier::Congested,
        _ => NetworkTier::Severe,
    }
}

pub fn next_fps(current: u32, user_cap: u32, tier: NetworkTier, recovery_ready: bool) -> u32 {
    let cap = user_cap.clamp(MIN_FPS, MAX_FPS);
    let next = match tier {
        NetworkTier::Good if recovery_ready => current.saturating_add(5),
        NetworkTier::Good | NetworkTier::Stable => current,
        NetworkTier::Congested => current.min(cap.saturating_mul(3) / 4),
        NetworkTier::Severe => current.min(cap / 2),
    };
    next.clamp(MIN_FPS, cap)
}

pub fn ratio_multiplier(tier: NetworkTier, dynamic_screen: bool) -> f32 {
    match (tier, dynamic_screen) {
        (NetworkTier::Good, true) => 1.10,
        (NetworkTier::Good, false) | (NetworkTier::Stable, _) => 1.0,
        (NetworkTier::Congested, _) => 0.85,
        (NetworkTier::Severe, _) => 0.70,
    }
}

pub fn capture_half_scale(quality: Quality) -> bool {
    !matches!(quality, Quality::Best)
}

#[cfg(test)]
mod tests {
    use super::*;
    use scrap::codec::Quality;

    #[test]
    fn delay_boundaries_are_exclusive_and_reachable() {
        let cases = [
            (99, NetworkTier::Good),
            (100, NetworkTier::Stable),
            (249, NetworkTier::Stable),
            (250, NetworkTier::Congested),
            (499, NetworkTier::Congested),
            (500, NetworkTier::Severe),
        ];

        for (delay, expected) in cases {
            assert_eq!(classify_delay(delay), expected, "delay={delay}");
        }
    }

    #[test]
    fn user_cap_is_never_exceeded() {
        assert_eq!(next_fps(60, 45, NetworkTier::Good, true), 45);
        assert_eq!(next_fps(45, 45, NetworkTier::Congested, false), 33);
        assert_eq!(next_fps(45, 45, NetworkTier::Severe, false), 22);
    }

    #[test]
    fn recovery_requires_a_ready_hysteresis_window() {
        assert_eq!(next_fps(30, 60, NetworkTier::Good, false), 30);
        assert_eq!(next_fps(30, 60, NetworkTier::Good, true), 35);
    }

    #[test]
    fn quality_maps_to_android_capture_scale() {
        assert!(!capture_half_scale(Quality::Best));
        assert!(capture_half_scale(Quality::Balanced));
        assert!(capture_half_scale(Quality::Low));
        assert!(capture_half_scale(Quality::Custom(1.0)));
    }
}
