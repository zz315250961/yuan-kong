from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from verify_android_artifact import ArtifactError, inspect_apk, inspect_elf


def elf(*, alignment=0x4000, machine=183, elf_class=2, endian=1, vaddr=0, kind=1):
    ident = b"\x7fELF" + bytes((elf_class, endian, 1)) + bytes(9)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, machine, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    program = struct.pack("<IIQQQQQQ", kind, 5, 0, vaddr, 0, 256, 256, alignment)
    return (header + program).ljust(256, b"\0")


class AndroidArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.apk = Path(self.temp.name) / "test.apk"

    def tearDown(self):
        self.temp.cleanup()

    def package(self, payload=None, *, name="lib/arm64-v8a/libtest.so", compressed=False, align_zip=True):
        with zipfile.ZipFile(self.apk, "w") as archive:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
            if align_zip:
                base = archive.fp.tell() + 30 + len(name.encode("utf-8")) + 4
                padding = (-base) % 16384
                info.extra = struct.pack("<HH", 0xCAFE, padding) + bytes(padding)
            archive.writestr(info, elf() if payload is None else payload)
        return inspect_apk(self.apk)

    def assert_elf_error(self, payload, message):
        with self.assertRaisesRegex(ArtifactError, message):
            inspect_elf(payload)

    def test_valid_arm64_stored_library_and_read_only_hash(self):
        report = self.package()
        before = self.apk.read_bytes()
        report = inspect_apk(self.apk)
        self.assertTrue(report.ok)
        self.assertEqual(report.libraries[0].loads[0].alignment, 0x4000)
        self.assertEqual(report.libraries[0].zip_data_offset % 16384, 0)
        self.assertIn("PASS", report.libraries[0].zip_alignment)
        self.assertEqual(len(report.sha256), 64)
        self.assertEqual(self.apk.read_bytes(), before)

    def test_64k_elf_alignment_also_passes(self):
        self.assertEqual(inspect_elf(elf(alignment=0x10000))[0].alignment, 0x10000)

    def test_compressed_library_marks_zip_alignment_not_applicable(self):
        report = self.package(compressed=True, align_zip=False)
        self.assertTrue(report.ok)
        self.assertIn("N/A (compressed", report.libraries[0].zip_alignment)
        self.assertEqual(report.libraries[0].elf_identity, "ELF64 little-endian AArch64")

    def test_4k_elf_alignment_fails_even_if_zip_is_aligned(self):
        report = self.package(elf(alignment=4096))
        self.assertFalse(report.ok)
        self.assertIn("p_align=0x1000", report.libraries[0].errors[0])

    def test_non_power_of_two_and_zero_alignment_fail(self):
        for alignment in (0, 0x6000):
            self.assert_elf_error(elf(alignment=alignment), "power of two")

    def test_offset_and_virtual_address_must_be_congruent(self):
        self.assert_elf_error(elf(vaddr=1), "not congruent")

    def test_every_load_segment_is_checked(self):
        payload = bytearray(elf())
        struct.pack_into("<H", payload, 56, 2)
        payload[120:176] = struct.pack("<IIQQQQQQ", 1, 6, 0, 0, 0, 256, 256, 4096)
        self.assert_elf_error(payload, r"PT_LOAD\[1\]")

    def test_wrong_machine_elf_class_and_endian_fail(self):
        self.assert_elf_error(elf(machine=62), "AArch64")
        self.assert_elf_error(elf(elf_class=1), "ELF64")
        self.assert_elf_error(elf(endian=2), "little-endian")

    def test_magic_and_truncated_headers_fail(self):
        self.assert_elf_error(bytes(64), "magic")
        self.assert_elf_error(elf()[:30], "truncated ELF64 header")
        self.assert_elf_error(elf()[:100], "program-header table")

    def test_invalid_header_size_and_program_entry_size_fail(self):
        for offset, value, message in ((52, 32, "header size"), (54, 40, "program-header size")):
            payload = bytearray(elf())
            struct.pack_into("<H", payload, offset, value)
            self.assert_elf_error(payload, message)

    def test_truncated_load_and_missing_load_fail(self):
        payload = bytearray(elf())
        struct.pack_into("<Q", payload, 64 + 32, 512)
        self.assert_elf_error(payload, "segment bounds")
        self.assert_elf_error(elf(kind=2), "no PT_LOAD")

    def test_other_abi_and_so_outside_native_directory_fail(self):
        for name in ("lib/armeabi-v7a/libtest.so", "lib/x86_64/libtest.so", "assets/libtest.so"):
            report = self.package(name=name)
            self.assertFalse(report.ok)
            self.assertTrue(any("unexpected ABI" in error for error in report.libraries[0].errors))

    def test_uncompressed_zip_data_offset_must_be_16k_aligned(self):
        report = self.package(align_zip=False)
        self.assertFalse(report.ok)
        self.assertEqual(report.libraries[0].zip_alignment, "FAIL")
        self.assertTrue(any("ZIP data offset" in error for error in report.libraries[0].errors))

    def test_bad_zip_and_no_native_libraries_fail(self):
        self.apk.write_bytes(b"PK\x03\x04")
        self.assertFalse(inspect_apk(self.apk).ok)
        with zipfile.ZipFile(self.apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"test")
        self.assertFalse(inspect_apk(self.apk).ok)

    def test_corrupt_local_header_is_reported(self):
        self.package()
        data = bytearray(self.apk.read_bytes())
        data[0:4] = b"BAD!"
        self.apk.write_bytes(data)
        report = inspect_apk(self.apk)
        self.assertFalse(report.ok)
        self.assertIn("local-header signature", report.libraries[0].errors[0])


if __name__ == "__main__":
    unittest.main()
