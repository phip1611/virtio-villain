#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generic suite API tests; no external suite or hypervisor is required."""

import contextlib
import gzip
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
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


class ControlTests(unittest.TestCase):
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

    def test_no_implicit_source_wildcard(self):
        result = subprocess.run(["make", "-s", "-C", str(ROOT), "appliance",
                                 f"APPLIANCE_TARGET={self.root / 'empty'}"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("set APPLIANCE_SRCS explicitly", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
