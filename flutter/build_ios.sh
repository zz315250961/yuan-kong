#!/usr/bin/env bash
set -euo pipefail

# Run on macOS with Xcode, Flutter, bridge codegen and native dependencies.
case "${1:-}" in
  ""|--unsigned|--simulator) ;;
  --help|-h)
    printf '%s\n' 'Usage: ./flutter/build_ios.sh [--unsigned|--simulator]' \
      'Default: signed IPA using the Runner signing team and ios/exportOptions.plist.' \
      '--unsigned: device build for inspection only; cannot be installed.' \
      '--simulator: debug iOS Simulator app on an Intel Mac; no Apple account needed.'
    exit 0
    ;;
  *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  printf '%s\n' 'Only one build mode may be supplied.' >&2
  exit 2
fi
if [[ "$(uname -s)" != Darwin ]]; then
  printf '%s\n' 'iOS builds require macOS and Xcode.' >&2
  exit 1
fi
for tool in cargo flutter flutter_rust_bridge_codegen python3 xcodebuild xcrun pod; do
  command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done
cargo expand --version >/dev/null
rust_target=aarch64-apple-ios
rust_features=flutter,hwcodec
apple_sdk=iphoneos
if [[ "${1:-}" == "--simulator" ]]; then
  if [[ "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'The verified simulator path requires an Intel Mac (CI: macos-15-intel).' >&2
    exit 1
  fi
  rust_target=x86_64-apple-ios
  rust_features=flutter
  apple_sdk=iphonesimulator
fi

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
python3 flutter/ios/verify_controller.py
(cd flutter && flutter pub get)
# Generate on every build so a clean checkout and changed FFI declarations work.
flutter_rust_bridge_codegen \
  --rust-input ./src/flutter_ffi.rs \
  --dart-output ./flutter/lib/generated_bridge.dart \
  --c-output ./flutter/ios/Runner/bridge_generated.h \
  --class-name Rustdesk
SDKROOT="$(xcrun --sdk "$apple_sdk" --show-sdk-path)" \
  IPHONEOS_DEPLOYMENT_TARGET=13.0 \
  cargo build --locked --features "$rust_features" --release --target "$rust_target" --lib
cd flutter

if [[ "${1:-}" == "--simulator" ]]; then
  flutter build ios --simulator --debug --no-codesign
  python3 ios/verify_controller.py --app build/ios/iphonesimulator/Runner.app --simulator
elif [[ "${1:-}" == "--unsigned" ]]; then
  flutter build ios --release --no-codesign
  python3 ios/verify_controller.py --app build/ios/iphoneos/Runner.app
else
  flutter build ipa --release --export-options-plist=ios/exportOptions.plist
  python3 ios/verify_controller.py --app build/ios/archive/Runner.xcarchive/Products/Applications/Runner.app
fi
