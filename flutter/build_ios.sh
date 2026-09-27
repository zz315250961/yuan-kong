#!/usr/bin/env bash
set -euo pipefail

# Run on macOS with Xcode, Flutter and the Rust iOS target installed. The Rust
# core must be linked into Runner before Flutter can build a working client.
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
cargo build --locked --features flutter,hwcodec --release --target aarch64-apple-ios --lib
cd flutter
flutter pub get

if [[ "${1:-}" == "--unsigned" ]]; then
  flutter build ios --release --no-codesign
else
  flutter build ipa --release --export-options-plist=ios/exportOptions.plist
fi
