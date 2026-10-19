#!/usr/bin/env python3
"""Boot real iOS Simulators, verify the first Flutter frame and capture native UI.

No account, developer certificate, or third-party test service is used.
Safari is only a background/foreground target, not the source of UI screenshots.
Run after ./flutter/build_ios.sh --simulator on an Intel Mac.
"""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


BUNDLE = "top.zperme.linkremote"
FRAME_MARKER = "LINKREMOTE_SMOKE_FIRST_FRAME"


def simctl(*arguments, timeout=120, check=True):
    result = subprocess.run(["xcrun", "simctl", *arguments], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("simctl " + " ".join(arguments) + ": " + result.stdout)
    return result.stdout


def choose_devices(inventory, sdk_version=None):
    runtimes = sorted(inventory["devices"],
                      key=lambda key: tuple(int(number) for number in re.findall(r"\d+", key)),
                      reverse=True)
    for runtime in runtimes:
        if ".iOS-" not in runtime:
            continue
        if sdk_version and not runtime.endswith(".iOS-" + sdk_version.replace(".", "-")):
            continue
        available = [device for device in inventory["devices"][runtime]
                     if device.get("isAvailable")]
        selected = [next((device for device in available if device["name"].startswith(family)),
                         None) for family in ("iPhone", "iPad")]
        if all(selected):
            return runtime, selected
    raise RuntimeError("No installed iOS runtime matching the selected SDK has both an available iPhone and iPad")


def wait_for_frame(log_path, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        if FRAME_MARKER in text:
            return
        if process.poll() is not None:
            raise RuntimeError("Simulator log stream ended before the first Flutter frame")
        time.sleep(0.5)
    raise RuntimeError("No first Flutter frame within 90 seconds; inspect the native log")


def screenshot(udid, target):
    simctl("io", udid, "screenshot", "--type=png", str(target))
    with target.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Simulator did not produce a PNG screenshot")


def smoke_device(device, app, output):
    udid = device["udid"]
    family = "iphone" if device["name"].startswith("iPhone") else "ipad"
    log_path = output / (family + ".log")
    result = {"model": device["name"], "udid": udid, "screenshots": [], "passed": False,
              "url_delivery_verified": False, "url_delivery_status": "not_attempted"}
    log_process = None
    was_booted = device["state"] == "Booted"

    def stage(name):
        result["last_stage"] = name
        print(f"[ios-smoke] {device['name']}: {name}", flush=True)

    def cleanup(name, action):
        try:
            action()
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            message = name + ": " + str(error)
            result.setdefault("cleanup_errors", []).append(message)
            if "error" not in result:
                result["error"] = "Cleanup failed: " + message
                result["passed"] = False
            print(f"[ios-smoke] {device['name']}: cleanup warning: {message}", flush=True)

    try:
        stage("boot and wait for runtime")
        if not was_booted:
            simctl("boot", udid)
        simctl("bootstatus", udid, "-b", timeout=180)
        simctl("ui", udid, "appearance", "light")
        stage("install app")
        simctl("install", udid, str(app))
        with log_path.open("w", encoding="utf-8") as log_stream:
            log_process = subprocess.Popen(
                ["xcrun", "simctl", "spawn", udid, "log", "stream", "--style", "compact",
                 "--level", "debug", "--predicate", 'process == "Runner"'],
                stdout=log_stream, stderr=subprocess.STDOUT)
            time.sleep(1)
            stage("launch and wait for first Flutter frame")
            launched = simctl("launch", "--terminate-running-process", udid, BUNDLE)
            result["launch"] = launched.strip()
            wait_for_frame(log_path, log_process)
            result["first_flutter_frame"] = True
            time.sleep(2)
            # Opening a custom URL may leave a system confirmation alert on
            # screen. Keep it last so it cannot obscure the normal UI checks.
            for state in ("home-light", "home-dark", "resumed", "warm-link"):
                stage(state)
                if state == "warm-link":
                    simctl("openurl", udid, "linkremote://")
                    result["url_open_requested"] = True
                    result["url_delivery_status"] = "system_confirmation_requires_manual_verification"
                elif state == "resumed":
                    simctl("ui", udid, "appearance", "light")
                    simctl("launch", udid, "com.apple.mobilesafari")
                    time.sleep(1)
                    result["resume_launch"] = simctl("launch", udid, BUNDLE).strip()
                elif state == "home-dark":
                    simctl("ui", udid, "appearance", "dark")
                time.sleep(2)
                target = output / (family + "-" + state + ".png")
                screenshot(udid, target)
                result["screenshots"].append(target.name)
            stage("verify original process and application log")
            pid = re.search(r":\s*(\d+)\s*$", launched)
            if pid is None:
                raise RuntimeError("Cannot determine the launched app PID")
            jobs = simctl("spawn", udid, "launchctl", "list")
            (output / (family + "-processes.log")).write_text(jobs, encoding="utf-8")
            if re.search(r"^\s*" + pid[1] + r"\s", jobs, re.MULTILINE) is None:
                raise RuntimeError("App exited or restarted during the UI smoke sequence")
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"Unhandled Exception|LateInitializationError|initialize failed:", text):
                raise RuntimeError("Startup or lifecycle error found in the Flutter/native log")
            result["passed"] = True
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        result["failed_stage"] = result.get("last_stage")
        print(f"[ios-smoke] {device['name']}: failed: {error}", flush=True)
        try:
            target = output / (family + "-failure.png")
            screenshot(udid, target)
            result["failure_screenshot"] = target.name
        except (RuntimeError, OSError, subprocess.SubprocessError) as capture_error:
            result["failure_screenshot_error"] = str(capture_error)
    finally:
        if log_process is not None:
            def stop_log():
                log_process.terminate()
                try:
                    log_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log_process.kill()
                    log_process.wait(timeout=5)
            cleanup("stop log stream", stop_log)
        cleanup("restore light appearance", lambda: simctl("ui", udid, "appearance", "light", check=False))
        if not was_booted:
            cleanup("shutdown", lambda: simctl("shutdown", udid, check=False))
        print(f"[ios-smoke] {device['name']}: finished, passed={result['passed']}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory already exists; choose a new path to preserve prior evidence")
    args.output.mkdir(parents=True)
    try:
        sdk_version = subprocess.check_output(
            ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], text=True).strip()
        runtime, devices = choose_devices(
            json.loads(simctl("list", "devices", "available", "--json")), sdk_version=sdk_version)
        results = [smoke_device(device, args.app.resolve(), args.output.resolve()) for device in devices]
        report = {"runtime": runtime, "devices": results,
                  "scope": "First native Flutter frame and original process survival, with light/dark and "
                           "foreground-return screenshots for manual inspection before requesting a URL. "
                           "URL delivery is not verified; system confirmation requires manual verification. "
                           "Does not validate live remote input, camera, cellular or device performance."}
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not all(result["passed"] for result in results):
            parser.exit(1, "iOS Simulator smoke failed; inspect report, logs and native screenshots.\n")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"iOS Simulator smoke failed: {error}\n")


if __name__ == "__main__":
    main()
