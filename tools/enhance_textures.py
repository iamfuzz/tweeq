#!/usr/bin/env python3
"""Texture upscaling for EQG model archives (the texture half of Tweeq's "Enhance").

Colour maps go through an upscaler engine, normal maps are resized and renormalised:

  * engine "esrgan"  : Real-ESRGAN x4 (realesrgan-ncnn-vulkan, installed separately by the user), then
                       Lanczos down to the target size.
  * engine "lanczos" : built-in fallback, no extra software: Lanczos + a light unsharp mask.
  * engine "auto"    : esrgan when it is installed and works, otherwise lanczos (with a warning).

Both engines bleed colour into fully transparent texels first (no dark halos) and resize alpha
separately. The DDS layout of each source is kept: uncompressed BGRA stays uncompressed BGRA, DXT1/3/5
is re-encoded as DXT1/DXT5, and a mip chain is rebuilt only if the source had one. Textures smaller
than MIN_SRC pixels (palettes, ramps) and ones already at/above the target are left alone, as are
formats this module cannot read (reported, never guessed at).

This module only transforms bytes: it never touches an EverQuest install.
"""
from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter

MIN_SRC = 64
SIZES = (512, 1024)
ESRGAN_TIMEOUT = 900


class TextureError(Exception):
    pass


# ----------------------------------------------------------------------------- DDS read / write
def dds_kind(d: bytes) -> str | None:
    """RGBA32 | DXT1 | DXT3 | DXT5, or None for anything else (DX10, palettised, 16/24-bit, TGA...)."""
    if len(d) < 128 or d[:4] != b"DDS ":
        return None
    fourcc = d[84:88]
    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        return fourcc.decode()
    bits, rm, gm, bm, am = struct.unpack_from("<IIIII", d, 88)
    if fourcc == b"\0\0\0\0" and bits == 32 and (rm, gm, bm, am) == (0xFF0000, 0xFF00, 0xFF, 0xFF000000):
        return "RGBA32"
    return None


def dds_has_mips(d: bytes) -> bool:
    return struct.unpack_from("<I", d, 28)[0] > 1


def read_dds(d: bytes) -> Image.Image:
    """Decode to RGBA in STORED row order (this is already EQ orientation; never flip)."""
    im = Image.open(io.BytesIO(d))
    im.load()
    return im.convert("RGBA")


def _levels(img: Image.Image, mips: bool) -> list[Image.Image]:
    out = [img]
    while mips and max(out[-1].size) > 1:
        w, h = out[-1].size
        out.append(out[-1].resize((max(1, w // 2), max(1, h // 2)), Image.BOX))
    return out


def write_dds(img: Image.Image, kind: str, mips: bool) -> bytes:
    """Encode `img` as a DDS of the given kind (RGBA32 | DXT1 | DXT3->DXT5 | DXT5)."""
    kind = "DXT5" if kind == "DXT3" else kind
    lv = _levels(img.convert("RGBA"), mips)
    if kind == "RGBA32":
        payload = [l.tobytes("raw", "BGRA") for l in lv]
        pf = struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    else:
        payload = []
        for l in lv:
            b = io.BytesIO()
            l.save(b, "DDS", pixel_format=kind)
            raw = b.getvalue()
            if raw[84:88] != kind.encode() or struct.unpack_from("<I", raw, 4)[0] != 124:
                raise TextureError("unexpected DDS layout from the encoder")
            payload.append(raw[128:])
        pf = struct.pack("<II4sIIIII", 32, 0x4, kind.encode(), 0, 0, 0, 0, 0)
    w, h = img.size
    flags = 0x1007 | 0x80000 | (0x20000 if mips else 0)
    caps = 0x1000 | (0x400008 if mips else 0)
    hdr = (b"DDS " + struct.pack("<7I", 124, flags, h, w, len(payload[0]), 0, len(lv) if mips else 0)
           + b"\0" * 44 + pf + struct.pack("<5I", caps, 0, 0, 0, 0))
    assert len(hdr) == 128
    return hdr + b"".join(payload)


def is_normal_map(name: str) -> bool:
    return name.lower().rsplit(".", 1)[0].endswith(("_n", "_nm", "_normal"))


# ----------------------------------------------------------------------------- image ops
def bleed(rgb: np.ndarray, valid: np.ndarray, iters: int = 16) -> np.ndarray:
    """Spread colours from valid texels into invalid ones (3x3 average per pass)."""
    rgb, valid = rgb.astype(np.float32).copy(), valid.copy()
    for _ in range(iters):
        if valid.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(valid.shape, np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                v = np.roll(valid, (dy, dx), (0, 1))
                acc += np.roll(rgb, (dy, dx), (0, 1)) * v[..., None]
                cnt += v
        fill = (~valid) & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill][:, None]
        valid |= fill
    return rgb


def upscale_normal(im: Image.Image, size: int) -> Image.Image:
    """Resize RGB and alpha SEPARATELY (Pillow premultiplies alpha on RGBA resize, which would zero the
    RGB of every alpha-0 texel; CTH's s01 normal map is alpha 0 over 96% of its area), then renormalise."""
    rgba = im.convert("RGBA")
    big = np.zeros((size, size, 4), np.float32)
    big[..., :3] = np.asarray(rgba.convert("RGB").resize((size, size), Image.LANCZOS))
    big[..., 3] = np.asarray(rgba.getchannel("A").resize((size, size), Image.LANCZOS))
    v = big[..., :3] / 255.0 * 2.0 - 1.0
    n = np.linalg.norm(v, axis=2, keepdims=True)
    v = np.where(n > 1e-6, v / np.maximum(n, 1e-6), np.array([0, 0, 1.0], np.float32))
    big[..., :3] = np.clip((v * 0.5 + 0.5) * 255.0 + 0.5, 0, 255)
    return Image.fromarray(big.astype(np.uint8), "RGBA")


def _native(p: str) -> str:
    """Path spelling the upscaler binary understands (WSL -> Windows path for a .exe)."""
    if sys.platform == "win32":
        return p
    r = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else p


def _work_dir(esrgan_dir: str | None) -> str:
    """Scratch folder for the upscaler's input/output PNGs. It lives NEXT TO the upscaler (same drive) so a
    Windows .exe launched from WSL can read it; falls back to the system temp folder."""
    if esrgan_dir:
        parent = os.path.dirname(os.path.abspath(esrgan_dir))
        try:
            return tempfile.mkdtemp(prefix=".work-", dir=parent)
        except OSError:
            pass
    return tempfile.mkdtemp(prefix="tweeq-tex-")


def find_binary(esrgan_dir: str) -> str | None:
    for n in ("realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan"):
        p = os.path.join(esrgan_dir, n)
        if os.path.isfile(p):
            return p
    return None


def esrgan_rgb(img: Image.Image, work: str, tag: str, esrgan_dir: str, model: str = "realesrgan-x4plus") -> Image.Image:
    exe = find_binary(esrgan_dir)
    if not exe:
        raise TextureError("realesrgan-ncnn-vulkan not found")
    src, dst = os.path.join(work, tag + "_in.png"), os.path.join(work, tag + "_out.png")
    img.convert("RGB").save(src)
    conv = _native if (exe.endswith(".exe") and sys.platform != "win32") else (lambda p: p)
    cmd = [exe, "-i", conv(src), "-o", conv(dst), "-n", model, "-m", conv(os.path.join(esrgan_dir, "models"))]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ESRGAN_TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise TextureError(f"Real-ESRGAN timed out on {tag}")
    if r.returncode != 0 or not os.path.exists(dst):
        raise TextureError(f"Real-ESRGAN failed on {tag}: {(r.stderr or r.stdout)[-200:].strip()}")
    return Image.open(dst).convert("RGB")


def upscale_color(im: Image.Image, size: int, engine: str, work: str, tag: str, esrgan_dir: str | None):
    """Returns (RGBA image, engine actually used, warning or None)."""
    a = np.asarray(im.convert("RGBA"))
    alpha = a[..., 3]
    filled = Image.fromarray(np.clip(bleed(a[..., :3], alpha > 0), 0, 255).astype(np.uint8))
    used, warn = "lanczos", None
    big = None
    if engine in ("esrgan", "auto") and esrgan_dir:
        try:
            big = esrgan_rgb(filled, work, tag, esrgan_dir).resize((size, size), Image.LANCZOS)
            used = "esrgan"
        except TextureError as e:
            if engine == "esrgan":
                raise
            warn = f"{tag}: {e}; used the built-in resize instead"
    elif engine == "esrgan":
        raise TextureError("the Real-ESRGAN upscaler is not installed")
    if big is None:
        big = filled.resize((size, size), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.2, 60, 2))
    out = big.convert("RGBA")
    out.putalpha(Image.fromarray(alpha).resize((size, size), Image.LANCZOS))
    return out, used, warn


# ----------------------------------------------------------------------------- planning / running
def _est_bytes(kind: str, size: int, mips: bool) -> int:
    base = {"DXT1": size * size // 2, "DXT3": size * size, "DXT5": size * size}.get(kind, size * size * 4)
    return int(base * (4 / 3 if mips else 1)) + 128


def plan_textures(files: dict[str, bytes], size: int) -> list[dict]:
    """What would happen to each .dds in the archive at target `size` (0 = textures off)."""
    rows = []
    for name, d in sorted(files.items()):
        if not name.lower().endswith(".dds"):
            continue
        kind = dds_kind(d)
        row = {"name": name, "bytes": len(d), "kind": kind}
        if kind is None:
            row.update(action="keep", why="format not supported for upscaling", w=0, h=0)
            rows.append(row)
            continue
        h, w = struct.unpack_from("<II", d, 12)
        row.update(w=w, h=h)
        if not size:
            row.update(action="keep", why="textures off")
        elif max(w, h) < MIN_SRC:
            row.update(action="keep", why=f"smaller than {MIN_SRC}px")
        elif max(w, h) >= size:
            row.update(action="keep", why=f"already {w}x{h}")
        else:
            row.update(action="normal" if is_normal_map(name) else "color", new_size=size,
                       new_bytes=_est_bytes(kind, size, dds_has_mips(d)))
        rows.append(row)
    return rows


def enhance_textures(files: dict[str, bytes], size: int, engine: str = "auto", esrgan_dir: str | None = None,
                     progress=None, cancel=None) -> tuple[dict[str, bytes], list[str]]:
    """Returns ({name: new bytes} for changed textures only, [warnings])."""
    if engine not in ("auto", "esrgan", "lanczos"):
        raise TextureError(f"unknown texture engine {engine!r}")
    todo = [r for r in plan_textures(files, size) if r["action"] in ("color", "normal")]
    out: dict[str, bytes] = {}
    warns: list[str] = []
    work = _work_dir(esrgan_dir)
    try:
        for i, r in enumerate(todo):
            if cancel and cancel():
                raise TextureError("cancelled")
            if progress:
                progress(f"texture {r['name']}", i / max(1, len(todo)))
            d = files[r["name"]]
            im = read_dds(d)
            if r["action"] == "normal":
                big = upscale_normal(im, size)
            else:
                tag = os.path.splitext(os.path.basename(r["name"]))[0]
                big, _used, w = upscale_color(im, size, engine, work, tag, esrgan_dir)
                if w:
                    warns.append(w)
            out[r["name"]] = write_dds(big, r["kind"], dds_has_mips(d))
        if progress:
            progress("textures done", 1.0)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out, warns
