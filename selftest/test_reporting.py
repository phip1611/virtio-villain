#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exact human output shared by the virtio and external suite runners."""

import io
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/python"))
from villain import reporting  # noqa: E402


class ReportingTests(unittest.TestCase):
    def test_standard_result_columns(self):
        row = reporting.format_test_line(
            "B0001", "PASS", ("hello world", "1.3", "5.2"), 11)
        self.assertEqual(row, "[PASS]   B0001   hello world 1.3  5.2         ")

    def test_external_suite_uses_the_same_columns_without_fake_spec_fields(self):
        row = reporting.format_test_line(
            "ML001", "PASS", ("create partition", "", ""), 16)
        self.assertEqual(row[:17], "[PASS]   ML001   ")
        self.assertEqual(row[17:33], "create partition")
        self.assertEqual(row[33:], " " * 18)

    def test_width_truncation_and_color_preserve_visible_columns(self):
        info = ("x" * 100, "1.3", "5.2")
        plain = reporting.format_test_line("B0001", "FAIL", info, 100)
        colored = reporting.format_test_line("B0001", "FAIL", info, 100, color=True)
        self.assertEqual(len(plain), 80)
        self.assertEqual(plain[17:62], "x" * 44 + "~")
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", colored), plain)
        self.assertTrue(colored.startswith("\033[31m[FAIL]  \033[0m"))
        self.assertEqual(reporting.desc_column_width(200, 40, 35), 40)
        self.assertEqual(reporting.desc_column_width(60, 40, 35), 25)
        self.assertEqual(reporting.desc_column_width(30, 40, 35), 10)

    def test_summary_and_infrastructure_status(self):
        counts = dict.fromkeys(reporting.STYLES, 1)
        counts["PASS"] = 2
        expected = ("\n2/9 tests passed, 1 failed, 1 rejected, 1 wedged, "
                    "1 skipped, 1 xfail, 1 xpass, 1 infra, 1 retried")
        self.assertEqual(reporting.format_summary(counts, 9), expected)
        colored = reporting.format_summary(counts, 9, color=True)
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", colored), expected)
        self.assertEqual(reporting.format_summary({"PASS": 7}, 7), "\n7/7 tests passed")

    def test_progress_and_failure_details(self):
        self.assertEqual(
            reporting.format_progress({"PASS": 2}, 2, 7, "ML002"),
            "\r\033[K2/7 \033[32m2P\033[0m | ML002")
        self.assertEqual(reporting.format_detail("expected 0, actual -1"),
                         "       expected 0, actual -1")
        self.assertEqual(
            reporting.format_failure_list([("B0001", "first case")]),
            "\nFAILED:\n  B0001   first case")
        self.assertEqual(
            reporting.format_failure_list([("ML001", "first case")], "INFRA"),
            "\nINFRASTRUCTURE:\n  ML001   first case")
        self.assertEqual(reporting.terminal_width(io.StringIO()), 80)
