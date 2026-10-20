import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "verify_android_signature.py"
SPEC = importlib.util.spec_from_file_location("verify_android_signature", SCRIPT)
signature = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signature)

EXPECTED = "c99e74633706c5ac413bf3c4e4782ceabd7b156eae6b288664986927d1aa1b01"
OTHER = "a" * 64
LEGACY_CERT = f"Signer #1 certificate SHA-256 digest: {EXPECTED}"
SDK37_CERT = f"V3.0 Signer: certificate SHA-256 digest: {EXPECTED}"
SDK36_LOG = f"""Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Number of signers: 1
{LEGACY_CERT}
Signer #1 public key SHA-256 digest: {OTHER}
"""
# Public stdout lines captured from run 39287263812 / job 118179317091.
# Optional unused signature schemes are false without invalidating "Verifies".
SDK37_LOG = f"""Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Verified using v3.1 scheme (APK Signature Scheme v3.1): false
Verified using v3.2 scheme (APK Signature Scheme v3.2): false
Verified using v4 scheme (APK Signature Scheme v4): false
Verified for SourceStamp: false
Number of signers: 1
{SDK37_CERT}
V3.0 Signer: public key SHA-256 digest: 34200cf86427ac4c1b448114a8e5765dde1846979b9383977243ee1a1d18321d
WARNING: META-INF/androidx.activity_activity.version not protected by signature.
"""


class SignatureLogTest(unittest.TestCase):
    def check(self, text=SDK37_LOG, expected=EXPECTED):
        return signature.verify_signature_log(text, expected)

    def reject(self, text=SDK37_LOG, expected=EXPECTED):
        with self.assertRaises(ValueError):
            self.check(text, expected)

    def test_accepts_sdk36_legacy_prefix(self):
        self.assertEqual(self.check(SDK36_LOG), EXPECTED)

    def test_accepts_real_sdk37_stdout_fixture(self):
        self.assertEqual(self.check(), EXPECTED)

    def test_normalizes_certificate_and_expected_hex_case(self):
        self.assertEqual(self.check(SDK37_LOG.replace(EXPECTED, EXPECTED.upper()),
                                    EXPECTED.upper()), EXPECTED)

    def test_accepts_repeated_identical_supported_certificate_digests(self):
        self.assertEqual(self.check(SDK37_LOG + LEGACY_CERT + "\n"), EXPECTED)

    def test_accepts_crlf_and_outer_line_whitespace(self):
        text = "\r\n".join("  " + line + "  " for line in SDK36_LOG.splitlines())
        self.assertEqual(self.check(text), EXPECTED)

    def test_success_verdict_allows_only_outer_whitespace(self):
        self.assertEqual(self.check(SDK37_LOG.replace("Verifies\n", " \tVerifies\t \n", 1)),
                         EXPECTED)

    def test_expected_digest_must_be_standard_64_hex(self):
        for expected in ("", "a" * 63, "a" * 65, "x" * 64, EXPECTED + "\n"):
            with self.subTest(expected_length=len(expected)):
                self.reject(expected=expected)

    def test_requires_exact_successful_verdict(self):
        self.reject(SDK37_LOG.replace("Verifies\n", "", 1))

    def test_rejects_fake_success_prefix(self):
        self.reject(SDK37_LOG.replace("Verifies\n", "Fake Verifies\n", 1))

    def test_rejects_nonwhitespace_verdict_prefix_or_suffix(self):
        for verdict in ("Verifies success", "Result: Verifies", "Not Verifies", "Verifies\x00"):
            with self.subTest(verdict=verdict):
                self.reject(SDK37_LOG.replace("Verifies\n", verdict + "\n", 1))

    def test_rejects_duplicate_success_verdict(self):
        self.reject(SDK37_LOG + "Verifies\n")

    def test_rejects_not_verified_even_with_success_line(self):
        self.reject(SDK37_LOG + "Not verified\n")

    def test_rejects_does_not_verify(self):
        self.reject(SDK37_LOG.replace("Verifies\n", "DOES NOT VERIFY\n", 1))

    def test_rejects_verification_failed(self):
        self.reject(SDK37_LOG + "Verification failed: invalid APK\n")

    def test_requires_signer_count(self):
        self.reject(SDK37_LOG.replace("Number of signers: 1\n", ""))

    def test_rejects_multiple_signers(self):
        self.reject(SDK37_LOG.replace("Number of signers: 1", "Number of signers: 2"))

    def test_rejects_zero_signers(self):
        self.reject(SDK37_LOG.replace("Number of signers: 1", "Number of signers: 0"))

    def test_rejects_duplicate_signer_count(self):
        self.reject(SDK37_LOG + "Number of signers: 1\n")

    def test_rejects_fake_signer_count_prefix(self):
        self.reject(SDK37_LOG.replace("Number of signers: 1", "Fake Number of signers: 1"))

    def test_rejects_missing_certificate_digest(self):
        self.reject(SDK37_LOG.replace(SDK37_CERT + "\n", ""))

    def test_rejects_wrong_certificate(self):
        self.reject(SDK37_LOG.replace(EXPECTED, OTHER))

    def test_rejects_additional_different_certificate(self):
        self.reject(SDK37_LOG + f"Signer #1 certificate SHA-256 digest: {OTHER}\n")

    def test_rejects_extra_unrecognized_certificate_digest_line(self):
        for extra in (f"Unknown signer certificate SHA-256 digest: {OTHER}",
                      f"Signer #1 certificate chain SHA-256 digest: {OTHER}",
                      f"Signer #1 certificate  SHA-256 digest: {OTHER}"):
            with self.subTest(extra=extra):
                self.reject(SDK37_LOG + extra + "\n")

    def test_rejects_additional_unsupported_signer_even_with_same_digest(self):
        self.reject(SDK37_LOG + f"Signer #2 certificate SHA-256 digest: {EXPECTED}\n")

    def test_rejects_only_public_key_digest(self):
        self.reject(SDK37_LOG.replace(SDK37_CERT,
                                      f"V3.0 Signer: public key SHA-256 digest: {EXPECTED}"))

    def test_rejects_fake_certificate_prefix(self):
        for prefix in ("Fake ", "WARNING: ", "2026-10-19T17:10:26Z ", "V3.0 Signer: "):
            with self.subTest(prefix=prefix):
                self.reject(SDK37_LOG.replace(SDK37_CERT, prefix + SDK37_CERT))

    def test_rejects_malformed_certificate_hex(self):
        for digest in ("", "a" * 63, "a" * 65, "g" * 64, "aa:bb", EXPECTED + " extra"):
            with self.subTest(length=len(digest)):
                self.reject(SDK37_LOG.replace(EXPECTED, digest))

    def test_rejects_unrecognized_certificate_marker_case(self):
        self.reject(SDK37_LOG + f"Signer #1 Certificate SHA-256 digest: {OTHER}\n")


class SignatureCliTest(unittest.TestCase):
    def run_cli(self, text=SDK37_LOG, expected=EXPECTED):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "signature.txt"
            path.write_text(text, encoding="utf-8")
            arguments = [sys.executable, str(SCRIPT), str(path)]
            if expected is not None:
                arguments += ["--expected-sha256", expected]
            return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8")

    def test_cli_accepts_sdk37_and_prints_public_digest(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS: one verified Android signer", result.stdout)
        self.assertIn(EXPECTED, result.stdout)

    def test_cli_accepts_sdk36(self):
        result = self.run_cli(SDK36_LOG)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_requires_expected_argument(self):
        result = self.run_cli(expected=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--expected-sha256", result.stderr)

    def test_cli_rejects_wrong_certificate(self):
        result = self.run_cli(SDK37_LOG.replace(EXPECTED, OTHER))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("PASS:", result.stdout)

    def test_cli_rejects_failed_verdict(self):
        result = self.run_cli("Not verified\n" + SDK37_LOG)
        self.assertEqual(result.returncode, 1)

    def test_cli_missing_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SCRIPT),
                                     str(Path(temporary) / "missing.txt"),
                                     "--expected-sha256", EXPECTED],
                                    capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cannot read", result.stderr)

    def test_cli_invalid_expected_never_echoes_input(self):
        private_input = "not-a-public-digest-do-not-print"
        result = self.run_cli(expected=private_input)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(private_input, result.stdout + result.stderr)

    def test_cli_failure_never_dumps_log_contents(self):
        private_input = "private material must not appear in diagnostics"
        result = self.run_cli("Not verified\n" + private_input)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(private_input, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
