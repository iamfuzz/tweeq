"""Put the user's EverQuest files back before Tweeq is uninstalled.

Walks every vault under the per-user data folder, switches all of its decisions off and applies, so the
vaulted originals go back byte for byte. It never deletes a vault, and a file that a game patch has replaced
since (it no longer matches anything Tweeq wrote) is left alone and reported instead of being overwritten.
"""
from __future__ import annotations

import glob
import json
import os

from .discover import _running_client, to_native
from .engine import SwapError, Swapper, norm_dir


class ClientRunning(SwapError):
    pass


def vaults(data_root: str) -> list[str]:
    found = [os.path.join(data_root, "vault")] + sorted(glob.glob(os.path.join(data_root, "vault_*")))
    return [v for v in found if os.path.exists(os.path.join(v, "manifest.json"))]


def restore_all(data_root: str) -> dict:
    """{restored: [{vault, eq, files}], drifted: [paths], skipped: [{vault, reason}], errors: [..]}"""
    out: dict = {"restored": [], "drifted": [], "skipped": [], "errors": []}
    for v in vaults(data_root):
        try:
            with open(os.path.join(v, "manifest.json"), encoding="utf-8") as f:
                eq = to_native(json.load(f).get("eq_dir", ""))
        except (OSError, ValueError) as e:
            out["errors"].append({"vault": v, "error": f"unreadable manifest: {e}"})
            continue
        if not eq or not os.path.isdir(eq):
            out["skipped"].append({"vault": v, "reason": "the EverQuest folder is gone"})
            continue
        running = [norm_dir(p) for p in _running_client()]
        if norm_dir(eq) in running:
            raise ClientRunning("EverQuest is running: close it first")
        try:
            sw = Swapper(eq, v, None)
            sw.set_all_enabled(False)
            reps = sw.apply(skip_drifted=True)
        except SwapError as e:
            out["errors"].append({"vault": v, "error": str(e)})
            continue
        drift = [r.path for r in reps if r.state == "drifted"]
        out["drifted"] += drift
        out["restored"].append({"vault": v, "eq": eq, "files": [r.path for r in reps if r.state == "clean"]})
    return out
