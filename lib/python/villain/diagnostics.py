# SPDX-License-Identifier: Apache-2.0
"""Shared VMM output classification without workload policy."""

# ---------------------------------------------------------------------------
# Security severity triage. A curated test or a fuzz blob can drive the
# VMM into anything from a graceful rejection to host memory corruption.
# Scoring the VMM output into a tier lets both the test runner and the
# fuzzer prioritise by attacker
# capability rather than a flat fail count. The harness can flag that a
# corruption is reachable but cannot prove exploitability, which always
# needs manual analysis.

# Severity tiers, most severe first.
SECURITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE")

# AddressSanitizer report markers that mean guest controlled memory
# corruption in the host VMM, the precursor to a guest to host escape.
CORRUPTION_MARKERS = (
    "heap-use-after-free",
    "heap-buffer-overflow",
    "stack-buffer-overflow",
    "global-buffer-overflow",
    "stack-use-after-return",
    "use-after-poison",
    "attempting double-free",
    "AddressSanitizer: SEGV",
    "AddressSanitizer: access-violation",
)


def is_sanitizer_noise_line(line):
    """True if the line is LeakSanitizer ptrace teardown noise.

    A VMM built with LeakSanitizer emits these lines at exit when its
    StopTheWorld ptrace attach is denied. The VMM marks itself non
    dumpable (prctl PR_SET_DUMPABLE 0), so a same uid tracer is refused
    with EPERM regardless of kernel.yama.ptrace_scope or seccomp; only
    CAP_SYS_PTRACE clears it (see apply_ptrace_cap). They are not a
    guest triggered fault. Note the ptrace warning text mentions
    seccomp, which would otherwise be misread as a real seccomp
    violation by the triage below.
    """
    return (
        "ptrace appears to be blocked" in line
        or "LeakSanitizer has encountered a fatal error" in line
        or "LeakSanitizer does not work under ptrace" in line
        or "LSAN_OPTIONS=verbosity" in line
        or ("Child exited with signal" in line and "==" in line)
    )


def security_triage(output, returncode=None):
    """Map a VMM outcome to a security severity tier and rationale.

    Returns (tier, rationale). returncode is optional: the test runner
    triages from captured output alone, while the fuzzer also passes the
    process exit code so a bare fatal signal with no output still scores.
    Tiers, most severe first:

    CRITICAL  guest controlled memory corruption in the host VMM, the
              precursor to a guest to host escape. Exploitability still
              needs manual analysis; this only flags that corruption is
              reachable.
    HIGH      the guest reliably crashes, aborts or hangs the whole VMM
              process, a denial of service against that guest.
    MEDIUM    reserved for info leaks, which output alone cannot prove,
              so it is never assigned automatically.
    LOW       the guest wedges its own device but the VMM keeps running.
    NONE      the device gracefully refused the input, no impact.
    """
    if returncode in (-11, -4, -7):  # SIGSEGV, SIGILL, SIGBUS
        return ("CRITICAL", "VMM killed by a memory fault signal")
    if not output:
        if returncode == -1:
            return ("HIGH", "VMM hang or timeout (guest stalls the VMM)")
        return ("NONE", "no VMM output")

    asan = panic = segv = seccomp = needs_reset = hang = False
    for line in output.splitlines():
        if is_sanitizer_noise_line(line):
            continue
        if any(m in line for m in CORRUPTION_MARKERS):
            asan = True
        low = line.lower()
        if "panicked" in low:
            panic = True
        if "segv on unknown address" in low or "segmentation fault" in low:
            segv = True
        if "seccomp" in low and "is seccomp enabled" not in low:
            seccomp = True
        if "NEEDS_RESET" in line or "Corrupted request detected" in line:
            needs_reset = True
        if "TIMEOUT after" in line:
            hang = True

    if asan or segv:
        return ("CRITICAL",
                "guest controlled memory corruption in the host VMM "
                "(possible guest to host escape, needs manual analysis)")
    if panic:
        return ("HIGH", "VMM panic (guest crashes the whole VMM process)")
    if seccomp:
        return ("HIGH", "VMM killed by a seccomp violation")
    if hang:
        return ("HIGH", "VMM hang or timeout (guest stalls the VMM)")
    if needs_reset:
        return ("LOW",
                "device wedged with NEEDS_RESET "
                "(self DoS of the guest device, recoverable by reset)")
    return ("NONE", "device rejected the input gracefully")
