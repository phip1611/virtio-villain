#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pack a static /init and mount points into a deterministic newc initramfs."""

import argparse
import gzip
from pathlib import Path
import shlex
import stat
import struct
import subprocess
import tempfile


def archive(init, *, compresslevel=9):
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
    return gzip.compress(result, compresslevel=compresslevel, mtime=0)


def build(init, output, *, strip=None, compresslevel=9):
    """Package /init, optionally stripping a temporary copy."""
    data = Path(init).read_bytes()
    if strip is not None:
        command = shlex.split(strip)
        if not command:
            raise ValueError("Strip command must not be empty")
        with tempfile.TemporaryDirectory(prefix="vv-initramfs-") as temporary:
            staged = Path(temporary) / "init"
            staged.write_bytes(data)
            subprocess.run([*command, str(staged)], check=True)
            data = staged.read_bytes()
    image = archive(data, compresslevel=compresslevel)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strip", metavar="COMMAND",
                        help="Strip a temporary copy with COMMAND before packaging")
    args = parser.parse_args()
    try:
        build(args.init, args.output, strip=args.strip)
    except (OSError, ValueError, struct.error, subprocess.SubprocessError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
