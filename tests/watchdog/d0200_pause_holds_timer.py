# SPDX-License-Identifier: Apache-2.0
"""
D0200 sidecar. Pauses then resumes the running VM. The guest checks
that the watchdog device does not surface FAILED or NEEDS_RESET.
"""
import time

SUPPORTED_BACKENDS = ("ch",)
REQUIRED_TOOLS = {"ch": ("ch-remote",)}


def run(ctx):
    if ctx.backend.name != "ch":
        ctx.skip("backend is not CH")
    if ctx.api_sock is None:
        ctx.skip("api socket disabled")
    if not ctx.wait_text("[vv]", timeout=20.0):
        raise RuntimeError("guest harness banner not seen")

    cp = ctx.vm_api("pause")
    if cp.returncode != 0:
        ctx.log(f"pause failed rc={cp.returncode} stderr={cp.stderr!r}")
        raise RuntimeError("ch-remote pause failed")
    ctx.log("paused")
    time.sleep(0.5)
    cp = ctx.vm_api("resume")
    if cp.returncode != 0:
        ctx.log(f"resume failed rc={cp.returncode} stderr={cp.stderr!r}")
        raise RuntimeError("ch-remote resume failed")
    ctx.log("resumed")
