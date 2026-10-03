"""Enhance: raise an EQG model's polygon count and/or texture resolution.

Pure byte-in / byte-out so the engine can keep the vanilla archive in its vault and rebuild the
enhanced one on demand:   build_enhanced(vanilla_archive_bytes, Params) -> enhanced_archive_bytes

Steps: `quail unzip` -> midpoint-subdivide every .mds (tools/mds_subdivide.py) -> upscale the textures
(tools/enhance_textures.py) -> `quail zip` -> re-read and verify the result. EQG archives are always
repacked with unzip/zip, never `quail convert` (convert cannot take a directory, and rewrites WLDs).
Only EQG (`eqg_mds`) models are supported; classic WLD models are refused with a clear message.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from .paths import BIN_DIR, NO_WINDOW, TOOLS  # noqa: E402
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

ENGINES = ("auto", "esrgan", "lanczos")
UPSCALER_VERSION = "v0.2.5.0"
UPSCALER_ZIP_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
                    "realesrgan-ncnn-vulkan-20220424-windows.zip")
UPSCALER_ZIP_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
QUAIL_TIMEOUT = 300


class EnhanceError(Exception):
    pass


@dataclass(frozen=True)
class Params:
    passes: int = 0          # subdivision passes: 0 = off, 1 = 4x faces, 2 = 16x faces
    tex_size: int = 0        # 0 = off, 512 or 1024
    engine: str = "auto"     # auto | esrgan | lanczos

    def validate(self) -> "Params":
        if self.passes not in (0, 1, 2):
            raise EnhanceError("passes must be 0, 1 or 2")
        if self.tex_size not in (0, 512, 1024):
            raise EnhanceError("texture size must be 0 (off), 512 or 1024")
        if self.engine not in ENGINES:
            raise EnhanceError(f"engine must be one of {', '.join(ENGINES)}")
        if not self.passes and not self.tex_size:
            raise EnhanceError("choose a polygon pass count and/or a texture size")
        return self

    def resolved(self, esrgan_dir: str | None) -> "Params":
        """`auto` becomes the engine that will really run (so the cache key tells the truth)."""
        if not self.tex_size:
            return Params(self.passes, 0, "lanczos")
        if self.engine == "auto":
            return Params(self.passes, self.tex_size, "esrgan" if esrgan_dir else "lanczos")
        return self

    def key(self) -> str:
        return f"p{self.passes}-t{self.tex_size}-{self.engine}"

    def to_dict(self) -> dict:
        return {"passes": self.passes, "tex_size": self.tex_size, "engine": self.engine}

    @staticmethod
    def from_dict(d: dict) -> "Params":
        return Params(int(d.get("passes", 0)), int(d.get("tex_size", 0)), d.get("engine", "auto"))


# ----------------------------------------------------------------------------- tool discovery
def find_quail(extra_dir: str | None = None) -> str:
    cands = [os.environ.get("TWEEQ_QUAIL"), os.path.join(BIN_DIR, "quail.exe"), os.path.join(BIN_DIR, "quail")]
    if extra_dir:
        cands += [os.path.join(extra_dir, "quail"), os.path.join(extra_dir, "quail.exe")]
    cands += [shutil.which("quail"), os.path.expanduser("~/go/bin/quail")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise EnhanceError("the quail tool was not found (install quail v1.6.0 or set TWEEQ_QUAIL)")


def find_esrgan(vault_dir: str | None = None, configured: str | None = None) -> str | None:
    """Folder holding a usable realesrgan-ncnn-vulkan (+ models/), or None."""
    from enhance_textures import find_binary
    tools = os.path.join(os.path.dirname(os.path.abspath(vault_dir)), "tools", "realesrgan") if vault_dir else None
    for d in (configured, os.environ.get("TWEEQ_ESRGAN"), tools):
        if d and os.path.isdir(d) and find_binary(d) and os.path.isdir(os.path.join(d, "models")):
            return d
    return None


def upscaler_dir(vault_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(vault_dir)), "tools", "realesrgan")


# ----------------------------------------------------------------------------- archive helpers
def _run_quail(quail: str, args: list[str], cwd: str | None = None) -> None:
    try:
        r = subprocess.run([quail, *args], capture_output=True, text=True, timeout=QUAIL_TIMEOUT,
                           creationflags=NO_WINDOW, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise EnhanceError(f"quail {args[0]} timed out")
    if r.returncode != 0:
        raise EnhanceError(f"quail {args[0]} failed: {(r.stderr or r.stdout)[-300:].strip()}")


def _load_members(path: str) -> dict[str, bytes]:
    from s3d import load
    return load(path)


def inspect(archive_path: str) -> dict:
    """Read an EQG archive: its models and textures, without changing anything."""
    import mds as mdsmod
    files = _load_members(archive_path)
    mds_names = sorted(n for n in files if n.lower().endswith(".mds"))
    if not mds_names:
        raise EnhanceError("this archive has no .mds model; only EQG models can be enhanced")
    models = []
    for n in mds_names:
        m = mdsmod.parse(files[n])
        models.append((n, m))
    return {"files": files, "mds": models}


def plan(archive_path: str, params: Params, esrgan_dir: str | None) -> dict:
    """Read-only estimate of what Enhance would do to this archive."""
    import mds_subdivide as sd
    import enhance_textures as et
    params.validate()
    info = inspect(archive_path)
    eff = params.resolved(esrgan_dir)
    warnings: list[str] = []
    models, blocked = [], None
    for name, m in info["mds"]:
        for model in m.models:
            before = {"verts": len(model.vertices), "faces": len(model.faces)}
            after = sd.predict(model, params.passes)
            row = {"name": model.name, "verts_before": before["verts"], "faces_before": before["faces"],
                   "verts_after": after["verts"], "faces_after": after["faces"]}
            if after["verts"] > sd.MAX_VERTS:
                row["blocked"] = (f"{after['verts']:,} vertices is over the {sd.MAX_VERTS:,} limit; "
                                  "use fewer passes")
                blocked = blocked or f"{model.name}: {row['blocked']}"
            models.append(row)
    tex = et.plan_textures(info["files"], params.tex_size)
    n_models = len(info["mds"])
    if n_models > 1:
        warnings.append(f"this archive holds {n_models} models; all of them are enhanced together")
    if params.tex_size and params.engine == "esrgan" and not esrgan_dir:
        blocked = blocked or "the Real-ESRGAN upscaler is not installed"
    if params.tex_size and eff.engine == "lanczos" and params.engine == "auto":
        warnings.append("the AI upscaler is not installed; textures use the built-in resize")
    unreadable = [t["name"] for t in tex if t["action"] == "keep" and t.get("why", "").startswith("format")]
    if unreadable:
        warnings.append(f"{len(unreadable)} texture(s) use a format that cannot be upscaled and stay as they are")
    if not params.passes and not any(t["action"] in ("color", "normal") for t in tex):
        blocked = blocked or "nothing to do: every texture is already at least that size"
    size_now = os.path.getsize(archive_path)
    size_after = size_now + sum(t.get("new_bytes", 0) - t["bytes"] for t in tex if t["action"] in ("color", "normal"))
    if params.passes:
        for _n, m in info["mds"]:
            for model in m.models:
                grow = sd.predict(model, params.passes)
                size_after += (grow["verts"] - len(model.vertices)) * 80 + (grow["faces"] - len(model.faces)) * 20
    return {"container": os.path.basename(archive_path), "params": params.to_dict(), "engine": eff.engine,
            "models": models, "textures": tex, "bytes_before": size_now, "bytes_after_estimate": size_after,
            "esrgan": bool(esrgan_dir), "warnings": warnings, "blocked": blocked}


# ----------------------------------------------------------------------------- build
def build_enhanced(base: bytes, params: Params, esrgan_dir: str | None = None, quail: str | None = None,
                   progress=None, cancel=None) -> tuple[bytes, dict]:
    """Enhance an EQG archive (given as bytes). Returns (new archive bytes, report). Raises EnhanceError."""
    import mds as mdsmod
    import mds_subdivide as sd
    import enhance_textures as et
    params.validate()
    quail = quail or find_quail()
    tell = progress or (lambda stage, pct: None)
    stop = cancel or (lambda: False)
    work = tempfile.mkdtemp(prefix="tweeq-enh-")
    try:
        src = os.path.join(work, "in.eqg")
        out = os.path.join(work, "out.eqg")
        tree = os.path.join(work, "x")
        with open(src, "wb") as f:
            f.write(base)
        tell("unpacking", 0.02)
        # quail reads `archive:member` from its archive argument, so a Windows drive path (C:\...) is cut at the colon:
        # unzip must be given just the file name, from the archive's own folder. (zip and convert take full paths.)
        _run_quail(quail, ["unzip", os.path.basename(src), tree], cwd=os.path.dirname(src))
        names = sorted(os.listdir(tree))
        mds_files = [n for n in names if n.lower().endswith(".mds")]
        if not mds_files:
            raise EnhanceError("this archive has no .mds model; only EQG models can be enhanced")
        before = _load_members(src)
        report: dict = {"models": [], "textures": [], "warnings": []}

        if params.passes:
            for i, n in enumerate(mds_files):
                tell(f"polygons: {n}", 0.05 + 0.15 * i / len(mds_files))
                path = os.path.join(tree, n)
                m = mdsmod.load(path)
                try:
                    report["models"] += sd.subdivide_mds(m, params.passes)
                except sd.SubdivideError as e:
                    raise EnhanceError(str(e))
                mdsmod.save(m, path)
        if stop():
            raise EnhanceError("cancelled")

        if params.tex_size:
            dds = {n: before[n] for n in before if n.lower().endswith(".dds")}

            def sub(stage, pct):
                tell(stage, 0.2 + 0.7 * pct)
            try:
                newtex, warns = et.enhance_textures(dds, params.tex_size, params.engine, esrgan_dir, sub, cancel)
            except et.TextureError as e:
                raise EnhanceError(str(e))
            report["warnings"] += warns
            for n, data in newtex.items():
                with open(os.path.join(tree, n), "wb") as f:
                    f.write(data)
            report["textures"] = sorted(newtex)

        if not report["models"] and not report["textures"]:
            raise EnhanceError("nothing to enhance: every texture is already at least that size")
        tell("repacking", 0.92)
        _run_quail(quail, ["zip", tree, out])
        if not os.path.exists(out):
            raise EnhanceError("quail zip produced no archive")
        _verify(src, out, before, mds_files, params, report)
        with open(out, "rb") as f:
            data = f.read()
        report["bytes_in"], report["bytes_out"] = len(base), len(data)
        tell("done", 1.0)
        return data, report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _verify(src: str, out: str, before: dict, mds_files: list[str], params: Params, report: dict) -> None:
    """Re-read the repacked archive: same members, untouched ones byte-identical, models parse with the
    predicted counts, enhanced textures decode at the new size."""
    import mds as mdsmod
    import enhance_textures as et
    after = _load_members(out)
    if set(after) != set(before):
        raise EnhanceError("verify: the repacked archive has different members than the original")
    changed = set(report["textures"]) | (set(mds_files) if params.passes else set())
    for n in before:
        if n not in changed and after[n] != before[n]:
            raise EnhanceError(f"verify: untouched member {n} changed during the rebuild")
    if params.passes:
        want = {s["name"]: s for s in report["models"]}
        for n in mds_files:
            for model in mdsmod.parse(after[n]).models:
                w = want.get(model.name)
                if not w or (len(model.vertices), len(model.faces)) != (w["verts_after"], w["faces_after"]):
                    raise EnhanceError(f"verify: model {model.name} does not match the expected counts")
    for n in report["textures"]:
        im = et.read_dds(after[n])
        if max(im.size) != params.tex_size:
            raise EnhanceError(f"verify: texture {n} is {im.size}, expected {params.tex_size}")


def params_hash(base_sha: str, params: Params) -> str:
    return hashlib.sha256(f"{base_sha}|{params.key()}".encode()).hexdigest()[:20]


# ----------------------------------------------------------------------------- upscaler install
def install_upscaler(vault_dir: str, zip_path: str | None = None, progress=None) -> dict:
    """Install the Real-ESRGAN ncnn-vulkan runtime into <app data>/tools/realesrgan.

    Downloads the official release (or uses a zip the user already has), refuses anything whose sha256
    differs from the pinned value, extracts with a path-traversal guard, then runs a tiny upscale to
    prove the GPU/Vulkan side works."""
    import urllib.request
    import zipfile
    tell = progress or (lambda stage, pct: None)
    dest = upscaler_dir(vault_dir)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".install-", dir=os.path.dirname(dest))   # same drive as the final folder
    try:
        if zip_path is None:
            zip_path = os.path.join(tmp, "realesrgan.zip")
            tell("downloading", 0.05)
            try:
                with urllib.request.urlopen(UPSCALER_ZIP_URL, timeout=60) as r, open(zip_path, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    got = 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            tell("downloading", 0.05 + 0.6 * got / total)
            except OSError as e:
                raise EnhanceError(f"download failed: {e}")
        h = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != UPSCALER_ZIP_SHA256:
            raise EnhanceError("the upscaler download does not match the expected checksum; nothing was installed")
        tell("extracting", 0.7)
        stage = os.path.join(tmp, "x")
        with zipfile.ZipFile(zip_path) as z:
            root = os.path.realpath(stage)
            for info in z.infolist():
                target = os.path.realpath(os.path.join(stage, info.filename))
                if not (target == root or target.startswith(root + os.sep)):
                    raise EnhanceError("the upscaler archive contains an unsafe path; nothing was installed")
            z.extractall(stage)
        from enhance_textures import find_binary
        exe = find_binary(stage)
        if not exe or not os.path.isdir(os.path.join(stage, "models")):
            raise EnhanceError("the upscaler archive is not laid out as expected")
        os.chmod(exe, 0o755)            # zip extraction drops the execute bit (matters under WSL/Linux)
        tell("testing", 0.85)
        _selftest(stage)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(stage, dest)
        tell("done", 1.0)
        return {"installed": True, "path": dest, "version": UPSCALER_VERSION}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest(esrgan_dir: str) -> None:
    from PIL import Image
    import enhance_textures as et
    work = et._work_dir(esrgan_dir)
    try:
        out = et.esrgan_rgb(Image.new("RGB", (32, 32), (90, 120, 60)), work, "selftest", esrgan_dir)
        if out.size != (128, 128):
            raise EnhanceError("the upscaler ran but returned an unexpected image")
    except et.TextureError as e:
        raise EnhanceError(f"the upscaler could not run on this PC (it needs a Vulkan-capable GPU): {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove_upscaler(vault_dir: str) -> bool:
    d = upscaler_dir(vault_dir)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False
