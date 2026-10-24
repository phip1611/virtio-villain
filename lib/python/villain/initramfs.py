#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pack a static /init and mount points into a deterministic newc initramfs."""

import argparse
import gzip
from pathlib import Path
import stat
import struct


def archive(init):
    if init[:4] != b"\x7fELF" or len(init) < 64 or init[4:6] != b"\x02\x01":
        raise ValueError("/init must be a little-endian ELF64 binary")
    phoff = struct.unpack_from("<Q", init, 32)[0]
    phsize, phcount = struct.unpack_from("<HH", init, 54)
    if phsize < 56 or phoff + phsize * phcount > len(init):
        raise ValueError("Invalid /init ELF program headers")
    for index in range(phcount):
        if struct.unpack_from("<I", init, phoff + index * phsize)[0] == 3:
            raise ValueError("/init must be statically linked (PT_INTERP present)")
    result = bytearray()
    entries = [(".", stat.S_IFDIR | 0o755, b""),
               ("dev", stat.S_IFDIR | 0o755, b""),
               ("proc", stat.S_IFDIR | 0o755, b""),
               ("sys", stat.S_IFDIR | 0o755, b""),
               ("init", stat.S_IFREG | 0o755, init),
               ("TRAILER!!!", 0, b"")]
    for ino, (name, mode, data) in enumerate(entries, 1):
        encoded = name.encode("ascii") + b"\0"
        fields = (ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0,
                  len(data), 0, 0, 0, 0, len(encoded), 0)
        result.extend(b"070701" + "".join(f"{field:08x}" for field in fields).encode("ascii"))
        result.extend(encoded)
        result.extend(b"\0" * (-len(result) % 4))
        result.extend(data)
        result.extend(b"\0" * (-len(result) % 4))
    return gzip.compress(result, mtime=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        image = archive(args.init.read_bytes())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image)
    except (OSError, ValueError, struct.error) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
