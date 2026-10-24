# SPDX-License-Identifier: Apache-2.0
"""Separate console stream capture and terminal handling."""

import codecs
import fcntl
import os
import sys
import termios


class FileFollower:
    """Display appended log bytes while leaving the complete log on disk."""

    def __init__(self, path, destination):
        self.stream = path.open("rb")
        self.destination = destination
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def pump(self):
        remaining = os.fstat(self.stream.fileno()).st_size - self.stream.tell()
        while remaining > 0:
            chunk = self.stream.read(min(remaining, 4096))
            if not chunk:
                break
            remaining -= len(chunk)
            binary = getattr(self.destination, "buffer", None)
            if binary is not None:
                binary.write(chunk)
                binary.flush()
            else:
                self.destination.write(self.decoder.decode(chunk))
                self.destination.flush()

    def close(self):
        try:
            self.pump()
            text = self.decoder.decode(b"", final=True)
            if text:
                self.destination.write(text)
                self.destination.flush()
        finally:
            self.stream.close()


def save_terminal():
    """Save terminal attributes if stdout is a tty."""
    try:
        return termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError):
        return None


def restore_terminal(saved):
    """Restore terminal attributes."""
    if saved is None:
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    except (termios.error, OSError):
        pass


def merge_streams(stdout_text, err_chunks):
    """Combine the clean marker stream with the VMM log stream.

    The VMM writes verdict markers on its stdout and its own log lines
    on stderr. They are captured on separate pipes so a log line never
    splices into a marker. Append the log stream after the guest output
    for logging and triage rather than interleaving it, so markers stay
    intact for parsing while the VMM logs remain available.
    """
    if not err_chunks:
        return stdout_text
    err_text = b"".join(err_chunks).decode(errors="replace")
    if not err_text:
        return stdout_text
    return stdout_text + "\n" + err_text


def ctty_preexec(slave_name):
    """Return a preexec_fn that starts a new session and claims *slave_name*
    as the controlling terminal.

    OpenVMM always runs its interactive REPL, whose input thread calls
    crossterm enable_raw_mode on the controlling terminal. With no
    controlling terminal that call fails with ENXIO and the resulting
    thread panic tears the whole VMM down, which the runner would misread
    as a guest triggered crash. Giving the child its own pty satisfies the
    raw mode call while the verdict markers still ride the stdout pipe.

    The body runs in the forked child before exec, so it stays limited to
    plain syscalls and touches no Python level locks.
    """
    def _pre():
        os.setsid()
        fd = os.open(slave_name, os.O_RDWR)
        try:
            fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
        except OSError:
            pass
        os.close(fd)
    return _pre


def drain_stream(stream, sink, stream_live):
    """Capture VMM diagnostics separately and optionally display them live."""
    try:
        for chunk in iter(lambda: stream.read1(4096), b""):
            sink.append(chunk)
            if stream_live:
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()
    except (OSError, ValueError):
        pass
