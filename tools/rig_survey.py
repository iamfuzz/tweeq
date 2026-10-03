#!/usr/bin/env python3
"""rig-survey: extract every character skeleton in an install and group by layout.

Answers "how many distinct rig families exist?" for the model-swap app.
Reads installed_model_index.json (from scan_install.py) only for the container list.

    python3 tools/rig_survey.py "$EQ" data/installed_model_index.json data/rig_survey.json

Per model we record the bone list (normalized names), parent array and bone count,
then group three ways: exact ordered names, topology (parent array), and name set.
"""
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mds  # noqa: E402
from s3d import load  # noqa: E402
from scan_install import name_at, wld_parts  # noqa: E402


def norm(name, tag):
    n = name.upper()
    for suf in ("_DAG", "_TRACK"):
        if n.endswith(suf):
            n = n[:-len(suf)]
    t = tag.upper()
    if n.startswith(t + "_"):
        n = n[len(t) + 1:]
    elif n == t:
        n = "ROOT"
    return n


def wld_skeleton_parents(data):
    """-> {actor tag: (bone names, parent index array)} for each skinned ACTORDEF."""
    table, frags = wld_parts(data)
    out = {}
    for ftype, body in frags:
        if ftype != 0x14:
            continue
        tag = name_at(table, struct.unpack_from("<i", body, 0)[0]).replace("_ACTORDEF", "").lower()
        sk = None
        for o in range(24, len(body) - 3, 4):
            v = struct.unpack_from("<i", body, o)[0]
            if 0 < v <= len(frags) and frags[v - 1][0] == 0x11:
                sk = frags[v - 1][1]
                break
        if sk is None:
            continue
        ref = struct.unpack_from("<i", sk, 4)[0]  # 0x11: nameRef, skeleton ref, flags
        if not (0 < ref <= len(frags)) or frags[ref - 1][0] != 0x10:
            continue
        b = frags[ref - 1][1]
        flags, ndags = struct.unpack_from("<II", b, 4)
        off = 16 + (12 if flags & 1 else 0) + (4 if flags & 2 else 0)
        names, parents = [], [-1] * ndags
        for i in range(ndags):
            nameref, _fl, _trk, _mesh, nchild = struct.unpack_from("<iIIII", b, off)
            names.append(name_at(table, nameref))
            for k in struct.unpack_from("<%di" % nchild, b, off + 20):
                if 0 <= k < ndags:
                    parents[k] = i
            off += 20 + 4 * nchild
        out[tag] = (names, parents)
    return out


def mds_skeleton(m):
    names = [b.name for b in m.bones]
    parents = [-1] * len(names)
    for i, b in enumerate(m.bones):
        if b.children_count and b.child_index >= 0:
            c = b.child_index
            while c != -1 and c < len(names):
                parents[c] = i
                c = m.bones[c].next
    return names, parents


def main(eq_dir, index_path, out_path):
    idx = json.load(open(index_path))
    wld_files = sorted({m["container"] for m in idx["models"] if m["format"] == "wld_skinned"})
    eqg_files = sorted({m["container"] for m in idx["models"] if m["format"] == "eqg_mds"})
    rigs, errors = [], []
    for f in wld_files:
        try:
            for member, data in load(os.path.join(eq_dir, f)).items():
                if member.lower().endswith(".wld"):
                    for tag, (names, parents) in wld_skeleton_parents(data).items():
                        rigs.append(("wld", tag, f, names, parents))
        except Exception as e:  # noqa: BLE001
            errors.append((f, repr(e)))
    for f in eqg_files:
        try:
            for member, data in load(os.path.join(eq_dir, f)).items():
                stem = member.lower()
                if not stem.endswith(".mds") or "_lod" in stem:
                    continue  # LOD variants reuse the base rig
                names, parents = mds_skeleton(mds.parse(data))
                rigs.append(("eqg", stem[:-4], f, names, parents))
        except Exception as e:  # noqa: BLE001
            errors.append((f, repr(e)))

    groups = {"exact_names": defaultdict(list), "topology": defaultdict(list),
              "name_set": defaultdict(list)}
    rows = []
    for fmt, tag, f, names, parents in rigs:
        nn = tuple(norm(n, tag) for n in names)
        groups["exact_names"][(fmt, nn)].append(f"{tag}@{f}")
        groups["topology"][(fmt, tuple(parents))].append(f"{tag}@{f}")
        groups["name_set"][(fmt, tuple(sorted(nn)))].append(f"{tag}@{f}")
        rows.append({"format": fmt, "tag": tag, "container": f, "bones": len(names)})

    summary = {"rigs": len(rigs), "errors": len(errors)}
    for k, g in groups.items():
        for fmt in ("wld", "eqg"):
            sizes = sorted((len(v) for (ff, _), v in g.items() if ff == fmt), reverse=True)
            summary[f"{k}/{fmt}"] = {"groups": len(sizes), "largest": sizes[:8],
                                     "singletons": sum(1 for s in sizes if s == 1)}
    summary["bone_count_hist"] = dict(sorted(Counter(r["bones"] for r in rows).items()))
    with open(out_path, "w") as fh:
        json.dump({"summary": summary, "rigs": rows, "errors": errors,
                   "groups_exact": [{"format": k[0], "bones": len(k[1]), "members": v}
                                    for k, v in groups["exact_names"].items()]}, fh)
    print(json.dumps({k: v for k, v in summary.items() if k != "bone_count_hist"}, indent=1))
    print("errors sample:", errors[:5])


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
