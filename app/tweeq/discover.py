"""Find the user's EverQuest install automatically.

A folder counts only if it really is an install: it must hold eqgame.exe AND racedata.txt.
Sources, best first (a running client is the most certain; scans are the fallback):
  env EQ_DIR -> running eqgame.exe -> registry uninstall entry -> standard Daybreak/SOE locations
  on every drive -> shallow scan of drive roots / Program Files / Games for folders named EverQuest*.
Works natively on Windows and from WSL (dev): paths are returned in the engine's own form plus a
Windows-style `display` string.
"""
from __future__ import annotations

import os
import re
import string
import subprocess

from .paths import NO_WINDOW

REQUIRED = ("eqgame.exe", "racedata.txt")
UNINSTALL_KEYS = [r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\EverQuest",
                  r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\EverQuest"]
STANDARD = ["Users/Public/Daybreak Game Company/Installed Games",
            "Program Files (x86)/Daybreak Game Company/Installed Games",
            "Program Files/Daybreak Game Company/Installed Games",
            "Daybreak Game Company/Installed Games",
            "Program Files (x86)/Sony/EverQuest", "Program Files (x86)/Sony Online Entertainment/Installed Games",
            "Program Files (x86)/Steam/steamapps/common", "SteamLibrary/steamapps/common", "Games", "EQ"]
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WSL_DRIVE = re.compile(r"^/mnt/([A-Za-z])/(.*)$")


def missing_files(path: str) -> list[str]:
    """Which of the required files are absent (case-insensitive)? [] means it is an install."""
    try:
        have = {n.lower() for n in os.listdir(path)}
    except OSError:
        return list(REQUIRED)
    return [r for r in REQUIRED if r not in have]


def is_eq_dir(path: str) -> bool:
    return not missing_files(path)


def to_native(p: str) -> str:
    """Windows path -> engine path (WSL form when not on Windows)."""
    p = p.strip().strip('"')
    if os.name != "nt" and _WIN_DRIVE.match(p):
        return "/mnt/%s/%s" % (p[0].lower(), p[3:].replace("\\", "/"))
    m = _WSL_DRIVE.match(p)
    if os.name == "nt" and m:                    # a manifest written by the WSL dev build: /mnt/c/x -> C:\x
        return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))
    return p


def to_display(p: str) -> str:
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\")) if m else p


def drives() -> list[str]:
    if os.name == "nt":
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return [f"/mnt/{c}" for c in string.ascii_lowercase if os.path.isdir(f"/mnt/{c}")]


def parse_reg_output(text: str) -> list[str]:
    """Candidate folders out of `reg query` text (InstallLocation / UninstallString / DisplayIcon)."""
    out = []
    for ln in text.splitlines():
        m = re.match(r"\s+(InstallLocation|UninstallString|DisplayIcon|InstallPath)\s+REG_\w+\s+(.*)$", ln.strip("\r"))
        if not m:
            continue
        v = m.group(2).strip().strip('"')
        v = re.sub(r"(?i)\\[^\\]+\.exe.*$", "", v) if v.lower().endswith(".exe") or ".exe" in v.lower() else v
        if v:
            out.append(v)
    return out


def _registry() -> list[str]:
    found: list[str] = []
    for key in UNINSTALL_KEYS:
        try:
            if os.name == "nt":
                import winreg  # type: ignore
                hive = winreg.HKEY_LOCAL_MACHINE
                sub = key.split("\\", 1)[1]
                with winreg.OpenKey(hive, sub) as k:
                    for name in ("InstallLocation", "UninstallString", "DisplayIcon"):
                        try:
                            found += parse_reg_output(f"    {name}    REG_SZ    {winreg.QueryValueEx(k, name)[0]}")
                        except OSError:
                            pass
            else:
                r = subprocess.run(["reg.exe", "query", key], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    found += parse_reg_output(r.stdout)
        except (OSError, subprocess.SubprocessError, ImportError):
            continue
    return found


def _running_client() -> list[str]:
    try:
        cmd = ["powershell.exe", "-NoProfile", "-Command",
               "(Get-Process eqgame -ErrorAction SilentlyContinue | Select-Object -First 1).Path"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
        p = r.stdout.strip()
        return [os.path.dirname(to_native(p)) if os.name != "nt" else os.path.dirname(p)] if p else []
    except (OSError, subprocess.SubprocessError):
        return []


def _scan_children(parent: str, found: list[tuple[str, str]], source: str) -> None:
    if is_eq_dir(parent):
        found.append((parent, source))
    try:
        for n in sorted(os.listdir(parent)):
            if n.lower().startswith("everquest"):
                p = os.path.join(parent, n)
                if os.path.isdir(p):
                    found.append((p, source))
    except OSError:
        pass


def discover(*, extra_roots: list[str] | None = None, drive_list: list[str] | None = None,
             use_registry: bool = True, use_process: bool = True, env: dict | None = None) -> list[dict]:
    env = os.environ if env is None else env
    raw: list[tuple[str, str]] = []
    if env.get("EQ_DIR"):
        raw.append((to_native(env["EQ_DIR"]), "EQ_DIR environment variable"))
    if use_process:
        raw += [(p, "running client") for p in _running_client()]
    if use_registry:
        raw += [(to_native(p), "registry") for p in _registry()]
    for root in extra_roots or []:
        _scan_children(root, raw, "given folder")
    for d in (drive_list if drive_list is not None else drives()):
        for sub in STANDARD:
            _scan_children(os.path.join(d, sub), raw, "standard location")
        _scan_children(d, raw, "drive scan")                      # D:\EverQuest etc.
        try:
            for n in os.listdir(d):
                if n.lower().startswith("program files"):
                    _scan_children(os.path.join(d, n), raw, "drive scan")
        except OSError:
            pass
    seen, out = set(), []
    for path, source in raw:
        key = os.path.normpath(path).lower()
        if key in seen or not is_eq_dir(path):
            continue
        seen.add(key)
        path = os.path.normpath(path)             # one separator style (the standard-location list uses "/")
        out.append({"path": path, "display": to_display(path), "source": source})
    return out
