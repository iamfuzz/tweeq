#!/usr/bin/env python3
"""Midpoint subdivision for EQG `.mds` models (the engine behind Tweeq's "Enhance" polygon option).

One pass splits every triangle into four by its edge midpoints. A midpoint vertex interpolates
position, UV, UV2 and vertex tint, averages the two parents' normals, and gets the merged bone
weights of its parents; the authored normals are kept (not recomputed, which would split them at UV seams). Winding,
material ids and face flags are preserved.

Limits (checked BEFORE any work is done, so a model that cannot be enhanced is refused with a clear
message instead of producing a broken file):
  * MAX_VERTS: the EQ client draws with 16-bit indices, so one model must stay under 65,535 vertices.
    (cth.eqg after two passes is 27,946 verts and runs in-game.)
  * the result must still pass `check_model` (indices in range, bone weights valid and normalised).

    python3 tools/mds_subdivide.py IN.mds OUT.mds [--passes 2]
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mds as mdsmod  # noqa: E402

MAX_VERTS = 65535
MAX_PASSES = 3


class SubdivideError(Exception):
    pass


def merge_weights(va, vb):
    """Average the bone weights of two vertices, keep the strongest 4, renormalise."""
    combined: dict[int, float] = {}
    for b, w in va.active_weights():
        combined[b] = combined.get(b, 0.0) + w * 0.5
    for b, w in vb.active_weights():
        combined[b] = combined.get(b, 0.0) + w * 0.5
    pairs = sorted(combined.items(), key=lambda x: -x[1])[:4]
    total = sum(w for _, w in pairs)
    if total > 0:
        pairs = [(b, w / total) for b, w in pairs]
    return pairs


def _avg(a, b):
    return tuple((x + y) * 0.5 for x, y in zip(a, b))


def subdivide_once(model, has_weights: bool = True):
    """One midpoint pass over a `mds.Model` (in place). Returns the model."""
    old = model.vertices
    mids: dict[tuple[int, int], int] = {}
    verts = list(old)

    def mid(i, j):
        key = (i, j) if i < j else (j, i)
        k = mids.get(key)
        if k is None:
            va, vb = old[i], old[j]
            n = _avg(va.normal, vb.normal)
            ln = math.sqrt(sum(c * c for c in n)) or 1.0
            v = mdsmod.Vertex(position=_avg(va.position, vb.position),
                              normal=(n[0] / ln, n[1] / ln, n[2] / ln),
                              uv=_avg(va.uv, vb.uv),
                              tint=tuple(int(round(c)) for c in _avg(va.tint, vb.tint)),
                              uv2=_avg(va.uv2, vb.uv2))
            if has_weights:
                v.set_weights(merge_weights(va, vb))
            k = mids[key] = len(verts)
            verts.append(v)
        return k

    faces = []
    for f in model.faces:
        a, b, c = f.indices
        ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
        m, fl = f.material_id, f.flags
        faces += [mdsmod.Face((a, ab, ca), m, fl), mdsmod.Face((ab, b, bc), m, fl),
                  mdsmod.Face((ca, bc, c), m, fl), mdsmod.Face((ab, bc, ca), m, fl)]
    model.vertices, model.faces = verts, faces
    model.sync_counts()
    return model


def recalculate_normals(model):
    """Smooth normals: per-face normals weighted by area, accumulated at each vertex."""
    acc = [[0.0, 0.0, 0.0] for _ in model.vertices]
    for f in model.faces:
        a, b, c = f.indices
        pa, pb, pc = (model.vertices[i].position for i in (a, b, c))
        ax, ay, az = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        bx, by, bz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
        for i in (a, b, c):
            acc[i][0] += n[0]; acc[i][1] += n[1]; acc[i][2] += n[2]
    for v, (x, y, z) in zip(model.vertices, acc):
        ln = math.sqrt(x * x + y * y + z * z)
        if ln > 0:
            v.normal = (x / ln, y / ln, z / ln)      # degenerate verts keep their old normal


def predict(model, passes: int) -> dict:
    """Exact vertex/face counts after `passes`, by running the split on indices only (no geometry)."""
    faces = [f.indices for f in model.faces]
    nverts = len(model.vertices)
    for _ in range(passes):
        mids: dict[tuple[int, int], int] = {}
        out = []
        nv = nverts

        def mid(i, j):
            nonlocal nv
            key = (i, j) if i < j else (j, i)
            k = mids.get(key)
            if k is None:
                k = mids[key] = nv
                nv += 1
            return k

        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        faces, nverts = out, nv
    return {"verts": nverts, "faces": len(faces)}


def check_model(model, bone_count: int, has_weights: bool = True) -> None:
    """Raise SubdivideError if the model is structurally unsound."""
    n = len(model.vertices)
    for f in model.faces:
        if any(i < 0 or i >= n for i in f.indices):
            raise SubdivideError("a face references a vertex that does not exist")
    if model.bone_count != n and bone_count and has_weights:
        raise SubdivideError("bone/weight record count does not match the vertex count")
    for v in model.vertices:
        if not all(math.isfinite(c) for c in (*v.position, *v.normal, *v.uv)):
            raise SubdivideError("a vertex has a non-finite value")
        if bone_count and has_weights:
            ws = v.active_weights()
            if not ws:
                raise SubdivideError("a vertex lost all of its bone weights")
            if any(b < 0 or b >= bone_count for b, _ in ws):
                raise SubdivideError("a vertex is weighted to a bone that does not exist")


def subdivide_mds(m, passes: int) -> list[dict]:
    """Subdivide every model in a parsed `mds.Mds` in place. Returns before/after stats per model."""
    if not 0 <= passes <= MAX_PASSES:
        raise SubdivideError(f"passes must be 0..{MAX_PASSES}")
    stats = []
    for model in m.models:
        pred = predict(model, passes)
        if pred["verts"] > MAX_VERTS:
            raise SubdivideError(
                f"model {model.name!r} would have {pred['verts']:,} vertices after {passes} pass(es); "
                f"the client limit used here is {MAX_VERTS:,}. Use fewer passes.")
        stats.append({"name": model.name, "verts_before": len(model.vertices),
                      "faces_before": len(model.faces), **{k + "_after": v for k, v in pred.items()}})
    for model, st in zip(m.models, stats):
        for _ in range(passes):
            subdivide_once(model, has_weights=model.has_weights)
        # Midpoint subdivision does not change the shape, so the authored normals are kept (each midpoint
        # averages its parents'). Rebuilding them per vertex index would give the split copies of a vertex on a
        # UV seam different normals and draw a hard lighting crease along every seam.
        check_model(model, len(m.bones), model.has_weights)
        if (len(model.vertices), len(model.faces)) != (st["verts_after"], st["faces_after"]):
            raise SubdivideError("internal error: result does not match the predicted counts")
    return stats


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--passes", type=int, default=1)
    a = ap.parse_args(argv)
    m = mdsmod.load(a.src)
    for s in subdivide_mds(m, a.passes):
        print("%s: %d -> %d verts, %d -> %d faces" % (s["name"], s["verts_before"], s["verts_after"],
                                                      s["faces_before"], s["faces_after"]))
    mdsmod.save(m, a.dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
