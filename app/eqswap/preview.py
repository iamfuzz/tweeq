"""On-demand model previews: model tag -> cached .glb the Godot viewer can load.

Dev implementation shells out to tools/mds_to_gltf.py (EQG) and tools/wce_chr_to_gltf.py (WLD,
needs the `quail` binary). A packaged build replaces `TOOLS` with the frozen eq_toolkit.
Cache key = tag + source container size/mtime, so a patch that changes the model regenerates it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from .models import ModelIndex

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools")
TIMEOUT = 180
# Bump when the converters change so stale cached previews are rebuilt.
# v2: classic .bmp texture support in wce_chr_to_gltf.py
# v3: root-level props (Agnarr's staff) share the body's animation shift instead of floating off
PIPELINE_VERSION = 4  # v4: rigid meshes bound to the DAG that names them


def _pick(eq_dir: str, cands: list[dict]) -> dict | None:
    """Prefer the smallest archive that defines the tag (global_chr is huge to convert)."""
    best, best_size = None, None
    for c in cands:
        p = os.path.join(eq_dir, c["container"])
        if os.path.exists(p):
            sz = os.path.getsize(p)
            if best is None or sz < best_size:
                best, best_size = c, sz
    return best


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT}s"
    return r.returncode == 0, (r.stderr or r.stdout)[-400:]


def _eqg(eq_dir: str, cand: dict, siblings: int, tag: str, out: str) -> tuple[bool, str]:
    src = os.path.join(eq_dir, cand["container"])
    tool = os.path.join(TOOLS, "mds_to_gltf.py")
    if siblings <= 1:
        return _run([sys.executable, tool, "--animate", src, out])
    # several models in one archive: extract just this model's files (animations end in _<tag>.ani)
    sys.path.insert(0, TOOLS)
    from s3d import load  # noqa: E402
    tmp = tempfile.mkdtemp(prefix="eqprev-")
    try:
        for name, data in load(src).items():
            low = name.lower()
            if low.endswith(".ani") and not low.endswith(f"_{tag}.ani"):
                continue
            if low.endswith(".mds") and low != f"{tag}.mds":
                continue
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(data)
        return _run([sys.executable, tool, "--ani-dir", tmp, os.path.join(tmp, f"{tag}.mds"), tmp, out])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _wld(eq_dir: str, cand: dict, tag: str, out: str) -> tuple[bool, str]:
    return _run([sys.executable, os.path.join(TOOLS, "wce_chr_to_gltf.py"),
                 os.path.join(eq_dir, cand["container"]), out, "--actor", tag, "--quiet"])


def ensure_preview(eq_dir: str, index: ModelIndex, tag: str, out_dir: str) -> dict:
    """Return {"status": ok|cached|unsupported|error, "path", "format", "seconds", "error"}."""
    tag = tag.lower()
    cands = index.candidates(tag)
    if not cands:
        return {"status": "error", "error": f"{tag!r} is not in the model index"}
    cand = _pick(eq_dir, cands)
    if cand is None:
        return {"status": "error", "error": "model archive not found in the install"}
    fmt = cand["format"]
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{tag}.glb")
    meta_p = os.path.join(out_dir, f"{tag}.json")
    st = os.stat(os.path.join(eq_dir, cand["container"]))
    key = {"container": cand["container"], "size": st.st_size, "mtime": int(st.st_mtime),
           "pipeline": PIPELINE_VERSION}
    if os.path.exists(out) and os.path.exists(meta_p):
        try:
            with open(meta_p) as f:
                if json.load(f) == key:
                    return {"status": "cached", "path": out, "format": fmt, "seconds": 0.0}
        except (OSError, ValueError):
            pass
    if fmt not in ("eqg_mds", "wld_skinned"):
        return {"status": "unsupported", "format": fmt,
                "error": f"previews for {fmt} models are not implemented"}
    t0 = time.time()
    tmp_out = out[:-4] + ".part.glb"   # converters require a .glb suffix
    if fmt == "eqg_mds":
        # how many skinned models share this archive decides whether we can convert it directly
        n = sum(1 for cs in index.by_tag.values() for c in cs
                if c["container"] == cand["container"] and c["format"] == "eqg_mds")
        ok, msg = _eqg(eq_dir, cand, n, tag, tmp_out)
    else:
        ok, msg = _wld(eq_dir, cand, tag, tmp_out)
    if not ok or not os.path.exists(tmp_out):
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return {"status": "error", "format": fmt, "error": msg.strip() or "converter produced no file"}
    os.replace(tmp_out, out)
    with open(meta_p, "w") as f:
        json.dump(key, f)
    return {"status": "ok", "path": out, "format": fmt, "seconds": round(time.time() - t0, 2)}
