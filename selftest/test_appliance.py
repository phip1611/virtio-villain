#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generic suite API tests; no external suite or hypervisor is required."""

import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import appliance, initramfs  # noqa: E402


def output(verdict="PASS"):
    counts = {"PASS": "1 0 0 0", "FAIL": "0 1 0 0", "SKIP": "0 0 1 1"}
    code = {"PASS": 0, "FAIL": 1, "SKIP": 2}[verdict]
    return ("VV-BEGIN token example-v1\nVV-RUN token EX001\n"
            f"VV-RESULT token EX001 {verdict}\n"
            f"VV-SUMMARY token {counts[verdict]}\nVV-END token {code}\n")


def parse(text, selected=("EX001",)):
    return appliance.parse_results(text, selected, "token", "example-v1")


class ResultTests(unittest.TestCase):
    def test_pass_fail_and_unsupported(self):
        for verdict, status in (("PASS", 0), ("FAIL", 1), ("SKIP", 2)):
            with self.subTest(verdict=verdict):
                self.assertEqual(parse(output(verdict))["status"], status)

    def test_missing_prerequisite(self):
        text = ("VV-BEGIN token example-v1\nvv-infra prerequisite missing\n"
                "VV-SUMMARY token 0 0 0 1\nVV-END token 2\n")
        self.assertEqual(parse(text)["status"], 2)

    def test_invalid_transcripts(self):
        valid = output()
        broken = [
            "", valid.replace("example-v1", "unreviewed"),
            valid.replace("token", "stale"), valid.replace("VV-END token 0\n", ""),
            valid.replace("VV-RUN token EX001\n", ""),
            valid.replace("VV-RESULT token EX001 PASS", "VV-RESULT token EX002 PASS"),
            valid.replace("1 0 0 0", "0 0 0 0"),
            valid.replace("VV-END token 0", "VV-END token 1"),
            valid + "VV-END token 0\n", valid.replace("VV-SUMMARY", "VV-UNKNOWN"),
            valid.replace("1 0 0 0", "-1 0 0 0"),
            valid.replace("VV-RESULT token EX001 PASS",
                          "VV-RESULT token EX001 PASS\nVV-RESULT token EX001 PASS"),
            output("SKIP").replace("0 0 1 1", "0 0 1 0").replace("token 2\n", "token 0\n"),
        ]
        for text in broken:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse(text)
        with self.assertRaises(RuntimeError):
            parse(valid, ("EX001", "EX002"))

    def test_custom_protocol_prefix(self):
        result = appliance.parse_results(output().replace("VV-", "DEMO-"),
                                         ["EX001"], "token", "example-v1", "DEMO")
        self.assertEqual(result["status"], 0)

    def test_numeric_options(self):
        suite = appliance.Suite("example", "example-v1", Path("init"), Path("archive"))
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                appliance.parse_args(suite, ["--list", "--timeout", value])
        for value in ("0", "-1", "nan", "1.5"):
            with self.subTest(jobs=value), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                appliance.parse_args(suite, ["--list", "--jobs", value])
        self.assertEqual(appliance.parse_args(suite, ["--list"]).jobs, 1)
        self.assertEqual(appliance.parse_args(suite, ["--list", "-j", "16"]).jobs, 16)

    def test_catalog_validation(self):
        for text in ("", "EX001 missing-tab", "EX001\ta\nEX001\tb", "invalid id\ta"):
            with self.subTest(text=text), \
                 patch.object(appliance, "executable", return_value="init"), \
                 patch.object(appliance.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0, text)), \
                 self.assertRaises(RuntimeError):
                appliance.catalog("init")

    def test_plugin_validation_errors_are_infrastructure(self):
        modules = (
            "def create_suite(): return object()\n",
            "# no factory\n",
            "def broken syntax\n",
            "from lib.appliance import Suite\n"
            "def create_suite(): return Suite('example', 'invalid contract', '.', '.')\n",
        )
        for text in modules:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as work:
                plugin = Path(work) / "suite.py"
                plugin.write_text(text)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(appliance.main(["--suite", str(plugin), "--list"]), 2)


class PackageTests(unittest.TestCase):
    def test_static_elf_and_reproducible_archive(self):
        elf = bytearray(64)
        elf[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", elf, 54, 56, 0)
        packed = initramfs.archive(elf)
        self.assertEqual(packed, initramfs.archive(elf))
        data = gzip.decompress(packed)
        offset = 0
        entries = {}
        while offset < len(data):
            self.assertEqual(data[offset:offset + 6], b"070701")
            fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16)
                      for i in range(13)]
            offset += 110
            name = data[offset:offset + fields[11] - 1].decode()
            offset = (offset + fields[11] + 3) & ~3
            entries[name] = (fields[1], data[offset:offset + fields[6]])
            offset = (offset + fields[6] + 3) & ~3
        self.assertEqual(set(entries), {".", "dev", "proc", "sys", "init", "TRAILER!!!"})
        self.assertEqual(entries["init"], (0o100755, elf))

    def test_reject_dynamic_or_invalid_init(self):
        elf = bytearray(120)
        elf[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<Q", elf, 32, 64)
        struct.pack_into("<HH", elf, 54, 56, 1)
        struct.pack_into("<I", elf, 64, 3)
        for value in (b"not ELF", elf, elf[:70]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                initramfs.archive(value)


@unittest.skipUnless(shutil.which("cc"), "C compiler required")
class ApplianceStartupTests(unittest.TestCase):
    def test_host_shutdown_waits_after_sync_without_poweroff(self):
        source = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <unistd.h>
#include "lib/test_runner.h"

pid_t __wrap_getpid(void) { return 1; }
int __wrap_mount(const char *source, const char *target, const char *type,
                 unsigned long flags, const void *data)
{
    assert(source && target && type && flags == 0 && data == NULL);
    return 0;
}
FILE *__wrap_fopen(const char *path, const char *mode)
{
    assert(!strcmp(path, "/proc/cmdline") && !strcmp(mode, "r"));
    FILE *stream = tmpfile();
    assert(stream);
    fprintf(stream, "vv.run=token vv.tests=EX001 %s\n", getenv("VV_TEST_CMDLINE"));
    rewind(stream);
    return stream;
}
void __wrap_sync(void) { puts("SYNC"); }
int __wrap_reboot(int command)
{
    assert(command == RB_POWER_OFF);
    puts("POWEROFF");
    return -1;
}
int __wrap_pause(void) { exit(0); }
void vv_list_tests(const struct vv_test_suite *suite) { assert(suite); }
int vv_run_suite(const struct vv_test_suite *suite, const char *selection,
                 const char *token, int ready)
{
    assert(suite && !strcmp(selection, "EX001") && !strcmp(token, "token"));
    printf("RUN ready=%d\n", ready);
    return ready ? 0 : 2;
}
int main(int argc, char **argv)
{
    const struct vv_test_suite suite = {0};
    return vv_appliance_main(argc, argv, &suite);
}
'''
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            fixture, binary = root / "startup.c", root / "startup"
            fixture.write_text(source)
            wrappers = ("getpid", "mount", "fopen", "sync", "reboot", "pause")
            subprocess.run([
                "cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT),
                str(fixture), str(ROOT / "lib/appliance_init.c"),
                *(f"-Wl,--wrap={name}" for name in wrappers), "-o", str(binary),
            ], check=True, capture_output=True)
            for cmdline, ready, host in (
                    ("", 1, False), ("vv.shutdown=poweroff", 1, False),
                    ("vv.shutdown=host", 1, True), ("vv.shutdown=invalid", 0, False),
                    ("vv.shutdown=host vv.shutdown=host", 0, True)):
                with self.subTest(cmdline=cmdline):
                    result = subprocess.run([str(binary)], capture_output=True, text=True,
                                            env={**os.environ, "VV_TEST_CMDLINE": cmdline},
                                            check=True, timeout=5)
                    self.assertIn(f"RUN ready={ready}\nSYNC\n", result.stdout)
                    self.assertEqual("POWEROFF" in result.stdout, not host)
                    self.assertEqual("SYNC\nvv-appliance-stop token\n" in result.stdout, host)


class HostShutdownTests(unittest.TestCase):
    def test_validated_completion_and_failure_paths(self):
        for mode in ("PASS", "FAIL", "invalid", "missing", "stale", "duplicate",
                     "panic", "exit", "crash"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                image = root / "image"
                image.write_bytes(b"image")
                transcript = output("FAIL" if mode == "FAIL" else "PASS")
                if mode == "invalid":
                    transcript = transcript.replace("VV-END token 0\n", "")
                marker = "vv-appliance-stop token\n"
                if mode in ("missing", "exit"):
                    marker = ""
                elif mode == "stale":
                    marker = "vv-appliance-stop stale\n"
                elif mode == "duplicate":
                    marker *= 2
                elif mode == "panic":
                    marker += "Kernel panic - not syncing: Fatal exception\n"
                script = (
                    "import signal, sys\n"
                    f"print(({transcript + marker!r}).replace('token', sys.argv[1]), "
                    "end='', flush=True)\n"
                    + ("sys.exit(3)\n" if mode == "crash" else
                       "sys.exit(0)\n" if mode == "exit" else "signal.pause()\n")
                )

                class ExampleSuite(appliance.Suite):
                    def build_command(self, args, selected, token):
                        return [sys.executable, "-u", "-c", script, token]

                suite = ExampleSuite("example", "example-v1", root / "init", image,
                                     host_shutdown=True)
                args = appliance.parse_args(suite, [
                    "--kernel", str(image), "--timeout", "1",
                    "--output", str(root / "results")])
                with contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()):
                    status = appliance.launch(suite, args, ["EX001"])
                expected = 0 if mode == "PASS" else 1 if mode == "FAIL" else 2
                self.assertEqual(status, expected)
                run = json.loads((root / "results/results.json").read_text())["runs"][0]
                self.assertEqual(run.get("host_shutdown", False), mode in ("PASS", "FAIL"))
                if mode in ("missing", "stale"):
                    self.assertTrue(run["timed_out"])
                elif mode == "panic":
                    self.assertIn("kernel panic", run["error"])


class ParallelTests(unittest.TestCase):
    def test_interruption_cleans_up_active_vms_and_stops_new_boots(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            image = root / "image"
            image.write_bytes(b"image")
            script = (
                "import os, signal, sys\n"
                "from pathlib import Path\n"
                f"(Path({str(root)!r}) / sys.argv[1]).write_text(str(os.getpid()))\n"
                "signal.pause()\n"
            )

            class ExampleSuite(appliance.Suite):
                def build_command(self, args, selected, token):
                    return [sys.executable, "-u", "-c", script, selected[0]]

            suite = ExampleSuite("example", "example-v1", root / "init", image)
            args = appliance.parse_args(suite, [
                "--kernel", str(image), "--jobs", "2", "--timeout", "5",
                "--output", str(root / "results")])
            original_wait = appliance.wait
            interrupted = False

            def interrupt_once(*args, **kwargs):
                nonlocal interrupted
                if not interrupted:
                    deadline = time.monotonic() + 4
                    while not all((root / name).exists() for name in ("EX001", "EX002")):
                        if time.monotonic() >= deadline:
                            self.fail("Parallel launchers did not start")
                        time.sleep(0.01)
                    interrupted = True
                    raise KeyboardInterrupt
                return original_wait(*args, **kwargs)

            with patch.object(appliance, "wait", side_effect=interrupt_once), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                status = appliance.launch(suite, args, ["EX001", "EX002", "EX003"])
            self.assertEqual(status, 2)
            self.assertFalse((root / "EX003").exists())
            report = json.loads((root / "results/results.json").read_text())
            self.assertTrue(report["interrupted"])
            self.assertEqual(len(report["runs"]), 2)
            for run in report["runs"]:
                self.assertTrue(run["interrupted"])
                pid = int((root / run["test"]).read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_timeout_does_not_block_later_parallel_boots(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            image = root / "image"
            image.write_bytes(b"image")
            script = (
                "import sys, time\n"
                "name, token = sys.argv[1:]\n"
                "if name == 'EX001': time.sleep(5)\n"
                f"print({output()!r}.replace('token', token).replace('EX001', name), "
                "end='', flush=True)\n"
            )

            class ExampleSuite(appliance.Suite):
                def build_command(self, args, selected, token):
                    return [sys.executable, "-u", "-c", script, selected[0], token]

            suite = ExampleSuite("example", "example-v1", root / "init", image)
            args = appliance.parse_args(suite, [
                "--kernel", str(image), "--jobs", "2", "--timeout", "1",
                "--output", str(root / "results")])
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                status = appliance.launch(suite, args, ["EX001", "EX002", "EX003"])
            self.assertEqual(status, 2)
            runs = json.loads((root / "results/results.json").read_text())["runs"]
            self.assertTrue(runs[0]["timed_out"])
            self.assertEqual([run["status"] for run in runs], [2, 0, 0])

    def test_concurrent_boot_limit_repeats_and_failure_aggregation(self):
        for jobs in (2, 16):
            with self.subTest(jobs=jobs), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                image = root / "image"
                image.write_bytes(b"image")
                script = (
                    "import json, signal, sys, time\n"
                    "from pathlib import Path\n"
                    f"root = Path({str(root)!r})\n"
                    "name, token = sys.argv[1:]\n"
                    "iteration = int(token.rsplit('-', 2)[1]) - 1\n"
                    "index = iteration * 4 + int(name[2:]) - 1\n"
                    f"group = index // {jobs}\n"
                    "start = time.monotonic_ns()\n"
                    "(root / f'ready-{group}-{index}').touch()\n"
                    "deadline = time.monotonic() + 4\n"
                    f"while len(list(root.glob(f'ready-{{group}}-*'))) < min({jobs}, 8):\n"
                    "    if time.monotonic() >= deadline: sys.exit(3)\n"
                    "    time.sleep(0.01)\n"
                    "(root / f'interval-{index}.json').write_text("
                    "json.dumps([start, time.monotonic_ns()]))\n"
                    f"transcript = {output()!r}\n"
                    "if name == 'EX001':\n"
                    f"    transcript = {output('FAIL')!r}\n"
                    "print(transcript.replace('token', token).replace('EX001', name), "
                    "end='', flush=True)\n"
                    "print(f'vv-appliance-stop {token}', flush=True)\n"
                    "signal.pause()\n"
                )

                class ExampleSuite(appliance.Suite):
                    def build_command(self, args, selected, token):
                        assert len(selected) == 1
                        return [sys.executable, "-u", "-c", script, selected[0], token]

                suite = ExampleSuite("example", "example-v1", root / "init", image,
                                     host_shutdown=True)
                args = appliance.parse_args(suite, [
                    "--kernel", str(image), "--jobs", str(jobs), "--repeat", "2",
                    "--timeout", "5", "--output", str(root / "results")])
                selected = ["EX001", "EX002", "EX003", "EX004"]
                with contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()):
                    status = appliance.launch(suite, args, selected)
                self.assertEqual(status, 1)
                report = json.loads((root / "results/results.json").read_text())
                self.assertEqual(report["jobs"], jobs)
                self.assertEqual([run["test"] for run in report["runs"]], selected * 2)
                self.assertEqual(len({run["run_id"] for run in report["runs"]}), 8)
                self.assertEqual(len({run["console"] for run in report["runs"]}), 8)
                for run in report["runs"]:
                    self.assertTrue(run["host_shutdown"])
                    self.assertEqual(run["status"], 1 if run["test"] == "EX001" else 0)
                events = []
                for path in root.glob("interval-*.json"):
                    start, end = json.loads(path.read_text())
                    events.extend(((start, 1), (end, -1)))
                self.assertEqual(len(events), 16)
                active = peak = 0
                for _, change in sorted(events):
                    active += change
                    peak = max(peak, active)
                self.assertEqual(active, 0)
                self.assertEqual(peak, min(jobs, 8))

    def test_failed_control_arm_stops_scheduling(self):
        class ExampleSuite(appliance.Suite):
            def build_command(self, args, selected, token):
                return [sys.executable, token]

        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            image = root / "image"
            image.write_bytes(b"image")
            suite = ExampleSuite("example", "example-v1", root / "init", image)
            args = appliance.parse_args(suite, [
                "--kernel", str(image), "--jobs", "2", "--control", sys.executable,
                "--output", str(root / "results")])
            with patch.object(appliance, "control", side_effect=RuntimeError("arm failed")), \
                 patch.object(appliance.subprocess, "Popen") as popen, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                status = appliance.launch(suite, args, ["EX001", "EX002", "EX003"])
            self.assertEqual(status, 2)
            popen.assert_not_called()
            report = json.loads((root / "results/results.json").read_text())
            self.assertGreaterEqual(len(report["runs"]), 1)
            self.assertLessEqual(len(report["runs"]), 2)
            for run in report["runs"]:
                self.assertEqual(run["status"], 2)
                self.assertIn("arm failed", run["error"])


class ControlTests(unittest.TestCase):
    def test_each_case_has_its_own_boot_and_continues_after_guest_failure(self):
        class ExampleSuite(appliance.Suite):
            def build_command(self, args, selected, token):
                assert len(selected) == 1
                return [sys.executable, selected[0], token]

        for failure in ("assertion", "crash", "timeout", "incomplete"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                image = root / "image"
                image.write_bytes(b"image")
                suite = ExampleSuite("example", "example-v1", root / "init", image)
                args = appliance.parse_args(suite, [
                    "--kernel", str(image), "--initramfs", str(image), "--repeat", "2",
                    "--output", str(root / "results")])
                boots = []

                def popen(command, **kwargs):
                    name, token = command[-2:]
                    boots.append((name, token))
                    verdict = "FAIL" if name == "EX001" and failure == "assertion" else "PASS"
                    text = output(verdict).replace("token", token).replace("EX001", name)
                    if name == "EX001" and failure == "incomplete":
                        text = ""
                    kwargs["stdout"].write(text.encode())
                    proc = Mock()
                    proc.poll.return_value = 0
                    proc.wait.return_value = 1 if name == "EX001" and failure == "crash" else 0
                    if name == "EX001" and failure == "timeout":
                        proc.poll.return_value = None
                        proc.wait.side_effect = [subprocess.TimeoutExpired(command, 0.01), 0]
                    return proc

                with patch.object(appliance.subprocess, "Popen", side_effect=popen), \
                     contextlib.redirect_stdout(io.StringIO()) as progress, \
                     contextlib.redirect_stderr(io.StringIO()):
                    status = appliance.launch(suite, args, ["EX001", "EX002"])
                self.assertEqual(status, 1 if failure == "assertion" else 2)
                self.assertEqual([name for name, _ in boots],
                                 ["EX001", "EX002", "EX001", "EX002"])
                self.assertEqual(len({token for _, token in boots}), 4)
                self.assertEqual(progress.getvalue().count("[RUN]"), 4)
                self.assertIn("[RUN] EX002 (run 2, timeout 120s)", progress.getvalue())
                report = json.loads((root / "results/results.json").read_text())
                self.assertEqual(len(report["runs"]), 4)
                self.assertEqual(len({run["console"] for run in report["runs"]}), 4)
                for run in report["runs"]:
                    if run["test"] == "EX002":
                        self.assertEqual(run["tests"], {"EX002": "PASS"})
                        self.assertEqual(run["status"], 0)
                self.assertEqual(report["status"], status)

    def test_launch_failure_and_recovery_controls(self):
        class ExampleSuite(appliance.Suite):
            def build_command(self, args, selected, token):
                return [sys.executable, token]

        modes = ("PASS", "FAIL", "infra", "timeout", "recovery_failure", "arm_failure",
                 "disarm_failure")
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as work:
                root = Path(work)
                kernel, image = root / "kernel", root / "archive"
                kernel.write_bytes(b"kernel")
                image.write_bytes(b"archive")
                suite = ExampleSuite("example", "example-v1", root / "init", image)
                args = appliance.parse_args(suite, [
                    "--kernel", str(kernel), "--control", sys.executable,
                    "--output", str(root / "results")])
                actions = []

                def fake_control(command, action, token, timeout, log):
                    actions.append(action)
                    if (mode, action) in (("recovery_failure", "recover"),
                                          ("arm_failure", "arm"), ("disarm_failure", "disarm")):
                        raise RuntimeError("control failed")

                def popen(command, **kwargs):
                    text = "" if mode in ("infra", "recovery_failure") else output(
                        "FAIL" if mode == "FAIL" else "PASS")
                    kwargs["stdout"].write(text.replace("token", command[-1]).encode())
                    proc = Mock()
                    proc.wait.return_value = 0
                    proc.poll.return_value = 0
                    if mode == "timeout":
                        proc.wait.side_effect = subprocess.TimeoutExpired(command, 0.01)
                        proc.poll.return_value = None
                    return proc

                with patch.object(appliance, "control", side_effect=fake_control), \
                     patch.object(appliance.subprocess, "Popen", side_effect=popen), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()):
                    status = appliance.launch(suite, args, ["EX001"])
                self.assertEqual(status, 0 if mode == "PASS" else 1 if mode == "FAIL" else 2)
                expected = {
                    "PASS": ["arm", "disarm"], "FAIL": ["arm", "disarm"],
                    "infra": ["arm", "recover", "disarm"],
                    "timeout": ["arm", "recover", "disarm"],
                    "recovery_failure": ["arm", "recover"], "arm_failure": ["arm"],
                    "disarm_failure": ["arm", "disarm"],
                }
                self.assertEqual(actions, expected[mode])
                report = json.loads((root / "results/results.json").read_text())
                self.assertEqual(report["status"], status)


@unittest.skipUnless(shutil.which("cc"), "C compiler required")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="vv-external-suite-")
        cls.root = Path(cls.work.name)
        cls.build = cls.root / "build"
        subprocess.run(["make", "-s", "-C", str(ROOT), "appliance-initramfs", "CC=cc",
                        f"APPLIANCE_SRCS={ROOT / 'selftest/test_appliance.c'}",
                        f"APPLIANCE_TARGET={cls.build}"],
                       check=True, capture_output=True)
        cls.binary = cls.build / "init"
        cls.plugin = cls.root / "suite.py"
        cls.plugin.write_text(
            "from pathlib import Path\n"
            "from lib.appliance import Suite\n"
            "class Example(Suite):\n"
            "    def build_command(self, args, selected, token):\n"
            "        return [str(args.binary), '--launcher', 'pass', ','.join(selected), token]\n"
            "def create_suite():\n"
            f"    return Example('example', 'example-v1', Path({str(cls.binary)!r}), "
            f"Path({str(cls.build / 'initramfs.cpio.gz')!r}))\n")

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_registry_execution_cleanup_and_selection(self):
        for mode, code in (("pass", 0), ("fail", 1), ("skip", 2), ("infra", 2), ("cleanup", 1)):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.binary), mode], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                parsed = parse(result.stdout, ("EX001", "EX002"))
                self.assertEqual(parsed["status"], code)
        for selection in ("EX002", "EX001", "EX002,EX001"):
            with self.subTest(selection=selection):
                result = subprocess.run([str(self.binary), "pass", selection],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(set(parse(result.stdout, selection.split(","))["tests"]),
                                 set(selection.split(",")))

    def test_invalid_selection_executes_nothing(self):
        for selection in ("", ",EX001", "EX001,", "EX001,,EX002", "EX001,EX001", "UNKNOWN"):
            with self.subTest(selection=selection):
                result = subprocess.run([str(self.binary), "pass", selection],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(parse(result.stdout)["tests"], {})

    def test_existing_runner_loads_plugin_and_preserves_selection(self):
        listed = subprocess.run([str(ROOT / "run"), "--suite", str(self.plugin), "--list"],
                                capture_output=True, text=True, check=True)
        self.assertIn("EX001\t", listed.stdout)
        self.assertIn("EX002\t", listed.stdout)
        directory = self.root / "results"
        result = subprocess.run([str(ROOT / "run"), "--suite", str(self.plugin),
                                 "--kernel", str(self.binary), "--output", str(directory),
                                 "--repeat", "2", "EX002"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((directory / "results.json").read_text())
        self.assertEqual(len(report["runs"]), 2)
        for run in report["runs"]:
            self.assertEqual(run["tests"], {"EX002": "PASS"})

    def test_existing_runner_boots_each_selected_case_separately(self):
        directory = self.root / "isolated-results"
        result = subprocess.run([str(ROOT / "run"), "--suite", str(self.plugin),
                                 "--kernel", str(self.binary), "--output", str(directory),
                                 "--repeat", "2", "EX001", "EX002"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((directory / "results.json").read_text())
        self.assertEqual([run["test"] for run in report["runs"]],
                         ["EX001", "EX002", "EX001", "EX002"])
        for run in report["runs"]:
            self.assertEqual(run["tests"], {run["test"]: "PASS"})
            self.assertEqual(run["command"][-2], run["test"])

    def test_no_implicit_source_wildcard(self):
        result = subprocess.run(["make", "-s", "-C", str(ROOT), "appliance",
                                 f"APPLIANCE_TARGET={self.root / 'empty'}"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set APPLIANCE_SRCS explicitly", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
