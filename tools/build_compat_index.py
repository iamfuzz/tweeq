#!/usr/bin/env python3
"""Build data/model_compat.json: per-model facts the swapper's pre-swap checks need.

Sources (all offline, from the user's own install):
  data/installed_model_index.json   tools/scan_install.py        which models exist, where
  data/anim_survey.json             tools/anim_survey.py         animation clips per model
  builds/cth_weapfix/mds_weap_survey.json   EQG slot bones, parents, hands (Opus weapon survey)
  builds/cth_weapfix/track_survey.json      per-clip weapon-track gap vs bind pose (same survey)
  WLD skeletons (this script)    <TAG>R_POINT / <TAG>L_POINT presence

TODO before shipping: fold the two builds/cth_weapfix survey scripts into eq_toolkit so the index
can be rebuilt on a buyer's machine; today it is built from the dev install.

    python3 tools/build_compat_index.py "$EQ"
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3d import load  # noqa: E402
from rig_survey import wld_skeleton_parents  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
D = os.path.join(ROOT, "data")
SURVEY = os.path.join(ROOT, "builds", "cth_weapfix")
HAND_WORDS = ("HAND", "FING", "THMB", "WRST", "TWST")
GAP_UNITS = 0.25   # weapon-track translation gap vs bind that counts as "off"


def jl(p):
    with open(p) as f:
        return json.load(f)


def under_hand(parent):
    return None if parent is None else any(w in parent.upper() for w in HAND_WORDS)


def main(eq_dir):
    idx = jl(os.path.join(D, "installed_model_index.json"))
    anim = jl(os.path.join(D, "anim_survey.json"))
    mds_w = jl(os.path.join(SURVEY, "mds_weap_survey.json"))
    tracks = jl(os.path.join(SURVEY, "track_survey.json"))

    out: dict[str, dict] = {}
    clip_map = defaultdict(set)
    for m in anim["models"]:
        clip_map[(m["format"], m["tag"].lower())].update(m["clips"])

    # EQG skinned models: slot bones + track sanity
    tr = defaultdict(lambda: defaultdict(list))
    for t in tracks:
        tr[t["tag"].lower()][t["bone"].strip()].append(t)
    for r in mds_w:
        tag = r["tag"].lower()
        weapon = {
            "primary": r["ARMR_WEAP"] is not None, "secondary": r["ARML_WEAP"] is not None,
            "shield": r["ARML_SHLD"] is not None,
            "primary_parent": r["ARMR_WEAP"], "secondary_parent": r["ARML_WEAP"],
            "primary_under_hand": under_hand(r["ARMR_WEAP"]),
            "secondary_under_hand": under_hand(r["ARML_WEAP"]),
            "tracks": {},
        }
        for bone, recs in tr.get(tag, {}).items():
            exact = [x for x in recs if x["exact"]]
            use = exact or recs
            over = [x for x in use if x["dt"] > GAP_UNITS]
            weapon["tracks"][bone] = {"clips": len(use), "over": len(over),
                                      "max_gap": round(max(x["dt"] for x in use), 3),
                                      "exact": bool(exact)}
        out[tag] = {"format": "eqg_mds", "container": r["cont"], "bones": r["bones"],
                    "clips": sorted(r["clips"]), "extra_arms": any("CHEST_ARM" in h for h in r["hands"]),
                    "weapon": weapon}

    # WLD skinned models: slot points + clips
    conts = sorted({m["container"] for m in idx["models"] if m["format"] == "wld_skinned"})
    for c in conts:
        try:
            files = load(os.path.join(eq_dir, c))
        except Exception:  # noqa: BLE001
            continue
        for member, data in files.items():
            if not member.lower().endswith(".wld"):
                continue
            for tag, (names, _p) in wld_skeleton_parents(data).items():
                up = {n.upper().replace("_DAG", "") for n in names}
                t = tag.lower()
                skel = names[0].upper().replace("_DAG", "")
                has = lambda suf: any(n.endswith(suf) for n in up)  # noqa: E731
                rec = {"format": "wld_skinned", "container": c, "bones": len(names),
                       "clips": sorted(clip_map.get(("wld", t), [])), "extra_arms": False,
                       "weapon": {"primary": has("R_POINT"), "secondary": has("L_POINT"),
                                  "shield": has("L_POINT"), "primary_under_hand": None,
                                  "secondary_under_hand": None, "tracks": {}}}
                prev = out.get(t)
                if prev is None or (prev["format"] == "wld_skinned" and len(rec["clips"]) > len(prev["clips"])):
                    out[t] = rec
    # EQG .mod / static WLD: existence only
    for m in idx["models"]:
        t = m["tag"].lower()
        if t not in out:
            out[t] = {"format": m["format"], "container": m["container"], "bones": None,
                      "clips": sorted(clip_map.get(("eqg", t), [])), "extra_arms": False, "weapon": None}

    # vocabulary: clips that most models of each format carry (meaning of WLD codes unverified)
    common = {}
    for fmt, key in (("eqg_mds", "eqg"), ("wld_skinned", "wld")):
        ms = [v for v in out.values() if v["format"] == fmt]
        cnt = defaultdict(int)
        for v in ms:
            for c in v["clips"]:
                cnt[c] += 1
        common[fmt] = sorted(c for c, n in cnt.items() if n >= 0.70 * len(ms))
    path = os.path.join(D, "model_compat.json")
    with open(path, "w") as f:
        json.dump({"version": 1, "gap_units": GAP_UNITS, "common_clips": common, "models": out}, f)
    n_w = sum(1 for v in out.values() if v["weapon"] and v["format"] == "wld_skinned" and v["weapon"]["primary"])
    print(f"{path}: {len(out)} models; common clips {common}")
    print(f"  WLD models with an R_POINT bone: {n_w} of {sum(1 for v in out.values() if v['format']=='wld_skinned')}")
    flagged = sorted((t, b, x['max_gap']) for t, v in out.items() if v['weapon']
                     for b, x in v['weapon']['tracks'].items() if x['exact'] and x['over'] / x['clips'] >= 0.5)
    print("  exact tracks mostly > %.2fu from bind: %s" % (GAP_UNITS, flagged))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest")
