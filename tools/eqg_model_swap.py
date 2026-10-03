#!/usr/bin/env python3
"""Swap one EQG character model into another model's slot.

Takes an extracted source model directory (e.g. CTH's files) and rebuilds it
under a different 3-letter model prefix, then packs it to <target>.eqg and
optionally deploys it into the live EverQuest directory.

Both prefixes MUST be the same byte length so that in-file string tables keep
their offsets valid (all EQ model codes are 3 chars, so this is normally free).

Texture files (.dds/.tga/.png) are copied VERBATIM -- the prefix substitution is
only applied to the structured formats (.mds/.mod/.ani/.lay/.pts/.prt), because
the 3-byte source prefix can occur by chance inside compressed texel data and
rewriting it there silently corrupts pixel blocks.

Usage:
    python3 tools/eqg_model_swap.py --src <dir> --from cth --to gra [--deploy]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EQ_DIR = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"
VANILLA_BACKUP = "/mnt/c/Users/brian/EQ_Launcher/backups/vanilla"
QUAIL = os.path.expanduser("~/go/bin/quail")

# Extensions whose *contents* carry the model prefix in string tables.
REWRITE_EXTS = {".mds", ".mod", ".ani", ".lay", ".pts", ".prt", ".mod"}
# Extensions to copy byte-for-byte.
VERBATIM_EXTS = {".dds", ".tga", ".png", ".bmp", ".ms", ".bat"}


def substitute(data: bytes, old: bytes, new: bytes) -> bytes:
    return data.replace(old, new).replace(old.upper(), new.upper())


def build(src_dir: str, old: str, new: str, work_root: str) -> str:
    if len(old) != len(new):
        raise SystemExit(
            f"prefix length mismatch: {old!r} ({len(old)}) vs {new!r} ({len(new)}); "
            "string-table offsets would break"
        )

    dst_dir = os.path.join(work_root, f"{new}_dst")
    if os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)

    ob, nb = old.encode(), new.encode()
    rewritten, copied = 0, 0

    for fname in sorted(os.listdir(src_dir)):
        spath = os.path.join(src_dir, fname)
        if not os.path.isfile(spath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        data = open(spath, "rb").read()

        new_fname = fname.replace(old, new).replace(old.upper(), new.upper())

        if ext in VERBATIM_EXTS:
            # Never touch texel data. The FILENAME must follow the same prefix
            # rewrite the .mds applies to its material references, otherwise a
            # source whose textures carry the tag (c_ork_bd_s00_c.dds) ships
            # references to c_orc_* files that do not exist. Models whose texture
            # names lack the tag (cth: c_ct_bd_*) are unaffected.
            copied += 1
        elif ext in REWRITE_EXTS:
            data = substitute(data, ob, nb)
            rewritten += 1
        else:
            print(f"  ! unknown extension {ext} for {fname}: copying verbatim")
            new_fname = fname
            copied += 1

        open(os.path.join(dst_dir, new_fname), "wb").write(data)

    print(f"  built {dst_dir}: {rewritten} rewritten, {copied} verbatim")
    return dst_dir


def pack(dst_dir: str, new: str, work_root: str) -> str:
    out = os.path.join(work_root, f"{new}.eqg")
    if os.path.exists(out):
        os.remove(out)
    res = subprocess.run(
        [QUAIL, "zip", dst_dir, out], capture_output=True, text=True
    )
    if res.returncode != 0 or not os.path.exists(out):
        raise SystemExit(f"quail zip failed:\n{res.stdout}\n{res.stderr}")
    print(f"  packed {out} ({os.path.getsize(out)} bytes)")
    return out


def verify(eqg_path: str, new: str) -> None:
    """Fail loudly rather than shipping a model the client will silently drop."""
    from s3d import load
    import mds

    arc = load(eqg_path)
    names = set(arc)

    model_file = f"{new}.mds" if f"{new}.mds" in names else f"{new}.mod"
    if model_file not in names:
        raise SystemExit(f"VERIFY FAIL: archive has no {new}.mds/.mod (has {sorted(names)[:5]})")

    m = mds.load_from_eqg(eqg_path, model_file)
    verts = sum(len(mo.vertices) for mo in m.models)
    faces = sum(len(mo.faces) for mo in m.models)
    print(f"  verify: {model_file} parses -- {verts} verts, {faces} faces, "
          f"{len(m.bones)} bones, {len(m.materials)} materials")

    missing = []
    for mat in m.materials:
        for p in mat.params:
            if isinstance(p.value, str) and p.value.lower().endswith(".dds"):
                if p.value.lower() not in names:
                    missing.append(p.value)
    if missing:
        raise SystemExit(f"VERIFY FAIL: materials reference absent textures: {missing}")
    print(f"  verify: all material textures present in archive")

    n_ani = sum(1 for k in names if k.endswith(".ani"))
    bad = [k for k in names if k.endswith(".ani") and not k.endswith(f"_{new}.ani")]
    if bad:
        raise SystemExit(f"VERIFY FAIL: animations not renamed to _{new}.ani: {bad[:5]}")
    print(f"  verify: {n_ani} animations, all named *_{new}.ani")


def deploy(eqg_path: str, new: str) -> None:
    live = os.path.join(EQ_DIR, f"{new}.eqg")
    backup = os.path.join(VANILLA_BACKUP, f"{new}.eqg")

    if os.path.exists(live) and not os.path.exists(backup):
        os.makedirs(VANILLA_BACKUP, exist_ok=True)
        shutil.copy2(live, backup)
        print(f"  backed up vanilla -> {backup} ({os.path.getsize(backup)} bytes)")
    elif os.path.exists(backup):
        print(f"  vanilla backup already present ({os.path.getsize(backup)} bytes) -- not overwriting")

    shutil.copy2(eqg_path, live)
    print(f"  DEPLOYED -> {live} ({os.path.getsize(live)} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="directory of extracted source model files")
    ap.add_argument("--from", dest="old", required=True, help="source model prefix, e.g. cth")
    ap.add_argument("--to", dest="new", required=True, help="target model prefix, e.g. gra")
    ap.add_argument("--work", default=None, help="working directory")
    ap.add_argument("--deploy", action="store_true", help="copy into the live EQ directory")
    args = ap.parse_args()

    work = args.work or os.path.join(
        os.path.dirname(os.path.abspath(args.src.rstrip("/"))), f"swap_{args.new}"
    )
    os.makedirs(work, exist_ok=True)

    print(f"[{args.old} -> {args.new}]")
    dst = build(args.src, args.old, args.new, work)
    eqg = pack(dst, args.new, work)
    verify(eqg, args.new)
    if args.deploy:
        deploy(eqg, args.new)
    else:
        print("  (not deployed; pass --deploy)")


if __name__ == "__main__":
    main()
