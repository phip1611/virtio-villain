# SPDX-License-Identifier: Apache-2.0
"""Common runner presentation and argument formatting."""

import argparse
from pathlib import Path
import re
import subprocess
import sys

INIT_BINARY = str(Path(__file__).resolve().parents[3] / "target/init")


def print_banner(f=sys.stderr, init_binary=INIT_BINARY):
    """Emit the virtio-villain banner. Sourced from target/init
    --banner when available so the artwork lives in one place."""
    try:
        out = subprocess.check_output([init_binary, "--banner"],
                                      stderr=subprocess.DEVNULL)
        f.write(out.decode())
        f.flush()
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        pass


class ShortFirstHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Render an option as short form first with a single metavar on
    every Python version. Python 3.13 argparse prints "-j, --jobs N",
    but older versions repeat the metavar as "-j N, --jobs N". This
    keeps the help identical across interpreters so the layout does not
    depend on the host Python."""

    def _format_action_invocation(self, action):
        if not action.option_strings or action.nargs == 0:
            return super()._format_action_invocation(action)
        default = self._get_default_metavar_for_optional(action)
        args_string = self._format_args(action, default)
        return ", ".join(action.option_strings) + " " + args_string


def normalize_memory(value):
    """Normalize a memory size to a single letter unit suffix.

    Some VMM size parsers only take uppercase unit suffixes (512M, 1G);
    a lowercase 512m then fails config parsing and aborts every test
    before boot. QEMU also rejects a two letter suffix and treats the
    single letter as binary (M is mebibytes), so drop a trailing B or
    iB to turn 256MB and 256MiB into 256M. Doing this at the CLI
    boundary is safe across backends, so all forms work.
    """
    v = value.strip().upper()
    m = re.fullmatch(r"(\d+)\s*([KMGTPE])(?:I?B)?", v)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return v
