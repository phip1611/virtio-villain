#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify complete build cleanup using isolated checkout fixtures."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))
from villain import clean as cleaner  # noqa: E402


@unittest.skipUnless(shutil.which("make") and shutil.which("git"), "Make and Git required")
class CleanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vv-clean-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "checkout"
        library = self.root / "lib/python/villain"
        library.mkdir(parents=True)
        shutil.copyfile(ROOT / "lib/python/villain/clean.py", library / "clean.py")
        shutil.copyfile(ROOT / "Makefile", self.root / "Makefile")
        (self.root / "selftest").mkdir()
        (self.root / "selftest/mock-vmm").write_text("fixture source\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)

    def make_clean(self, *variables):
        return subprocess.run(
            ["make", "--no-print-directory", "-s", "-C", str(self.root),
             "clean", *variables], capture_output=True, text=True, timeout=20)

    def test_full_default_cleanup_is_repeatable_and_preserves_sources(self):
        for name in (
                "target/init", "target/initramfs.cpio.gz", "target/obj/test.o",
                "target/kernel/images/kernel", "target/kernel-cache/source",
                "target/results/results.json", "target/boot-components/esp/shim",
                "selftest/test_lib", "selftest/test_irq"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"generated")
        (self.root / "selftest/cloud-hypervisor").symlink_to("mock-vmm")
        for _ in range(2):
            result = self.make_clean()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.root / "target").exists())
            for name in ("test_lib", "test_irq", "cloud-hypervisor"):
                self.assertFalse((self.root / "selftest" / name).is_symlink())
                self.assertFalse((self.root / "selftest" / name).exists())
            self.assertTrue((self.root / "selftest/mock-vmm").is_file())
            self.assertTrue((self.root / "Makefile").is_file())
            self.assertTrue((self.root / "lib/python/villain/clean.py").is_file())

    def test_custom_target_object_and_appliance_outputs_are_all_removed(self):
        outputs = [self.root / "custom-target", self.root.parent / "objects",
                   self.root.parent / "appliance"]
        for output in outputs:
            output.mkdir()
            (output / "generated").write_bytes(b"generated")
        default = self.root / "target"
        default.mkdir()
        result = self.make_clean(
            f"TARGET={outputs[0]}", f"OBJDIR={outputs[1]}", f"APPLIANCE_TARGET={outputs[2]}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(default.is_dir())
        self.assertTrue(all(not path.exists() for path in outputs))

    def test_validation_finishes_before_any_output_is_removed(self):
        output = self.root / "target"
        output.mkdir()
        marker = output / "keep"
        marker.write_bytes(b"keep")
        result = self.make_clean("APPLIANCE_TARGET=lib")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to clean tracked sources", result.stderr)
        self.assertTrue(marker.is_file())

    def test_protected_paths_and_parent_traversal_cannot_reach_deletion(self):
        for output in (
                self.root, self.root / "..", self.root / "../..",
                self.root / ".git/objects", ROOT, Path.home(), Path("/"), Path("/tmp")):
            with self.subTest(output=output), \
                 patch.object(cleaner.shutil, "rmtree") as remove, \
                 patch.object(Path, "unlink") as unlink, \
                 self.assertRaisesRegex(ValueError, "Refusing to clean"):
                try:
                    cleaner.clean(self.root, [output])
                finally:
                    remove.assert_not_called()
                    unlink.assert_not_called()

    def test_symlink_output_is_unlinked_without_removing_its_target(self):
        external = self.root.parent / "external input"
        external.mkdir()
        marker = external / "keep"
        marker.write_bytes(b"keep")
        link = self.root / "output link"
        link.symlink_to(external, target_is_directory=True)
        cleaner.clean(self.root, [link])
        self.assertFalse(link.is_symlink())
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_source_aliases_and_other_checkout_sources_are_protected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root / "lib", target_is_directory=True)
        other = self.root.parent / "other-checkout"
        source = other / "include/source.h"
        source.parent.mkdir(parents=True)
        source.write_text("fixture source\n")
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "add", "include"], check=True)
        for path in (alias / "python", other, other / "include", ROOT / "lib"):
            with self.subTest(path=path), \
                 patch.object(cleaner.shutil, "rmtree") as remove, \
                 patch.object(Path, "unlink") as unlink, \
                 self.assertRaisesRegex(ValueError, "Refusing to clean"):
                try:
                    cleaner.clean(self.root, [path])
                finally:
                    remove.assert_not_called()
                    unlink.assert_not_called()
        self.assertTrue(source.is_file())

    def test_source_inspection_failure_does_not_remove_outputs(self):
        output = self.root / "target"
        output.mkdir()
        with patch.object(cleaner.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, ["git"])), \
             self.assertRaises(subprocess.CalledProcessError):
            cleaner.clean(self.root, [output])
        self.assertTrue(output.is_dir())


if __name__ == "__main__":
    unittest.main()
