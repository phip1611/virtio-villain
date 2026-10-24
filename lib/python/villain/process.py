# SPDX-License-Identifier: Apache-2.0
"""Tracked runner processes and existing termination cleanup."""

import os
import signal
import subprocess
import threading

# Registry of live VMM subprocess handles. We track every Popen we
# create so a Ctrl+C or unexpected exception path can SIGKILL them all
# before the runner exits, instead of leaving CH/QEMU instances
# reparented to init.
_live_procs = set()
_live_procs_lock = threading.Lock()


def register_process(proc):
    with _live_procs_lock:
        _live_procs.add(proc)


def unregister_process(proc):
    with _live_procs_lock:
        _live_procs.discard(proc)


def kill_all_processes():
    """SIGKILL every tracked VMM proc. Best effort, never raises."""
    with _live_procs_lock:
        procs = list(_live_procs)
    for p in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            p.kill()
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


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


def kill_process(proc):
    """Kill a process and its entire session group."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    # Defensive: also signal the immediate proc in case the pgroup
    # kill missed (e.g. the child re-setsid'd itself).
    try:
        proc.kill()
    except OSError:
        pass
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
