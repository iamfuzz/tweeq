#!/usr/bin/env python3
"""scan-install: census every character/actor model in an EverQuest install.

Reads only the user's own client files. Writes installed_model_index.json:

  {"models": [{"tag", "format", "container", "member", ...}], "summary": {...}}

formats
  wld_skinned  s3d WLD actor whose ACTORDEF -> HIERARCHICALSPRITE (skeleton, animated)
  wld_static   s3d WLD actor that points straight at a mesh (no skeleton)
  eqg_mds      .mds skinned model inside an .eqg
  eqg_mod      animated .mod character model (container has .ani; zone props are NOT listed)
  eqg_other    .eqg members we do not classify (zone terrain etc.), counted only

    python3 tools/scan_install.py "$EQ" out/installed_model_index.json
"""
import json
import os
import struct
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3d import load  # noqa: E402

HASH_KEY = bytes([0x95, 0x3A, 0xC5, 0x2A, 0x95, 0x7A, 0x95, 0x6A])


def wld_parts(data):
    magic, _ver, frag_count = struct.unpack_from("<III", data, 0)
    if magic != 0x54503D02:
        raise ValueError("bad wld magic %08x" % magic)
    hash_len = struct.unpack_from("<I", data, 20)[0]
    table = bytes(b ^ HASH_KEY[i % 8] for i, b in enumerate(data[28:28 + hash_len]))
    off = 28 + hash_len
    frags = []
    for _ in range(frag_count):
        size, ftype = struct.unpack_from("<II", data, off)
        frags.append((ftype, data[off + 8:off + 8 + size]))
        off += 8 + size
    return table, frags


def name_at(table, ref):
    if ref >= 0:
        return ""
    s = -ref
    return table[s:table.index(b"\0", s)].decode("latin1")


def scan_wld(name, data, container):
    table, frags = wld_parts(data)
    out = []
    for ftype, body in frags:
        if ftype != 0x14 or len(body) < 20:
            continue
        nameref = struct.unpack_from("<i", body, 0)[0]
        tag = name_at(table, nameref)
        # ACTORDEF: nameRef, flags, callbackRef, nAction, nFragRef, boundsRef,
        # then a variable-size action table, then the fragment refs. The action
        # table size varies, so look for the first aligned ref that lands on a
        # skeleton (0x11) or mesh (0x2D/0x36) fragment.
        nfrag, = struct.unpack_from("<I", body, 16)
        target = None
        if nfrag:
            for o in range(24, len(body) - 3, 4):
                v = struct.unpack_from("<i", body, o)[0]
                if 0 < v <= len(frags) and frags[v - 1][0] in (0x11, 0x2D, 0x36):
                    target = frags[v - 1][0]
                    break
        fmt = "wld_skinned" if target == 0x11 else "wld_static"
        out.append({"tag": tag.replace("_ACTORDEF", "").lower(), "format": fmt,
                    "container": container, "member": name,
                    "fragment_target": target})
    return out


def scan_eqg(path, container):
    """Character models in an .eqg.

    `.mds` is a skinned model. `.mod` is also used for static zone props (101k of them), so a
    `.mod` counts as a character model ONLY when the container carries animations (`.ani`)
    and the `.mod` is the container's own model (stem == container stem, e.g. bat.eqg/bat.mod).
    """
    out, others = [], Counter()
    members = load(path)
    cstem = os.path.splitext(container)[0].lower()
    has_ani = any(k.lower().endswith(".ani") for k in members)
    for member, data in members.items():
        ext = member.rsplit(".", 1)[-1].lower() if "." in member else ""
        stem = member.rsplit(".", 1)[0].lower()
        if ext == "mds":
            out.append({"tag": stem, "format": "eqg_mds", "container": container,
                        "member": member, "bytes": len(data)})
        elif ext == "mod" and has_ani and stem == cstem:
            out.append({"tag": stem, "format": "eqg_mod", "container": container,
                        "member": member, "bytes": len(data)})
        else:
            others[ext] += 1
    return out, others


def main(eq_dir, out_path):
    t0 = time.time()
    models, errors = [], []
    other_ext = Counter()
    files = sorted(os.listdir(eq_dir))
    chr_s3d = [f for f in files if f.lower().endswith(".s3d") and "_chr" in f.lower()]
    eqg = [f for f in files if f.lower().endswith(".eqg")]
    for i, f in enumerate(chr_s3d):
        try:
            for member, data in load(os.path.join(eq_dir, f)).items():
                if member.lower().endswith(".wld"):
                    models += scan_wld(member, data, f)
        except Exception as e:  # noqa: BLE001 - census must not die on one file
            errors.append({"file": f, "error": repr(e)})
    print(f"chr s3d: {len(chr_s3d)} files, {time.time() - t0:.0f}s", flush=True)
    for i, f in enumerate(eqg):
        try:
            m, o = scan_eqg(os.path.join(eq_dir, f), f)
            models += m
            other_ext.update(o)
        except Exception as e:  # noqa: BLE001
            errors.append({"file": f, "error": repr(e)})
        if i % 500 == 499:
            print(f"eqg {i + 1}/{len(eqg)} {time.time() - t0:.0f}s", flush=True)
    summary = {
        "chr_s3d_files": len(chr_s3d), "eqg_files": len(eqg),
        "by_format": dict(Counter(m["format"] for m in models)),
        "unique_tags": len({m["tag"] for m in models}),
        "eqg_other_members": dict(other_ext.most_common(12)),
        "errors": len(errors),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump({"summary": summary, "models": models, "errors": errors}, fh)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
