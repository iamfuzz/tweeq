"""scan: read the user's own EverQuest files once and write what Tweeq needs to know about them.

Produces, next to each other in one per-install folder:
  installed_model_index.json   every character model and where it lives          (was tools/scan_install.py)
  model_compat.json            per-model facts for the pre-swap checks            (was tools/build_compat_index.py
                               + tools/anim_survey.py + the two weapon survey scripts)
  scan_meta.json               scanner version + install fingerprint + counts

It reads only the user's installed client files and writes nothing into the install. A fingerprint of the
model archives (names, sizes, mtimes) tells `status` whether the scan is still current, so it only runs again
after the game changes. Archives that Tweeq itself has enhanced are read from the vaulted ORIGINAL, so
enhancing a model never makes the scan look stale or learn about the enhanced file.

Cheap by design: the census lists archive directories without decompressing anything; only the ~545 character
.s3d files (their .wld) and the ~470 .eqg archives that hold skinned models (their .mds/.ani) are decompressed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import time
from collections import Counter, defaultdict

from .paths import TOOLS, replace_file

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

SCAN_VERSION = 1
GAP_UNITS = 0.25          # weapon-track translation gap vs bind that counts as "off the hand"
HAND_WORDS = ("HAND", "FING", "THMB", "WRST", "TWST")
CLIP_CODE = re.compile(r"^([A-Z]\d\d)")
SLOT_BONES = ("ARMR_WEAP", "ARML_WEAP", "ARML_SHLD")
INDEX_NAME, COMPAT_NAME, META_NAME = "installed_model_index.json", "model_compat.json", "scan_meta.json"


class ScanCancelled(Exception):
    pass


# ----------------------------------------------------------------------------- sources + fingerprint
def sources(eq_dir: str, base_path=None) -> list[tuple[str, str]]:
    """[(archive name, path to read)] for every character .s3d and .eqg in the install, sorted by name.

    `base_path(name) -> path | None` lets the caller substitute the vaulted original for a managed archive."""
    out = []
    for f in sorted(os.listdir(eq_dir)):
        low = f.lower()
        if (low.endswith(".s3d") and "_chr" in low) or low.endswith(".eqg"):
            out.append((f, (base_path(f) if base_path else None) or os.path.join(eq_dir, f)))
    return out


def fingerprint(srcs: list[tuple[str, str]]) -> str:
    h = hashlib.sha256(f"tweeq-scan-{SCAN_VERSION}\n".encode())
    for name, path in srcs:
        st = os.stat(path)
        h.update(f"{name.lower()}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def status(eq_dir: str, out_dir: str, base_path=None) -> dict:
    """Is the scan in `out_dir` current for this install? (stat calls only; well under a second)"""
    meta_p = os.path.join(out_dir, META_NAME)
    need = [os.path.join(out_dir, n) for n in (INDEX_NAME, COMPAT_NAME, META_NAME)]
    if not all(os.path.exists(p) for p in need):
        return {"fresh": False, "reason": "missing", "models": 0}
    try:
        with open(meta_p, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {"fresh": False, "reason": "missing", "models": 0}
    if meta.get("scanner_version") != SCAN_VERSION:
        return {"fresh": False, "reason": "version", "models": meta.get("models", 0)}
    if meta.get("fingerprint") != fingerprint(sources(eq_dir, base_path)):
        return {"fresh": False, "reason": "changed", "models": meta.get("models", 0)}
    return {"fresh": True, "reason": "ok", "models": meta.get("models", 0)}


# ----------------------------------------------------------------------------- WLD helpers
def wld_clips(data: bytes, tags: list[str]) -> dict[str, set]:
    """{tag: set(clip codes)} for each actor tag in this .wld (clips are 3-char codes prefixed onto TRACK names)."""
    from rig_survey import wld_skeleton_parents
    from scan_install import name_at, wld_parts
    table, frags = wld_parts(data)
    # an actor can reuse another model's skeleton (vsg uses GMF/GMN) and its tracks are named after the
    # SKELETON's tag; the first bone name carries that tag (e.g. TMT_DAG)
    skel_tag = {t: names[0].upper().replace("_DAG", "") for t, (names, _p) in wld_skeleton_parents(data).items()}
    by_skel: dict[str, set] = defaultdict(set)
    for ftype, body in frags:
        if ftype != 0x13:
            continue
        n = name_at(table, struct.unpack_from("<i", body, 0)[0]).upper()
        m = CLIP_CODE.match(n)
        if not m:
            continue
        rest = n[3:]
        for t in set(skel_tag.values()):
            if rest.startswith(t):
                by_skel[t].add(m.group(1))
    return {t: by_skel.get(skel_tag.get(t, ""), set()) for t in tags}


# ----------------------------------------------------------------------------- EQG survey of one archive
def _qd(a, b):
    import numpy as np
    a, b = np.array(a), np.array(b)
    return min(np.abs(a - b).max(), np.abs(a + b).max())


def survey_eqg(path: str, container: str, rows: list[dict]):
    """-> (anim rows, weapon survey rows, weapon track rows) for the skinned models of one .eqg."""
    import numpy as np
    import ani
    import mds
    from s3d import load
    files = load(path, want=lambda n: n.lower().endswith((".mds", ".ani")))
    anis = {k: v for k, v in files.items() if k.lower().endswith(".ani")}
    per: dict[str, set] = defaultdict(set)
    for member in anis:
        parts = member[:-4].lower().split("_")
        per[parts[-1]].add(parts[0])
    anim = [{"format": "eqg", "tag": t, "container": container, "clips": sorted(c)} for t, c in per.items()]
    weap, tracks = [], []
    for r in rows:
        data = files.get(r["member"]) or files.get(r["member"].lower())
        if data is None:
            continue
        try:
            m = mds.parse(data)
        except Exception:  # noqa: BLE001 - one unreadable model must not stop the scan
            continue
        par, names = m.bone_parents(), [b.name for b in m.bones]

        def parent_of(n):
            if n in names:
                i = names.index(n)
                return names[par[i]] if par[i] >= 0 else "-"
            return None

        tag = r["tag"].lower()
        mine = [k for k in anis if k.lower().endswith("_" + tag + ".ani")]
        weap.append({
            "cont": container, "tag": r["tag"], "mem": r["member"], "bones": len(names),
            "ARMR_WEAP": parent_of("ARMR_WEAP"), "ARML_WEAP": parent_of("ARML_WEAP"),
            "ARML_SHLD": parent_of("ARML_SHLD"), "ARMR_SHLD": parent_of("ARMR_SHLD"),
            "hands": [n for n in names if n.endswith("_HAND")],
            "clips": sorted({k.split("_")[0].lower() for k in mine}),
            "variants": sorted({k.split("_")[1].lower() for k in mine if k.count("_") >= 3}),
        })
        if not (weap[-1]["ARMR_WEAP"] or weap[-1]["ARML_WEAP"]) or r["tag"].endswith(("_lod1", "_lod2", "_bak")):
            continue
        ix = {b.name: i for i, b in enumerate(m.bones)}
        for k in mine:
            try:
                a = ani.parse(anis[k])
            except Exception:  # noqa: BLE001
                continue
            for t in a.bones:
                nm = t.name.strip()
                if nm not in SLOT_BONES or nm not in ix or not t.frames:
                    continue
                b = m.bones[ix[nm]]
                dt = max(np.abs(np.array(f.translation) - np.array(b.pivot)).max() for f in t.frames)
                dq = max(_qd(f.rotation, b.quaternion) for f in t.frames)
                tracks.append({"tag": r["tag"], "clip": k, "bone": nm, "exact": t.name == nm,
                               "dt": float(dt), "dq": float(dq), "n": len(t.frames)})
    return anim, weap, tracks


# ----------------------------------------------------------------------------- merge into model_compat.json
def _under_hand(parent):
    return None if parent is None else any(w in parent.upper() for w in HAND_WORDS)


def build_compat(idx_models: list[dict], anim_models: list[dict], mds_w: list[dict], tracks: list[dict],
                 wld_skel: list[tuple[str, list[tuple[str, list[str]]]]]) -> dict:
    """The model_compat.json document. `wld_skel` = [(container, [(actor tag, bone names)])], sorted by container."""
    out: dict[str, dict] = {}
    clip_map: dict = defaultdict(set)
    for m in anim_models:
        clip_map[(m["format"], m["tag"].lower())].update(m["clips"])

    tr = defaultdict(lambda: defaultdict(list))
    for t in tracks:
        tr[t["tag"].lower()][t["bone"].strip()].append(t)
    for r in mds_w:
        tag = r["tag"].lower()
        weapon = {
            "primary": r["ARMR_WEAP"] is not None, "secondary": r["ARML_WEAP"] is not None,
            "shield": r["ARML_SHLD"] is not None,
            "primary_parent": r["ARMR_WEAP"], "secondary_parent": r["ARML_WEAP"],
            "primary_under_hand": _under_hand(r["ARMR_WEAP"]),
            "secondary_under_hand": _under_hand(r["ARML_WEAP"]),
            "tracks": {},
        }
        for bone, recs in tr.get(tag, {}).items():
            exact = [x for x in recs if x["exact"]]
            use = exact or recs
            over = [x for x in use if x["dt"] > GAP_UNITS]
            weapon["tracks"][bone] = {"clips": len(use), "over": len(over),
                                      "max_gap": round(max(x["dt"] for x in use), 3), "exact": bool(exact)}
        out[tag] = {"format": "eqg_mds", "container": r["cont"], "bones": r["bones"],
                    "clips": sorted(r["clips"]), "extra_arms": any("CHEST_ARM" in h for h in r["hands"]),
                    "weapon": weapon}

    for c, actors in wld_skel:
        for tag, names in actors:
            up = {n.upper().replace("_DAG", "") for n in names}
            t = tag.lower()
            has = lambda suf: any(n.endswith(suf) for n in up)  # noqa: E731
            rec = {"format": "wld_skinned", "container": c, "bones": len(names),
                   "clips": sorted(clip_map.get(("wld", t), [])), "extra_arms": False,
                   "weapon": {"primary": has("R_POINT"), "secondary": has("L_POINT"), "shield": has("L_POINT"),
                              "primary_under_hand": None, "secondary_under_hand": None, "tracks": {}}}
            prev = out.get(t)
            if prev is None or (prev["format"] == "wld_skinned" and len(rec["clips"]) > len(prev["clips"])):
                out[t] = rec
    for m in idx_models:
        t = m["tag"].lower()
        if t not in out:
            out[t] = {"format": m["format"], "container": m["container"], "bones": None,
                      "clips": sorted(clip_map.get(("eqg", t), [])), "extra_arms": False, "weapon": None}

    common = {}
    for fmt in ("eqg_mds", "wld_skinned"):
        ms = [v for v in out.values() if v["format"] == fmt]
        cnt: dict = defaultdict(int)
        for v in ms:
            for c in v["clips"]:
                cnt[c] += 1
        common[fmt] = sorted(c for c, n in cnt.items() if n >= 0.70 * len(ms))
    return {"version": 1, "gap_units": GAP_UNITS, "common_clips": common, "models": out}


# ----------------------------------------------------------------------------- the scan
def _write_json(path: str, doc) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    replace_file(tmp, path)


def run(eq_dir: str, out_dir: str, base_path=None, progress=None, cancel=None) -> dict:
    """Scan the install and write the three files into `out_dir`. Nothing is written if cancelled."""
    from rig_survey import wld_skeleton_parents
    from s3d import list_members, load
    from scan_install import scan_eqg_members, scan_wld
    t0 = time.time()
    tell = progress or (lambda stage, pct: None)

    def check():
        if cancel and cancel():
            raise ScanCancelled("cancelled")

    srcs = sources(eq_dir, base_path)
    fp = fingerprint(srcs)
    chr_files = [(n, p) for n, p in srcs if n.lower().endswith(".s3d")]
    eqg_files = [(n, p) for n, p in srcs if n.lower().endswith(".eqg")]
    models: list[dict] = []
    errors: list[dict] = []
    anim: list[dict] = []
    wld_skel: list = []
    other_ext: Counter = Counter()

    # 1. classic models: decompress only the .wld of each character archive
    for i, (f, path) in enumerate(chr_files):
        check()
        tell("reading classic models", 0.0 + 0.15 * i / max(1, len(chr_files)))
        try:
            wlds = load(path, want=lambda n: n.lower().endswith(".wld"))
            per_member = {m: scan_wld(m, d, f) for m, d in wlds.items()}
            for rows in per_member.values():
                models += rows
            skinned = sorted({r["tag"] for rows in per_member.values() for r in rows if r["format"] == "wld_skinned"})
            if skinned:
                actors = []
                for member, data in wlds.items():
                    for t, c in wld_clips(data, skinned).items():
                        anim.append({"format": "wld", "tag": t, "container": f, "clips": sorted(c)})
                    actors += [(t, names) for t, (names, _p) in wld_skeleton_parents(data).items()]
                wld_skel.append((f, actors))
        except Exception as e:  # noqa: BLE001 - one bad archive must not stop the scan
            errors.append({"file": f, "error": repr(e)})

    # 2. census of every .eqg: directory listing only
    for i, (f, path) in enumerate(eqg_files):
        check()
        tell("listing models", 0.15 + 0.20 * i / max(1, len(eqg_files)))
        try:
            rows, others = scan_eqg_members(list_members(path), f)
            models += rows
            other_ext.update(others)
        except Exception as e:  # noqa: BLE001
            errors.append({"file": f, "error": repr(e)})

    # 3. skinned EQG models: bones, weapon slots, animation clips, weapon-track sanity
    by_cont: dict[str, list[dict]] = defaultdict(list)
    for m in models:
        if m["format"] == "eqg_mds":
            by_cont[m["container"]].append(m)
    paths = dict(eqg_files)
    weap: list[dict] = []
    tracks: list[dict] = []
    conts = sorted(by_cont)
    for i, c in enumerate(conts):
        check()
        tell("checking animations and weapon slots", 0.35 + 0.63 * i / max(1, len(conts)))
        try:
            a, w, t = survey_eqg(paths[c], c, by_cont[c])
            anim += a
            weap += w
            tracks += t
        except Exception as e:  # noqa: BLE001
            errors.append({"file": c, "error": repr(e)})
    check()

    tell("saving", 0.99)
    compat = build_compat(models, anim, weap, tracks, wld_skel)
    summary = {"chr_s3d_files": len(chr_files), "eqg_files": len(eqg_files),
               "by_format": dict(Counter(m["format"] for m in models)),
               "unique_tags": len({m["tag"] for m in models}),
               "eqg_other_members": dict(other_ext.most_common(12)), "errors": len(errors)}
    os.makedirs(out_dir, exist_ok=True)
    _write_json(os.path.join(out_dir, INDEX_NAME), {"summary": summary, "models": models, "errors": errors})
    _write_json(os.path.join(out_dir, COMPAT_NAME), compat)
    seconds = round(time.time() - t0, 1)
    _write_json(os.path.join(out_dir, META_NAME), {
        "scanner_version": SCAN_VERSION, "fingerprint": fp, "models": len({m["tag"] for m in models}),
        "entries": len(models), "seconds": seconds, "eq": eq_dir, "errors": len(errors)})
    tell("done", 1.0)
    return {"models": len({m["tag"] for m in models}), "entries": len(models), "compat": len(compat["models"]),
            "seconds": seconds, "errors": len(errors)}
