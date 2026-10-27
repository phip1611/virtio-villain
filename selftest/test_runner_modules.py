#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared runner primitives without VM or device prerequisites."""

import io
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))
from villain import cli, console, process, vmm  # noqa: E402


class ModuleTests(unittest.TestCase):
    def test_entry_points_import_shared_helpers_without_loading_other_executables(self):
        check = (
            "import runpy, sys\n"
            "entry = runpy.run_path(sys.argv[1], run_name='runner_import_test')\n"
            "from villain import cli, console, vmm\n"
            "assert not any(name.startswith('vv_run') for name in sys.modules)\n"
            "if sys.argv[1].endswith('/run'):\n"
            "    assert entry['detect_vmm'] is vmm.detect_vmm\n"
            "    assert entry['print_banner'] is cli.print_banner\n"
            "    assert entry['_canonicalize_tests'] is cli.canonicalize_tests\n"
            "    assert entry['drain_stream'] is console.drain_stream\n"
            "elif sys.argv[1].endswith('/run-fuzz'):\n"
            "    assert entry['print_banner'] is cli.print_banner\n"
            "    assert entry['ctty_preexec'] is console.ctty_preexec\n"
            "else:\n"
            "    assert entry['run_guest'].__defaults__ == (vmm,)\n")
        for entry in ("run", "run-fuzz", "run-perf"):
            with self.subTest(entry=entry):
                result = subprocess.run(
                    [sys.executable, "-c", check, str(ROOT / entry)],
                    cwd="/", capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_detection_is_owned_by_the_shared_module(self):
        for name, backend in (
                ("cloud-hypervisor", vmm.CloudHypervisor),
                ("qemu-system-x86_64", vmm.Qemu),
                ("openvmm", vmm.OpenVmm)):
            with self.subTest(name=name):
                self.assertIs(type(vmm.detect_vmm(name)), backend)
                self.assertEqual(backend.__module__, "villain.vmm")

    def test_banner_uses_the_checkout_binary_without_changing_output(self):
        destination = io.StringIO()
        with patch.object(cli.subprocess, "check_output", return_value=b"banner\n") as run:
            cli.print_banner(destination)
        self.assertEqual(destination.getvalue(), "banner\n")
        run.assert_called_once_with(
            [str(ROOT / "target/init"), "--banner"], stderr=subprocess.DEVNULL)

    def test_test_ids_resolve_to_registered_spelling_in_request_order(self):
        self.assertEqual(
            cli.canonicalize_tests(["ex002", "EX001", "missing", "eX002"],
                                   ["Ex001", "EX002"]),
            (["EX002", "Ex001", "EX002"], ["missing"]))


class ConsoleTests(unittest.TestCase):
    def test_file_following_retains_split_utf8_and_the_final_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "console"
            path.write_bytes(b"First \xe2")
            destination = io.StringIO()
            follower = console.FileFollower(path, destination)
            follower.pump()
            self.assertEqual(destination.getvalue(), "First ")
            with path.open("ab") as stream:
                stream.write(b"\x82\xac\nLast \xff")
            follower.close()
            self.assertEqual(destination.getvalue(), "First \u20ac\nLast \ufffd")
            self.assertTrue(follower.stream.closed)
            self.assertEqual(path.read_bytes(), b"First \xe2\x82\xac\nLast \xff")

    def test_file_following_preserves_raw_console_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "console"
            content = b"Guest \xff\x00\n" * 1000
            path.write_bytes(content)
            destination = Mock()
            destination.buffer = io.BytesIO()
            follower = console.FileFollower(path, destination)
            follower.pump()
            follower.close()
            self.assertEqual(destination.buffer.getvalue(), content)
            destination.write.assert_not_called()

    def test_file_follower_closes_the_log_when_output_fails(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "console"
            path.write_bytes(b"Guest output\n")

            class BrokenOutput(io.StringIO):
                def write(self, text):
                    raise OSError("Console sink failed")

            follower = console.FileFollower(path, BrokenOutput())
            with self.assertRaisesRegex(OSError, "Console sink failed"):
                follower.close()
            self.assertTrue(follower.stream.closed)
            self.assertEqual(path.read_bytes(), b"Guest output\n")

    def test_drain_retains_bytes_and_streams_diagnostics_without_rewriting(self):
        content = b"diagnostic\xff\n" * 600
        captured = []
        live = io.BytesIO()
        with patch.object(console.sys, "stderr") as stderr:
            stderr.buffer = live
            console.drain_stream(io.BytesIO(content), captured, True)
        self.assertEqual(b"".join(captured), content)
        self.assertEqual(live.getvalue(), content)
        self.assertEqual(
            console.merge_streams("VV-RESULT token EX001 PASS\n", captured),
            "VV-RESULT token EX001 PASS\n\n" + content.decode(errors="replace"))

    def test_separate_capture_keeps_guest_markers_intact(self):
        guest = b"VV-RESULT token EX001 PASS\n" * 1000
        diagnostic = b"VMM diagnostic\n" * 1000
        command = [
            sys.executable, "-c",
            "import os\n"
            "for _ in range(1000):\n"
            "    os.write(1, b'VV-RESULT token EX001 PASS\\n')\n"
            "    os.write(2, b'VMM diagnostic\\n')\n"]
        with subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True) as child:
            errors = []
            reader = threading.Thread(
                target=console.drain_stream, args=(child.stderr, errors, False))
            reader.start()
            try:
                output = child.stdout.read()
                child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    process.kill_process(child)
                reader.join(timeout=5)
            self.assertFalse(reader.is_alive())
            self.assertEqual(output, guest)
            self.assertEqual(b"".join(errors), diagnostic)


class ProcessTests(unittest.TestCase):
    def test_registered_processes_are_killed_and_reaped(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        process.register_process(child)
        try:
            process.kill_all_processes()
            self.assertEqual(child.poll(), -signal.SIGKILL)
        finally:
            process.unregister_process(child)
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_killing_an_already_reaped_process_remains_safe(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"], start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        child.wait(timeout=5)
        process.kill_process(child)
        self.assertEqual(child.returncode, 0)


if __name__ == "__main__":
    unittest.main()
