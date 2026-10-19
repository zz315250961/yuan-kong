from pathlib import Path
import shutil
import tempfile
import unittest

from run_http_status_tests import ROOT, check_sources


class HttpStatusSourceContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in ("src/ui_interface.rs", "src/ui_interface/http_status.rs"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def tearDown(self):
        self.temp.cleanup()

    def replace(self, relative, before, after):
        target = self.root / relative
        source = target.read_text(encoding="utf-8")
        self.assertIn(before, source)
        target.write_text(source.replace(before, after), encoding="utf-8")

    def test_current_production_wiring_matches(self):
        check_sources(self.root)

    def test_ui_must_declare_the_consume_helper(self):
        self.replace("src/ui_interface.rs", "mod http_status;", "// no consume helper")
        with self.assertRaisesRegex(ValueError, "declare its production child module"):
            check_sources(self.root)

    def test_ui_cannot_bypass_the_production_helper(self):
        self.replace("src/ui_interface.rs", "http_status::read_status(&mut statuses, &url)", "statuses.get(&url).cloned()")
        with self.assertRaisesRegex(ValueError, "delegate under its existing mutex"):
            check_sources(self.root)

    def test_ui_must_keep_the_cache_lock_while_reading_and_consuming(self):
        self.replace("src/ui_interface.rs", "let mut statuses = ASYNC_HTTP_STATUS.lock().unwrap();", "let mut statuses = HashMap::new();")
        with self.assertRaisesRegex(ValueError, "existing mutex"):
            check_sources(self.root)

    def test_helper_cannot_hide_native_http_dependencies(self):
        self.replace("src/ui_interface/http_status.rs", "use std::collections::HashMap;", "use std::collections::HashMap;\nuse reqwest::Client;")
        with self.assertRaisesRegex(ValueError, "self-contained"):
            check_sources(self.root)

    def test_real_rust_regressions_cannot_be_silently_removed(self):
        self.replace("src/ui_interface/http_status.rs", "#[test]", "#[allow(dead_code)]")
        with self.assertRaisesRegex(ValueError, "nine real Rust regression tests"):
            check_sources(self.root)


if __name__ == "__main__":
    unittest.main()
