"""Swift source-policy regressions, not AVAudioSession/acoustic execution tests."""

from pathlib import Path
import unittest


class IosVoicePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent / "Runner" /
                      "LinkRemoteVoiceSession.swift").read_text(encoding="utf-8")
        cls.prepare = cls.source.split("  private func prepare(", 1)[1].split(
            "  private func failPrepare(", 1)[0]
        cls.restore = cls.source.split("  private func restore(", 1)[1].split(
            "  deinit", 1)[0]
        cls.failure = cls.restore.split("      } else {", 1)[1]

    def test_exhausted_restore_keeps_saved_policy_and_does_not_release_busy(self):
        self.assertIn("private var restorationFailure: AudioPolicy?", self.source)
        self.assertIn("restorationFailure = saved", self.failure)
        self.assertIn("restoring = true", self.failure)
        self.assertNotIn("restoring = false", self.failure)
        self.assertNotIn("previous = saved", self.failure)

    def test_prepare_reports_cleanup_failure_before_generic_busy_or_new_ownership(self):
        failed = self.prepare.index("guard restorationFailure == nil else")
        busy = self.prepare.index("guard owner == nil && !restoring else")
        owner = self.prepare.index("owner = key")
        self.assertLess(failed, busy)
        self.assertLess(busy, owner)
        failed_guard = self.prepare[failed:busy]
        self.assertIn('code: "audio_cleanup_failed"', failed_guard)
        self.assertIn("Restart", failed_guard)
        self.assertIn("return", failed_guard)

    def test_only_successful_restore_releases_busy_and_clears_failed_policy(self):
        success = self.restore.split("    } catch {", 1)[0]
        self.assertIn("try audio.setCategory(saved.0, mode: saved.1, options: saved.2)", success)
        self.assertIn("restorationFailure = nil", success)
        self.assertIn("restoring = false", success)
        self.assertLess(success.index("try audio.setCategory"),
                        success.index("restoring = false"))

    def test_cleanup_retry_stays_bounded_and_cannot_change_a_new_owner_policy(self):
        self.assertIn("guard owner == nil && generation == token else { return }", self.restore)
        self.assertIn("if attempt < 3", self.restore)
        self.assertIn(".milliseconds(100)", self.restore)
        self.assertIn("token: token, deactivate: deactivate, attempt: attempt + 1", self.restore)
        self.assertNotIn("prepare(", self.restore)
        self.assertNotIn("requestRecordPermission", self.restore)


if __name__ == "__main__":
    unittest.main()
