"""S3D (PFS) archive parser for EverQuest zone files.

The S3D format stores files identified by an opaque 32-bit ID, not a hash.
The filename directory blob lists filenames in the same order as files appear
in the archive (sorted by byte offset). We match them positionally.
"""

import struct
import zlib
from pathlib import Path


FILENAME_LIST_ID = 0x61580AC9  # well-known ID for the filename directory blob


def load(path: str | Path) -> dict[str, bytes]:
    """Parse an S3D archive and return {filename: bytes} for all contained files."""
    with open(path, "rb") as f:
        data = f.read()

    dir_offset, magic, _version = struct.unpack_from("<I4sI", data, 0)
    if magic != b"PFS ":
        raise ValueError(f"Not an S3D file (bad magic): {magic!r}")

    file_count = struct.unpack_from("<I", data, dir_offset)[0]
    entries: list[tuple[int, int, int]] = []  # (id, offset, size)
    for i in range(file_count):
        base = dir_offset + 4 + i * 12
        fid, offset, size = struct.unpack_from("<III", data, base)
        entries.append((fid, offset, size))

    # Separate the filename-list entry from the rest
    fname_entry = next((e for e in entries if e[0] == FILENAME_LIST_ID), None)
    file_entries = sorted(
        (e for e in entries if e[0] != FILENAME_LIST_ID),
        key=lambda e: e[1],  # sort by byte offset = filename list order
    )

    filenames: list[str] = []
    if fname_entry:
        fname_data = _decompress(data, fname_entry[1], fname_entry[2])
        filenames = _parse_filename_list(fname_data)

    result: dict[str, bytes] = {}
    for i, (fid, offset, size) in enumerate(file_entries):
        name = filenames[i] if i < len(filenames) else f"__unknown_{fid:08X}"
        result[name] = _decompress(data, offset, size)

    return result


def _decompress(data: bytes, offset: int, total_size: int) -> bytes:
    """Decompress a series of zlib blocks: each is [deflated_len:4][inflated_len:4][data]."""
    out = bytearray()
    pos = offset
    while len(out) < total_size:
        deflated_sz, inflated_sz = struct.unpack_from("<II", data, pos)
        pos += 8
        block = zlib.decompress(data[pos : pos + deflated_sz])
        out.extend(block[:inflated_sz])
        pos += deflated_sz
    return bytes(out[:total_size])


def _parse_filename_list(data: bytes) -> list[str]:
    """Parse the filename directory: uint32 count, then (uint32 len, name_with_null) per entry."""
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    names: list[str] = []
    pos = 4
    for _ in range(count):
        if pos + 4 > len(data):
            break
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        name = data[pos : pos + length].rstrip(b"\x00").decode("ascii", errors="replace")
        pos += length
        if name:
            names.append(name)
    return names
