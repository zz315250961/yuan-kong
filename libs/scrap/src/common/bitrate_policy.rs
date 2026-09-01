pub fn fps_bitrate_scale(target_fps: u32) -> f32 {
    ((target_fps.max(1) as f32 / 30.0).sqrt()).clamp(0.75, 1.45)
}

#[cfg(test)]
mod tests {
    use super::fps_bitrate_scale;

    #[test]
    fn thirty_fps_is_the_bitrate_baseline() {
        assert!((fps_bitrate_scale(30) - 1.0).abs() < 0.001);
    }

    #[test]
    fn sixty_fps_gets_a_bounded_sublinear_budget() {
        let scale = fps_bitrate_scale(60);
        assert!(scale > 1.40 && scale < 1.42);
    }

    #[test]
    fn invalid_extremes_are_clamped() {
        assert!((fps_bitrate_scale(1) - 0.75).abs() < 0.001);
        assert!((fps_bitrate_scale(120) - 1.45).abs() < 0.001);
    }
}
