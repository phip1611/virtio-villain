# SPDX-License-Identifier: Apache-2.0
"""Shared build descriptions and dispatch for external appliance suites."""

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Callable, Optional, Sequence

FRAMEWORK = Path(__file__).resolve().parents[3]
FLAGS = "-static -O2 -Wall -Wextra -Werror"


@dataclass(frozen=True)
class KernelResult:
    """An actual kernel build artifact, not a predicted output filename."""

    image: Path
    arch: str
    format: str

    def validate(self):
        if self.arch not in ("x86_64", "aarch64"):
            raise RuntimeError(f"Unsupported kernel result architecture: {self.arch}")
        if not self.image.is_absolute() or not isinstance(self.format, str) or not self.format:
            raise RuntimeError("Kernel result requires an absolute image path and image format")

    @classmethod
    def read(cls, path):
        try:
            data = json.loads(path.read_text())
        except (ValueError, UnicodeError) as error:
            raise RuntimeError(f"Invalid kernel result {path}: {error}") from error
        if (not isinstance(data, dict) or not isinstance(data.get("image"), str)
                or not isinstance(data.get("arch"), str)
                or not isinstance(data.get("format"), str)):
            raise RuntimeError(f"Invalid kernel result fields: {path}")
        result = cls(Path(data["image"]), data["arch"], data["format"])
        result.validate()
        return result


@dataclass
class BuildSpec:
    """Suite owned sources, output layout and optional preparation/kernel actions.

    Appliance paths are relative to TARGET, or the suite's default target root.
    Kernel actions return actual produced artifacts, which may live elsewhere.
    Callbacks receive the same resolved paths and variables as the compiler.
    Defining metadata performs no build actions.
    """

    root: Path
    sources: Callable[[Path], Sequence[Path]]
    directory: Path = Path("appliance")
    cc: str = "musl-gcc"
    cppflags: Callable[["BuildContext"], Sequence[str]] = lambda context: ()
    prepare: Optional[Callable[["BuildContext"], None]] = None
    kernel: Optional[Callable[["BuildContext"], KernelResult]] = None

    def context(self, target=None, framework=None):
        root = Path(self.root).resolve()
        target = Path(target if target is not None else os.environ.get("TARGET", "target"))
        if not target.is_absolute():
            target = root / target
        directory = Path(self.directory)
        if directory.is_absolute() or ".." in directory.parts:
            raise RuntimeError("Suite build directory must be relative to its output root")
        return BuildContext(self, root, target.resolve(),
                            Path(framework or FRAMEWORK).resolve())


@dataclass
class BuildContext:
    """Resolved build paths and settings shared by Make and runner entry points."""

    spec: BuildSpec
    root: Path
    target: Path
    framework: Path
    variables: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    @property
    def directory(self):
        return self.target / self.spec.directory

    @property
    def binary(self):
        return self.directory / "init"

    @property
    def initramfs(self):
        return self.directory / "initramfs.cpio.gz"

    @property
    def kernel_image(self):
        try:
            return KernelResult.read(self.target / "kernel-result.json").image
        except FileNotFoundError:
            return None

    def build_kernel(self, arguments, *, expected_arch=None, image=None, env=None):
        """Select an artifact reported by build-kernel without guessing its path."""
        self.target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".kernel-build-", dir=self.target) as temporary:
            report = Path(temporary) / "result.json"
            self.run([self.framework / "build-kernel", *arguments, "--result", report], env=env)
            try:
                data = json.loads(report.read_text())
            except (OSError, ValueError, UnicodeError) as error:
                message = f"Cannot read kernel builder result {report}: {error}"
                raise RuntimeError(message) from error
        if not isinstance(data, dict):
            raise RuntimeError("Kernel builder returned invalid result metadata")
        arch = data.get("arch")
        if expected_arch is not None and arch != expected_arch:
            raise RuntimeError(f"Kernel architecture: expected {expected_arch}, actual {arch}")
        kind = image if image is not None else data.get("boot_image")
        images = data.get("images")
        if not isinstance(kind, str) or not isinstance(images, dict):
            raise RuntimeError("Kernel builder did not report boot image artifacts")
        artifact = images.get(kind)
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise RuntimeError(f"Kernel builder did not produce requested image format: {kind}")
        result = KernelResult(Path(artifact["path"]), arch, kind)
        result.validate()
        return result

    def run(self, command, **kwargs):
        # Preserve GNU Make jobserver descriptors across Python dispatch.
        subprocess.run(list(map(str, command)), cwd=self.root, check=True,
                       close_fds=False, **kwargs)

    def build(self, actions):
        actions = set(actions)
        unknown = actions - {"init", "initramfs", "kernel", "prepare"}
        if unknown or not actions:
            raise RuntimeError(f"Invalid suite build actions: {sorted(actions)}")
        if "kernel" in actions and self.spec.kernel is None:
            raise RuntimeError("Suite does not provide a kernel build action")
        if actions == {"prepare"} and self.spec.prepare is None:
            raise RuntimeError("Suite does not provide a preparation action")
        flags = self.variables.get("MAKEFLAGS", "").split(" -- ", 1)[0].split()
        short = flags[0] if flags and not flags[0].startswith("-") else ""
        if "n" in short or {"--dry-run", "--just-print", "--recon"} & set(flags):
            print(f"Suite build dry run: {self.root} -> {self.target} "
                  f"({', '.join(sorted(actions))})")
            return
        if actions & {"init", "initramfs", "prepare"}:
            if self.spec.prepare is not None:
                self.spec.prepare(self)
        if actions & {"init", "initramfs"}:
            sources = [Path(source) for source in self.spec.sources(self.root)]
            sources = [(source if source.is_absolute() else self.root / source).resolve()
                       for source in sources]
            if not sources:
                raise RuntimeError("Suite build source list is empty")
            for path in [*sources, self.directory]:
                if any(character.isspace() for character in str(path)):
                    raise RuntimeError(f"Make appliance paths cannot contain whitespace: {path}")
            command = [
                "make", "--no-print-directory", "-C", self.framework, "SUITE=",
                "appliance",
                f"APPLIANCE_SRCS={' '.join(map(str, sources))}",
                f"APPLIANCE_TARGET={self.directory}",
                f"APPLIANCE_CPPFLAGS={shlex.join(self.spec.cppflags(self))}",
                f"CC={self.variables.get('CC', self.spec.cc)}",
                f"CFLAGS={self.variables.get('CFLAGS', FLAGS)}",
                f"LDFLAGS={self.variables.get('LDFLAGS', '-static')}",
            ]
            self.run(command)
            if "initramfs" in actions:
                self.run([
                    sys.executable, self.framework / "lib/python/villain/initramfs.py",
                    "--init", self.binary, "--output", self.initramfs])
            expected = [self.binary]
            if "initramfs" in actions:
                expected.append(self.initramfs)
            for path in expected:
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError(f"Suite build did not produce a nonempty file: {path}")
        if "kernel" in actions:
            result = self.spec.kernel(self)
            if not isinstance(result, KernelResult):
                raise RuntimeError("Kernel build action must return a KernelResult")
            result.validate()
            if not result.image.is_file() or result.image.stat().st_size == 0:
                raise RuntimeError(f"Kernel build did not produce a nonempty image: {result.image}")
            self.target.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".kernel-record-",
                                             dir=self.target) as temporary:
                staged = Path(temporary) / "kernel-result.json"
                staged.write_text(json.dumps({
                    "image": str(result.image), "arch": result.arch, "format": result.format,
                }, indent=2) + "\n")
                staged.replace(self.target / "kernel-result.json")


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from villain.appliance import load_suite

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("actions", nargs="+",
                        choices=("init", "initramfs", "kernel", "prepare"))
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
        if suite.build_spec is None:
            raise RuntimeError("Suite does not provide build metadata")
        suite.build_spec.context(args.build_dir).build(args.actions)
    except (OSError, RuntimeError, ValueError, ImportError, AttributeError, TypeError, SyntaxError,
            subprocess.SubprocessError) as error:
        parser.exit(2, f"Suite build failed: {error}\n")


if __name__ == "__main__":
    main()
