#!/usr/bin/env python3
"""Check the iOS controller's source configuration and optional device artifact.

Uses only the Python standard library, so source checks also run on Windows/Linux.
These checks do not replace remote-session or signing validation on an iOS device.
"""

import argparse
import json
import plistlib
import re
import struct
from pathlib import Path


IOS = Path(__file__).resolve().parent
PROJECT = IOS.parent.parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_plist(path):
    with path.open("rb") as stream:
        return plistlib.load(stream)


def check_info(info):
    require(info.get("CFBundleDisplayName") == "LinkRemote", "Unexpected app display name")
    schemes = [scheme for item in info.get("CFBundleURLTypes", [])
               for scheme in item.get("CFBundleURLSchemes", [])]
    require(schemes == ["linkremote"], "Expected the LinkRemote connection URL scheme")
    require(info.get("FlutterDeepLinkingEnabled") is False,
            "uni_links must own deep-link delivery, without Flutter's default handler")
    for device in ("", "~ipad"):
        orientations = info.get("UISupportedInterfaceOrientations" + device, [])
        require({"UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft",
                 "UIInterfaceOrientationLandscapeRight"}.issubset(orientations),
                "Missing portrait or landscape support for " + (device or "iPhone"))
    for permission in ("NSCameraUsageDescription", "NSPhotoLibraryUsageDescription",
                       "NSLocalNetworkUsageDescription", "NSMicrophoneUsageDescription"):
        require(bool(info.get(permission)), "Missing permission purpose: " + permission)
    require(not info.get("UIBackgroundModes"),
            "The controller must not claim unsupported background host capabilities")


def check_source():
    check_info(read_plist(IOS / "Runner/Info.plist"))
    project = (IOS / "Runner.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
    bundle_ids = re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);", project)
    require(len(bundle_ids) == 3 and set(bundle_ids) == {"top.zperme.linkremote"},
            "Debug/Profile/Release bundle IDs do not match")
    require(set(re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([^;]+);", project)) == {"13.0"},
            "Project deployment targets must match iOS 13")
    require(read_plist(IOS / "Flutter/AppFrameworkInfo.plist")["MinimumOSVersion"] == "13.0",
            "Flutter framework deployment target differs from Runner")
    require("platform :ios, '13.0'" in (IOS / "Podfile").read_text(encoding="utf-8"),
            "CocoaPods deployment target differs from Runner")
    icons = IOS / "Runner/Assets.xcassets/AppIcon.appiconset"
    entries = json.loads((icons / "Contents.json").read_text(encoding="utf-8"))["images"]
    for entry in entries:
        filename = icons / entry["filename"]
        with filename.open("rb") as stream:
            header = stream.read(26)
        require(header[:8] == b"\x89PNG\r\n\x1a\n", "Not a PNG: " + str(filename))
        width, height = struct.unpack(">II", header[16:24])
        expected = round(float(entry["size"].split("x")[0]) * float(entry["scale"][:-1]))
        require(width == height == expected, "Wrong icon dimensions: " + str(filename))
        require(header[25] == 2, "iOS icons must be opaque RGB: " + str(filename))
    require(any(entry["idiom"] == "ios-marketing" for entry in entries),
            "Missing App Store icon")
    print(f"PASS: iOS source configuration and {len(entries)} opaque app icon slots")


def check_app(app, simulator=False):
    info = read_plist(app / "Info.plist")
    check_info(info)
    require(info.get("CFBundleIdentifier") == "top.zperme.linkremote", "Wrong artifact bundle ID")
    version = re.search(r"^version:\s*([^+\s]+)\+(\d+)\s*$",
                        (IOS.parent / "pubspec.yaml").read_text(encoding="utf-8"), re.MULTILINE)
    require(version is not None, "Cannot read Flutter release version")
    require(info.get("CFBundleShortVersionString") == version[1] and
            info.get("CFBundleVersion") == version[2], "Artifact version differs from pubspec")
    expected_platform = "iPhoneSimulator" if simulator else "iPhoneOS"
    require(info.get("CFBundleSupportedPlatforms") == [expected_platform],
            "Not an iOS " + ("simulator" if simulator else "device") + " artifact")
    executable = app / info["CFBundleExecutable"]
    with executable.open("rb") as stream:
        header = stream.read(4096)
    if header[:4] == b"\xcf\xfa\xed\xfe":
        architectures = {struct.unpack("<I", header[4:8])[0]}
    elif header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        count = struct.unpack(">I", header[4:8])[0]
        stride = 32 if header[:4] == b"\xca\xfe\xba\xbf" else 20
        architectures = {struct.unpack(">I", header[8 + index * stride:12 + index * stride])[0]
                         for index in range(count)}
    else:
        raise ValueError("App executable is not a supported Mach-O binary")
    expected_cpu = 0x01000007 if simulator else 0x0100000C
    require(architectures == {expected_cpu},
            "Simulator executable must contain only x86_64" if simulator else
            "Device executable must contain only arm64")
    require((app / "Assets.car").is_file(), "Missing compiled asset catalog")
    require(bool(info.get("CFBundleIcons", {}).get("CFBundlePrimaryIcon")),
            "Missing compiled primary app icon")
    print(f"PASS: {info['CFBundleIdentifier']} {version[1]} ({version[2]}), {expected_platform}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, help="Optional built Runner.app to inspect")
    parser.add_argument("--simulator", action="store_true", help="Inspect an Intel Simulator app")
    args = parser.parse_args()
    try:
        check_source()
        if args.app:
            check_app(args.app.resolve(), simulator=args.simulator)
    except (ValueError, OSError, KeyError, struct.error) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
