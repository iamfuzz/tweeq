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
