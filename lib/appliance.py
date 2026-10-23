# SPDX-License-Identifier: Apache-2.0
"""Shared appliance orchestration for explicitly loaded external test suites."""

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid


@dataclass
class Suite:
    """Suite policy; the framework owns selection, execution and result handling."""

    name: str
    contract: str
    binary: Path
    initramfs: Path
    protocol_prefix: str = "VV"
    requires_control: bool = False

    def add_arguments(self, parser):
        """Register suite-specific launch options without changing the core CLI."""

    def preflight(self, args):
        """Raise an actionable error when host prerequisites are missing."""

    def build_command(self, args, selected, run_id):
        """Return an argv list; stdout/stderr must include the guest console."""
        raise NotImplementedError


def load_suite(path):
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("vv_external_" + uuid.uuid4().hex, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load suite module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
        suite = module.create_suite()
    finally:
        sys.path.pop(0)
        sys.modules.pop(spec.name, None)
    if not isinstance(suite, Suite):
        raise RuntimeError("Suite create_suite() must return lib.appliance.Suite")
    if (not suite.name or not re.fullmatch(r"\S+", suite.contract)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", suite.protocol_prefix)):
        raise RuntimeError("Invalid suite name, contract, or protocol prefix")
    suite.binary = Path(suite.binary).resolve()
    suite.initramfs = Path(suite.initramfs).resolve()
    return suite


def executable(value):
    path = shutil.which(str(value))
    if path is None:
        raise RuntimeError(f"Executable not found: {value}")
    return str(Path(path).resolve())


def catalog(binary):
    result = subprocess.run([executable(binary), "--list"], check=True,
                            capture_output=True, text=True, timeout=10)
    tests = {}
    for line in result.stdout.splitlines():
        name, separator, description = line.partition("\t")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name) or name in tests:
            raise RuntimeError(f"Invalid test catalog entry: {line!r}")
        tests[name] = description
    if not tests:
        raise RuntimeError("Suite executable contains no tests")
    return dict(sorted(tests.items()))


def parse_results(text, selected, token, contract, prefix="VV"):
    records = [line.strip().split() for line in text.splitlines()
               if line.startswith(prefix + "-")]
    if not records or records[0] != [prefix + "-BEGIN", token, contract]:
        raise RuntimeError("Missing/incorrect BEGIN contract or run ID")
    verdicts = {}
    running = None
    summary = None
    ended = False
    status = None
    for fields in records[1:]:
        if len(fields) < 2 or fields[1] != token or ended:
            raise RuntimeError(f"Unexpected suite record: {fields}")
        kind = fields[0]
        if kind == prefix + "-RUN" and len(fields) == 3 and summary is None:
            name = fields[2]
            if running or name not in selected or name in verdicts:
                raise RuntimeError(f"Unexpected/duplicate test start: {name}")
            running = name
        elif kind == prefix + "-RESULT" and len(fields) == 4 and summary is None:
            name, verdict = fields[2:]
            if name != running or verdict not in ("PASS", "FAIL", "SKIP"):
                raise RuntimeError(f"Unexpected test result: {fields}")
            verdicts[name] = verdict
            running = None
        elif kind == prefix + "-SUMMARY" and len(fields) == 6 and summary is None:
            if running or any(not re.fullmatch(r"[0-9]+", value) for value in fields[2:]):
                raise RuntimeError("Incomplete test or invalid summary counts")
            summary = dict(zip(("pass", "fail", "skip", "infra"), map(int, fields[2:])))
        elif kind == prefix + "-END" and len(fields) == 3 and summary is not None:
            expected = 2 if summary["infra"] else 1 if summary["fail"] else 0
            if fields[2] != str(expected):
                raise RuntimeError("END disagrees with summary")
            status = expected
            ended = True
        else:
            raise RuntimeError(f"Malformed or out-of-order suite record: {fields}")
    if not ended:
        raise RuntimeError("Appliance did not report a complete END record")
    for verdict in ("PASS", "FAIL", "SKIP"):
        if summary[verdict.lower()] != list(verdicts.values()).count(verdict):
            raise RuntimeError("Summary disagrees with individual test results")
    if not summary["infra"] and set(verdicts) != set(selected):
        raise RuntimeError("Selected tests were not all executed")
    if not summary["pass"] and not summary["fail"] and not summary["infra"]:
        raise RuntimeError("Entirely unexecuted/unsupported suite cannot pass")
    return {"status": status, "summary": summary, "tests": verdicts}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def control(command, action, token, timeout, log):
    args = [command, action, "--run-id", token]
    if action == "arm":
        args += ["--timeout", str(timeout)]
    result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=30)
    if result.returncode:
        raise RuntimeError(f"External control {action} failed: exit {result.returncode}")


def launch(suite, args, selected):
    if suite.requires_control and not args.control:
        raise RuntimeError("This suite requires external watchdog/recovery controls")
    token = uuid.uuid4().hex
    directory = (args.output or suite.binary.parent / "results" /
                 (time.strftime("%Y%m%d-%H%M%S") + "-" + token[:8])).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    report = {"suite": suite.name, "contract": suite.contract, "run_id": token,
              "selected": selected, "runs": [], "status": 2,
              "kernel": str(args.kernel), "kernel_sha256": digest(args.kernel),
              "initramfs": str(args.initramfs), "initramfs_sha256": digest(args.initramfs)}
    control_command = executable(args.control) if args.control else None
    with (directory / "control.log").open("wb", buffering=0) as control_log:
        for iteration in range(args.repeat):
            run_id = token + f"-{iteration + 1}"
            outcome = {"run_id": run_id, "status": 2}
            armed = False
            proc = None
            try:
                command = suite.build_command(args, selected, run_id)
                if (not isinstance(command, list) or not command
                        or any(not isinstance(arg, str) for arg in command)):
                    raise RuntimeError("Suite build_command() must return a nonempty argv list")
                command[0] = executable(command[0])
                log_path = directory / f"console-{iteration + 1}.log"
                outcome.update(command=command, console=str(log_path))
                if control_command:
                    control(control_command, "arm", run_id, args.timeout, control_log)
                    armed = True
                with log_path.open("wb", buffering=0) as console:
                    proc = subprocess.Popen(command, stdout=console, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL)
                    try:
                        returncode = proc.wait(timeout=args.timeout)
                    except subprocess.TimeoutExpired:
                        outcome["timed_out"] = True
                        raise RuntimeError(f"Appliance exceeded {args.timeout}s deadline")
                if returncode:
                    raise RuntimeError(f"Launcher exited {returncode}; inspect {log_path}")
                if log_path.stat().st_size > 16 * 1024 * 1024:
                    raise RuntimeError("Console exceeds 16 MiB parsing limit; full log retained")
                outcome.update(parse_results(log_path.read_text(errors="replace"), selected,
                                             run_id, suite.contract, suite.protocol_prefix))
            except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                outcome["error"] = str(error) or "Interrupted"
                outcome["status"] = 2
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        outcome["termination_error"] = str(error)
                        outcome["status"] = 2
                if armed:
                    if outcome["status"] == 2:
                        try:
                            control(control_command, "recover", run_id, args.timeout, control_log)
                        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                            outcome["recovery_error"] = str(error)
                    if "recovery_error" not in outcome:
                        try:
                            control(control_command, "disarm", run_id, args.timeout, control_log)
                        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                            outcome["disarm_error"] = str(error)
                            outcome["status"] = 2
                report["runs"].append(outcome)
                report["status"] = max(run["status"] for run in report["runs"])
                (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
            for name, verdict in outcome.get("tests", {}).items():
                print(f"[{verdict}] {name} (run {iteration + 1})")
            for field in ("error", "termination_error", "recovery_error", "disarm_error"):
                if field in outcome:
                    print(f"[INFRA] {outcome[field]}", file=sys.stderr)
            if outcome["status"]:
                break
    verdict = ("PASS", "FAIL", "INFRA")[report["status"]]
    print(f"{suite.name} overall: {verdict}\nDiagnostics: {directory}")
    return report["status"]


def parse_args(suite, argv):
    parser = argparse.ArgumentParser(description=f"{suite.name} appliance suite",
                                     allow_abbrev=False)
    parser.add_argument("tests", nargs="*", help="Exact test IDs; default: all")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--binary", type=Path, default=suite.binary)
    parser.add_argument("--kernel", type=Path, help="Explicit kernel image; never downloaded")
    parser.add_argument("--initramfs", type=Path, default=suite.initramfs)
    parser.add_argument("--control", help="External watchdog/recovery control executable")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, help="New diagnostics directory")
    suite.add_arguments(parser)
    args = parser.parse_args(argv)
    if args.repeat < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--repeat and --timeout must be positive and finite")
    if args.list and args.tests:
        parser.error("--list cannot be combined with test selection")
    if not args.list:
        if not args.kernel or (suite.requires_control and not args.control):
            parser.error("execution requires --kernel and this suite's recovery prerequisites")
        for name in ("kernel", "initramfs"):
            path = getattr(args, name).resolve()
            if not path.is_file() or path.stat().st_size == 0:
                parser.error(f"{name} must be a nonempty file: {path}")
            setattr(args, name, path)
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--suite":
        print("usage: run --suite /path/to/suite.py [suite options]", file=sys.stderr)
        return 2
    try:
        suite = load_suite(argv[1])
        args = parse_args(suite, argv[2:])
        tests = catalog(args.binary)
        if args.list:
            for name, description in tests.items():
                print(f"{name}\t{description}")
            return 0
        if len(set(args.tests)) != len(args.tests) or any(t not in tests for t in args.tests):
            raise RuntimeError("Unknown or duplicate test selection; use --list")
        suite.preflight(args)
        return launch(suite, args, args.tests or list(tests))
    except (OSError, RuntimeError, subprocess.SubprocessError, ImportError,
            AttributeError, ValueError, TypeError, SyntaxError) as error:
        print(f"[INFRA] {error}", file=sys.stderr)
        return 2
