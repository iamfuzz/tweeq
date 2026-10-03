"""Where things live, for a source checkout and for the installed (bundled) layout alike.

Installed layout:  <app>\\app\\tweeq (this package), <app>\\tools, <app>\\data, <app>\\bin\\quail.exe
Dev layout:        <repo>/app/tweeq,                <repo>/tools, <repo>/data
Both put `tools` and `data` two folders above this package, so the same code works in each.
"""
from __future__ import annotations

import os
import subprocess

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TOOLS = os.path.join(APP_ROOT, "tools")
DATA_DIR = os.path.join(APP_ROOT, "data")
BIN_DIR = os.path.join(APP_ROOT, "bin")

# Keep a console window from flashing up when the GUI starts a helper program on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def bundled_quail() -> str | None:
    for n in ("quail.exe", "quail"):
        p = os.path.join(BIN_DIR, n)
        if os.path.isfile(p):
            return p
    return None


class FileLock:
    """Exclusive, non-blocking lock on a file, released by the OS if the process dies (so there is never a stale lock to
    clean up). Used so two Tweeq processes never rewrite the same vault and game files at the same time."""

    def __init__(self, path: str):
        self.path = path
        self._f = None

    def holder(self) -> str:
        """Who holds the lock right now (best effort; byte 0 is the lock itself, the note starts at byte 1)."""
        try:
            with open(self.path, "rb") as f:
                f.seek(1)
                return f.read(200).decode("utf-8", "replace").strip()
        except OSError:
            return ""

    def acquire(self, wait: float = 3.0) -> bool:
        """Try for up to `wait` seconds (the previous owner may be a moment away from releasing)."""
        import time
        deadline = time.monotonic() + wait
        while True:
            f = open(self.path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.1)
                continue
            self._f = f
            try:
                import sys
                f.seek(1)
                f.truncate(1)
                f.write(f"pid {os.getpid()}: {' '.join(sys.argv[-4:])}".encode()[:200])
                f.flush()
            except OSError:
                pass
            return True

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


def replace_file(src: str, dst: str, attempts: int = 10) -> None:
    """os.replace that survives Windows' habit of refusing it for a moment (antivirus, the search indexer or another
    program has the target open) and a target carrying the read-only attribute. Raises only if it still cannot work."""
    import stat
    import time
    last = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last = e
            try:                                     # a read-only target is the other common cause
                if os.path.exists(dst) and not os.access(dst, os.W_OK):
                    os.chmod(dst, os.stat(dst).st_mode | stat.S_IWRITE)
            except OSError:
                pass
            time.sleep(0.05 * (i + 1))
    raise last
