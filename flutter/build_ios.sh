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
build_log_dir="$project_root/flutter/build/linkremote-ios-logs"
mkdir -p "$build_log_dir"
build_log="$build_log_dir/$rust_target-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}.log"
# Capture only build output. pipefail preserves errors from codegen, Cargo and
# Flutter while the workflow can upload this file before cache post-processing.
python3 flutter/ios/verify_controller.py 2>&1 | tee "$build_log"
(cd flutter && flutter pub get) 2>&1 | tee -a "$build_log"
# Generate on every build so a clean checkout and changed FFI declarations work.
flutter_rust_bridge_codegen \
  --rust-input ./src/flutter_ffi.rs \
  --dart-output ./flutter/lib/generated_bridge.dart \
  --c-output ./flutter/ios/Runner/bridge_generated.h \
  --class-name Rustdesk 2>&1 | tee -a "$build_log"
SDKROOT="$(xcrun --sdk "$apple_sdk" --show-sdk-path)" \
  IPHONEOS_DEPLOYMENT_TARGET=13.0 \
  cargo build --locked --features "$rust_features" --release --target "$rust_target" --lib \
    2>&1 | tee -a "$build_log"
cd flutter

if [[ "${1:-}" == "--simulator" ]]; then
  flutter build ios --simulator --debug --no-codesign 2>&1 | tee -a "$build_log"
  python3 ios/verify_controller.py --app build/ios/iphonesimulator/Runner.app --simulator \
    2>&1 | tee -a "$build_log"
elif [[ "${1:-}" == "--unsigned" ]]; then
  flutter build ios --release --no-codesign 2>&1 | tee -a "$build_log"
  python3 ios/verify_controller.py --app build/ios/iphoneos/Runner.app 2>&1 | tee -a "$build_log"
else
  flutter build ipa --release --export-options-plist=ios/exportOptions.plist 2>&1 | tee -a "$build_log"
  python3 ios/verify_controller.py --app build/ios/archive/Runner.xcarchive/Products/Applications/Runner.app \
    2>&1 | tee -a "$build_log"
fi
