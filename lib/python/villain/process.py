# SPDX-License-Identifier: Apache-2.0
"""Tracked runner processes and existing termination cleanup."""

from contextlib import contextmanager, ExitStack
import os
import pty
import signal
import subprocess
import threading
from villain.console import ctty_preexec

# Registry of live VMM subprocess handles. We track every Popen we
# create so a Ctrl+C or unexpected exception path can SIGKILL them all
# before the runner exits, instead of leaving CH/QEMU instances
# reparented to init.
_live_procs = {}
_live_procs_lock = threading.Lock()


def register_process(proc, *, process_group=True):
    with _live_procs_lock:
        _live_procs[proc] = process_group


def unregister_process(proc):
    with _live_procs_lock:
        _live_procs.pop(proc, None)


def _signal_process(proc, process_group):
    if process_group:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def kill_all_processes():
    """SIGKILL every tracked VMM proc. Best effort, never raises."""
    with _live_procs_lock:
        procs = list(_live_procs.items())
    for proc, process_group in procs:
        _signal_process(proc, process_group)
    for proc, _ in procs:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _stop_if_running(proc, process_group):
    if proc.poll() is None:
        kill_process(proc, process_group=process_group)


@contextmanager
def spawn(command, *, terminal=False, start_new_session=True, stop=None, **options):
    """Yield a tracked process and its optional private terminal master."""
    if terminal and ("stdin" in options or "preexec_fn" in options):
        raise ValueError("A private terminal owns stdin and preexec_fn")
    if stop is not None and stop.is_set():
        raise KeyboardInterrupt
    with ExitStack() as cleanup:
        master = None
        slave_stream = None
        process_group = terminal or start_new_session
        if terminal:
            master, slave = pty.openpty()
            cleanup.callback(os.close, master)
            slave_stream = cleanup.enter_context(os.fdopen(slave, "rb", buffering=0))
            options["stdin"] = slave
            options["preexec_fn"] = ctty_preexec(os.ttyname(slave).encode())
            start_new_session = False
        else:
            options.setdefault("stdin", subprocess.DEVNULL)
        proc = subprocess.Popen(command, start_new_session=start_new_session, **options)
        cleanup.callback(unregister_process, proc)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                cleanup.callback(stream.close)
        cleanup.callback(_stop_if_running, proc, process_group)
        register_process(proc, process_group=process_group)
        if slave_stream is not None:
            slave_stream.close()
        if stop is not None and stop.is_set():
            raise KeyboardInterrupt
        yield proc, master


def capture(command, *, timeout=None, **options):
    """Capture separate byte streams, retaining both on a supervised timeout."""
    process_group = options.get("terminal", False) or options.get("start_new_session", True)
    with spawn(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               **options) as (proc, _):
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            kill_process(proc, process_group=process_group)
            error.output, error.stderr = proc.communicate()
            raise
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def install_termination_as_interrupt():
    """Route SIGTERM and SIGHUP through the KeyboardInterrupt cleanup.

    `timeout` sends SIGTERM and a closed controlling terminal sends
    SIGHUP. Under their default disposition the process dies without
    unwinding the stack, so the run loop cleanup that SIGKILLs tracked
    VMMs is skipped and in-flight VMs are left reparented to init.
    Raising KeyboardInterrupt makes both signals take the existing
    interrupt path that reaps every tracked VMM before exit.
    """
    def _raise_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):
            pass


def kill_process(proc, *, process_group=True):
    """Kill a process and its entire session group."""
    _signal_process(proc, process_group)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
