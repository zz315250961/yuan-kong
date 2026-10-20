"""Bounded PNG-reader and narrow known-corruption-sentinel regressions.

Synthetic fixtures are generated only in memory. Original artifact fixtures are
read-only and optional outside the evidence workstation; the test reports skips
when they are not available rather than substituting synthetic pass evidence.
"""

import contextlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

import verify_simulator_image as verifier


def chunk(kind, payload=b""):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def png(width, height, color=6, rows=None, filters=None, palette=b"", transparency=None,
        raw=None, depth=8, interlace=0):
    channels = verifier.CHANNELS.get(color, 4)
    if rows is None:
        rows = [bytes([255] * width * channels)] * height
    if raw is None:
        previous = bytes(width * channels)
        encoded = []
        for index, row in enumerate(rows):
            filter_type = filters[index] if filters else 0
            filtered = bytearray(len(row))
            for x, value in enumerate(row):
                left = row[x - channels] if x >= channels else 0
                above = previous[x]
                upper_left = previous[x - channels] if x >= channels else 0
                predictor = (0, left, above, (left + above) // 2,
                             verifier._paeth(left, above, upper_left))[filter_type]
                filtered[x] = (value - predictor) & 255
            encoded.append(bytes([filter_type]) + filtered)
            previous = row
        raw = b"".join(encoded)
    contents = verifier.PNG_SIGNATURE + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
    if palette:
        contents += chunk(b"PLTE", palette)
    if transparency is not None:
        contents += chunk(b"tRNS", transparency)
    return contents + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND")


def bars(color=6, transparent=False, stair=False):
    width, height = 180, 320
    rows = []
    for y in range(height):
        edge = 30 + ((y // 8) % 6) * 18 if stair else 30
        row = bytearray()
        for x in range(width):
            black = y >= 32 and (y // 8) % 2 == 0 and edge <= x < 150
            value = 0 if black else 255
            if color == 6:
                row.extend((value, value, value, 0 if transparent and black else 255))
            elif color == 2:
                row.extend((value, value, value))
            elif color == 4:
                row.extend((value, 0 if transparent and black else 255))
            else:
                row.append(0 if black else 1 if color == 3 else value)
        rows.append(bytes(row))
    palette = b"\x00\x00\x00\xff\xff\xff" if color == 3 else b""
    return png(width, height, color, rows, palette=palette)


class PngReaderTests(unittest.TestCase):
    def test_all_five_filters_reconstruct_exact_rgb_and_rgba_bytes(self):
        for color in (0, 2, 3, 4, 6):
            with self.subTest(color=color):
                channels = verifier.CHANNELS[color]
                rows = [bytes((x * 17 + y * 23) % 256 for x in range(7 * channels))
                        for y in range(5)]
                palette = bytes(range(256)) * 3 if color == 3 else b""
                image = verifier.read_png_bytes(
                    png(7, 5, color, rows, filters=list(range(5)), palette=palette))
                self.assertEqual(image.rows, tuple(rows))

    def test_all_five_pixel_formats_preserve_rgba(self):
        cases = [(0, b"\x13", b"", (19, 19, 19, 255)),
                 (2, b"\x01\x02\x03", b"", (1, 2, 3, 255)),
                 (3, b"\x01", b"\x00\x00\x00\x01\x02\x03", (1, 2, 3, 255)),
                 (4, b"\x13\x18", b"", (19, 19, 19, 24)),
                 (6, b"\x01\x02\x03\x18", b"", (1, 2, 3, 24))]
        for color, row, palette, expected in cases:
            with self.subTest(color=color):
                image = verifier.read_png_bytes(png(1, 1, color, [row], palette=palette))
                self.assertEqual(image.pixel(image.rows[0], 0), expected)

    def test_trns_is_not_mistaken_for_opaque_black(self):
        cases = [(0, b"\x00", b"", b"\x00\x00"),
                 (2, b"\x00\x00\x00", b"", b"\x00" * 6),
                 (3, b"\x00", b"\x00\x00\x00", b"\x00")]
        for color, row, palette, transparency in cases:
            with self.subTest(color=color):
                image = verifier.read_png_bytes(png(
                    1, 1, color, [row], palette=palette, transparency=transparency))
                self.assertEqual(image.pixel(image.rows[0], 0)[3], 0)

    def test_bad_signature_truncation_crc_and_trailing_bytes_rejected(self):
        good = png(1, 1)
        damaged_crc = bytearray(good)
        damaged_crc[29] ^= 1
        for data in (b"not png", good[:-1], bytes(damaged_crc), good + b"extra"):
            with self.subTest(data=data[:16]):
                with self.assertRaises(verifier.ImageError):
                    verifier.read_png_bytes(data)

    def test_declared_chunk_length_cannot_read_outside_file(self):
        data = verifier.PNG_SIGNATURE + struct.pack(">I", 0xffffffff) + b"IHDR" + b"0" * 13
        with self.assertRaisesRegex(verifier.ImageError, "chunk length"):
            verifier.read_png_bytes(data)

    def test_dimensions_and_pixel_limit_checked_before_inflation(self):
        for width, height in ((0, 10), (8193, 1), (1, 8193), (8192, 8192)):
            data = verifier.PNG_SIGNATURE + chunk(
                b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            with self.subTest(dimensions=(width, height)):
                with patch.object(verifier.zlib, "decompressobj") as decoder:
                    with self.assertRaises(verifier.ImageError):
                        verifier.read_png_bytes(data)
                    decoder.assert_not_called()

    def test_file_size_and_inflation_limits_are_enforced(self):
        with patch.object(verifier, "MAX_FILE_BYTES", 10):
            with self.assertRaisesRegex(verifier.ImageError, "byte limit"):
                verifier.read_png_bytes(png(1, 1))
        with patch.object(verifier, "MAX_INFLATED_BYTES", 1):
            with self.assertRaisesRegex(verifier.ImageError, "inflated PNG exceeds"):
                verifier.read_png_bytes(png(1, 1))

    def test_decompression_bomb_and_short_stream_rejected(self):
        for raw in (b"\x00" * 1000000, b"\x00", b"\x00" * 6):
            with self.subTest(size=len(raw)):
                with self.assertRaisesRegex(verifier.ImageError, "inflated size"):
                    verifier.read_png_bytes(png(1, 1, raw=raw))

    def test_concatenated_zlib_stream_is_not_silently_accepted(self):
        header = png(1, 1).split(b"IDAT")[0][:-4]
        data = (header + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff" * 4)
                               + zlib.compress(b"extra")) + chunk(b"IEND"))
        with self.assertRaisesRegex(verifier.ImageError, "inflated size"):
            verifier.read_png_bytes(data)

    def test_unsupported_depth_color_interlace_and_filter_fail_closed(self):
        inputs = [png(1, 1, depth=16), png(1, 1, color=1), png(1, 1, interlace=1),
                  png(1, 1, raw=b"\x05" + b"\xff" * 4)]
        for data in inputs:
            with self.assertRaises(verifier.ImageError):
                verifier.read_png_bytes(data)

    def test_missing_palette_and_out_of_range_palette_index_rejected(self):
        for data in (png(1, 1, color=3, rows=[b"\x00"]),
                     png(1, 1, color=3, rows=[b"\x01"], palette=b"\x00\x00\x00")):
            with self.assertRaises(verifier.ImageError):
                verifier.read_png_bytes(data)

    def test_repeated_header_noncontiguous_idat_and_unknown_critical_rejected(self):
        good = png(1, 1)
        header = good[8:33]
        for data in (good[:33] + header + good[33:],
                     good[:33] + chunk(b"WHAT") + good[33:],
                     good[:-12] + chunk(b"tEXt", b"x") + chunk(b"IDAT") + good[-12:]):
            with self.assertRaises(verifier.ImageError):
                verifier.read_png_bytes(data)


class KnownCorruptionTests(unittest.TestCase):
    def test_regular_large_bars_and_steps_detected_in_every_supported_format(self):
        for color in (0, 2, 3, 4, 6):
            for stair in (False, True):
                with self.subTest(color=color, stair=stair):
                    report = verifier.inspect_home_image(
                        verifier.read_png_bytes(bars(color, stair=stair)))
                    self.assertTrue(report["known_corruption_detected"])
                    self.assertIn("large_regular_opaque_black_bars_or_steps", report["reasons"])

    def test_transparent_bars_do_not_match_opaque_corruption(self):
        for color in (4, 6):
            report = verifier.inspect_home_image(
                verifier.read_png_bytes(bars(color, transparent=True)))
            self.assertFalse(report["known_corruption_detected"])

    def test_white_solid_dark_and_single_black_footer_are_not_approved_or_flagged(self):
        width, height = 180, 320
        for value in (25, 100, 255):
            rows = [bytes([value, value, value, 255]) * width] * height
            report = verifier.inspect_home_image(verifier.read_png_bytes(
                png(width, height, rows=rows)))
            self.assertFalse(report["known_corruption_detected"])
            self.assertIn("not visual approval", report["scope"])
        rows[-8:] = [b"\x00\x00\x00\xff" * width] * 8
        self.assertFalse(verifier.inspect_home_image(verifier.read_png_bytes(
            png(width, height, rows=rows)))["known_corruption_detected"])

    def test_entire_opaque_black_fails_but_transparent_black_does_not(self):
        for alpha, expected in ((255, True), (0, False)):
            rows = [bytes([0, 0, 0, alpha]) * 32] * 32
            report = verifier.inspect_home_image(verifier.read_png_bytes(png(32, 32, rows=rows)))
            self.assertEqual(report["known_corruption_detected"], expected)

    def test_cli_returns_distinct_corrupt_clean_and_unreadable_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            for data, expected in ((bars(), 1), (png(1, 1), 0), (b"bad", 2)):
                path.write_bytes(data)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(verifier.main([str(path)]), expected)


class OriginalArtifactTests(unittest.TestCase):
    evidence = Path(__file__).resolve().parents[2].parent / "artifacts"

    def assert_original(self, relative, expected):
        path = self.evidence / relative
        if not path.is_file():
            self.skipTest("Original unmodified screenshot evidence not present on this host")
        report = verifier.inspect_home_image(verifier.read_png(path))
        self.assertEqual(report["known_corruption_detected"], expected, (str(path), report))

    def test_original_144_light_damaged_frame_is_red(self):
        self.assert_original(
            "v1.0.44-candidate/ios-c0354c-39287264581/terminal-evidence-20261020/"
            "ui-evidence/ipad-home-light.png", True)

    def test_original_143_dark_damaged_frame_is_red(self):
        self.assert_original(
            "v1.0.43-candidate/ios-simulator-owned-replay-39279520818/ipad/ui/ipad-home-dark.png", True)

    def test_original_same_build_normal_light_dark_and_resume_do_not_match(self):
        for name in ("ipad-home-light.png", "ipad-home-dark.png", "ipad-resumed.png"):
            with self.subTest(name=name):
                self.assert_original(
                    "v1.0.44-candidate/ios-ipad-diagnostic-39412871110-1/evidence/ui/" + name, False)

    def test_original_native_settings_light_dark_and_safari_do_not_match(self):
        for name in ("ipad-native-settings-light.png", "ipad-native-settings-dark.png",
                     "ipad-native-safari-prewarmed.png"):
            with self.subTest(name=name):
                self.assert_original(
                    "v1.0.44-candidate/ios-ipad-diagnostic-39412871110-1/evidence/ui/" + name, False)


if __name__ == "__main__":
    unittest.main()
