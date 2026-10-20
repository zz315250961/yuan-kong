"""Opt-in, bounded, read-only evidence for an existing Simulator smoke run.

Never changes App arguments, appearance, device state, or the smoke verdict.
Only processes started by this collector may be terminated during cleanup.
"""

import itertools
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class SimulatorDiagnostics:
    def __init__(self, output):
        self.output = Path(output) / "diagnostics"
        self.output.mkdir()
        self.errors = []
        self._lock = threading.Lock()
        self._processes = set()
        self._streams = []
        self._threads = []
        self._stream_timers = []
        self._stopped = threading.Event()
        self._resume_cancel = None
        self._resource_stop = None
        self._serial = 0
        self._usable = True
        self._device_started_wall = time.time()
        self.udid = None
        self.family = None
        self.app_pid = None

    def error(self, action, error):
        with self._lock:
            self.errors.append({"action": action, "error": str(error)})

    def _event(self, item):
        with self._lock:
            with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(time_utc=utc_now(), **item)) + "\n")

    def command_started(self, arguments, timeout):
        with self._lock:
            self._serial += 1
            identifier = self._serial
        self._event({"event": "simctl_start", "id": identifier,
                     "arguments": list(arguments), "timeout_seconds": timeout})
        return identifier

    def command_finished(self, identifier, elapsed, exit_code=None, error=None):
        self._event({"event": "simctl_end", "id": identifier,
                     "elapsed_seconds": round(elapsed, 3), "exit_code": exit_code,
                     "error": error})

    def _start(self, arguments, stream):
        # Serialize starts with close: a collector that is closing cannot spawn
        # another process after the owned-process cleanup snapshot was taken.
        with self._lock:
            if self._stopped.is_set() or not self._usable:
                return None
            process = subprocess.Popen(arguments, stdout=stream, stderr=subprocess.STDOUT,
                                       text=True)
            self._processes.add(process)
            return process

    def _run(self, arguments, timeout=5):
        self._event({"event": "diagnostic_command_start", "arguments": arguments,
                     "timeout_seconds": timeout})
        process = self._start(arguments, subprocess.PIPE)
        if process is None:
            return ""
        started = time.monotonic()
        try:
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=2)
                self.error("diagnostic command timeout", " ".join(arguments))
            self._event({"event": "diagnostic_command_end", "arguments": arguments,
                         "elapsed_seconds": round(time.monotonic() - started, 3),
                         "exit_code": process.returncode})
            if process.returncode:
                self.error("diagnostic command exit", " ".join(arguments) +
                           f": {process.returncode}")
            return output or ""
        except (OSError, subprocess.SubprocessError) as error:
            self.error("diagnostic command", error)
            self._event({"event": "diagnostic_command_end", "arguments": arguments,
                         "elapsed_seconds": round(time.monotonic() - started, 3),
                         "exit_code": process.poll(), "error": str(error)})
            return ""
        finally:
            # A failed second communicate/kill must not lose the still-running
            # owned probe: stop_device gets another bounded cleanup attempt.
            if process.poll() is not None:
                with self._lock:
                    self._processes.discard(process)

    def _save(self, name, text):
        with self._lock:
            (self.output / name).write_text(text, encoding="utf-8")

    def start_device(self, udid, family):
        if not self._usable:
            self.error("start device diagnostics", "prior owned collector did not stop cleanly")
            return
        self._stopped.clear()
        self._device_started_wall = time.time()
        self.udid, self.family = udid, family
        self.app_pid = None
        self._save(family + "-host-info.txt", self._run(
            ["sysctl", "hw.memsize", "hw.logicalcpu", "hw.model"]))
        self.snapshot("before-boot")
        self._resource_stop = threading.Event()
        resource_stop = self._resource_stop

        def resources():
            index = 0
            while not resource_stop.wait(15) and not self._stopped.is_set():
                index += 1
                self.snapshot(f"resources-{index:03d}")

        thread = threading.Thread(target=resources, name="ios-smoke-resource-diagnostic", daemon=True)
        self._threads.append(thread)
        thread.start()

    def start_logs(self):
        # A simctl-spawn log stream cannot start while the device is Shutdown.
        # Start it only after the unchanged bootstatus gate has completed.
        if not self._usable:
            return
        predicate = ('process == "Runner" OR process == "SpringBoard" OR '
                     'process == "backboardd" OR process == "runningboardd"')
        commands = [
            (self.family + "-system.log", ["xcrun", "simctl", "spawn", self.udid, "log", "stream",
             "--style", "compact", "--level", "info", "--predicate", predicate]),
            (self.family + "-host-coresimulator.log", ["log", "stream", "--style", "compact",
             "--level", "info", "--predicate", 'process == "CoreSimulatorService" OR '
             'process == "com.apple.CoreSimulator.CoreSimulatorService"']),
        ]
        for name, arguments in commands:
            stream = (self.output / name).open("w", encoding="utf-8")
            self._streams.append(stream)
            process = self._start(arguments, stream)
            if process is not None:
                self._event({"event": "diagnostic_stream_start", "arguments": arguments,
                             "pid": process.pid, "timeout_seconds": 1200})
                timer = threading.Timer(1200, self._stop_stream, args=(process,))
                timer.daemon = True
                self._stream_timers.append(timer)
                timer.start()

    def _stop_stream(self, process):
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            self._event({"event": "diagnostic_stream_end", "pid": process.pid,
                         "exit_code": process.returncode})
        except (OSError, subprocess.SubprocessError) as error:
            self.error("stop owned diagnostic process", error)

    def launched(self, launch):
        if not self._usable:
            return
        match = re.search(r":\s*(\d+)\s*$", launch)
        if match:
            self.app_pid = int(match[1])

    def snapshot(self, label, sample=False):
        """Each subprocess is bounded; there is no terminate/relaunch of a target."""
        if not self._usable:
            return
        prefix = self.family + "-" + label
        try:
            process_text = self._run(["ps", "-axo", "pid,ppid,stat,pcpu,rss,comm"])
            # Do not persist arbitrary other apps' names or command arguments.
            selected = [line for line in process_text.splitlines() if
                        re.search(r"Runner|SpringBoard|backboardd|runningboardd|CoreSimulator", line)]
            self._save(prefix + "-processes.txt", "\n".join(selected) + "\n")
            self._save(prefix + "-resources.txt", "time_utc=" + utc_now() + "\n" +
                       self._run(["vm_stat"]) + self._run(["uptime"]))
            if sample:
                # This read-only query may itself fail when Simulator's control
                # plane is wedged. Keep the original App PID for host sampling.
                jobs = self._run(["xcrun", "simctl", "spawn", self.udid,
                                  "launchctl", "list"])
                self._save(prefix + "-launchctl.txt", jobs)
                targets = [self.app_pid] if self.app_pid else []
                for line in jobs.splitlines():
                    if "SpringBoard" in line:
                        match = re.match(r"\s*(\d+)\s", line)
                        if match:
                            targets.append(int(match[1]))
                for line in selected:
                    if "CoreSimulatorService" in line:
                        match = re.match(r"\s*(\d+)\s", line)
                        if match:
                            targets.append(int(match[1]))
                # Bound the number of samples even if the host reports many
                # services. No other booted device is targeted or modified.
                for pid in list(dict.fromkeys(targets))[:3]:
                    target = self.output / (prefix + f"-sample-{pid}.txt")
                    command_output = self._run(["sample", str(pid), "3", "-file", str(target)],
                                               timeout=5)
                    self._save(prefix + f"-sample-command-{pid}.log", command_output)
                    if not target.is_file() or not target.stat().st_size:
                        self.error("sample output", f"no nonempty stack file for PID {pid}")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self.error("snapshot " + label, error)

    def begin_resume(self):
        if not self._usable:
            return
        self._resume_cancel = threading.Event()
        cancel = self._resume_cancel

        def probe():
            if not cancel.wait(25) and not self._stopped.is_set():
                self.snapshot("resume-wait-25s", sample=True)

        thread = threading.Thread(target=probe, name="ios-smoke-resume-diagnostic", daemon=True)
        self._threads.append(thread)
        thread.start()

    def end_resume(self):
        if self._resume_cancel is not None:
            self._resume_cancel.set()

    def failure(self):
        if not self._usable:
            return
        self.end_resume()
        self.snapshot("failure", sample=True)
        # Read only reports written since this device run for the original App
        # PID. Bound directory entries, files read, and copied bytes. Looking at
        # a matching old PID alone would risk importing an unrelated old crash.
        try:
            directories = [Path.home() / "Library/Logs/DiagnosticReports"]
            if re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", self.udid or ""):
                directories.append(Path.home() / "Library/Developer/CoreSimulator/Devices" /
                                   self.udid / "data/Library/Logs/CrashReporter")
            copied = 0
            for directory in directories:
                candidates = list(itertools.islice(itertools.chain(
                    directory.glob("Runner*"), directory.glob("JetsamEvent*")), 200))
                candidates = [report for report in candidates if report.is_file() and
                              report.stat().st_mtime >= self._device_started_wall]
                for report in sorted(candidates, key=lambda item: item.stat().st_mtime,
                                     reverse=True)[:20]:
                    try:
                        report.resolve().relative_to(directory.resolve())
                    except ValueError:
                        self.error("read diagnostic report", "refusing report symlink outside report directory")
                        continue
                    with report.open("r", encoding="utf-8", errors="replace") as stream:
                        text = stream.read(256 * 1024)
                    if self.app_pid and re.search(r'(?:"pid"\s*:\s*|\[)' +
                                                  str(self.app_pid) + r'(?:\s*[,}]|\])', text):
                        self._save(self.family + "-failure-report-" + str(copied) + ".txt", text)
                        copied += 1
                    if copied >= 3:
                        break
                if copied >= 3:
                    break
        except OSError as error:
            self.error("read original App diagnostic reports", error)

    def stop_device(self):
        self.end_resume()
        if self._resource_stop is not None:
            self._resource_stop.set()
        self._stopped.set()
        for timer in self._stream_timers:
            timer.cancel()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            self._stop_stream(process)
        remaining_threads = []
        for thread in self._threads:
            thread.join(timeout=3)
            if thread.is_alive():
                self.error("stop diagnostic worker", "worker did not finish within 3 seconds")
                self._usable = False
                remaining_threads.append(thread)
        for stream in self._streams:
            stream.close()
        self._streams = []
        self._threads = remaining_threads
        self._stream_timers = []
        with self._lock:
            remaining = {process for process in self._processes if process.poll() is None}
            self._processes = remaining
        if remaining:
            self.error("stop owned diagnostic process", "owned probe still alive after bounded cleanup")
            self._usable = False

    def close(self):
        self.stop_device()
        self._save("errors.json", json.dumps(self.errors, indent=2))
