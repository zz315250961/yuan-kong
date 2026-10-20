#!/usr/bin/env python3
"""Read-only, narrow damaged-home-frame sentinel, not a general visual approval.

Recognises the repeated opaque black bars/steps captured in the LinkRemote iOS
Simulator home screenshots, plus an almost entirely opaque black frame. Use on
known home/restore checkpoints only: an intentionally striped picture can match,
and an unrecognised broken layout can pass. No original image is changed.

The dependency-free PNG reader deliberately accepts only non-interlaced 8-bit
PNG (grey, RGB, indexed, grey/alpha, RGBA). Unsupported or malformed images fail
closed, with separate exit code 2; a recognised damaged frame exits 1.
"""

import argparse
import json
from pathlib import Path
import statistics
import struct
import sys
import zlib
from dataclasses import dataclass


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_INFLATED_BYTES = 68 * 1024 * 1024
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class ImageError(ValueError):
    """The bounded reader cannot safely interpret this input."""


@dataclass(frozen=True)
class PngImage:
    width: int
    height: int
    color_type: int
    rows: tuple
    palette: tuple = ()
    transparency: bytes = b""

    def pixel(self, row, x):
        channels = CHANNELS[self.color_type]
        offset = x * channels
        if self.color_type == 6:
            return tuple(row[offset:offset + 4])
        if self.color_type == 2:
            rgb = tuple(row[offset:offset + 3])
            transparent = (struct.unpack(">HHH", self.transparency)
                           if self.transparency else None)
            return (*rgb, 0 if rgb == transparent else 255)
        if self.color_type == 3:
            index = row[offset]
            if index >= len(self.palette):
                raise ImageError("palette index is outside PLTE")
            alpha = self.transparency[index] if index < len(self.transparency) else 255
            return (*self.palette[index], alpha)
        grey = row[offset]
        if self.color_type == 4:
            return (grey, grey, grey, row[offset + 1])
        transparent = (struct.unpack(">H", self.transparency)[0]
                       if self.transparency else None)
        return (grey, grey, grey, 0 if grey == transparent else 255)


def _paeth(left, above, upper_left):
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above),
                 abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


def read_png_bytes(data):
    """Validate CRC/structure and inflate at most the declared bounded surface."""
    if len(data) > MAX_FILE_BYTES:
        raise ImageError("PNG file exceeds byte limit")
    if not data.startswith(PNG_SIGNATURE):
        raise ImageError("invalid PNG signature")
    position = len(PNG_SIGNATURE)
    header = None
    palette = ()
    transparency = b""
    compressed = []
    idat_started = False
    idat_finished = False
    ended = False
    seen = set()
    while position < len(data):
        if len(data) - position < 12:
            raise ImageError("truncated PNG chunk")
        length = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data):
            raise ImageError("chunk length exceeds file")
        payload = data[position + 8:end - 4]
        actual_crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != actual_crc:
            raise ImageError("PNG chunk CRC mismatch")
        if not all(65 <= value <= 90 or 97 <= value <= 122 for value in kind):
            raise ImageError("invalid PNG chunk type")
        if not header and kind != b"IHDR":
            raise ImageError("IHDR must be first")
        if kind == b"IHDR":
            if header or length != 13:
                raise ImageError("invalid or repeated IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload)
            if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
                raise ImageError("PNG dimensions exceed limit")
            if width * height > MAX_PIXELS:
                raise ImageError("PNG pixel count exceeds limit")
            if depth != 8 or color not in CHANNELS or compression or filtering or interlace:
                raise ImageError("unsupported PNG format (need non-interlaced 8-bit)")
            header = (width, height, color)
        elif kind == b"PLTE":
            if kind in seen or idat_started or not length or length % 3 or length > 768:
                raise ImageError("invalid PLTE")
            if header[2] in (0, 4):
                raise ImageError("PLTE is invalid for grey PNG")
            palette = tuple(tuple(payload[i:i + 3]) for i in range(0, length, 3))
        elif kind == b"tRNS":
            if kind in seen or idat_started:
                raise ImageError("invalid tRNS ordering")
            color = header[2]
            if (color == 0 and length != 2 or color == 2 and length != 6
                    or color == 3 and (not palette or not 0 < length <= len(palette))
                    or color in (4, 6)):
                raise ImageError("invalid tRNS")
            transparency = payload
        elif kind == b"IDAT":
            if idat_finished or header[2] == 3 and not palette:
                raise ImageError("invalid IDAT ordering")
            compressed.append(payload)
            idat_started = True
        elif kind == b"IEND":
            if length or not idat_started or end != len(data):
                raise ImageError("invalid IEND or trailing bytes")
            ended = True
            break
        elif not kind[0] & 32:
            raise ImageError("unsupported critical PNG chunk")
        if idat_started and kind != b"IDAT":
            idat_finished = True
        seen.add(kind)
        position = end
    if not ended:
        raise ImageError("missing IEND")
    width, height, color = header
    channels = CHANNELS[color]
    stride = width * channels
    expected = (stride + 1) * height
    if expected > MAX_INFLATED_BYTES:
        raise ImageError("inflated PNG exceeds limit")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(b"".join(compressed), expected + 1)
    except zlib.error as error:
        raise ImageError("invalid PNG zlib stream") from error
    if (len(raw) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise ImageError("PNG inflated size/stream does not match IHDR")
    previous = bytearray(stride)
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if filter_type == 1:
            for x in range(channels, stride):
                row[x] = (row[x] + row[x - channels]) & 255
        elif filter_type == 2:
            row = bytearray((value + above) & 255 for value, above in zip(row, previous))
        elif filter_type in (3, 4):
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                upper_left = previous[x - channels] if x >= channels else 0
                predictor = ((left + previous[x]) // 2 if filter_type == 3
                             else _paeth(left, previous[x], upper_left))
                row[x] = (row[x] + predictor) & 255
        elif filter_type != 0:
            raise ImageError("invalid PNG row filter")
        if color == 3 and max(row) >= len(palette):
            raise ImageError("palette index is outside PLTE")
        rows.append(bytes(row))
        previous = row
    return PngImage(width, height, color, tuple(rows), palette, transparency)


def read_png(path):
    with Path(path).open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    return read_png_bytes(data)


def inspect_home_image(image):
    """Detect only the known large regular opaque black bar/step signature."""
    step = max(1, image.width // 400)
    locations = range(0, image.width, step)
    sample_count = len(locations)
    signatures = []
    black_pixels = opaque_pixels = 0
    for row in image.rows:
        longest_start = longest_end = current_start = 0
        in_run = False
        for index, x in enumerate(locations):
            red, green, blue, alpha = image.pixel(row, x)
            opaque = alpha >= 250
            opaque_pixels += opaque
            black = opaque and max(red, green, blue) <= 8
            black_pixels += black
            if black and not in_run:
                current_start = index
                in_run = True
            if in_run and (not black or index == sample_count - 1):
                end = index if not black else index + 1
                if end - current_start > longest_end - longest_start:
                    longest_start, longest_end = current_start, end
                in_run = False
        width_fraction = (longest_end - longest_start) / sample_count
        signatures.append((longest_start, longest_end) if width_fraction >= 0.15 else None)
    total_samples = sample_count * image.height
    black_fraction = black_pixels / total_samples
    opaque_fraction = opaque_pixels / total_samples
    bands = []
    start = 0
    for y in range(1, image.height + 1):
        if y == image.height or signatures[y] != signatures[start]:
            signature = signatures[start]
            if signature is not None and y - start >= 3:
                bands.append((start, y - start, *signature))
            start = y
    median_height = statistics.median(band[1] for band in bands) if bands else 0
    regular = [band for band in bands
               if abs(band[1] - median_height) <= max(1, median_height * 0.1)]
    spacings = [right[0] - left[0] for left, right in zip(regular, regular[1:])]
    median_spacing = statistics.median(spacings) if spacings else 0
    regular_spacings = sum(abs(value - median_spacing) <= max(1, median_spacing * 0.1)
                           for value in spacings)
    band_area = sum(height * (right - left) / sample_count
                    for _, height, left, right in regular) / image.height
    periodic_bars = (len(regular) >= 12 and median_height <= image.height * 0.08
                     and len(regular) >= len(bands) * 0.6
                     and len(spacings) >= 11 and regular_spacings >= len(spacings) * 0.6
                     and median_height <= median_spacing <= median_height * 3
                     and band_area >= 0.06)
    all_black = black_fraction >= 0.995 and opaque_fraction >= 0.995
    reasons = []
    if all_black:
        reasons.append("almost_entirely_opaque_black")
    if periodic_bars:
        reasons.append("large_regular_opaque_black_bars_or_steps")
    return {
        "width": image.width, "height": image.height,
        "known_corruption_detected": bool(reasons), "reasons": reasons,
        "sample_step_x": step, "opaque_fraction": round(opaque_fraction, 6),
        "black_fraction": round(black_fraction, 6),
        "regular_black_band_count": len(regular),
        "median_black_band_height": median_height,
        "median_band_start_spacing": median_spacing,
        "regular_black_band_area_fraction": round(band_area, 6),
        "scope": "known home-frame corruption sentinel; no finding is not visual approval",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png", type=Path, help="unaltered known home/restore screenshot")
    args = parser.parse_args(argv)
    try:
        report = inspect_home_image(read_png(args.png))
    except (OSError, ImageError) as error:
        print(json.dumps({"image": str(args.png), "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["known_corruption_detected"] else 0


if __name__ == "__main__":
    sys.exit(main())
