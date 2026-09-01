extern crate self as scrap;

pub mod codec {
    #[derive(Clone, Copy, Debug, PartialEq)]
    pub enum Quality {
        Best,
        Balanced,
        Low,
        Custom(f32),
    }
}

#[path = "../src/server/video_policy.rs"]
mod video_policy;
