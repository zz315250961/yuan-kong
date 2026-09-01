# LinkRemote Video Quality and FPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FPS limits, QoS decisions, encoder timebase, bitrate budget, and Android capture scaling consistent and observable.

**Architecture:** Put threshold and scaling decisions in pure functions, keep `VideoQoS` responsible for per-session state, and let `video_service` apply the resulting capture/encoder settings. Extend the existing `TestDelay` status message only with optional fields so older peers remain compatible.

**Tech Stack:** Rust 1.75, protobuf, Flutter 3.24.5/Dart, existing RustDesk capture and encoder abstractions.

**Spec:** `docs/superpowers/specs/2026-09-01-linkremote-performance-platform-web-management-design.md`

## Global Constraints

- Formal remote-control targets are Windows x64 and Android arm64-v8a.
- Product FPS range is 10–60; default is 60.
- Best quality uses full-resolution Android capture; Balanced and Low may use half-resolution capture.
- Do not enable the dormant Android MediaCodec direct path.
- Do not add `unwrap()` or `expect()` calls.
- Every behavior change starts with a failing test and each task ends in an independent commit.

---

### Task 1: Extract a testable QoS policy

**Files:**
- Create: `src/server/video_policy.rs`
- Modify: `src/server.rs:68-72`
- Test: `src/server/video_policy.rs`

**Interfaces:**
- Consumes: `scrap::codec::Quality`.
- Produces: `NetworkTier`, `classify_delay(u32)`, `next_fps(u32, u32, NetworkTier, bool)`, `ratio_multiplier(NetworkTier, bool)`, and `capture_half_scale(Quality)`.

- [ ] **Step 1: Register the new module and write failing table tests**

```rust
// src/server.rs
mod video_policy;

// src/server/video_policy.rs
#[cfg(test)]
mod tests {
    use super::*;

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
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cargo test --lib server::video_policy::tests -- --nocapture`

Expected: compilation fails because the policy types and functions do not exist.

- [ ] **Step 3: Implement the pure policy**

```rust
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
```

- [ ] **Step 4: Run the policy tests and verify GREEN**

Run: `cargo test --lib server::video_policy::tests -- --nocapture`

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/server.rs src/server/video_policy.rs
git commit -m "test: define adaptive video policy"
```

### Task 2: Replace unreachable QoS branches and align FPS limits

**Files:**
- Modify: `src/server/video_qos.rs`
- Modify: `src/client.rs:2298-2315,2504-2520`
- Modify: `src/client/io_loop.rs:1182-1187`
- Modify: `flutter/lib/consts.dart:269-271`
- Test: `src/server/video_qos.rs`

**Interfaces:**
- Consumes: Task 1 policy functions and constants.
- Produces: `VideoQoS::network_tier() -> NetworkTier`, a 10–60 custom FPS contract, and hysteretic FPS/ratio adjustment.

- [ ] **Step 1: Write failing state-level QoS tests**

```rust
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
        assert_eq!(qos.highest_fps(), DEFAULT_FPS);
        qos.user_custom_fps(7, 45);
        assert_eq!(qos.highest_fps(), 45);
        qos.user_custom_fps(7, 61);
        assert_eq!(qos.highest_fps(), 45);
    }

    #[test]
    fn android_or_desktop_never_raises_a_user_cap() {
        let mut qos = connected_qos();
        qos.user_custom_fps(7, 30);
        for _ in 0..RECOVERY_SAMPLES {
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
        } else {
            panic!("connected user missing");
        }
        qos.adjust_ratio(true);
        assert!(qos.ratio() < BR_BALANCED);
        let congested = qos.ratio();
        if let Some(user) = qos.users.get_mut(&7) {
            user.delay.delay_history = VecDeque::from([600, 600]);
        } else {
            panic!("connected user missing");
        }
        qos.adjust_ratio(true);
        assert!(qos.ratio() < congested);
    }

    #[test]
    fn client_fps_normalization_matches_the_server_contract() {
        assert_eq!(crate::client::normalize_custom_fps(5), 10);
        assert_eq!(crate::client::normalize_custom_fps(45), 45);
        assert_eq!(crate::client::normalize_custom_fps(120), 60);
    }
}
```

- [ ] **Step 2: Run and verify RED**

Run: `cargo test --lib server::video_qos::tests -- --nocapture`

Expected: tests fail because the current Android cap can rise to 60, values above 60 are accepted, and the 600 ms branch ordering does not produce the expected tiers.

- [ ] **Step 3: Integrate the pure policy into `VideoQoS`**

```rust
use super::video_policy::{
    classify_delay, next_fps, ratio_multiplier, NetworkTier, DEFAULT_FPS as FPS,
    INITIAL_FPS as INIT_FPS, MAX_FPS, MIN_FPS, RECOVERY_SAMPLES,
};

pub fn network_tier(&self) -> NetworkTier {
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
```

In `user_network_delay`, replace the current `<50/<100/<600/...` tree with `classify_delay(avg_delay)`, increment the recovery counter only for `Good`, reset it for every other tier, and call:

```rust
let recovery_ready = user.delay.increase_fps_count >= RECOVERY_SAMPLES;
if recovery_ready {
    user.delay.increase_fps_count = 0;
}
fps = next_fps(fps, highest_fps, tier, recovery_ready);
user.delay.fps = Some(fps);
```

In `adjust_ratio`, replace the unreachable delay tree with:

```rust
let tier = classify_delay(max_delay);
let v = current_ratio * ratio_multiplier(tier, dynamic_screen);
self.ratio = v.clamp(min, max);
```

Delete the misleading `DELAY_THRESHOLD_150MS = 600` constant and the Android-only `upper_fps = 60` branch.

- [ ] **Step 4: Align client and Flutter validation**

```dart
const double kMinFps = 10;
const double kDefaultFps = 60;
const double kMaxFps = 60;
```

```rust
pub(crate) fn normalize_custom_fps(fps: i32) -> i32 {
    fps.clamp(10, 60)
}

let mut custom_fps = custom_fps.unwrap_or(60);
custom_fps = normalize_custom_fps(custom_fps as i32) as usize;
```

Call `normalize_custom_fps` both when reading `custom-fps` into an `OptionMessage` and in `set_custom_fps` before saving or sending it, so persisted legacy values cannot bypass the UI range.

- [ ] **Step 5: Run Rust and Flutter-focused verification**

Run:

```bash
cargo test --lib server:: -- --nocapture
cd flutter && flutter test test/server_settings_dialog_test.dart
```

Expected: Rust QoS tests pass; the existing Flutter test remains green.

- [ ] **Step 6: Commit**

```bash
git add src/server/video_qos.rs src/client.rs src/client/io_loop.rs flutter/lib/consts.dart
git commit -m "fix: make video qos thresholds reachable"
```

### Task 3: Scale bitrate by FPS and configure hardware encoder timebase

**Files:**
- Modify: `libs/scrap/src/common/codec.rs:941-983`
- Modify: `libs/scrap/src/common/hwcodec.rs:29-83`
- Modify: `libs/scrap/src/common/vram.rs:38-80`
- Modify: `libs/scrap/examples/benchmark.rs`
- Modify: `src/server/video_qos.rs`
- Modify: `src/server/video_service.rs:570-630,979-1086,1370-1390`
- Test: `libs/scrap/src/common/codec.rs`

**Interfaces:**
- Consumes: `VideoQoS::fps()` and raw image-quality ratio.
- Produces: `fps_bitrate_scale(u32) -> f32`, `VideoQoS::encoder_ratio() -> f32`, and explicit `fps: u32` fields on HWRAM/VRAM configs.

- [ ] **Step 1: Write failing bitrate scaling tests**

```rust
#[cfg(test)]
mod fps_bitrate_tests {
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
```

- [ ] **Step 2: Run and verify RED**

Run: `cargo test -p scrap fps_bitrate_tests -- --nocapture`

Expected: compilation fails because `fps_bitrate_scale` does not exist.

- [ ] **Step 3: Implement the FPS coefficient**

```rust
pub fn fps_bitrate_scale(target_fps: u32) -> f32 {
    ((target_fps.max(1) as f32 / 30.0).sqrt()).clamp(0.75, 1.45)
}
```

- [ ] **Step 4: Make the encoder ratio respond to both QoS ratio and FPS**

```rust
pub fn encoder_ratio(&mut self) -> f32 {
    self.ratio() * scrap::codec::fps_bitrate_scale(self.fps())
}
```

Initialize `quality` with `video_qos.encoder_ratio()` and in `check_qos` compare against a single freshly computed `encoder_ratio`. A change to FPS must therefore call `encoder.set_quality(encoder_ratio)` even when the raw quality ratio is unchanged.

- [ ] **Step 5: Add explicit encoder FPS fields**

```rust
pub struct HwRamEncoderConfig {
    pub name: String,
    pub mc_name: Option<String>,
    pub width: usize,
    pub height: usize,
    pub quality: f32,
    pub fps: u32,
    pub keyframe_interval: Option<usize>,
}

pub struct VRamEncoderConfig {
    pub device: AdapterDevice,
    pub width: usize,
    pub height: usize,
    pub quality: f32,
    pub fps: u32,
    pub feature: FeatureContext,
    pub keyframe_interval: Option<usize>,
}
```

Use `config.fps.clamp(1, 120) as i32` for HWRAM `EncodeContext.fps` and `config.fps.clamp(1, 120) as i32` for VRAM `DynamicContext.framerate`. Pass the current QoS FPS from `setup_encoder` into `get_encoder_config`, and set the benchmark sample to `fps: 60`.

- [ ] **Step 6: Run focused tests and feature compilation**

Run:

```bash
cargo test -p scrap fps_bitrate_tests -- --nocapture
cargo check --lib --features hwcodec
cargo check --lib --features hwcodec,vram
```

Expected: 3 scaling tests pass and both encoder feature sets compile.

- [ ] **Step 7: Commit**

```bash
git add libs/scrap/src/common/codec.rs libs/scrap/src/common/hwcodec.rs libs/scrap/src/common/vram.rs libs/scrap/examples/benchmark.rs src/server/video_qos.rs src/server/video_service.rs
git commit -m "fix: align encoder bitrate with target fps"
```

### Task 4: Drive Android capture scale from the active connection quality

**Files:**
- Modify: `src/server/video_qos.rs`
- Modify: `src/server/video_service.rs:1124-1158`
- Modify: `src/server/connection.rs:4657-4683,6740-6760,6860-6880`
- Modify: `libs/scrap/src/android/ffi.rs:31-195`
- Modify: `flutter/android/app/src/main/kotlin/com/carriez/flutter_hbb/MainService.kt:213-226,384-436`
- Test: `src/server/video_qos.rs`

**Interfaces:**
- Consumes: Task 1 `capture_half_scale(Quality)`.
- Produces: `VideoQoS::capture_half_scale() -> bool`; `user_image_quality` and `on_connection_close` return whether capture scale changed.

- [ ] **Step 1: Write failing scale-transition tests**

```rust
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
```

- [ ] **Step 2: Run and verify RED**

Run: `cargo test --lib server::video_qos::tests -- --nocapture`

Expected: compilation fails because the transition APIs do not return change information.

- [ ] **Step 3: Implement state transitions without writing global config**

```rust
pub fn capture_half_scale(&self) -> bool {
    super::video_policy::capture_half_scale(self.latest_quality())
}

pub fn user_image_quality(&mut self, id: i32, image_quality: i32) -> bool {
    let before = self.capture_half_scale();
    // retain the existing quality conversion and latest timestamp update
    let after = self.capture_half_scale();
    before != after
}
```

Apply the same before/after comparison in `on_connection_close` before resetting an empty `VideoQoS`.

- [ ] **Step 4: Refresh capture only when the policy changes**

In `Connection::update_options`, retain the `VideoQoS` lock only long enough to update quality, then on Android call `self.refresh_video_display(None)` when the returned value is true. In `AuthedConnID::drop`, release the QoS lock before asking `CLIENT_SERVER` to set `OPTION_REFRESH` on all video services.

Replace the global option lookup in `check_change_scale` with:

```rust
let half_scale = VIDEO_QOS
    .lock()
    .map(|qos| qos.capture_half_scale())
    .unwrap_or(true);
```

Do not add a `Config::set_option` call.

- [ ] **Step 5: Run regression tests and Android compile check**

Before compiling, isolate the dormant direct MediaCodec experiment from formal builds: gate the encoded-frame queue/functions and the direct-send branch with `#[cfg(feature = "mediacodec")]`, and rename Kotlin `useVP9` to `useExperimentalMediaCodec`. Keep it `false`; the arm64 release feature list remains `flutter,hwcodec`.

Run:

```bash
cargo test --lib server::video_qos::tests -- --nocapture
cargo check --lib
cargo ndk --target arm64-v8a --platform 21 check --features flutter,hwcodec
```

Expected: QoS tests pass; host build passes; Android arm64 code compiles when the NDK is available. If the NDK is unavailable, record that command as CI-required rather than reporting it as passed.

- [ ] **Step 6: Commit**

```bash
git add src/server/video_qos.rs src/server/video_service.rs src/server/connection.rs libs/scrap/src/android/ffi.rs flutter/android/app/src/main/kotlin/com/carriez/flutter_hbb/MainService.kt
git commit -m "fix: honor controller quality on android capture"
```

### Task 5: Expose truthful QoS state in the quality monitor

**Files:**
- Modify: `libs/hbb_common/protos/message.proto:701-706`
- Modify: `src/server/video_qos.rs`
- Modify: `src/server/connection.rs:1095-1110`
- Modify: `src/client/helper.rs`
- Modify: `src/ui_session_interface.rs:1900-1915`
- Modify: `src/client/io_loop.rs:305-345`
- Modify: `src/flutter.rs:714-742`
- Modify: `flutter/lib/models/model.dart:3558-3625`
- Modify: `flutter/lib/common/widgets/overlay.dart:561-610`
- Create: `flutter/test/quality_monitor_data_test.dart`

**Interfaces:**
- Consumes: `VideoQoS::fps()`, `network_tier()`, and `capture_half_scale()`.
- Produces: optional protobuf fields `target_fps`, `qos_tier`, `capture_scale`; Flutter fields with the same meaning plus local transport type.

- [ ] **Step 1: Write a failing Dart parser test**

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/models/model.dart';

void main() {
  test('quality monitor parses adaptive video fields', () {
    final data = QualityMonitorData();
    data.applyEvent({
      'target_fps': '45',
      'qos_tier': 'congested',
      'capture_scale': 'half',
      'transport': 'relay',
    });
    expect(data.targetFps, '45');
    expect(data.qosTier, 'congested');
    expect(data.captureScale, '1/2');
    expect(data.transport, 'relay');
  });
}
```

- [ ] **Step 2: Run and verify RED**

Run: `cd flutter && flutter test test/quality_monitor_data_test.dart`

Expected: compilation fails because `applyEvent` and the new fields do not exist.

- [ ] **Step 3: Add backward-compatible status fields**

```proto
message TestDelay {
  int64 time = 1;
  bool from_client = 2;
  uint32 last_delay = 3;
  uint32 target_bitrate = 4;
  uint32 target_fps = 5;
  string qos_tier = 6;
  string capture_scale = 7;
}
```

When the server sends `TestDelay`, take one QoS lock and populate all four server metrics from a `VideoQosSnapshot { target_bitrate, target_fps, tier, capture_scale }`. Older peers ignore fields 5–7.

- [ ] **Step 4: Carry the fields through Rust and parse them in one Dart method**

Extend `QualityStatus` with `target_fps: Option<i32>`, `qos_tier: Option<String>`, `capture_scale: Option<String>`, and `transport: Option<String>`. The server emits `"full"` or `"half"`; an older peer leaves the string empty, so the UI does not claim a scale it did not receive. The periodic client status sets `transport` from the existing `direct` boolean. `flutter.rs` emits the four string fields.

Move the current per-key assignments into `QualityMonitorData.applyEvent` and add:

```dart
String? targetFps;
String? qosTier;
String? captureScale;
String? transport;

void applyEvent(Map<String, dynamic> evt) {
  if ((evt['target_fps'] as String? ?? '').isNotEmpty) {
    targetFps = evt['target_fps'];
  }
  if ((evt['qos_tier'] as String? ?? '').isNotEmpty) {
    qosTier = evt['qos_tier'];
  }
  if ((evt['capture_scale'] as String? ?? '').isNotEmpty) {
    captureScale = evt['capture_scale'] == 'half' ? '1/2' : 'Full';
  }
  if ((evt['transport'] as String? ?? '').isNotEmpty) {
    transport = evt['transport'];
  }
  final speedValue = evt['speed'] as String? ?? '';
  final delayValue = evt['delay'] as String? ?? '';
  final bitrateValue = evt['target_bitrate'] as String? ?? '';
  final codecValue = evt['codec_format'] as String? ?? '';
  final chromaValue = evt['chroma'] as String? ?? '';
  if (speedValue.isNotEmpty) speed = speedValue;
  if (delayValue.isNotEmpty) delay = delayValue;
  if (bitrateValue.isNotEmpty) targetBitrate = bitrateValue;
  if (codecValue.isNotEmpty) codecFormat = codecValue;
  if (chromaValue.isNotEmpty) chroma = chromaValue;
}
```

- [ ] **Step 5: Render only available metrics**

Add rows for `Target FPS`, `Network`, `Capture`, and `Transport`. Retain `-` for an older peer that does not send the optional fields; do not display packet loss, encoder FPS, or queue depth because those values are not currently measured.

- [ ] **Step 6: Run the Dart test and Rust checks**

Run:

```bash
cd flutter && flutter test test/quality_monitor_data_test.dart
cd .. && cargo test --lib server::video_qos::tests -- --nocapture
cargo check --lib
```

Expected: the new Dart parser test and QoS tests pass, and protobuf regeneration compiles.

- [ ] **Step 7: Commit**

```bash
git add libs/hbb_common/protos/message.proto src/server/video_qos.rs src/server/connection.rs src/client/helper.rs src/ui_session_interface.rs src/client/io_loop.rs src/flutter.rs flutter/lib/models/model.dart flutter/lib/common/widgets/overlay.dart flutter/test/quality_monitor_data_test.dart
git commit -m "feat: expose adaptive video status"
```

### Task 6: Document the reproducible performance validation

**Files:**
- Create: `docs/performance-validation.md`
- Modify: `docs/superpowers/specs/2026-09-01-linkremote-performance-platform-web-management-design.md`

**Interfaces:**
- Consumes: the metrics exposed by Task 5.
- Produces: a fixed Windows/Android validation procedure and explicit pass/fail rules.

- [ ] **Step 1: Write the validation procedure**

The document must define four 120-second scenes: static text, continuous webpage scroll, 1080p video, and rapid window dragging. Run them for Windows-controlled and Android-controlled targets on LAN, then under 100/250/500 ms shaped latency. For each run record actual FPS, target FPS, bitrate, QoS tier, capture scale, codec, direct/relay, crashes, black screens, and reconnect count.

- [ ] **Step 2: Add the release gate**

Record these exact rules:

```text
- Actual FPS is at least 95% of the v1.0.35 baseline in the same scene.
- Best on Android reports Full; Balanced and Low report 1/2.
- A 60 FPS session reports encoder target 60 rather than 30.
- 99/100/249/250/499/500 ms samples enter the expected QoS tiers.
- Recovery requires three consecutive Good samples.
- No crash, black screen, or infinite reconnect is accepted.
```

- [ ] **Step 3: Check documentation formatting and commit**

Run: `git diff --check`

Expected: exit code 0.

```bash
git add docs/performance-validation.md docs/superpowers/specs/2026-09-01-linkremote-performance-platform-web-management-design.md
git commit -m "docs: define remote video performance gate"
```
