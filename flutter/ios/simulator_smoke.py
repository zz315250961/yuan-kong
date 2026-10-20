#!/usr/bin/env python3
"""Boot real iOS Simulators, verify the first Flutter frame and capture native UI.

No account, developer certificate, or third-party test service is used.
Safari is only a background/foreground target, not the source of UI screenshots.
Run after ./flutter/build_ios.sh --simulator on an Intel Mac.
"""

import argparse
import contextvars
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from simulator_diagnostics import SimulatorDiagnostics
from verify_simulator_image import ImageError, inspect_home_image, read_png


BUNDLE = "top.zperme.linkremote"
FRAME_MARKER = "LINKREMOTE_SMOKE_FIRST_FRAME"
_DIAGNOSTICS = contextvars.ContextVar("ios_smoke_diagnostics", default=None)


def observe(method, *arguments):
    """Diagnostic failures never replace or turn green a smoke result."""
    collector = _DIAGNOSTICS.get()
    if collector is None:
        return None
    try:
        return getattr(collector, method)(*arguments)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        collector.error(method, error)
        return None


def simctl(*arguments, timeout=120, check=True):
    started = time.monotonic()
    identifier = observe("command_started", arguments, timeout)
    try:
        result = subprocess.run(["xcrun", "simctl", *arguments], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        captured = error.output or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        observe("command_finished", identifier, elapsed, None, "timeout")
        raise RuntimeError("simctl " + " ".join(arguments) +
                           f" timed out after {timeout}s (elapsed {elapsed:.1f}s); "
                           "captured output tail:\n" + captured[-8000:]) from error
    except OSError as error:
        observe("command_finished", identifier, time.monotonic() - started, None, str(error))
        raise
    observe("command_finished", identifier, time.monotonic() - started, result.returncode)
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


def check_home_screenshot(target):
    """A negative-only corruption gate; no finding never grants visual approval."""
    try:
        report = inspect_home_image(read_png(target))
    except (ImageError, OSError) as error:
        raise RuntimeError("Cannot inspect home screenshot: " + str(error)) from error
    if report["known_corruption_detected"]:
        raise RuntimeError("Known home-frame corruption: " + json.dumps(report, sort_keys=True))
    return report


def smoke_device(device, app, output, renderer="default", stress_cycles=0):
    if renderer not in ("default", "skia"):
        raise ValueError("renderer must be default or skia")
    if type(stress_cycles) is not int or not 0 <= stress_cycles <= 6:
        raise ValueError("stress_cycles must be an integer from 0 to 6")
    engine_arguments = ("--enable-impeller=false",) if renderer == "skia" else ()
    udid = device["udid"]
    family = "iphone" if device["name"].startswith("iPhone") else "ipad"
    log_path = output / (family + ".log")
    result = {"model": device["name"], "udid": udid, "screenshots": [], "native_screenshots": [],
              "passed": False,
              "requested_renderer": renderer, "renderer_backend_verified": False,
              "stress_cycles_requested": stress_cycles, "stress_cycles_completed": 0,
              "image_checks": [],
              "url_delivery_verified": False, "url_delivery_status": "not_attempted"}
    log_process = None
    was_booted = device["state"] == "Booted"
    stage_started = None

    def finish_stage():
        nonlocal stage_started
        if stage_started is not None:
            elapsed = time.monotonic() - stage_started
            result.setdefault("stage_timings", []).append({
                "stage": result["last_stage"], "elapsed_seconds": round(elapsed, 3)})
            print(f"[ios-smoke] {device['name']}: completed {result['last_stage']} "
                  f"in {elapsed:.2f}s", flush=True)
            stage_started = None

    def stage(name):
        nonlocal stage_started
        finish_stage()
        result["last_stage"] = name
        stage_started = time.monotonic()
        print(f"[ios-smoke] {device['name']}: {name}", flush=True)

    def native_capture(state):
        target = output / (family + "-native-" + state + ".png")
        screenshot(udid, target)
        result["native_screenshots"].append(target.name)

    def check_capture(target):
        try:
            report = check_home_screenshot(target)
        except RuntimeError as error:
            result["image_checks"].append({"image": target.name, "error": str(error)})
            raise
        result["image_checks"].append(dict(report, image=target.name))

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
        observe("start_device", udid, family)
        stage("boot selected simulator")
        if not was_booted:
            simctl("boot", udid)
        stage("wait for runtime boot completion")
        simctl("bootstatus", udid, "-b", timeout=180)
        observe("start_logs")
        stage("set initial light appearance")
        simctl("ui", udid, "appearance", "light")
        # Safari's first cold launch can take longer than a Flutter Debug app's
        # iOS background task allowance. Finish that work before the tested App
        # ever starts; retain the strict original-App PID check after returning.
        stage("prewarm native Safari before target app startup")
        browser_started = time.monotonic()
        result["safari_prewarm_launch"] = simctl("launch", udid, "com.apple.mobilesafari").strip()
        result["safari_prewarm_launch_seconds"] = round(time.monotonic() - browser_started, 3)
        time.sleep(2)
        native_capture("safari-prewarmed")
        stage("native Settings light control")
        result["native_control_bundle"] = "com.apple.Preferences"
        result["native_control_launch"] = simctl("launch", udid, "com.apple.Preferences").strip()
        simctl("ui", udid, "appearance", "light")
        time.sleep(2)
        native_capture("settings-light")
        stage("native Settings dark control")
        simctl("ui", udid, "appearance", "dark")
        time.sleep(2)
        native_capture("settings-dark")
        stage("restore light appearance before target app startup")
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
            launched = simctl("launch", "--terminate-running-process", udid, BUNDLE, *engine_arguments)
            result["launch"] = launched.strip()
            observe("launched", launched)
            original_pid = re.search(r":\s*(\d+)\s*$", launched)
            if original_pid is None:
                raise RuntimeError("Cannot determine the launched app PID")
            wait_for_frame(log_path, log_process)
            result["first_flutter_frame"] = True
            time.sleep(2)
            # Opening a custom URL may leave a system confirmation alert on
            # screen. Keep it last so it cannot obscure the normal UI checks.
            sequence = [(state, state) for state in ("home-light", "home-dark", "resumed")]
            sequence.extend((f"stress-{cycle:02d}-{state}", state)
                            for cycle in range(1, stress_cycles + 1)
                            for state in ("home-light", "home-dark", "resumed"))
            sequence.append(("warm-link", "warm-link"))
            for checkpoint, state in sequence:
                stage(checkpoint)
                if state == "warm-link":
                    simctl("openurl", udid, "linkremote://")
                    result["url_open_requested"] = True
                    result["url_delivery_status"] = "system_confirmation_requires_manual_verification"
                elif state == "resumed":
                    simctl("ui", udid, "appearance", "light")
                    result["background_launch"] = simctl("launch", udid, "com.apple.mobilesafari").strip()
                    time.sleep(1)
                    observe("begin_resume")
                    result["resume_launch"] = simctl("launch", udid, BUNDLE, *engine_arguments).strip()
                    observe("end_resume")
                    resumed_pid = re.search(r":\s*(\d+)\s*$", result["resume_launch"])
                    if resumed_pid is None or resumed_pid[1] != original_pid[1]:
                        raise RuntimeError("App exited or restarted during foreground return")
                elif state == "home-dark":
                    simctl("ui", udid, "appearance", "dark")
                elif state == "home-light" and checkpoint != "home-light":
                    simctl("ui", udid, "appearance", "light")
                time.sleep(2)
                target = output / (family + "-" + checkpoint + ".png")
                screenshot(udid, target)
                result["screenshots"].append(target.name)
                check_capture(target)
                if state == "resumed" and checkpoint.startswith("stress-"):
                    result["stress_cycles_completed"] += 1
                if checkpoint == "home-light" and _DIAGNOSTICS.get() is not None:
                    # Extra raw images are diagnostic evidence only, never a
                    # visual-pass assertion or a replacement for the original.
                    for index in (1, 2):
                        time.sleep(2)
                        extra = output / (family + f"-diagnostic-light-{index}.png")
                        try:
                            screenshot(udid, extra)
                            result.setdefault("diagnostic_screenshots", []).append(extra.name)
                        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                            observe("error", "extra light screenshot", error)
                        else:
                            check_capture(extra)
            stage("verify original process and application log")
            pid = re.search(r":\s*(\d+)\s*$", launched)
            if pid is None:
                raise RuntimeError("Cannot determine the launched app PID")
            jobs = simctl("spawn", udid, "launchctl", "list")
            (output / (family + "-processes.log")).write_text(jobs, encoding="utf-8")
            if re.search(r"^\s*" + pid[1] + r"\s", jobs, re.MULTILINE) is None:
                raise RuntimeError("App exited or restarted during the UI smoke sequence")
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"Unhandled Exception|LateInitializationError|initialize failed:|"
                         r"A RenderFlex overflowed|thread '[^']*' panicked at|EXC_BAD_ACCESS|SIGABRT", text):
                raise RuntimeError("Startup or lifecycle error found in the Flutter/native log")
            result["passed"] = True
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        finish_stage()
        result["error"] = str(error)
        result["failed_stage"] = result.get("last_stage")
        print(f"[ios-smoke] {device['name']}: failed: {error}", flush=True)
        try:
            target = output / (family + "-failure.png")
            screenshot(udid, target)
            result["failure_screenshot"] = target.name
        except (RuntimeError, OSError, subprocess.SubprocessError) as capture_error:
            result["failure_screenshot_error"] = str(capture_error)
        observe("failure")
    finally:
        finish_stage()
        observe("stop_device")
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


def smoke_fresh_device(device, runtime, inventory_udids, app, output,
                       renderer="default", stress_cycles=0):
    """Create a new data container, then delete only the UUID returned by this create."""
    result = {"model": device["name"], "screenshots": [], "passed": False,
              "inventory_udid": device["udid"], "qa_created_device": False,
              "url_delivery_verified": False, "url_delivery_status": "not_attempted"}
    owned_udid = None
    stage = "create isolated simulator"
    try:
        identifier = device.get("deviceTypeIdentifier", "")
        if not identifier.startswith("com.apple.CoreSimulator.SimDeviceType."):
            raise RuntimeError("Selected inventory device has no usable device type identifier")
        print(f"[ios-smoke] {device['name']}: create an isolated data container", flush=True)
        created = simctl("create", "LinkRemote QA " + str(uuid.uuid4()), identifier, runtime).strip()
        if re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", created) is None:
            raise RuntimeError("simctl create did not return one unambiguous owned UUID")
        if created.lower() in {existing.lower() for existing in inventory_udids}:
            raise RuntimeError("simctl create returned a pre-existing inventory UUID; refusing ownership")
        # Assign ownership only after validating the create response. Never
        # delete the selected inventory device, another booted device, or all.
        owned_udid = created
        selected = dict(device, udid=owned_udid, state="Shutdown")
        result["qa_created_device"] = True
        result["udid"] = owned_udid
        stage = "run isolated simulator"
        result.update(smoke_device(selected, app, output, renderer=renderer,
                                   stress_cycles=stress_cycles))
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        result["failed_stage"] = stage
        print(f"[ios-smoke] {device['name']}: isolated-device failure: {error}", flush=True)
    finally:
        if owned_udid is not None:
            try:
                simctl("delete", owned_udid)
                result["qa_device_deleted"] = True
            except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                result.setdefault("cleanup_errors", []).append("delete owned simulator: " + str(error))
                result.setdefault("error", "Owned Simulator deletion failed: " + str(error))
                result["passed"] = False
                result["qa_device_deleted"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--family", choices=("both", "iphone", "ipad"), default="both",
                        help="Run one family on a clean runner, or both sequentially")
    parser.add_argument("--fresh-devices", action="store_true",
                        help="Create and clean up only QA-owned devices of the same runtime and types")
    parser.add_argument("--diagnostics", action="store_true",
                        help="Collect bounded read-only OS/host evidence; does not change smoke gates")
    parser.add_argument("--renderer", choices=("default", "skia"), default="default",
                        help="Request default engine or disable Impeller for a diagnostic A/B; not backend proof")
    parser.add_argument("--stress-cycles", type=int, choices=range(7), default=0,
                        help="Additional bounded light/dark/foreground-return cycles before the URL prompt (0..6)")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory already exists; choose a new path to preserve prior evidence")
    args.output.mkdir(parents=True)
    collector = None
    token = None
    diagnostic_setup_errors = []
    try:
        if args.diagnostics:
            try:
                collector = SimulatorDiagnostics(args.output)
                token = _DIAGNOSTICS.set(collector)
            except OSError as error:
                diagnostic_setup_errors.append(str(error))
        sdk_version = subprocess.check_output(
            ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], text=True).strip()
        inventory = json.loads(simctl("list", "devices", "available", "--json"))
        runtime, devices = choose_devices(inventory, sdk_version=sdk_version)
        if args.family != "both":
            prefix = "iPhone" if args.family == "iphone" else "iPad"
            devices = [device for device in devices if device["name"].startswith(prefix)]
        inventory_udids = {device["udid"] for group in inventory["devices"].values()
                           for device in group if device.get("udid")}
        results = [(smoke_fresh_device(device, runtime, inventory_udids,
                                      args.app.resolve(), args.output.resolve(),
                                      renderer=args.renderer, stress_cycles=args.stress_cycles)
                    if args.fresh_devices else
                    smoke_device(device, args.app.resolve(), args.output.resolve(),
                                 renderer=args.renderer, stress_cycles=args.stress_cycles)) for device in devices]
        report = {"runtime": runtime, "devices": results,
                  "requested_renderer": args.renderer, "renderer_backend_verified": False,
                  "stress_cycles_requested": args.stress_cycles,
                  "device_source": "qa_created" if args.fresh_devices else "runner_inventory",
                  "scope": "Native Safari/Settings control screenshots for manual comparison, followed by "
                           "first native Flutter frame and original process survival, with light/dark and "
                           "foreground-return screenshots with a negative-only known black-bar/full-black "
                           "corruption gate; no finding is not visual approval. Manual inspection remains "
                           "required, and a renderer flag is a request, not verified backend selection. "
                           "Optional stress cycles complete before requesting a URL. "
                           "URL delivery is not verified; system confirmation requires manual verification. "
                           "Does not validate live remote input, camera, cellular or device performance."}
        if collector is not None:
            report["diagnostics"] = {"enabled": True, "directory": "diagnostics",
                                     "errors": collector.errors,
                                     "final_errors_file": "diagnostics/errors.json",
                                     "visual_verdict": "manual inspection required"}
        elif diagnostic_setup_errors:
            report["diagnostics"] = {"enabled": False, "errors": diagnostic_setup_errors}
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not all(result["passed"] for result in results):
            parser.exit(1, "iOS Simulator smoke failed; inspect report, logs and native screenshots.\n")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"iOS Simulator smoke failed: {error}\n")
    finally:
        if collector is not None:
            observe("close")
        if token is not None:
            _DIAGNOSTICS.reset(token)


if __name__ == "__main__":
    main()
