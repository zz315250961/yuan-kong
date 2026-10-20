"""Check the identity in stdout from a successful apksigner verify command.

The caller must independently require apksigner's exit status to be zero (for
example with Bash set -euo pipefail). This parser is not a signature verifier.
Only the public certificate fingerprint is printed; input log contents are not.
"""

import argparse
from pathlib import Path
import re


_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_CERTIFICATE = re.compile(
    r"(?:Signer #1 certificate SHA-256 digest:|"
    r"V3\.0 Signer: certificate SHA-256 digest:) ([0-9a-fA-F]{64})"
)
_CERTIFICATE_MARKER = re.compile(r"\bcertificate\b.*\bSHA-256\b.*\bdigest\b", re.IGNORECASE)
_FAILED_VERDICT = re.compile(
    r"\b(?:does not verify|not verified|verification failed)\b", re.IGNORECASE
)


def verify_signature_log(text: str, expected_sha256: str) -> str:
    """Return the normalized expected digest or raise ValueError on ambiguity.

    SDK 36 identifies a certificate as ``Signer #1``; SDK 37 uses
    ``V3.0 Signer:``. Outer line whitespace is tolerated; non-whitespace verdict
    prefixes/suffixes and unknown certificate prefixes are rejected, not ignored.
    """
    if _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("Expected certificate SHA-256 must be exactly 64 hex characters")
    expected = expected_sha256.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines.count("Verifies") != 1 or any(_FAILED_VERDICT.search(line) for line in lines):
        raise ValueError("Missing or contradictory successful verification verdict")
    signer_lines = [line for line in lines if "number of signers:" in line.lower()]
    if signer_lines != ["Number of signers: 1"]:
        raise ValueError("Expected exactly one unambiguous signer")

    digests = []
    for line in lines:
        if _CERTIFICATE_MARKER.search(line) is None:
            continue
        match = _CERTIFICATE.fullmatch(line)
        if match is None:
            raise ValueError("Unsupported or malformed certificate SHA-256 line")
        digests.append(match[1].lower())
    if not digests:
        raise ValueError("Missing certificate SHA-256 digest")
    if any(digest != expected for digest in digests):
        raise ValueError("Signing certificate differs from the expected certificate")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Saved stdout of apksigner verify --print-certs")
    parser.add_argument("--expected-sha256", required=True,
                        help="Required stable signing-certificate SHA-256 (64 hex characters)")
    args = parser.parse_args(argv)
    try:
        text = args.log.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        parser.exit(1, "FAIL: Cannot read the signature verification log\n")
    try:
        digest = verify_signature_log(text, args.expected_sha256)
    except ValueError as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(f"PASS: one verified Android signer; certificate SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
