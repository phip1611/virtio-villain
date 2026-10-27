#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared runner primitives without VM or device prerequisites."""

import io
import os
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
            "from unittest.mock import patch\n"
            "entry = runpy.run_path(sys.argv[1], run_name='runner_import_test')\n"
            "from villain import cli, console, initramfs, vmm\n"
            "assert not any(name.startswith('vv_run') for name in sys.modules)\n"
            "if sys.argv[1].endswith('/run'):\n"
            "    assert entry['detect_vmm'] is vmm.detect_vmm\n"
            "    assert entry['print_banner'] is cli.print_banner\n"
            "    assert entry['_canonicalize_tests'] is cli.canonicalize_tests\n"
            "    assert entry['drain_stream'] is console.drain_stream\n"
            "elif sys.argv[1].endswith('/run-fuzz'):\n"
            "    assert entry['print_banner'] is cli.print_banner\n"
            "    assert entry['ctty_preexec'] is console.ctty_preexec\n"
            "    with patch.object(initramfs, 'build') as build:\n"
            "        entry['build_initramfs']('fuzz', 'archive')\n"
            "        build.assert_called_once_with('fuzz', 'archive', "
            "strip='strip', compresslevel=1)\n"
            "    with patch.object(vmm, 'fetch_kernel', return_value='/cache/kernel') as fetch:\n"
            "        with patch.object(entry['subprocess'], 'run', "
            "side_effect=AssertionError('Another runner must not be executed')):\n"
            "            assert entry['_fetch_kernel']() == '/cache/kernel'\n"
            "        fetch.assert_called_once_with()\n"
            "    with patch.object(vmm, 'fetch_kernel', "
            "side_effect=RuntimeError('Bad checksum')):\n"
            "        try: entry['_fetch_kernel']()\n"
            "        except SystemExit as error: assert str(error) == 'Bad checksum'\n"
            "        else: raise AssertionError('Acquisition error was ignored')\n"
            "    with patch.object(vmm.CloudHypervisor, 'build_launch_cmd', "
            "return_value=['/vmm']) as launch:\n"
            "        command = entry['_fuzz_vmm_cmd']("
            "'/vmm', '/kernel', '/initramfs', '/disk', '/pmem', '/work', 2, '128M')\n"
            "        launch.assert_called_once_with(\n"
            "            vmm.LaunchConfig(cpus=2, memory='128M',\n"
            "                kernel='/kernel', initramfs='/initramfs',\n"
            "                cmdline='console=hvc0 initcall_blacklist=virtio_blk_init'),\n"
            "            memory_hotplug_size='256M', memory_hotplug_method='virtio-mem',\n"
            "            serial='off', console='null', seccomp=False)\n"
            "        assert command[0] == '/vmm' and '--disk' in command\n"
            "else:\n"
            "    assert entry['run_guest'].__defaults__ == (vmm,)\n")
        for entry in ("run", "run-fuzz", "run-perf"):
            with self.subTest(entry=entry):
                result = subprocess.run(
                    [sys.executable, "-c", check, str(ROOT / entry)],
                    cwd="/", capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_fetch_kernel_utility_prints_only_a_path_or_an_explicit_error(self):
        check = (
            "import runpy, sys\n"
            "from unittest.mock import patch\n"
            f"sys.path.insert(0, {str(ROOT / 'lib/python')!r})\n"
            "from villain import vmm\n"
            "entry, mode = sys.argv[1:]\n"
            "options = ({'return_value': '/cache/verified/kernel'} if mode == 'pass' else\n"
            "           {'side_effect': RuntimeError('Kernel checksum failed')})\n"
            "sys.argv = [entry, 'fetch-kernel']\n"
            "with patch.object(vmm, 'fetch_kernel', **options):\n"
            "    runpy.run_path(entry, run_name='__main__')\n")
        for mode in ("pass", "fail"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-c", check, str(ROOT / "run"), mode],
                    cwd="/", capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if mode == "pass" else 1)
                self.assertEqual(result.stdout,
                                 "/cache/verified/kernel\n" if mode == "pass" else "")
                self.assertEqual(result.stderr,
                                 "" if mode == "pass" else "Kernel checksum failed\n")

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


class LaunchCommandTests(unittest.TestCase):
    def test_portable_direct_boot_configuration_works_across_all_backends(self):
        config = vmm.LaunchConfig(
            cpus=2, memory="256M", kernel=Path("/kernel"),
            initramfs=Path("/initramfs"), cmdline="console=ttyS0")
        expected = (
            (vmm.CloudHypervisor, [
                "/vmm", "--kernel", "/kernel", "--initramfs", "/initramfs",
                "--cmdline", "console=ttyS0", "--cpus", "boot=2", "--memory", "size=256M"]),
            (vmm.Qemu, [
                "/vmm", "-kernel", "/kernel", "-initrd", "/initramfs",
                "-append", "console=ttyS0", "-m", "256M", "-smp", "2"]),
            (vmm.QemuMicrovm, [
                "/vmm", "-M", "microvm,rtc=off", "-global", "virtio-mmio.force-legacy=false",
                "-kernel", "/kernel", "-initrd", "/initramfs",
                "-append", "console=ttyS0", "-m", "256M", "-smp", "2"]),
            (vmm.OpenVmm, [
                "/vmm", "-k", "/kernel", "-r", "/initramfs",
                "-c", "console=ttyS0", "-m", "256M", "-p", "2"]))
        for backend_type, command in expected:
            with self.subTest(backend=backend_type.name), \
                 patch.object(vmm.platform, "machine", return_value="x86_64"), \
                 patch.dict(os.environ, {"VV_CH_DEBUG": "1"}), \
                 patch.object(vmm.CloudHypervisor, "_next_cid",
                              side_effect=AssertionError("Fixture CID must not be allocated")), \
                 patch.object(vmm.Qemu, "_next_cid",
                              side_effect=AssertionError("Fixture CID must not be allocated")):
                self.assertEqual(backend_type("/vmm").build_launch_cmd(config), command)

    def test_portable_settings_reject_backend_cpu_syntax_and_invalid_resources(self):
        for cpus in ("boot=2", 0, -1, True):
            with self.subTest(cpus=cpus), self.assertRaisesRegex(ValueError, "CPU count"):
                vmm.LaunchConfig(cpus=cpus, memory="256M")
        for memory in ("", " ", None):
            with self.subTest(memory=memory), self.assertRaisesRegex(ValueError, "Memory size"):
                vmm.LaunchConfig(cpus=2, memory=memory)

    def test_backend_launch_support_must_be_explicit(self):
        config = vmm.LaunchConfig(cpus=2, memory="256M")
        with self.assertRaisesRegex(NotImplementedError, "launch command building"):
            vmm.VmmBackend("/unknown-vmm").build_launch_cmd(config)
        config = vmm.LaunchConfig(cpus=2, memory="256M", firmware=Path("/firmware"))
        with self.assertRaisesRegex(ValueError, "openvmm firmware boot"):
            vmm.OpenVmm("/vmm").build_launch_cmd(config)

    def test_qemu_firmware_launch_has_no_direct_boot_or_virtio_fixtures(self):
        config = vmm.LaunchConfig(cpus=2, memory="256M", firmware=Path("/firmware"))
        with patch.object(vmm.platform, "machine", return_value="x86_64"):
            command = vmm.Qemu("/vmm").build_launch_cmd(config)
        self.assertEqual(command, [
            "/vmm", "-bios", "/firmware", "-m", "256M", "-smp", "2"])

    def test_qemu_launch_adapters_preserve_explicit_aarch64_platform_options(self):
        config = vmm.LaunchConfig(
            cpus=2, memory="256M", kernel=Path("/kernel"), initramfs=Path("/initramfs"))
        for backend_type in (vmm.Qemu, vmm.QemuMicrovm):
            with self.subTest(backend=backend_type.name), \
                 patch.object(vmm.platform, "machine", return_value="aarch64"):
                command = backend_type("/vmm").build_launch_cmd(
                    config, kvm=True, nographic=True, no_reboot=True)
                expected = ["/vmm", "-M", "virt", "-cpu", "host"]
                if backend_type is vmm.QemuMicrovm:
                    expected += ["-global", "virtio-mmio.force-legacy=false"]
                expected += [
                    "-enable-kvm", "-kernel", "/kernel", "-initrd", "/initramfs",
                    "-nographic", "-no-reboot", "-m", "256M", "-smp", "2"]
                self.assertEqual(command, expected)

    def test_all_virtio_builders_delegate_boot_and_resource_configuration(self):
        for backend_type in (vmm.CloudHypervisor, vmm.Qemu, vmm.QemuMicrovm, vmm.OpenVmm):
            with self.subTest(backend=backend_type.name), \
                 patch.object(vmm.platform, "machine", return_value="x86_64"):
                backend = backend_type("/vmm")
                with patch.object(backend, "build_launch_cmd", return_value=["/vmm"]) as launch:
                    command = backend.build_cmd(
                        "/kernel", "/initramfs", "/disk.raw", "console=ttyS0",
                        {"cpus": 2, "memory": "128M", "fuzz_device": "rng", "mmio_device": "rng"})
                launch.assert_called_once()
                self.assertEqual(launch.call_args.args, (vmm.LaunchConfig(
                    cpus=1 if backend_type is vmm.QemuMicrovm else 2, memory="128M",
                    kernel="/kernel", initramfs="/initramfs", cmdline="console=ttyS0"),))
                for flag in ("--kernel", "--cpus", "--memory", "-kernel", "-m", "-smp", "-k", "-p"):
                    self.assertNotIn(flag, command)

    def test_firmware_launch_has_no_implicit_virtio_fixtures_or_host_policy(self):
        backend = vmm.CloudHypervisor("/vmm")
        with patch.dict(os.environ, {"VV_CH_DEBUG": "1"}), \
             patch.object(backend, "_next_cid",
                          side_effect=AssertionError("Fixture CID must not be allocated")):
            command = backend.build_launch_cmd(
                vmm.LaunchConfig(cpus=2, memory="2G", firmware=Path("/boot/firmware.fd")),
                nested=True, serial="tty", console="off")
        self.assertEqual(command, [
            "/vmm", "--firmware", "/boot/firmware.fd",
            "--cpus", "boot=2,nested=on", "--memory", "size=2G",
            "--serial", "tty", "--console", "off"])

    def test_direct_boot_and_optional_debug_and_seccomp_are_explicit(self):
        backend = vmm.CloudHypervisor("/vmm")
        for seccomp in (False, True):
            with self.subTest(seccomp=seccomp):
                command = backend.build_launch_cmd(
                    vmm.LaunchConfig(
                        cpus=1, memory="128M", kernel=Path("/kernel"),
                        initramfs=Path("/initramfs"), cmdline="console=ttyS0"),
                    serial="off", console="null", seccomp=seccomp, debug=True)
                self.assertEqual(command, [
                    "/vmm", "-vv", "--kernel", "/kernel", "--initramfs", "/initramfs",
                    "--cmdline", "console=ttyS0", "--cpus", "boot=1",
                    "--memory", "size=128M", "--serial", "off", "--console", "null",
                    "--seccomp", "true" if seccomp else "false"])
                self.assertTrue(all(isinstance(argument, str) for argument in command))

    def test_virtio_builder_preserves_queue_limits_and_fixture_configuration(self):
        backend = vmm.CloudHypervisor("/vmm")
        options = {
            "cpus": 1, "memory": "256M", "blk_queues": 8, "net_queues": 4,
            "pmem_path": "/pmem", "fs_socket": "/fs", "api_socket": "/api"}
        with patch.dict(os.environ, {"VV_CH_DEBUG": ""}), \
             patch.object(backend, "_next_cid", return_value=23):
            command = backend.build_cmd(
                "/kernel", "/initramfs", "/disk.raw", "console=ttyS0", options)
        self.assertEqual(command, [
            "/vmm", "--kernel", "/kernel", "--initramfs", "/initramfs",
            "--cmdline", "console=ttyS0", "--cpus", "boot=8",
            "--memory", "size=256M,shared=on,hotplug_size=256M,hotplug_method=virtio-mem",
            "--serial", "tty", "--console", "null", "--seccomp", "false",
            "--disk", "path=/disk.raw,image_type=raw,num_queues=8,iommu=virtio",
            "--net", "tap=,num_queues=4", "--vsock", "cid=23,socket=/disk.vsock",
            "--balloon", "size=0", "--watchdog", "--rng", "src=/dev/urandom",
            "--pmem", "file=/pmem,size=128M,discard_writes=on",
            "--fs", "tag=vvfs,socket=/fs,num_queues=1,queue_size=128",
            "--api-socket", "path=/api"])
        self.assertEqual(options["cpus"], 1)


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
