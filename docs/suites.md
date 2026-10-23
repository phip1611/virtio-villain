# External appliance suites

The default suite in Virtio Villain is still the virtio test appliance. Its device
setup, source lists, command line and automatic guest kernel download are
unchanged. Other suites can use the framework without inheriting virtio
discovery, PCI/MMIO setup, negotiation, or queue fixtures.

An external suite contains its own tests, prerequisites, kernel contract and
launch policy. It need not be stored in this repository. The public extension
points contain no device or kernel interfaces specific to an external suite.

## Framework components

| Component | Responsibility |
| --- | --- |
| `lib/test.h` | Result types and diagnostic return helpers with typed test registration |
| `lib/test_runner.h` / `.c` | Test selection and contexts with cleanup and result reporting |
| `lib/appliance_init.c` | PID 1 startup and mounts with kernel command line selection and shutdown |
| `lib/initramfs.py` | Reproducible packaging of a static ELF executable as `/init` |
| `lib/appliance.py` | Catalog discovery, launch supervision, console capture, result validation and JSON reporting |
| `lib/reporting.py` | Shared human result rows, terminal progress, summaries and failure lists |
| `run --suite FILE` | Explicitly load a trusted suite policy module |
| `make appliance-initramfs` | Build an explicit external source list independently of virtio |

## Guest interface

Include `lib/test_runner.h` and register tests in a dedicated linker section:

```c
struct context {
    int resource;
};

static test_result_t example(struct context *context)
{
    if (context->resource < 0)
        TFAIL("resource: expected a usable descriptor, actual %d", context->resource);
    return TEST_PASS;
}

VV_REGISTER_TEST(example_registry, EX001, example, "example resource operation");
```

The registration macro creates an adapter for the typed context. It does not
cast incompatible function pointers. Define a `struct vv_test_suite` with:

- A contract identifier and protocol prefix without whitespace, such as
  `example-v1` and `VV`.
- Registry bounds (`__start_example_registry`, `__stop_example_registry`).
- `context_size` and an optional `init_context` callback.
- An optional `preflight` callback. Zero means prerequisites are met.
  Nonzero means an infrastructure problem, not a skipped or successful test.
- A `cleanup` callback returning a test verdict. It runs after every returned
  test outcome, including assertions and skips. Cleanup failure fails the test.

Call `vv_appliance_main(argc, argv, &suite)` from the `main` function of the suite.
`--list` is safe on the host and prints `ID<TAB>description`. Test execution
requires PID 1. The appliance mounts procfs, sysfs and devtmpfs and reads
`vv.run=RUN_ID` and optional `vv.tests=ID,ID` from `/proc/cmdline`.

Tests return `TEST_PASS`, `TEST_FAIL`, or `TEST_SKIP`. Other verdicts fail unless
a suite applies its own remapping before returning. Use skips only for optional
capabilities. Missing execution prerequisites belong in `preflight`. An empty,
unexecuted, or entirely skipped suite is not a pass.

`TFAIL` and related helpers return immediately. Keep owned resources in the
context so the cleanup callback can release them. These callbacks cannot
recover from a kernel hang or a test that does not return.

## Build a separate appliance

Provide an explicit source list, include paths and output directory for the suite:

```sh
make appliance-initramfs CC=gcc \
  APPLIANCE_SRCS="/work/example/init.c /work/example/cases.c" \
  APPLIANCE_CPPFLAGS="-I/work/example" \
  APPLIANCE_TARGET=/work/example/build
```

The framework links the generic guest runner and PID 1 startup automatically.
Outputs are `build/init` and `build/initramfs.cpio.gz`. Generated headers must be
prepared by the external suite before invoking this target. Choose a separate
output directory for each suite and compiler configuration.

External sources are never added to the virtio `SRCS` wildcards. The existing
`init`, `initramfs`, fuzz and performance targets continue to build their
original executables and archives.

Kernel construction remains independent. Use `build-kernel --source ...`
with the config supplied by the kernel and an explicit external `--output` and
`--cache-dir`. The suite runner requires an explicit kernel image and does not
fetch a default.

## Host policy interface

Create a trusted Python file outside the repository:

```python
from pathlib import Path
from lib.appliance import Suite

ROOT = Path(__file__).resolve().parent

class ExampleSuite(Suite):
    def add_arguments(self, parser):
        parser.add_argument("--launcher")

    def preflight(self, args):
        if not args.launcher:
            raise RuntimeError("provide --launcher for the required environment")

    def build_command(self, args, selected, run_id):
        cmdline = (
            "console=ttyS0 rdinit=/init "
            f"vv.run={run_id} vv.tests={','.join(selected)}"
        )
        return [
            args.launcher, "--kernel", str(args.kernel),
            "--initramfs", str(args.initramfs), "--cmdline", cmdline,
        ]

def create_suite():
    return ExampleSuite(
        name="example", contract="example-v1",
        binary=ROOT / "build/init",
        initramfs=ROOT / "build/initramfs.cpio.gz",
        requires_control=True,
    )
```

This policy runs within the framework process, not through another full test
runner. The framework owns listing, selection, supervision, protocol checks
and reporting. The policy supplies launch arguments and prerequisite checks.
Only explicitly requested modules are loaded. Do not load untrusted code.

```sh
./run --suite /work/example/suite.py --list
./run --suite /work/example/suite.py \
  --kernel /work/kernel/image --launcher /work/bin/start-appliance \
  --control /work/bin/external-control --repeat 3 EX001
```

Put `--suite FILE` first. `--help` after it shows both common and suite
options. `--list` does not run the host preflight, boot an appliance, or require
a kernel or recovery control.

A policy can override `list_tests(self, binary)` to return IDs and
descriptions from its sources before an executable has been built.
`lib.appliance.source_catalog(paths, macro)` reads literal registration
descriptions with either three arguments or an initial registry argument.
It ignores comments and rejects empty, duplicate or malformed entries.
This is a source index, not a C preprocessor. Execution always uses the
compiled executable's catalog, including its conditional build choices.

Each selected test runs in a fresh appliance VM with its own run identifier,
console log and timeout. `--repeat N` repeats that sequence N times.
Use `--jobs N` or `-j N` to run up to N appliance VMs concurrently.
The default is one. Tests and repetitions share the concurrency limit.
Completed results are saved as they arrive and listed in selection order
in the JSON report. Each test retains its own VM and diagnostics.
Assertion failures, guest crashes and timeouts do not prevent later tests
from running once the previous launcher has been terminated.

Execution stops on interruption or failed launcher termination. It also
stops if external control cannot arm, recover or disarm. Results completed
before the stop remain in the report. Any failure keeps the overall result
failing even when later tests pass.

During parallel execution, interruption or a safety control failure stops
new launches and cancels active VMs through the normal cleanup path.
External controls used with parallel jobs must support independent run IDs
and simultaneous control requests.

## Result and process contract

With protocol prefix `VV`, the guest emits:

```text
VV-BEGIN RUN_ID CONTRACT
VV-RUN RUN_ID EX001
VV-RESULT RUN_ID EX001 PASS
VV-SUMMARY RUN_ID 1 0 0 0
VV-END RUN_ID 0
```

Summary counters are pass, fail, skip, infrastructure. The host checks the run
identifier and contract. It verifies execution ordering and exact selected
IDs. It also checks individual verdicts, counters and final status.
Missing, stale, duplicate, malformed or
incomplete records are infrastructure failures. Ordinary diagnostic lines are
preserved in the console log.

The launcher must remain attached until the appliance shuts down, relay its
console to stdout/stderr, clean up its own subordinate processes, and return
zero for a completed boot/shutdown. Test failures are conveyed by guest
records, not by the exit code of the launcher. A nonzero launcher exit is an
infrastructure failure.

Suites whose guests cannot power off can opt into `host_shutdown=True` and
pass `vv.shutdown=host` on the kernel command line. After result reporting,
cleanup and synchronization, the guest emits `vv-appliance-stop RUN_ID` and
waits without requesting poweroff. The runner validates the complete result
protocol and matching stop marker before terminating the attached launcher.
The launcher must support SIGTERM without leaving subordinate processes.
The report records `host_shutdown` when the runner stops the launcher.
Missing completion markers, malformed results and kernel panics remain
infrastructure failures. This mode does not validate guest poweroff.
The default shutdown mode and virtio appliance operation are unchanged.

Exit codes are 0 (pass), 1 (assertion/cleanup failure), and 2 (infrastructure,
including timeouts and entirely unexecuted suites). The diagnostics directory
contains console logs, external control logs and `results.json`. The JSON
report includes image hashes, exact launch arguments and test verdicts.

Human output uses the same aligned result rows, terminal colors, progress,
count summary and failure lists as the virtio runner. Test descriptions come
from the suite catalog. External suites leave virtio specification columns
empty. Infrastructure problems remain `INFRA`, including failures after an
otherwise passing guest result. Failure output includes console paths and
diagnostic excerpts. Repeated cases identify their run in the description.

## External timeout and recovery

Setting `requires_control=True` makes `--control` mandatory. The executable is
called without a shell:

```text
CONTROL arm --run-id ID --timeout SECONDS
CONTROL recover --run-id ID
CONTROL disarm --run-id ID
```

`arm` must establish a watchdog **outside the machine under test** and return
zero only after it is active. `recover` must reset or power cycle the test
environment and confirm completion. `disarm` cancels the external
watchdog. A failed recovery leaves it armed. All operations must be idempotent
for the run identifier and return nonzero with diagnostics on failure.

The framework also enforces a process deadline and attempts bounded process
termination. That does not contain a kernel failure or recover a hung host.
The control executable must contact a real management service. A local
sleep or a script that does nothing is not external recovery. Killing the
test process is not external recovery either.
The external suite and execution environment define hosting and recovery policy.
