"""Supervise an offline compiler and stop it if the transaction owner exits."""

import os
import signal
import subprocess
import sys
import threading
import time


class _ParentGone(Exception):
    pass


def main():
    timeout = float(sys.argv[1])
    closed = threading.Event()

    def watch_parent():
        os.read(sys.stdin.fileno(), 1)
        closed.set()

    process = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL,
                               start_new_session=os.name != "nt",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    threading.Thread(target=watch_parent, daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if closed.wait(0.02):
                raise _ParentGone
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(sys.argv[2:], timeout)
    except (_ParentGone, subprocess.TimeoutExpired) as exc:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        return 124 if isinstance(exc, subprocess.TimeoutExpired) else 125
    # Map external exit 124 away from the supervisor's reserved timeout status.
    if process.returncode == 124:
        return 126
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
