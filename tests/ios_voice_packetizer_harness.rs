// Compile the production packetizer and its real Rust unit tests. No copied
// implementation or simulated CPAL/Opus/device API is used by this harness.
#[path = "../src/client/voice_packetizer.rs"]
mod voice_packetizer;

// Hardware functions are iOS-gated. Include the same cancellation guard and
// stop-state code on host platforms, without copying it or stubbing CPAL.
#[path = "../src/client/ios_voice.rs"]
mod ios_voice;

#[path = "../src/client/ios_audio_credit.rs"]
mod ios_audio_credit;
