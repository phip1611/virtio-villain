# SPDX-License-Identifier: Apache-2.0
"""Shared appliance orchestration for explicitly loaded external test suites."""

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field as dataclass_field
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Optional
import uuid
from villain import reporting
from villain.console import FileFollower


@dataclass
class Suite:
    """Suite policy; the framework owns selection, execution and result handling."""

    name: str
    contract: str
    binary: Path
    initramfs: Path
    protocol_prefix: str = "VV"
    requires_control: bool = False
    host_shutdown: bool = False
    kernel: Optional[Path] = None
    kernel_build_hint: Optional[str] = None
    definition: Optional[Path] = dataclass_field(
        default=None, init=False, repr=False, compare=False)

    def list_tests(self, binary):
        """Return the browsable catalog, optionally from source registrations."""
        return catalog(binary)

    def add_arguments(self, parser):
        """Register suite-specific launch options without changing the core CLI."""

    def preflight(self, args):
        """Raise an actionable error when host prerequisites are missing."""

    def build_command(self, args, selected, run_id):
        """Return an argv list; stdout/stderr must include the guest console."""
        raise NotImplementedError

    def execution_command(self, argv):
        """Run suite preparation inside the supervised appliance process."""
        if self.definition is None:
            raise RuntimeError("Load the suite before constructing its execution command")
        return [sys.executable, str(Path(__file__).with_name("suite_exec.py")),
                str(self.definition), *argv]

    def execute(self, argv):
        """Prepare an appliance and replace this process with its VMM."""
        raise NotImplementedError


def load_suite(path):
    path = Path(path).resolve()
    if path.is_dir():
        path = path / "lib/suite.py"
    if not path.is_file():
        raise RuntimeError(f"Suite definition not found: {path}")
    spec = importlib.util.spec_from_file_location(
        "vv_external_" + uuid.uuid4().hex, path,
        submodule_search_locations=[str(path.parent)])
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load suite module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loaded = False
    try:
        spec.loader.exec_module(module)
        suite = module.create_suite()
        if not isinstance(suite, Suite):
            raise RuntimeError("Suite create_suite() must return villain.appliance.Suite")
        if (not suite.name or not re.fullmatch(r"\S+", suite.contract)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*", suite.protocol_prefix)):
            raise RuntimeError("Invalid suite name, contract, or protocol prefix")
        suite.binary = Path(suite.binary).resolve()
        suite.initramfs = Path(suite.initramfs).resolve()
        if suite.kernel is not None:
            suite.kernel = Path(suite.kernel).resolve()
        suite.definition = path
        loaded = True
        return suite
    finally:
        if not loaded:
            for name in list(sys.modules):
                if name == spec.name or name.startswith(spec.name + "."):
                    sys.modules.pop(name, None)


def executable(value):
    path = shutil.which(str(value))
    if path is None:
        raise RuntimeError(f"Executable not found: {value}")
    return str(Path(path).resolve())


def catalog(binary):
    result = subprocess.run([executable(binary), "--list"], check=True,
                            capture_output=True, text=True, timeout=10)
    return parse_catalog(result.stdout)


def parse_catalog(text):
    tests = {}
    for line in text.splitlines():
        name, separator, description = line.partition("\t")
        if (not separator or not description or "\t" in description
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name) or name in tests):
            raise RuntimeError(f"Invalid test catalog entry: {line!r}")
        tests[name] = description
    if not tests:
        raise RuntimeError("Suite catalog contains no tests")
    return dict(sorted(tests.items()))


def source_catalog(paths, macro="VV_REGISTER_TEST"):
    """Read literal three or four argument registrations without compiling C."""
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    literal = r'"(?:[^"\\\r\n]|\\[^\r\n])*"'
    start = rf"^[ \t]*{re.escape(macro)}\s*\("
    registration = re.compile(
        start + rf"\s*(?:{identifier}\s*,\s*)?({identifier})\s*,\s*"
        + rf"{identifier}\s*,\s*({literal})\s*\)\s*;?[ \t]*$", re.MULTILINE)
    comments = re.compile(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*', re.DOTALL)
    entries = []
    for path in sorted(map(Path, paths)):
        text = comments.sub(
            lambda match: match[0] if match[0].startswith('"') else
            "\n" * match[0].count("\n"), path.read_text())
        matches = list(registration.finditer(text))
        if not matches or len(matches) != len(re.findall(start, text, re.MULTILINE)):
            raise RuntimeError(f"Expected literal {macro} registrations in {path}")
        for match in matches:
            try:
                description = json.loads(match[2])
            except json.JSONDecodeError as error:
                raise RuntimeError(f"Unsupported description literal in {path}: {error}") \
                    from error
            if not description or any(char in description for char in "\r\n\t"):
                raise RuntimeError(f"Invalid test description in {path}")
            entries.append(f"{match[1]}\t{description}")
    return parse_catalog("\n".join(entries))


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


def wait_for_appliance(proc, suite, args, selected, run_id, log_path, outcome, stop=None,
                       follower=None):
    if not suite.host_shutdown and stop is None and follower is None:
        return proc.wait(timeout=args.timeout)
    deadline = time.monotonic() + args.timeout
    marker = f"vv-appliance-stop {run_id}"
    while True:
        if stop is not None and stop.is_set():
            raise KeyboardInterrupt
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, args.timeout)
        try:
            returncode = proc.wait(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            returncode = None
        if follower is not None:
            follower.pump()
        if not suite.host_shutdown:
            if returncode is not None:
                return returncode
            continue
        if log_path.stat().st_size > 16 * 1024 * 1024:
            raise RuntimeError("Console exceeds 16 MiB parsing limit; full log retained")
        text = log_path.read_text(errors="replace")
        if "Kernel panic - not syncing:" in text:
            raise RuntimeError(f"Guest kernel panic; inspect {log_path}")
        markers = text.splitlines().count(marker)
        if markers > 1:
            raise RuntimeError("Duplicate host shutdown marker")
        if markers == 1:
            parse_results(text, selected, run_id, suite.contract, suite.protocol_prefix)
            if returncode is None:
                proc.terminate()
                try:
                    returncode = proc.wait(timeout=5)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("Launcher did not stop within 5s of guest completion") \
                        from error
                if returncode not in (0, -signal.SIGTERM):
                    raise RuntimeError(f"Launcher exited {returncode} during host shutdown")
                outcome["host_shutdown"] = True
                return 0
            return returncode
        if returncode is not None:
            if returncode == 0:
                raise RuntimeError("Launcher exited without the host shutdown marker")
            return returncode


def launch(suite, args, selected, descriptions=None):
    if suite.requires_control and not args.control:
        raise RuntimeError("This suite requires external watchdog/recovery controls")
    token = uuid.uuid4().hex
    directory = (args.output or suite.binary.parent / "results" /
                 (time.strftime("%Y%m%d-%H%M%S") + "-" + token[:8])).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    jobs = 1 if args.console else args.jobs
    report = {"suite": suite.name, "contract": suite.contract, "run_id": token,
              "selected": selected, "jobs": jobs, "runs": [], "status": 2,
              "kernel": str(args.kernel), "kernel_sha256": digest(args.kernel),
              "initramfs": str(args.initramfs), "initramfs_sha256": digest(args.initramfs)}
    control_command = executable(args.control) if args.control else None
    stopped = threading.Event()
    policy_lock = threading.Lock()
    output_lock = threading.Lock()
    completed = {}
    human_results = []
    descriptions = descriptions or {}
    color = sys.stdout.isatty()
    total = len(selected) * args.repeat
    suffix = f" (run {args.repeat})" if args.repeat > 1 else ""
    longest_desc = max((len(descriptions.get(name, "")) + len(suffix)
                        for name in selected), default=10)
    tasks = iter(enumerate((iteration, test) for iteration in range(args.repeat)
                           for test in selected))
    launcher = getattr(args, "launcher", None) or getattr(args, "vmm", None) or suite.name
    print(f"Running {total} test(s) against {launcher} ({suite.name})\n", flush=True)

    def clear_progress():
        if color and not args.console:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def progress(current=""):
        if color and not args.console:
            counts = Counter(status for _, status, _ in human_results)
            sys.stdout.write(reporting.format_progress(
                counts, len(human_results), total, current,
                reporting.terminal_width(sys.stdout)))
            sys.stdout.flush()

    def show_outcome(outcome):
        name = outcome["test"]
        status = "INFRA" if outcome["status"] == 2 else outcome["tests"].get(name, "INFRA")
        description = descriptions.get(name, "")
        if args.repeat > 1:
            description += f" (run {outcome['iteration']})"
        info = (description, "", "")
        with output_lock:
            clear_progress()
            human_results.append((name, status, info))
            if not args.console:
                print(reporting.format_test_line(
                    name, status, info, longest_desc, reporting.terminal_width(sys.stdout),
                    color), flush=True)
            if status in ("FAIL", "INFRA"):
                for field in ("error", "termination_error", "console_error",
                              "recovery_error", "disarm_error"):
                    if field in outcome:
                        print(reporting.format_detail(outcome[field], color), flush=True)
                if "console" in outcome and not args.console:
                    log_path = Path(outcome["console"])
                    print(reporting.format_detail(f"log: {log_path}", color), flush=True)
                    if log_path.is_file():
                        with log_path.open("rb") as stream:
                            stream.seek(max(0, log_path.stat().st_size - 8192))
                            tail = stream.read().decode(errors="replace").splitlines()[-10:]
                        for line in tail:
                            if line:
                                print(reporting.format_detail(line, color), flush=True)
            progress()

    def record(index, outcome):
        if outcome is not None:
            completed[index] = outcome
            if outcome.get("interrupted"):
                report["interrupted"] = True
            report["runs"] = [completed[key] for key in sorted(completed)]
            report["status"] = max(run["status"] for run in report["runs"])
        if report.get("interrupted"):
            report["status"] = 2
        (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        if outcome is not None:
            show_outcome(outcome)

    with (directory / "control.log").open("wb", buffering=0) as control_log:
        def run_one(iteration, test):
            if stopped.is_set():
                return None
            run_id = token + f"-{iteration + 1}-{test}"
            outcome = {"run_id": run_id, "test": test, "iteration": iteration + 1,
                       "tests": {}, "status": 2}
            armed = False
            control_ready = control_command is None
            proc = None
            follower = None
            try:
                with policy_lock:
                    command = suite.build_command(args, [test], run_id)
                if (not isinstance(command, list) or not command
                        or any(not isinstance(arg, str) for arg in command)):
                    raise RuntimeError("Suite build_command() must return a nonempty argv list")
                command[0] = executable(command[0])
                log_path = directory / f"console-{iteration + 1}-{test}.log"
                outcome.update(command=command, console=str(log_path))
                if control_command:
                    control(control_command, "arm", run_id, args.timeout, control_log)
                    armed = True
                    control_ready = True
                if stopped.is_set():
                    raise KeyboardInterrupt
                with log_path.open("wb", buffering=0) as console:
                    if args.console:
                        follower = FileFollower(log_path, sys.stdout)
                    proc = subprocess.Popen(command, stdout=console, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL)
                    try:
                        returncode = wait_for_appliance(
                            proc, suite, args, [test], run_id, log_path, outcome,
                            stopped if jobs > 1 else None, follower)
                    except subprocess.TimeoutExpired:
                        outcome["timed_out"] = True
                        raise RuntimeError(f"Appliance exceeded {args.timeout}s deadline")
                if returncode:
                    raise RuntimeError(f"Launcher exited {returncode}; inspect {log_path}")
                if log_path.stat().st_size > 16 * 1024 * 1024:
                    raise RuntimeError("Console exceeds 16 MiB parsing limit; full log retained")
                outcome.update(parse_results(log_path.read_text(errors="replace"), [test],
                                             run_id, suite.contract, suite.protocol_prefix))
            except (OSError, RuntimeError, ValueError,
                    subprocess.SubprocessError, KeyboardInterrupt) as error:
                outcome["error"] = str(error) or "Interrupted"
                outcome["status"] = 2
                if isinstance(error, KeyboardInterrupt):
                    outcome["interrupted"] = True
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        outcome["termination_error"] = str(error)
                        outcome["status"] = 2
                if follower is not None:
                    try:
                        follower.close()
                    except (OSError, ValueError) as error:
                        outcome["console_error"] = str(error)
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
            if not control_ready or any(field in outcome for field in (
                    "interrupted", "termination_error", "console_error",
                    "recovery_error", "disarm_error")):
                stopped.set()
            return outcome

        if jobs == 1:
            for index, (iteration, test) in tasks:
                progress(test)
                record(index, run_one(iteration, test))
                if stopped.is_set():
                    break
        else:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                progress("starting...")
                pending = {}
                try:
                    while True:
                        try:
                            while not stopped.is_set() and len(pending) < jobs:
                                task = next(tasks, None)
                                if task is None:
                                    break
                                index, (iteration, test) = task
                                pending[pool.submit(run_one, iteration, test)] = index
                            if not pending:
                                break
                            done, _ = wait(pending, return_when=FIRST_COMPLETED)
                            for future in done:
                                outcome = future.result()
                                record(pending.pop(future), outcome)
                        except KeyboardInterrupt:
                            report["interrupted"] = True
                            stopped.set()
                            record(None, None)
                finally:
                    stopped.set()
    clear_progress()
    counts = Counter(status for _, status, _ in human_results)
    if report.get("interrupted"):
        print("\nInterrupted.")
        if not counts["INFRA"]:
            counts["INFRA"] = 1
    print(reporting.format_summary(counts, len(human_results), color))
    for status in ("FAIL", "INFRA"):
        entries = [(name, info[0]) for name, verdict, info in human_results if verdict == status]
        if entries:
            print(reporting.format_failure_list(entries, status, color))
    print(f"Diagnostics: {directory}", flush=True)
    return report["status"]


def parse_args(suite, argv):
    parser = argparse.ArgumentParser(description=f"{suite.name} appliance suite",
                                     allow_abbrev=False)
    parser.add_argument("tests", nargs="*", help="Exact test IDs; default: all")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("-c", "--console", action="store_true",
                        help="Stream VM output directly to stdout (runs serially)")
    parser.add_argument("--binary", type=Path, default=suite.binary)
    parser.add_argument("--kernel", type=Path, default=suite.kernel,
                        help="Kernel image override; defaults to the suite image when configured")
    parser.add_argument("--initramfs", type=Path, default=suite.initramfs)
    parser.add_argument("--control", help="External watchdog/recovery control executable")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                        help="Maximum concurrent appliance VMs (default: 1)")
    parser.add_argument("--output", type=Path, help="New diagnostics directory")
    suite.add_arguments(parser)
    args = parser.parse_args(argv)
    if args.repeat < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--repeat and --timeout must be positive and finite")
    if args.jobs < 1:
        parser.error("--jobs must be a positive integer")
    if args.list and args.tests:
        parser.error("--list cannot be combined with test selection")
    if not args.list:
        if not args.kernel:
            parser.error("execution requires --kernel when the suite has no default kernel")
        if suite.requires_control and not args.control:
            parser.error("execution requires --control for this suite")
        for name in ("kernel", "initramfs"):
            path = getattr(args, name).resolve()
            if not path.is_file() or path.stat().st_size == 0:
                message = f"{name} must be a nonempty file: {path}"
                if (name == "kernel" and suite.kernel is not None
                        and path == Path(suite.kernel).resolve()):
                    hint = suite.kernel_build_hint or (
                        "Build the suite kernel or select another image with --kernel PATH.")
                    message += f". {hint}"
                parser.error(message)
            setattr(args, name, path)
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--suite":
        print("usage: run --suite /path/to/suite [suite options]", file=sys.stderr)
        return 2
    try:
        suite = load_suite(argv[1])
        args = parse_args(suite, argv[2:])
        if args.list:
            tests = suite.list_tests(args.binary)
            for name, description in tests.items():
                print(f"{name}\t{description}")
            return 0
        tests = catalog(args.binary)
        if len(set(args.tests)) != len(args.tests) or any(t not in tests for t in args.tests):
            raise RuntimeError("Unknown or duplicate test selection; use --list")
        suite.preflight(args)
        return launch(suite, args, args.tests or list(tests), tests)
    except (OSError, RuntimeError, subprocess.SubprocessError, ImportError,
            AttributeError, ValueError, TypeError, SyntaxError) as error:
        print(f"[INFRA] {error}", file=sys.stderr)
        return 2
