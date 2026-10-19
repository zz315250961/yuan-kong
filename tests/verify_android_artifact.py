#!/usr/bin/env python3
"""Read-only ARM64 APK ELF/ZIP alignment checks using Python's standard library.

Usage: python tests/verify_android_artifact.py path/to/app.apk [--json]
Scope: ABI, ELF64 little-endian AArch64, PT_LOAD alignment and stored .so ZIP
data offsets. This does not run zipalign, verify signing, test Android loading,
check every page-size runtime assumption, or establish store eligibility.
Reference: https://developer.android.com/guide/practices/page-sizes
"""

import argparse
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

PAGE_SIZE = 16 * 1024
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
ZIP_LOCAL_HEADER = struct.Struct("<4s5H3I2H")
PT_LOAD = 1
EM_AARCH64 = 183


class ArtifactError(ValueError):
    pass


@dataclass
class LoadSegment:
    index: int
    offset: int
    virtual_address: int
    alignment: int


@dataclass
class LibraryReport:
    name: str
    compression: int
    zip_data_offset: int | None = None
    zip_alignment: str = "not checked"
    elf_identity: str = "not checked"
    loads: list[LoadSegment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


@dataclass
class ApkReport:
    path: str
    sha256: str = ""
    libraries: list[LibraryReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.libraries) and not self.errors and all(item.ok for item in self.libraries)


def inspect_elf(data):
    """Return every PT_LOAD after structural and 16 KiB alignment validation."""
    if len(data) < ELF_HEADER.size:
        raise ArtifactError("truncated ELF64 header")
    header = ELF_HEADER.unpack_from(data)
    ident, _, machine, version, _, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = header
    if ident[:4] != b"\x7fELF":
        raise ArtifactError("invalid ELF magic")
    if ident[4] != 2:
        raise ArtifactError("ELF class must be ELF64")
    if ident[5] != 1:
        raise ArtifactError("ELF byte order must be little-endian")
    if ident[6] != 1 or version != 1:
        raise ArtifactError("unsupported ELF version")
    if machine != EM_AARCH64:
        raise ArtifactError(f"ELF machine must be AArch64 (183), found {machine}")
    if ehsize != ELF_HEADER.size:
        raise ArtifactError(f"invalid ELF64 header size {ehsize}")
    if phnum == 0 or phnum == 0xFFFF:
        raise ArtifactError("missing or unsupported extended program-header count")
    if phentsize != PROGRAM_HEADER.size:
        raise ArtifactError(f"invalid ELF64 program-header size {phentsize}")
    if phoff < ELF_HEADER.size or phoff + phentsize * phnum > len(data):
        raise ArtifactError("truncated or invalid ELF program-header table")

    loads = []
    for index in range(phnum):
        kind, _, offset, vaddr, _, filesz, memsz, alignment = PROGRAM_HEADER.unpack_from(data, phoff + index * phentsize)
        if kind != PT_LOAD:
            continue
        if alignment < PAGE_SIZE or alignment & (alignment - 1):
            raise ArtifactError(f"PT_LOAD[{index}] p_align=0x{alignment:x} must be a power of two >= 0x4000")
        if offset % alignment != vaddr % alignment:
            raise ArtifactError(f"PT_LOAD[{index}] offset/vaddr are not congruent modulo p_align=0x{alignment:x}")
        if filesz > memsz or offset > len(data) or filesz > len(data) - offset:
            raise ArtifactError(f"PT_LOAD[{index}] has invalid or truncated segment bounds")
        if vaddr + memsz > 1 << 64:
            raise ArtifactError(f"PT_LOAD[{index}] virtual-address range overflows ELF64")
        loads.append(LoadSegment(index, offset, vaddr, alignment))
    if not loads:
        raise ArtifactError("ELF contains no PT_LOAD segments")
    return loads


def local_data_offset(stream, info, archive_size):
    """Compute the actual data offset from the ZIP local header, not central extra."""
    if info.header_offset < 0 or info.header_offset + ZIP_LOCAL_HEADER.size > archive_size:
        raise ArtifactError("truncated ZIP local header")
    stream.seek(info.header_offset)
    raw = stream.read(ZIP_LOCAL_HEADER.size)
    if len(raw) != ZIP_LOCAL_HEADER.size:
        raise ArtifactError("truncated ZIP local header")
    fields = ZIP_LOCAL_HEADER.unpack(raw)
    if fields[0] != b"PK\x03\x04":
        raise ArtifactError("invalid ZIP local-header signature")
    flags, method, name_size, extra_size = fields[2], fields[3], fields[9], fields[10]
    if method != info.compress_type or flags != info.flag_bits:
        raise ArtifactError("ZIP local/central flags or compression differ")
    name = stream.read(name_size)
    try:
        decoded = name.decode("utf-8" if flags & 0x800 else "cp437")
    except UnicodeDecodeError as error:
        raise ArtifactError("invalid ZIP local filename encoding") from error
    if len(name) != name_size or decoded != info.orig_filename:
        raise ArtifactError("ZIP local/central filenames differ or are truncated")
    offset = info.header_offset + ZIP_LOCAL_HEADER.size + name_size + extra_size
    if offset > archive_size or info.compress_size > archive_size - offset:
        raise ArtifactError("truncated ZIP library data")
    return offset


def inspect_apk(path):
    report = ApkReport(str(Path(path).resolve()))
    try:
        with open(path, "rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            report.sha256 = digest.hexdigest()
            archive_size = stream.tell()
            stream.seek(0)
            with zipfile.ZipFile(stream, "r") as archive:
                seen = set()
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".so"):
                        continue
                    library = LibraryReport(info.filename, info.compress_type)
                    report.libraries.append(library)
                    parts = info.filename.split("/")
                    if len(parts) != 3 or parts[:2] != ["lib", "arm64-v8a"] or not parts[2] or "\\" in info.filename:
                        library.errors.append("native library must be lib/arm64-v8a/*.so; unexpected ABI or path")
                    if info.filename in seen:
                        library.errors.append("duplicate ZIP library path")
                    seen.add(info.filename)
                    try:
                        library.zip_data_offset = local_data_offset(stream, info, archive_size)
                        if info.compress_type == zipfile.ZIP_STORED:
                            if library.zip_data_offset % PAGE_SIZE:
                                library.zip_alignment = "FAIL"
                                library.errors.append(f"uncompressed .so ZIP data offset 0x{library.zip_data_offset:x} is not 16 KiB aligned")
                            else:
                                library.zip_alignment = "PASS (uncompressed, 16 KiB aligned)"
                        else:
                            library.zip_alignment = "N/A (compressed .so; ZIP mmap alignment does not apply)"
                        library.loads = inspect_elf(archive.read(info))
                        library.elf_identity = "ELF64 little-endian AArch64"
                    except (ArtifactError, zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as error:
                        library.errors.append(str(error))
                if not report.libraries:
                    report.errors.append("no packaged .so libraries found; native-library policy could not be verified")
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        report.errors.append(str(error))
    return report


def print_report(report):
    print(f"APK: {report.path}\nSHA-256: {report.sha256 or 'unavailable'}")
    for library in report.libraries:
        print(f"\n{'PASS' if library.ok else 'FAIL'} {library.name}")
        print(f"  ELF: {library.elf_identity}")
        for load in library.loads:
            print(f"  PT_LOAD[{load.index}]: offset=0x{load.offset:x}, vaddr=0x{load.virtual_address:x}, p_align=0x{load.alignment:x}")
        offset = f"0x{library.zip_data_offset:x}" if library.zip_data_offset is not None else "unknown"
        print(f"  ZIP: method={library.compression}, data_offset={offset}; {library.zip_alignment}")
        for error in library.errors:
            print(f"  ERROR: {error}")
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"\n{'PASS' if report.ok else 'FAIL'}: ARM64 ABI, ELF PT_LOAD and applicable ZIP alignment checks")
    print("STATIC CHECK ONLY: zipalign/signature verification, Android runtime loading, other page-size assumptions and store eligibility were not tested.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--json", action="store_true", help="Print a machine-readable per-library report")
    args = parser.parse_args(argv)
    report = inspect_apk(args.apk)
    if args.json:
        result = asdict(report)
        result["ok"] = report.ok
        result["scope"] = "Static ABI/ELF/PT_LOAD/applicable ZIP alignment only; no runtime, signature, zipalign or store assessment"
        print(json.dumps(result, indent=2))
    else:
        print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
