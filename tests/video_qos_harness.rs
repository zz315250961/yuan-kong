extern crate self as hbb_common;
extern crate self as scrap;

use std::collections::HashMap;

#[path = "../libs/scrap/src/common/bitrate_policy.rs"]
mod bitrate_policy;

// These small types isolate platform/native dependencies only. Their numeric
// contracts are checked against production by run_video_policy_tests.py.
pub mod codec {
    pub use super::bitrate_policy::fps_bitrate_scale;

    #[derive(Clone, Copy, Debug, Default, PartialEq)]
    pub enum Quality {
        Best,
        #[default]
        Balanced,
        Low,
        Custom(f32),
    }

    impl Quality {
        pub fn ratio(self) -> f32 {
            match self {
                Self::Best => BR_BEST,
                Self::Balanced => BR_BALANCED,
                Self::Low => BR_SPEED,
                Self::Custom(value) => value,
            }
        }
    }

    pub const BR_BEST: f32 = 1.5;
    pub const BR_BALANCED: f32 = 0.67;
    pub const BR_SPEED: f32 = 0.5;
}

pub fn get_time() -> i64 {
    1
}

pub struct Config;

impl Config {
    pub fn get_option(_name: &str) -> String {
        String::new()
    }
}

#[derive(Clone, Copy)]
pub enum ImageQuality {
    Balanced,
    Low,
    Best,
}

impl ImageQuality {
    pub fn value(self) -> i32 {
        match self {
            Self::Balanced => 3,
            Self::Low => 2,
            Self::Best => 4,
        }
    }
}

pub mod client {
    pub fn normalize_custom_fps(fps: i32) -> i32 {
        fps.clamp(10, 120)
    }
}

#[path = "../src/server/video_policy.rs"]
mod video_policy;

#[path = "../src/server/video_qos.rs"]
mod video_qos;
