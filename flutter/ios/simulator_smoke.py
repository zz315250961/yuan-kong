#!/usr/bin/env python3
"""Boot real iOS Simulators, verify the first Flutter frame and capture native UI.

No account, developer certificate, browser, or third-party test service is used.
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
    result = {"model": device["name"], "udid": udid, "screenshots": [], "passed": False}
    log_process = None
    was_booted = device["state"] == "Booted"
    try:
        if not was_booted:
            simctl("boot", udid)
        simctl("bootstatus", udid, "-b", timeout=180)
        simctl("ui", udid, "appearance", "light")
        simctl("install", udid, str(app))
        with log_path.open("w", encoding="utf-8") as log_stream:
            log_process = subprocess.Popen(
                ["xcrun", "simctl", "spawn", udid, "log", "stream", "--style", "compact",
                 "--level", "debug", "--predicate", 'process == "Runner"'],
                stdout=log_stream, stderr=subprocess.STDOUT)
            time.sleep(1)
            launched = simctl("launch", "--terminate-running-process", udid, BUNDLE)
            result["launch"] = launched.strip()
            wait_for_frame(log_path, log_process)
            time.sleep(2)
            for state in ("home-light", "warm-link", "resumed", "home-dark"):
                if state == "warm-link":
                    simctl("openurl", udid, "linkremote://")
                elif state == "resumed":
                    simctl("launch", udid, "com.apple.mobilesafari")
                    time.sleep(1)
                    result["resume_launch"] = simctl("launch", udid, BUNDLE).strip()
                elif state == "home-dark":
                    simctl("ui", udid, "appearance", "dark")
                time.sleep(2)
                target = output / (family + "-" + state + ".png")
                screenshot(udid, target)
                result["screenshots"].append(target.name)
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
            result["first_flutter_frame"] = True
            result["passed"] = True
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    finally:
        if log_process is not None:
            log_process.terminate()
            try:
                log_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log_process.kill()
                log_process.wait()
        simctl("ui", udid, "appearance", "light", check=False)
        if not was_booted:
            simctl("shutdown", udid, check=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        sdk_version = subprocess.check_output(
            ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], text=True).strip()
        runtime, devices = choose_devices(
            json.loads(simctl("list", "devices", "available", "--json")), sdk_version=sdk_version)
        results = [smoke_device(device, args.app.resolve(), args.output.resolve()) for device in devices]
        report = {"runtime": runtime, "devices": results,
                  "scope": "First native Flutter frame, warm URL, foreground return and light/dark UI. "
                           "Does not validate live remote input, camera, cellular or device performance."}
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not all(result["passed"] for result in results):
            parser.exit(1, "iOS Simulator smoke failed; inspect report, logs and native screenshots.\n")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"iOS Simulator smoke failed: {error}\n")


if __name__ == "__main__":
    main()
