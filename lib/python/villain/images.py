# SPDX-License-Identifier: Apache-2.0
"""Disk image creation without VMM or bootloader policy."""

import os
import subprocess
from typing import Union

_QEMU_FORMATS = {"raw": "raw", "qcow2": "qcow2", "vhd": "vpc", "vhdx": "vhdx"}
FORMATS = tuple(_QEMU_FORMATS)


def _validate_size(size):
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("Image size must be a nonnegative integer in bytes")


def create_raw(path: Union[str, os.PathLike[str]], size: int, *,
               exclusive: bool = False) -> None:
    """Create a zero filled raw image with the exact byte length."""
    _validate_size(size)
    with open(path, "xb" if exclusive else "wb") as stream:
        stream.truncate(size)


def create(path: Union[str, os.PathLike[str]], size: int,
           image_format: str = "raw") -> None:
    """Create a raw, qcow2, fixed VHD or VHDX disk image.

    Container capacity alignment follows qemu-img.
    """
    try:
        qemu_format = _QEMU_FORMATS[image_format]
    except KeyError:
        raise ValueError(f"Unsupported disk image format {image_format!r}") from None
    if image_format == "raw":
        create_raw(path, size)
        return
    _validate_size(size)
    command = ["qemu-img", "create", "-f", qemu_format]
    if image_format == "vhd":
        command += ["-o", "subformat=fixed"]
    command += [os.fspath(path), str(size)]
    subprocess.run(command, check=True, capture_output=True)
