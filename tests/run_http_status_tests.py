#!/usr/bin/env python3
"""Test the production namespaced HTTP-result cache without native dependencies.

Source-only checks verify production wiring, not Rust execution. Full mode
compiles the production file directly with rustc and runs its actual unit tests.
Neither mode tests a live HTTP server, Flutter engine lifetime or cache TTL.
"""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODULE = "src/ui_interface/http_status.rs"


def check_sources(root=ROOT):
    source = (root / "src/ui_interface.rs").read_text(encoding="utf-8")
    if not re.search(r"(?m)^mod http_status;$", source):
        raise ValueError("UI HTTP status must declare its production child module")
    wrapper = re.search(
        r"pub fn get_async_http_status\(url: String\) -> Option<String>\s*\{([^}]+)\}",
        source,
    )
    expected = "letmutstatuses=ASYNC_HTTP_STATUS.lock().unwrap();http_status::read_status(&mutstatuses,&url)"
    if not wrapper or re.sub(r"\s+", "", wrapper[1]) != expected:
        raise ValueError("UI HTTP status must delegate under its existing mutex to the production consume helper")
    module = root / MODULE
    if not module.is_file():
        raise ValueError("Missing production HTTP status helper")
    body = module.read_text(encoding="utf-8")
    if "pub(crate) fn read_status(" not in body:
        raise ValueError("Production HTTP status helper must expose read_status")
    production = body.split("#[cfg(test)]", 1)[0]
    if re.search(r"\b(?:tokio|reqwest|hbb_common|crate::|super::)", production):
        raise ValueError("HTTP status helper must remain self-contained for direct host Rust tests")
    if len(re.findall(r"#\[test\]\s*fn\s+\w+\s*\(", body)) != 9:
        raise ValueError("Production HTTP status helper must retain its nine real Rust regression tests")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--rustc", default="rustc")
    args = parser.parse_args(argv)
    try:
        check_sources()
        print("PASS: production namespaced HTTP-result cache wiring", flush=True)
        if args.source_only:
            print("SOURCE ONLY: Rust HTTP-result cache unit tests were not run")
            return 0
        rustc = shutil.which(args.rustc)
        if not rustc:
            print("Rust compiler unavailable; no Rust HTTP cache tests ran. Use --source-only for limited checks.", file=sys.stderr)
            return 2
        subprocess.run([rustc, "--version"], check=True)
        with tempfile.TemporaryDirectory(prefix="linkremote-http-status-") as directory:
            output = Path(directory) / ("http_status_tests.exe" if sys.platform == "win32" else "http_status_tests")
            subprocess.run([rustc, "--edition=2021", "--test", str(ROOT / MODULE), "-o", str(output)], check=True)
            subprocess.run([str(output), "--nocapture"], check=True)
        print("PASS: production HTTP-result cache Rust tests; no live HTTP, engine teardown or TTL tested")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
