#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Remove selected build outputs without removing checkout sources."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def clean(root, outputs):
    root = Path(root).resolve()
    checkouts = {root, Path(__file__).resolve().parents[3]}
    protected = checkouts | {Path.home().resolve()}
    paths = []
    for output in outputs:
        output = root / output
        path = Path(os.path.abspath(output.parent.resolve() / output.name))
        if (path.parent == Path(path.anchor) or path.parent == Path.home().parent
                or any(path == entry or path in entry.parents for entry in protected)
                or ".git" in path.parts):
            raise ValueError(f"Refusing to clean protected directory: {path}")
        if not path.is_symlink() and (path / ".git").exists():
            raise ValueError(f"Refusing to clean a Git checkout: {path}")
        for parent in path.parents:
            if (parent / ".git").exists():
                checkouts.add(parent)
                break
        paths.append(path)
    sources = []
    for checkout in checkouts:
        tracked = subprocess.run(
            ["git", "-C", str(checkout), "ls-files", "-z"],
            check=True, stdout=subprocess.PIPE, text=True).stdout
        sources.extend(checkout / name for name in tracked.split("\0") if name)
    for path in paths:
        if any(path == source or path in source.parents for source in sources):
            raise ValueError(f"Refusing to clean tracked sources: {path}")
    for path in paths:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("outputs", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        clean(args.root, args.outputs)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
