# SPDX-License-Identifier: Apache-2.0
"""Explicit host tuning shared by runner entry points."""

import atexit
import os
import shutil
import signal
import subprocess
import sys
from villain.process import kill_all_processes

# --- Host system configuration (opt-in via --apply-sysconfig) ----------
#
# Some host kernel settings make a sanitizer instrumented VMM misbehave in
# ways that look like guest triggered faults. This registry lists sysctls
# the harness can tune on request. Add new entries here as they prove
# useful; each is self describing.
#
# Fields:
#   key  sysctl name in dotted form, mapped to /proc/sys/<slashed path>
#   want desired value as a string
#   why  one line reason shown to the user before the change
#
# Only entries whose current value differs from want are changed, and only
# changed entries are reverted on exit, so applying is idempotent and the
# revert never clobbers a value the harness did not set.
SYSCONFIGS = [
    {
        "key": "kernel.yama.ptrace_scope",
        "want": "0",
        "why": "fallback for the LeakSanitizer exit time leak scan; "
               "only helps a dumpable VMM. A non dumpable VMM is fixed "
               "by the CAP_SYS_PTRACE grant instead",
    },
]


def _sysctl_path(key):
    """Map a dotted sysctl name to its /proc/sys path."""
    return os.path.join("/proc/sys", key.replace(".", "/"))


def _sysctl_read(key):
    """Return the current sysctl value as a string, or None if absent."""
    try:
        with open(_sysctl_path(key)) as f:
            return f.read().split()[0]
    except (OSError, IndexError):
        return None


def _sysctl_write(key, value):
    """Set a sysctl through sudo. Returns True on success."""
    try:
        r = subprocess.run(
            ["sudo", "sysctl", "-w", f"{key}={value}"],
            capture_output=True, text=True)
    except OSError as e:
        print(f"  sysctl: cannot run sudo: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"  sysctl: failed to set {key}={value}: "
              f"{r.stderr.strip()}", file=sys.stderr)
        return False
    return True


# --- VMM capability tuning (CAP_SYS_PTRACE for the leak scan) -----------
#
# A LeakSanitizer instrumented VMM runs a leak scan at process exit whose
# StopTheWorld step ptrace attaches the VMM's own threads. The VMM marks
# itself non dumpable (prctl PR_SET_DUMPABLE 0) as hardening, so a same
# uid tracer is refused with EPERM, LSan aborts, and the process exits non
# zero with teardown noise that looks like a guest triggered crash. This
# is independent of kernel.yama.ptrace_scope and of seccomp, so neither
# sysctl nor disabling seccomp clears it. Granting CAP_SYS_PTRACE to the
# VMM binary, which its in process tracer inherits, bypasses the non
# dumpable restriction so the scan completes cleanly and the VMM exits 0.
# The capability is a file capability set once with setcap and reverted on
# exit, so the VMM is still launched directly and the harness keeps full
# control of the process group for kill on marker and timeout.
_PTRACE_CAP = "cap_sys_ptrace"


def _is_leak_sanitized(path):
    """True if the binary embeds the LeakSanitizer runtime.

    Streamed substring scan so a large debug binary is not read whole
    into memory. Used to avoid widening privilege on a VMM that does not
    need the capability.
    """
    needle = b"LeakSanitizer"
    try:
        with open(path, "rb") as f:
            tail = b""
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return False
                if needle in tail + chunk:
                    return True
                tail = chunk[-len(needle):]
    except OSError:
        return False


def get_capabilities(path):
    """Return the file capability expression for path.

    Parses `getcap PATH`, whose output is "PATH cap_a,cap_b=ep". Returns
    the expression after the path (e.g. "cap_net_admin=ep"), or "" when
    the file has no capabilities, or None when getcap is missing or
    errors so the caller can refuse to touch caps it cannot read back.
    """
    if not shutil.which("getcap"):
        return None
    try:
        r = subprocess.run(["getcap", path], capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    if not out:
        return ""
    # Output is "<path> <expr>", and some versions insert "= " before
    # the expression. Take everything after the path, then strip any
    # leading "=" so the result is a clean cap_from_text expression.
    rest = out.split(None, 1)[1] if " " in out else ""
    return rest.lstrip("= ").strip()


def _setcap_apply(expr, path):
    """Set file capabilities on path through sudo. Returns True on ok."""
    try:
        r = subprocess.run(["sudo", "setcap", expr, path],
                           capture_output=True, text=True)
    except OSError as e:
        print(f"  cap: cannot run sudo setcap: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"  cap: setcap '{expr}' failed: {r.stderr.strip()}",
              file=sys.stderr)
        return False
    return True


def _setcap_remove(path):
    """Clear all file capabilities on path through sudo."""
    try:
        r = subprocess.run(["sudo", "setcap", "-r", path],
                           capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0


def apply_ptrace_cap(vmm_path):
    """Grant CAP_SYS_PTRACE to the VMM for its exit time leak scan.

    Returns the original capability expression to restore (a string,
    possibly empty), or None when nothing was changed, the VMM is not
    LeakSanitizer instrumented, or the grant could not be applied. The
    new capability is appended as a separate clause so any existing file
    capability, for example cap_net_admin for tap networking, is kept.
    """
    if not vmm_path or not os.path.exists(vmm_path):
        return None
    if not (shutil.which("setcap") and shutil.which("getcap")):
        return None
    if not _is_leak_sanitized(vmm_path):
        return None
    orig = get_capabilities(vmm_path)
    if orig is None:
        print(f"  cap: cannot read capabilities of {vmm_path}, skipping "
              "CAP_SYS_PTRACE", file=sys.stderr)
        return None
    if _PTRACE_CAP in orig:
        return None  # already present, nothing to change or revert
    new = (orig + " " if orig else "") + f"{_PTRACE_CAP}=ep"
    print(f"  cap: granting {_PTRACE_CAP} to {os.path.basename(vmm_path)} "
          "so LeakSanitizer can complete its exit time leak scan",
          file=sys.stderr)
    if not _setcap_apply(new, vmm_path):
        print("  cap: WARNING: could not set CAP_SYS_PTRACE. A "
              "LeakSanitizer instrumented VMM may print teardown noise "
              "and exit non zero; the output filter still keeps that "
              "from scoring as a crash.", file=sys.stderr)
        return None
    return orig


def revert_ptrace_cap(vmm_path, orig):
    """Restore the VMM file capabilities apply_ptrace_cap changed."""
    if orig is None or not vmm_path:
        return
    ok = _setcap_remove(vmm_path) if orig == "" else _setcap_apply(orig, vmm_path)
    if ok:
        shown = orig if orig else "(none)"
        print(f"  cap: {os.path.basename(vmm_path)} capabilities "
              f"restored to {shown}", file=sys.stderr)


def apply_sysconfigs(configs=None):
    """Apply registry entries whose value differs from desired.

    Returns a restore map {key: old_value} covering only the entries this
    call actually changed, suitable for passing to revert_sysconfigs.

    Entries that need changing but cannot be written, for example when
    sudo is missing or authentication fails, are reported with a clear
    warning so the caller is not left believing the tuning took effect.
    """
    if configs is None:
        configs = SYSCONFIGS
    print("apply-sysconfig: changing system wide kernel settings "
          "(reverted on exit)", file=sys.stderr)
    restore = {}
    failed = []
    for cfg in configs:
        key, want, why = cfg["key"], cfg["want"], cfg["why"]
        cur = _sysctl_read(key)
        if cur is None:
            print(f"  {key}: not present on this host, skipping",
                  file=sys.stderr)
            continue
        if cur == want:
            continue
        print(f"  {key}: {cur} -> {want}", file=sys.stderr)
        print(f"    reason: {why}", file=sys.stderr)
        if _sysctl_write(key, want):
            restore[key] = cur
        else:
            failed.append(key)
    if failed:
        print("apply-sysconfig: WARNING: could not apply "
              f"{', '.join(failed)}. Continuing without the tuning; "
              "a sanitizer instrumented VMM may report false crashes. "
              "Check that sudo is available and you can authenticate.",
              file=sys.stderr)
    return restore


def revert_sysconfigs(restore):
    """Restore the entries apply_sysconfigs changed. Best effort."""
    if not restore:
        return
    for key, old in list(restore.items()):
        if _sysctl_write(key, old):
            print(f"  sysctl: {key} restored to {old}", file=sys.stderr)
        del restore[key]


def enable_sysconfigs(vmm_path=None):
    """Apply the sysconfig registry and arrange automatic revert.

    Reverts on normal exit and on unhandled exceptions through atexit,
    and on the common fatal signals that would otherwise bypass atexit
    and leave the host changed. Only the values this process changed are
    restored. SIGKILL cannot be intercepted, but these sysctls are not
    persistent, so a reboot restores the boot default in that case.

    When vmm_path names a LeakSanitizer instrumented VMM, also grant it
    CAP_SYS_PTRACE so its exit time leak scan can ptrace the non dumpable
    VMM, the actual fix for the teardown abort that the yama sysctl alone
    does not address. The file capability is reverted the same way.
    Safe to call from any entry point that wants the tuning.
    """
    restore = apply_sysconfigs()
    cap_orig = apply_ptrace_cap(vmm_path)
    if not restore and cap_orig is None:
        return
    atexit.register(revert_sysconfigs, restore)
    atexit.register(revert_ptrace_cap, vmm_path, cap_orig)

    def _on_signal(signum, _frame):
        kill_all_processes()
        revert_sysconfigs(restore)
        revert_ptrace_cap(vmm_path, cap_orig)
        # Restore default disposition and re raise so the process dies
        # with the conventional status for the signal.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    # SIGINT and SIGTERM are the usual interactive and kill paths.
    # SIGHUP fires when the controlling terminal closes, SIGQUIT on
    # Ctrl backslash, SIGABRT on assert or abort. All bypass atexit
    # under their default disposition, so revert explicitly first.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP,
                signal.SIGQUIT, signal.SIGABRT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
