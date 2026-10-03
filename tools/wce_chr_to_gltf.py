#!/usr/bin/env python3
"""
wce_chr_to_gltf.py -- convert an old-world EverQuest WLD/S3D *character* into
binary glTF (.glb), the same way tools/mds_to_gltf.py does for EQG models.

    python3 tools/wce_chr_to_gltf.py btx_chr.s3d       builds/btx/btx.glb
    python3 tools/wce_chr_to_gltf.py btx_chr.quail/    builds/btx/btx.glb

Pre-Luclin models (Bertoxxulous, Cazic Thule's original, all the classic
mobs) never got an EQG rebuild, so they live in `<model>_chr.s3d` as a WLD.
`quail convert` turns that into its WCE text form; this tool parses the WCE.


WCE character format, as emitted by quail v1.6
==============================================

Top-level blocks start in column 0; everything belonging to them is indented
with tabs.  That is the only structural rule needed to split the file.

  SIMPLESPRITEDEF  "BTXHR01_SPRITE"   texture: NUMFRAMES -> FRAME -> FILE
  MATERIALDEFINITION "BTXHR01_MDF"    RENDERMETHOD + SIMPLESPRITEINST -> sprite
  MATERIALPALETTE  "BTX_MP"           ordered material list; faces index it
  DMSPRITEDEF2     "MESH_BTX_..."     the one and only mesh
  TRACKDEFINITION / TRACKINSTANCE     one pair per bone, in DAG order
  HIERARCHICALSPRITEDEF "BTX_HS_DEF"  the skeleton (DAG list) + attached skin
  ACTORDEF                            wrapper, ignored here

Quirks that cost real debugging time
------------------------------------

* **Mesh vertices are stored in BONE-LOCAL space.**  This is the big one and
  the opposite of EQG/MDS, where vertices are in model space.  A finger vertex
  in btx.wce reads `VXYZ 0.0129 -0.041 0.0224` -- a couple of hundredths of a
  unit, not a position on a 30-unit-tall god.  The bind pose is therefore
  `world = bone_global_matrix * vertex`, and the inverse bind matrix is simply
  `inverse(bone_global_matrix)`.  Skinning is rigid: exactly one bone per
  vertex, weight 1.0.

* **SKINASSIGNMENTGROUPS is (count, bone) run-length pairs**, not (bone,count).
  `62  3 62  12 29 ...` = 62 groups; 3 vertices on bone 62, 12 on bone 29, ...
  The runs walk the vertex array in order and must sum to NUMVERTICES.
  FACEMATERIALGROUPS is the same shape over the face array.

* **TRACKDEFINITION frames are fixed point, and quail's `scale` field is a
  BONE SCALE, not the translation denominator.**  Quail prints
  `FRAME [scale x-loc y-loc z-loc w-rot x-rot y-rot z-rot]`, all integers,
  which is WLD fragment 0x12's `shift_denominator` followed by the shift and
  the rotation.  The translation denominator is the **fixed 256**; the
  `shift_denominator` field additionally scales the bone and everything under
  it by `denom / 256`.  Rotation divides by the fixed 16384 and is stored
  **w first**.

  Dividing the translation by `scale` instead (and dropping the scale) is
  right only for the overwhelmingly common `scale == 256`, and silently
  wrecks every bone that carries a real one.  On Bertoxxulous exactly seven
  bones do -- HEJAW01 (the horns), HEEYEL01/HEEYER01 (the glowing eyes) and
  FRLEFT01/FRMID01/FRRIGHT01/BKMID01 (the four loincloth panels), at
  denominators 26 and 29, i.e. a 0.10x scale.  Ignoring it shipped 7.5-unit
  horns, eye quads stretched into 1.1-unit green pillars out of the sockets,
  and skirt panels 5 units across on a 12-unit character -- and pushed each
  of those bones ~10x too far from its parent as well, because the
  translation was being divided by 26 instead of 256.  Confirmed by the
  parent geometry: the loincloth roots land 0.37 u from a pelvis 0.7 u wide
  (not 3.7 u away), and the eyes 0.42 u along a head that is 1.1 u long.

  The decisive oracle is global_chr, where the scale sits on `*PE_DAG` -- the
  pelvis, i.e. the root of the whole body -- so it sizes the entire race.
  Measured base height over all 24 playable race/gender models:

      reading                     range          dwf / dwm    gnf / gnm
      scale honoured (correct)    5.88 .. 6.91   6.60 / 6.28  6.78 / 6.84
      scale ignored (the bug)     3.92 .. 6.81   6.60 / 3.92  6.78 / 4.35

  Honouring it puts every base model in a +-8% band, which is what an engine
  that then applies a per-race size multiplier (EQEmu: gnome 3, dwarf 4,
  human 6, ogre 9) wants. Ignoring it claims a male dwarf is 68% shorter than
  a female dwarf and a male gnome 36% shorter than a female gnome, while
  those models' *unscaled* female counterparts carry no scale at all and
  already measure ~6.7. Nothing but the shift_denominator distinguishes them.

  Because the scale is inherited down the subtree it is emitted as a glTF
  node `scale`, not baked into the vertices.

* **Bones translate along their own local +X.**  Classic EQ rig convention:
  chest/neck/finger tracks are `t = (len, 0, 0)` and the pelvis carries the
  (0.5,-0.5,-0.5,-0.5) quaternion that turns local +X into world +Z.

* **Base tracks are positionally aligned with the DAG list**, and that is the
  only safe way to read them: btx.wce ships *duplicate* tag names
  (`BTX_FIINDEXR02_TRACKDEF` twice), so a name->track dict silently loses a
  bone.  Animation files, by contrast, are NOT positionally aligned -- see
  below -- so those must be matched by name.

* **Animation track names are the base name with the 3-char animation code
  glued on the front**: `BTX_PEBIP01_TRACKDEF` -> `C05BTX_PEBIP01_TRACKDEF`.
  The model root is the exception and gets it twice:
  `BTX_TRACKDEF` -> `C05C05_BTX_TRACKDEF`.
  Animation files also hold tracks for bones the skeleton does not have
  (`BTX_HEJAW02`, `BTX_HEEYEL02`, ...) and omit the attachment-point bones
  (`BTXHEAD_POINT`, `BTXL_POINT`, `BTXR_POINT`, `BTXSHIELD_POINT`), so the
  counts match by coincidence only.

* **Frame timing comes from TRACKINSTANCE `SLEEP?`**, in milliseconds per
  frame (33 -> 30 fps).  It is NULL on single-frame (static) tracks.

Quaternion convention
---------------------
WLD stores the bone rotation directly.  It is NOT conjugated the way MDS and
ANI are, so the default here is the opposite of mds_to_gltf.py's, and
`--conj-quat` forces the other one.

Confirmed on two models rather than assumed.  Raw quaternions stand
Bertoxxulous up along EQ's +Z with his hands level and mirrored across Y
(head z -24.7, pelvis -26.5, feet -29.7; hands at y +-2.51); conjugating them
tips him onto the Y axis instead.  On global_chr's `hum` both conventions
stand the figure up -- a simpler chain -- but conjugating swaps `*_L` and
`*_R` to the wrong sides.

Two oracles that look obvious and are NOT usable here, recorded so nobody
spends an afternoon on them again:

* **Seam length between adjacent bones is blind to this.**  A child's seam
  vertices sit at its own local origin, so `G_child * v ~= G_parent * t_child`
  no matter how the child is rotated -- the seam gap reduces to `|v - t|` in
  the parent's frame, which the child's rotation never enters.  Measured on
  btx: 0.673 u raw against 0.686 u conjugated. Useless.

* **HIERARCHICALSPRITEDEF BOUNDINGRADIUS does not settle it either.**  btx
  declares 31.4663; with the bone scales honoured the base pose measures
  30.10 raw and 28.24 conjugated, so both fit under it and it discriminates
  nothing.  (Before the shift_denominator fix below, raw measured 33.59 --
  i.e. reading it as an upper bound would have picked the wrong answer.)  It
  is a client-side envelope over the animations, not a bind-pose radius.
  DMSPRITEDEF2's own BOUNDINGRADIUS is no better: btx declares 5.932 against
  a measured 3.69 about the pelvis, and 7.16 over the raw bone-local
  vertices.  Do not use either as a scale oracle -- use parent/child
  geometry, and for global_chr the cross-race height band.

What is left is `pose_report()`: extent along each axis, plus how many
non-adjacent bone clouds interpenetrate.  A scrambled rig folds limbs into
each other and stops being tallest along up.  It is printed for both
conventions and warns, but never overrides the WLD default.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mds_to_gltf import (          # noqa: E402
    GlbBuilder, ZUP_TO_YUP_QUAT, dds_to_rgba, mat_from_trs, mat_inverse,
    mat_mul, mat_to_gltf, pack_glb, png_decode, png_encode, quat_mul, to_yup,
    validate_glb,
)

ROT_DENOM = 16384.0
TRANS_DENOM = 256.0        # WLD 0x12 shift fixed-point denominator (constant)
SCALE_DENOM = 256.0        # shift_denominator / 256 is the bone's own scale
DEFAULT_SLEEP_MS = 100


# ==========================================================================
# WCE tokenizer / block splitter
# ==========================================================================

def _split(line):
    """Split a WCE line into tokens, honouring "quoted strings"."""
    out, buf, quoted = [], [], False
    i = 0
    while i < len(line):
        c = line[i]
        if quoted:
            if c == '"':
                out.append("".join(buf))
                buf, quoted = [], False
            else:
                buf.append(c)
        elif c == '"':
            if buf:
                out.append("".join(buf))
                buf = []
            quoted = True
        elif c.isspace():
            if buf:
                out.append("".join(buf))
                buf = []
        elif c == "/" and line[i:i + 2] == "//":
            break                                   # rest of line is a comment
        else:
            buf.append(c)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


class Block:
    """A top-level WCE block: its keyword, its tag, and its indented body."""

    __slots__ = ("kind", "tag", "body", "source")

    def __init__(self, kind, tag, source):
        self.kind = kind
        self.tag = tag
        self.source = source
        self.body = []                              # [(KEY, [args...]), ...]

    def first(self, key, default=None):
        for k, a in self.body:
            if k == key:
                return a
        return default

    def value(self, key, default=None):
        a = self.first(key)
        return a[0] if a else default

    def __repr__(self):
        return "<%s %r %d lines>" % (self.kind, self.tag, len(self.body))


def parse_wce(path):
    """Split one .wce file into top-level Blocks (column-0 keyword starts one)."""
    blocks = []
    cur = None
    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            indented = raw[0] in " \t"
            toks = _split(raw)
            if not toks:
                continue
            if not indented:
                kind = toks[0]
                if kind == "INCLUDE":
                    continue
                cur = Block(kind, toks[1] if len(toks) > 1 else "", path)
                blocks.append(cur)
            elif cur is not None:
                cur.body.append((toks[0], toks[1:]))
    return blocks


# ==========================================================================
# Model assembly from blocks
# ==========================================================================

def _ints(args):
    return [int(x) for x in args]


def _rle(args, total, what):
    """`n  c0 v0  c1 v1 ...` run-length pairs -> a flat per-item value list."""
    vals = _ints(args)
    n, rest = vals[0], vals[1:]
    if len(rest) != 2 * n:
        raise ValueError("%s: header says %d groups but %d numbers follow"
                         % (what, n, len(rest)))
    out = []
    for i in range(n):
        count, value = rest[2 * i], rest[2 * i + 1]
        out.extend([value] * count)
    if len(out) != total:
        raise ValueError("%s: runs cover %d items, expected %d"
                         % (what, len(out), total))
    return out


class Track:
    __slots__ = ("name", "frames", "sleep_ms")

    def __init__(self, name):
        self.name = name
        self.frames = []            # [(trans(x,y,z), quat(x,y,z,w), scale)]
        self.sleep_ms = None


class Bone:
    __slots__ = ("name", "parent", "children", "track", "sprite")

    def __init__(self, name):
        self.name = name
        self.parent = -1
        self.children = []
        self.track = None
        self.sprite = ""


class Mesh:
    __slots__ = ("name", "verts", "uvs", "normals", "faces", "face_mats",
                 "vert_bones", "palette", "center", "radius", "rigid")

    def __init__(self, name):
        self.name = name
        self.verts = []
        self.uvs = []
        self.normals = []
        self.faces = []
        self.face_mats = []
        self.vert_bones = []
        self.palette = ""
        self.center = (0.0, 0.0, 0.0)
        self.radius = 0.0
        self.rigid = False          # no skin data: the skeleton attaches the whole mesh to one bone


class Material:
    __slots__ = ("name", "sprite", "rendermethod", "doublesided", "textures")

    def __init__(self, name):
        self.name = name
        self.sprite = ""
        self.rendermethod = ""
        self.doublesided = False
        self.textures = []


def _parse_track_frames(block):
    tr = Track(block.tag)
    for key, args in block.body:
        if key != "FRAME":
            continue
        v = _ints(args)
        if len(v) < 8:
            continue
        denom, x, y, z, rw, rx, ry, rz = v[:8]
        # `denom` is WLD 0x12's shift_denominator. It is NOT the translation
        # divisor -- that is the fixed 256 -- it is a uniform scale on this
        # bone and its whole subtree. See the module docstring.
        tr.frames.append(((x / TRANS_DENOM, y / TRANS_DENOM, z / TRANS_DENOM),
                          (rx / ROT_DENOM, ry / ROT_DENOM,
                           rz / ROT_DENOM, rw / ROT_DENOM),
                          (denom / SCALE_DENOM) if denom else 1.0))
    return tr


class CharModel:
    def __init__(self):
        self.sprites = {}           # SIMPLESPRITEDEF tag -> [texture files]
        self.materials = {}         # MATERIALDEFINITION tag -> Material
        self.palettes = {}          # MATERIALPALETTE tag -> [material tags]
        self.meshes = []
        self.bones = []
        self.skeleton_tag = ""
        self.skins = []             # mesh tags attached to the skeleton
        self.animations = {}        # anim code -> {bone index: Track}


def load_blocks(blocks, model, log):
    """Fold one file's blocks into `model` (base definitions, no animations)."""
    trackdefs, trackinsts = [], []

    for b in blocks:
        if b.kind == "SIMPLESPRITEDEF":
            files = [a[0] for k, a in b.body if k == "FILE" and a]
            model.sprites[b.tag] = files

        elif b.kind == "MATERIALDEFINITION":
            m = Material(b.tag)
            m.rendermethod = b.value("RENDERMETHOD", "")
            m.sprite = b.value("SIMPLESPRITETAG", "")
            m.doublesided = b.value("DOUBLESIDED", "0") not in ("0", "NULL")
            model.materials[b.tag] = m

        elif b.kind == "MATERIALPALETTE":
            model.palettes[b.tag] = [a[0] for k, a in b.body
                                     if k == "MATERIAL" and a]

        elif b.kind == "DMSPRITEDEF2":
            model.meshes.append(_parse_mesh(b, log))

        elif b.kind == "TRACKDEFINITION":
            trackdefs.append(_parse_track_frames(b))

        elif b.kind == "TRACKINSTANCE":
            sleep = b.value("SLEEP?", "NULL")
            trackinsts.append((b.tag, b.value("SPRITE", ""),
                               None if sleep == "NULL" else int(sleep)))

        elif b.kind == "HIERARCHICALSPRITEDEF":
            _parse_skeleton(b, model)

    # Rigid meshes ride on the bone whose DAG names them (vertices are in that bone's local space,
    # exactly like skinned vertices).
    for mesh in model.meshes:
        if mesh.rigid:
            bi = next((i for i, bn in enumerate(model.bones) if bn.sprite.upper() == mesh.name.upper()), None)
            if bi is not None:
                mesh.vert_bones = [bi] * len(mesh.verts)

    # TRACKDEFINITION i is always followed by its TRACKINSTANCE i.
    for i, (_, _, sleep) in enumerate(trackinsts):
        if i < len(trackdefs):
            trackdefs[i].sleep_ms = sleep
    return trackdefs, trackinsts


def _parse_mesh(b, log):
    mesh = Mesh(b.tag)
    for key, args in b.body:
        if key == "VXYZ":
            mesh.verts.append((float(args[0]), float(args[1]), float(args[2])))
        elif key == "UV":
            mesh.uvs.append((float(args[0]), float(args[1])))
        elif key == "NXYZ":
            mesh.normals.append((float(args[0]), float(args[1]),
                                 float(args[2])))
        elif key == "TRIANGLE":
            mesh.faces.append((int(args[0]), int(args[1]), int(args[2])))
        elif key == "MATERIALPALETTE":
            mesh.palette = args[0] if args else ""
        elif key == "CENTEROFFSET":
            mesh.center = tuple(float(x) for x in args[:3])
        elif key == "BOUNDINGRADIUS":
            mesh.radius = float(args[0])
        elif key == "SKINASSIGNMENTGROUPS":
            if args and int(args[0]) == 0:
                # Old-style rigid model (boats, ships, thought bleeders...): no skinning at all. The skeleton's
                # DAG whose SPRITETAG names this mesh carries it as one rigid piece (bound in load_blocks).
                mesh.rigid = True
            else:
                mesh.vert_bones = _rle(args, len(mesh.verts),
                                       "%s SKINASSIGNMENTGROUPS" % b.tag)
        elif key == "FACEMATERIALGROUPS":
            mesh.face_mats = _rle(args, len(mesh.faces),
                                  "%s FACEMATERIALGROUPS" % b.tag)
    if not mesh.face_mats:
        mesh.face_mats = [0] * len(mesh.faces)
    if not mesh.vert_bones:
        mesh.vert_bones = [0] * len(mesh.verts)
    log("  mesh %-28s %5d verts  %5d faces  palette=%s"
        % (mesh.name, len(mesh.verts), len(mesh.faces), mesh.palette or "-"))
    return mesh


def _parse_skeleton(b, model):
    model.skeleton_tag = b.tag
    bones, subs = [], []
    cur = None
    for key, args in b.body:
        if key == "DAG":
            cur = Bone("")
            bones.append(cur)
            subs.append([])
        elif cur is None:
            continue
        elif key == "TAG":
            cur.name = args[0] if args else ""
        elif key == "SPRITETAG":
            cur.sprite = args[0] if args else ""
        elif key == "TRACK":
            cur.track = args[0] if args else ""
        elif key == "SUBDAGLIST":
            v = _ints(args)
            subs[-1] = v[1:1 + v[0]] if v else []
        elif key == "DMSPRITE":
            model.skins.append(args[0] if args else "")
    for i, kids in enumerate(subs):
        for c in kids:
            if 0 <= c < len(bones) and bones[c].parent == -1 and c != i:
                bones[c].parent = i
            bones[i].children.append(c)
    model.bones = bones


def attach_base_tracks(model, trackdefs, trackinsts, log):
    """Bind base-pose tracks to bones. Positional, because tags repeat."""
    if not model.bones:
        return
    n = len(model.bones)
    if len(trackdefs) < n:
        raise ValueError("skeleton has %d DAGs but only %d TRACKDEFINITIONs"
                         % (n, len(trackdefs)))
    mismatched = 0
    for i, bone in enumerate(model.bones):
        want = bone.track                            # TRACKINSTANCE tag
        got = trackinsts[i][0] if i < len(trackinsts) else ""
        if want and got and want != got:
            mismatched += 1
        bone.track = trackdefs[i]
    if mismatched:
        log("  ! %d DAG(s) name a TRACKINSTANCE that is not at the same index; "
            "positional pairing used anyway" % mismatched)


# ==========================================================================
# animations
# ==========================================================================

def _strip_anim_prefix(name, code):
    """C05BTX_PEBIP01_TRACKDEF -> BTX_PEBIP01_TRACKDEF.

    The model root track is prefixed twice (C05 + C05_BTX_TRACKDEF), so peel
    a second time when the remainder still starts with the animation code.
    """
    if not name.upper().startswith(code):
        return None
    rest = name[len(code):]
    if rest.upper().startswith(code):
        rest2 = rest[len(code):]
        return rest2[1:] if rest2.startswith("_") else rest2
    return rest


def load_animation(path, model, log):
    """Parse one animations/<code>_<model>.wce into {bone index: Track}."""
    code = os.path.basename(path).split("_", 1)[0].upper()
    blocks = parse_wce(path)
    defs, insts = [], []
    for b in blocks:
        if b.kind == "TRACKDEFINITION":
            defs.append(_parse_track_frames(b))
        elif b.kind == "TRACKINSTANCE":
            sleep = b.value("SLEEP?", "NULL")
            insts.append(None if sleep == "NULL" else int(sleep))
    for i, s in enumerate(insts):
        if i < len(defs):
            defs[i].sleep_ms = s

    # base tag -> queue of bone indices (duplicated tags keep their order)
    pending = {}
    for i, bone in enumerate(model.bones):
        pending.setdefault(bone.track.name.upper(), []).append(i)

    out, unknown = {}, []
    for tr in defs:
        base = _strip_anim_prefix(tr.name, code)
        key = base.upper() if base else None
        queue = pending.get(key) if key else None
        if not queue:
            unknown.append(tr.name)
            continue
        out[queue.pop(0)] = tr
    missing = sum(len(q) for q in pending.values())
    log("  anim %-4s %3d tracks -> %3d bones (%d unmatched, %d bones static)"
        % (code, len(defs), len(out), len(unknown), missing))
    return code, out


# ==========================================================================
# skeleton maths
# ==========================================================================

def bone_locals(model, conj, scale, yup):
    locals_ = []
    for i, bone in enumerate(model.bones):
        frames = bone.track.frames if bone.track else []
        if frames:
            t, q, s = frames[0]
        else:
            t, q, s = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0
        t = tuple(c * scale for c in t)
        if conj:
            q = (-q[0], -q[1], -q[2], q[3])
        if bone.parent == -1 and yup:
            t = to_yup(t)
            q = quat_mul(ZUP_TO_YUP_QUAT, q)
        locals_.append((t, _norm_quat(q), (s, s, s)))
    return locals_


def bone_globals(model, locals_):
    n = len(model.bones)
    globals_ = [None] * n
    order = [i for i in range(n) if model.bones[i].parent == -1]
    queue = list(order)
    while queue:
        i = queue.pop(0)
        lm = mat_from_trs(*locals_[i])
        p = model.bones[i].parent
        globals_[i] = lm if p == -1 else mat_mul(globals_[p], lm)
        for c in model.bones[i].children:
            if 0 <= c < n and globals_[c] is None and c not in queue:
                queue.append(c)
    for i in range(n):
        if globals_[i] is None:                     # orphan / cycle guard
            globals_[i] = mat_from_trs(*locals_[i])
    return globals_


def _norm_quat(q):
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)


def xform_point(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def xform_dir(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z,
            m[4] * x + m[5] * y + m[6] * z,
            m[8] * x + m[9] * y + m[10] * z)


def skin_mesh(mesh, globals_, scale):
    """Bone-local vertices -> bind-pose world positions + normals."""
    nb = len(globals_)
    pos, nrm = [], []
    for i, v in enumerate(mesh.verts):
        b = mesh.vert_bones[i]
        m = globals_[b] if 0 <= b < nb else None
        v = tuple(c * scale for c in v)
        n = mesh.normals[i] if i < len(mesh.normals) else (0.0, 0.0, 1.0)
        if m is not None:
            v, n = xform_point(m, v), xform_dir(m, n)
        ln = math.sqrt(sum(c * c for c in n))
        nrm.append(tuple(c / ln for c in n) if ln > 1e-9 else (0.0, 1.0, 0.0))
        pos.append(v)
    return pos, nrm


def pose_report(model, meshes, globals_, scale, up_axis):
    """
    Cheap structural read on an assembled bind pose.

    Returns (extents xyz, tallest axis, interpenetrating bone pairs,
    mean bone-to-its-own-geometry offset).

    * extents / tallest axis -- a character stands along EQ's +Z. A rig posed
      with the wrong rotations lies down or folds up.
    * interpenetration -- wrap each bone's vertices in a sphere and count
      non-adjacent pairs whose centres are closer than half their combined
      radii. Correct poses keep limbs apart; scrambled ones stack them.
    * bone-vs-geometry offset -- the WLD analogue of mds_to_gltf.py's
      bind_pose_error. Vertices are already bone-local here, so this is just
      the mean centroid magnitude: it says bones sit inside the geometry they
      drive, but being rotation-invariant it says nothing about convention.
    """
    pos = []
    clouds = {}
    for mesh in meshes:
        p, _ = skin_mesh(mesh, globals_, scale)
        pos.extend(p)
        for i, v in enumerate(mesh.verts):
            clouds.setdefault(mesh.vert_bones[i], []).append(p[i])
    if not pos:
        return (0.0, 0.0, 0.0), up_axis, 0, 0.0

    ext = tuple(max(p[k] for p in pos) - min(p[k] for p in pos)
                for k in range(3))
    tallest = max(range(3), key=lambda k: ext[k])

    spheres, local_off = {}, []
    for b, vs in clouds.items():
        c = tuple(sum(v[k] for v in vs) / len(vs) for k in range(3))
        spheres[b] = (c, max(math.dist(v, c) for v in vs))
        if 0 <= b < len(globals_):
            g = globals_[b]
            local_off.append(math.dist(c, (g[3], g[7], g[11])))

    keys = sorted(spheres)
    overlaps = 0
    for ii in range(len(keys)):
        for jj in range(ii + 1, len(keys)):
            a, b = keys[ii], keys[jj]
            if model.bones[a].parent == b or model.bones[b].parent == a:
                continue
            (ca, ra), (cb, rb) = spheres[a], spheres[b]
            if math.dist(ca, cb) < (ra + rb) * 0.5:
                overlaps += 1
    mean_off = sum(local_off) / len(local_off) if local_off else 0.0
    return ext, tallest, overlaps, mean_off


# ==========================================================================
# textures
# ==========================================================================

def bmp_decode(data):
    """Classic Windows BMP (1/4/8-bit palettised, 24/32-bit, uncompressed) -> (w, h, RGBA bytes),
    rows top-down. Pre-DDS EQ character textures (e.g. liz*.bmp) are plain 8-bit BMPs."""
    import struct
    if data[:2] != b"BM":
        raise ValueError("not a BMP")
    off, = struct.unpack_from("<I", data, 10)
    dib, w, h, planes, bpp, comp = struct.unpack_from("<IiiHHI", data, 14)
    if comp != 0 or bpp not in (1, 4, 8, 24, 32) or dib < 40:
        raise ValueError("unsupported BMP (compression=%d bpp=%d)" % (comp, bpp))
    bottom_up = h > 0
    h = abs(h)
    ncol, = struct.unpack_from("<I", data, 14 + 32)
    pal = []
    if bpp <= 8:
        ncol = ncol or (1 << bpp)
        p0 = 14 + dib
        for i in range(ncol):
            b, g, r, _a = data[p0 + 4 * i:p0 + 4 * i + 4]
            pal.append((r, g, b))
    stride = ((w * bpp + 31) // 32) * 4
    out = bytearray(w * h * 4)
    for y in range(h):
        row = data[off + y * stride: off + (y + 1) * stride]
        dy = (h - 1 - y) if bottom_up else y
        o = dy * w * 4
        for x in range(w):
            if bpp == 8:
                r, g, b = pal[row[x]] if row[x] < len(pal) else (0, 0, 0)
                a = 255
            elif bpp == 24:
                b, g, r = row[3 * x:3 * x + 3]
                a = 255
            elif bpp == 32:
                b, g, r, a = row[4 * x:4 * x + 4]
                a = 255 if a == 0 else a
            elif bpp == 4:
                v = row[x // 2]
                r, g, b = pal[(v >> 4) if x % 2 == 0 else (v & 15)]
                a = 255
            else:
                v = (row[x // 8] >> (7 - x % 8)) & 1
                r, g, b = pal[v]
                a = 255
            out[o:o + 4] = bytes((r, g, b, a))
            o += 4
    return w, h, bytes(out)


def load_texture(assets_dir, filename, extra_dirs=()):
    base = os.path.splitext(os.path.basename(filename))[0].lower()
    for d in list(extra_dirs) + [assets_dir]:
        if not d or not os.path.isdir(d):
            continue
        for ext in (".dds", ".png", ".bmp"):
            p = os.path.join(d, base + ext)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    data = fh.read()
                if data[:4] == b"DDS ":
                    w, h, px = dds_to_rgba(data)
                    return w, h, px, p
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    w, h, px = png_decode(data)
                    return w, h, px, p
                if data[:2] == b"BM":
                    w, h, px = bmp_decode(data)
                    return w, h, px, p
    return None


# ==========================================================================
# glTF assembly
# ==========================================================================

def convert(model, opts, log):
    scale = opts.scale
    yup = not opts.z_up

    # ------------------------------------------------------------- meshes
    # A character .wce can hold variant bodies and heads side by side (hum
    # ships four heads). HIERARCHICALSPRITEDEF's ATTACHEDSKIN list is the
    # authoritative "this is the model" set, so follow it unless overridden.
    meshes = [m for m in model.meshes if m.verts and m.faces]
    if opts.meshes:
        want = {n.upper() for n in opts.meshes}
        meshes = [m for m in meshes if m.name.upper() in want]
        if not meshes:
            raise SystemExit("no mesh matched --mesh (have: %s)"
                             % ", ".join(m.name for m in model.meshes))
    elif model.skins and not opts.all_meshes:
        keep = {s.upper() for s in model.skins}
        chosen = [m for m in meshes if m.name.upper() in keep]
        if chosen and len(chosen) != len(meshes):
            log("  using the %d ATTACHEDSKIN mesh(es), skipping %d variant(s)"
                " (--all-meshes / --mesh to change): %s"
                % (len(chosen), len(meshes) - len(chosen),
                   ", ".join(m.name for m in meshes if m not in chosen)))
        meshes = chosen or meshes

    # ------------------------------------------------------- quaternion sign
    # WLD stores rotations unconjugated; only an explicit flag changes that.
    conj = bool(opts.conj_quat)

    locals_ = bone_locals(model, conj, scale, yup)
    globals_ = bone_globals(model, locals_)
    ibms = [mat_inverse(g) for g in globals_]

    # glTF skinning at the bind pose is global*IBM, which must come out as the
    # identity or every vertex moves the moment the skin binds.
    worst = 0.0
    for g, ib in zip(globals_, ibms):
        m = mat_mul(g, ib)
        for k in range(16):
            worst = max(worst, abs(m[k] - (1.0 if k % 5 == 0 else 0.0)))
    if worst > 1e-3:
        raise ValueError("inverse bind matrices are wrong: global*IBM is off "
                         "the identity by %.6f" % worst)

    if meshes and model.bones:
        _report_pose(model, meshes, scale, yup, conj, opts, log)
        allp = [p for m in meshes for p in skin_mesh(m, globals_, scale)[0]]
        lo = [min(p[k] for p in allp) for k in range(3)]
        hi = [max(p[k] for p in allp) for k in range(3)]
        log("  bind pose: skin matrices identity to %.2e, mesh bounds "
            "(%.2f %.2f %.2f) .. (%.2f %.2f %.2f)"
            % (worst, lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))
        if max(abs(lo[k] + hi[k]) / 2 for k in range(3)) > 2 * max(
                hi[k] - lo[k] for k in range(3)):
            log("  note: the model sits well away from its own origin -- "
                "faithful to the WLD root track, viewers must frame on the "
                "mesh bounds rather than the origin")

    gltf = {"asset": {"version": "2.0",
                      "generator": "eq wce_chr_to_gltf.py "
                                   "(EQ HD upscale project)"},
            "scene": 0}
    builder = GlbBuilder()

    # ------------------------------------------------------------ materials
    images, gtextures, gmaterials = [], [], []
    samplers = [{"magFilter": 9729, "minFilter": 9987,
                 "wrapS": 10497, "wrapT": 10497}]
    image_index, mat_index = {}, {}

    def add_image(fname):
        base = os.path.splitext(os.path.basename(fname))[0].lower()
        if base in image_index:
            return image_index[base]
        got = load_texture(opts.assets, fname, opts.texture_dirs)
        if got is None:
            log("  ! texture not found: %s" % fname)
            images.append({"uri": base + ".png", "name": base})
        else:
            w, h, px, origin = got
            png = png_encode(w, h, px)
            if opts.dump_textures:
                os.makedirs(opts.dump_textures, exist_ok=True)
                with open(os.path.join(opts.dump_textures, base + ".png"),
                          "wb") as fh:
                    fh.write(png)
            if opts.no_textures:
                images.append({"uri": base + ".png", "name": base})
            else:
                view = builder.add_view(png)
                images.append({"bufferView": view, "mimeType": "image/png",
                               "name": base})
            log("  texture %-14s %4dx%-4d %6d B png  <- %s"
                % (base, w, h, len(png), os.path.basename(origin)))
        image_index[base] = len(images) - 1
        return image_index[base]

    # Only the materials the converted faces actually index. A shared archive
    # palette can carry every cloak and armour skin in the game -- hum's is
    # 117 textures for a 791-vertex body.
    used = set()
    for mesh in meshes:
        pal = model.palettes.get(mesh.palette, [])
        for pid in set(mesh.face_mats):
            if 0 <= pid < len(pal):
                used.add(pal[pid])

    for name, mat in model.materials.items():
        if name not in used:
            continue
        g = {"name": name, "doubleSided": bool(mat.doublesided),
             "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                      "roughnessFactor": 0.9}}
        files = model.sprites.get(mat.sprite, [])
        if files:
            img = add_image(files[0])
            gtextures.append({"sampler": 0, "source": img})
            g["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": len(gtextures) - 1}
        # USERDEFINED_2x with an odd suffix, and the "TRANSPARENT" family, are
        # EQ's masked/alpha render methods. Everything else is flat opaque.
        rm = (mat.rendermethod or "").upper()
        if "TRANSPARENT" in rm or rm in ("USERDEFINED_20", "USERDEFINED_21",
                                         "USERDEFINED_24", "USERDEFINED_12",
                                         "USERDEFINED_13"):
            g["alphaMode"] = "MASK"
            g["alphaCutoff"] = 0.5
            g["doubleSided"] = True
        mat_index[name] = len(gmaterials)
        gmaterials.append(g)

    # ---------------------------------------------------------------- nodes
    nodes = []
    for i, bone in enumerate(model.bones):
        t, q, s = locals_[i]
        node = {"name": bone.name or ("bone_%d" % i)}
        if any(abs(v) > 1e-9 for v in t):
            node["translation"] = [float(v) for v in t]
        if abs(q[0]) + abs(q[1]) + abs(q[2]) > 1e-9 or abs(q[3] - 1.0) > 1e-9:
            node["rotation"] = [float(v) for v in q]
        if any(abs(v - 1.0) > 1e-9 for v in s):
            node["scale"] = [float(v) for v in s]
        nodes.append(node)
    for i, bone in enumerate(model.bones):
        for c in bone.children:
            if 0 <= c < len(nodes) and model.bones[c].parent == i:
                nodes[i].setdefault("children", []).append(c)
    roots = [i for i, b in enumerate(model.bones) if b.parent == -1]

    skin_index = None
    if model.bones:
        ibm_data = b"".join(struct.pack("<16f", *mat_to_gltf(x)) for x in ibms)
        acc = builder.add_accessor(ibm_data, 5126, "MAT4", len(ibms))
        skin = {"inverseBindMatrices": acc,
                "joints": list(range(len(model.bones)))}
        if len(roots) == 1:
            skin["skeleton"] = roots[0]
        gltf["skins"] = [skin]
        skin_index = 0

    # --------------------------------------------------------------- meshes
    gmeshes, mesh_nodes = [], []
    total_v = total_f = 0
    for mi, mesh in enumerate(meshes):
        # Already Y-up: bone_locals() folded the Z-up->Y-up rotation into the
        # root bone, so every global matrix outputs glTF axes. Converting the
        # skinned positions again here would rotate the model 180 deg about X
        # -- upside down and back to front, with the skeleton still upright.
        pos, nrm = skin_mesh(mesh, globals_, scale)
        uvs = []
        for i in range(len(pos)):
            u, v = mesh.uvs[i] if i < len(mesh.uvs) else (0.0, 0.0)
            uvs.append((u, (1.0 - v) if opts.flip_v else v))

        palette = model.palettes.get(mesh.palette, [])
        pos_data = b"".join(struct.pack("<3f", *p) for p in pos)
        nrm_data = b"".join(struct.pack("<3f", *n) for n in nrm)
        uv_data = b"".join(struct.pack("<2f", *t) for t in uvs)
        pmin = [min(p[i] for p in pos) for i in range(3)]
        pmax = [max(p[i] for p in pos) for i in range(3)]
        attrs = {
            "POSITION": builder.add_accessor(pos_data, 5126, "VEC3", len(pos),
                                             34962, (pmin, pmax)),
            "NORMAL": builder.add_accessor(nrm_data, 5126, "VEC3", len(nrm),
                                           34962),
            "TEXCOORD_0": builder.add_accessor(uv_data, 5126, "VEC2",
                                               len(uvs), 34962),
        }
        if skin_index is not None:
            nb = len(model.bones)
            j_data, w_data = bytearray(), bytearray()
            for b in mesh.vert_bones:
                if not (0 <= b < nb):
                    b = 0
                j_data += struct.pack("<4H", b, 0, 0, 0)
                w_data += struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
            attrs["JOINTS_0"] = builder.add_accessor(bytes(j_data), 5123,
                                                     "VEC4", len(pos), 34962)
            attrs["WEIGHTS_0"] = builder.add_accessor(bytes(w_data), 5126,
                                                      "VEC4", len(pos), 34962)

        # WLD TRIANGLE vertex order is CLOCKWISE when viewed from outside,
        # the opposite of glTF's counter-clockwise front-face convention. The
        # bone globals are pure rotations (det +1, asserted above), so nothing
        # downstream flips it back: shipping the source order verbatim makes
        # every face a back face, and a glTF material with "doubleSided":
        # false renders NOTHING while the mesh still reports a correct AABB
        # -- an invisible model that frames and animates normally. Reverse the
        # winding here, then prove it against the vertex normals.
        groups = {}
        for fi, tri in enumerate(mesh.faces):
            a, b_, c = tri
            groups.setdefault(mesh.face_mats[fi], []).extend((a, c, b_))

        agree = seen = 0
        for tri in mesh.faces:
            a, b_, c = tri
            pa, pb, pc = pos[a], pos[c], pos[b_]      # reversed order
            u = [pb[k] - pa[k] for k in range(3)]
            v = [pc[k] - pa[k] for k in range(3)]
            fn = (u[1] * v[2] - u[2] * v[1],
                  u[2] * v[0] - u[0] * v[2],
                  u[0] * v[1] - u[1] * v[0])
            if sum(x * x for x in fn) < 1e-18:
                continue
            vn = [(nrm[a][k] + nrm[b_][k] + nrm[c][k]) for k in range(3)]
            if sum(x * x for x in vn) < 1e-18:
                continue
            seen += 1
            if sum(fn[k] * vn[k] for k in range(3)) > 0.0:
                agree += 1
        if seen:
            frac = agree / seen
            log("  mesh %-28s winding: %.1f%% of %d faces face outward"
                % (mesh.name or mi, 100.0 * frac, seen))
            if frac < 0.6:
                raise ValueError(
                    "mesh %s: after reversing the WLD winding only %.1f%% of "
                    "faces agree with their vertex normals -- the model would "
                    "render inside-out or invisible under backface culling"
                    % (mesh.name or mi, 100.0 * frac))

        prims = []
        big = len(pos) > 65535
        for pal_id, idx in sorted(groups.items()):
            fmt, ctype = ("<I", 5125) if big else ("<H", 5123)
            data = b"".join(struct.pack(fmt, i) for i in idx)
            acc = builder.add_accessor(data, ctype, "SCALAR", len(idx), 34963)
            prim = {"attributes": attrs, "indices": acc, "mode": 4}
            if 0 <= pal_id < len(palette):
                mname = palette[pal_id]
                if mname in mat_index:
                    prim["material"] = mat_index[mname]
            prims.append(prim)

        name = mesh.name or ("mesh_%d" % mi)
        gmeshes.append({"name": name, "primitives": prims})
        node = {"name": name, "mesh": len(gmeshes) - 1}
        if skin_index is not None:
            node["skin"] = skin_index
        nodes.append(node)
        mesh_nodes.append(len(nodes) - 1)
        total_v += len(pos)
        total_f += len(mesh.faces)
        log("  mesh %-28s %5d verts  %5d faces  %d primitive(s)  bones=%d"
            % (name, len(pos), len(mesh.faces), len(prims),
               len(set(mesh.vert_bones))))

    # ----------------------------------------------------------- animations
    if model.animations and model.bones:
        anims, stats = build_animations(model, locals_, builder, conj, opts,
                                        log)
        if anims:
            gltf["animations"] = anims
            log("  animations: %d, %d channels, %d keys (%d constant channels "
                "dropped)" % (len(anims), stats["channels"], stats["keys"],
                              stats["skipped"]))

    gltf["nodes"] = nodes
    gltf["meshes"] = gmeshes
    gltf["scenes"] = [{"nodes": roots + mesh_nodes}]
    if gmaterials:
        gltf["materials"] = gmaterials
    if images:
        gltf["images"] = images
        gltf["textures"] = gtextures
        gltf["samplers"] = samplers
    gltf["bufferViews"] = builder.views
    gltf["accessors"] = builder.accessors
    gltf["buffers"] = [{"byteLength": len(builder.bin) +
                        ((4 - len(builder.bin) % 4) % 4)}]
    log("  totals: %d bones, %d verts, %d faces, %d KB binary"
        % (len(model.bones), total_v, total_f, len(builder.bin) // 1024))
    return gltf, builder.bin


def _report_pose(model, meshes, scale, yup, conj, opts, log):
    """Print the bind-pose read for both conventions; warn, never override."""
    up = 1 if yup else 2                       # glTF +Y, or EQ +Z
    axes = "XYZ"
    results = {}
    for probe in (False, True):
        g = bone_globals(model, bone_locals(model, probe, scale, yup))
        results[probe] = pose_report(model, meshes, g, scale, up)

    for probe in (conj, not conj):
        ext, tallest, ov, off = results[probe]
        log("  pose (%s quaternions%s): extent %.1f x %.1f x %.1f, tallest "
            "axis %s, %d interpenetrating bone pairs, mean bone-to-geometry "
            "offset %.3f u"
            % ("conjugated" if probe else "raw",
               "" if probe == conj else ", not used",
               ext[0], ext[1], ext[2], axes[tallest], ov, off))

    ours, theirs = results[conj], results[not conj]
    if ours[1] != up:
        log("  ! the model is not tallest along %s -- if this is a humanoid "
            "the rig is mis-posed (try --%s)"
            % (axes[up], "raw-quat" if conj else "conj-quat"))
    if theirs[1] == up and ours[1] != up:
        log("  ! the other quaternion convention does stand it up along %s"
            % axes[up])


_PATHS = (("translation", "VEC3", "<3f"), ("rotation", "VEC4", "<4f"),
          ("scale", "VEC3", "<3f"))


def _const(values, eps):
    first = values[0]
    for v in values[1:]:
        for a, b in zip(first, v):
            if abs(a - b) > eps:
                return False
    return True


def _root_level_shift(model, tracks, locals_, scale):
    """{bone: translation shift} shared by every direct child of the root bone.

    Animation tracks live in one origin space that differs from the bind track's (the body root
    PEBIP01 sits at bind Y -26.5 but animates around 0), so frame 0 is shifted onto the bind pose.
    Props can hang off the root BESIDE the body (Agnarr's staff: KARSTAFB01, a sibling of PEBIP01,
    animated in that same space). Shifting each by its own bind-vs-frame-0 gap pulled the staff 3.4
    units off his hand; they must share the body's shift. The body is the root child with the
    largest subtree. Returns {} when there is nothing to align.
    """
    B = model.bones
    roots = [i for i, b in enumerate(B) if b.parent == -1]
    if len(roots) != 1:
        return {}
    kids = [i for i, b in enumerate(B) if b.parent == roots[0]]
    if len(kids) < 2:
        return {}
    children = {}
    for i, b in enumerate(B):
        children.setdefault(b.parent, []).append(i)

    def size(i):
        return 1 + sum(size(c) for c in children.get(i, []))

    body = max(kids, key=size)
    tr = tracks.get(body)
    if not tr or not tr.frames:
        return {}
    t0 = tuple(c * scale for c in tr.frames[0][0])
    shift = tuple(locals_[body][0][k] - t0[k] for k in range(3))
    return {k: shift for k in kids if k in tracks}


def build_animations(model, locals_, builder, conj, opts, log):
    yup = not opts.z_up
    scale = opts.scale
    eps = opts.anim_epsilon
    out = []
    stats = {"keys": 0, "channels": 0, "skipped": 0}

    for code in sorted(model.animations):
        tracks = model.animations[code]
        channels, samplers = [], []
        n_keys = 0
        sleeps = [t.sleep_ms for t in tracks.values()
                  if t.sleep_ms and len(t.frames) > 1]
        step = (max(set(sleeps), key=sleeps.count) if sleeps
                else DEFAULT_SLEEP_MS) / 1000.0

        shared_shift = _root_level_shift(model, tracks, locals_, scale)
        for bi, tr in sorted(tracks.items()):
            if not tr.frames:
                continue
            is_root = model.bones[bi].parent == -1
            bind_t, bind_q, bind_s = locals_[bi]
            times = [k * step for k in range(len(tr.frames))]
            trans, rots, scales = [], [], []
            prev = None
            for t, q, s in tr.frames:
                t = tuple(c * scale for c in t)
                if conj:
                    q = (-q[0], -q[1], -q[2], q[3])
                if is_root and yup:
                    t = to_yup(t)
                    q = quat_mul(ZUP_TO_YUP_QUAT, q)
                q = _norm_quat(q)
                scales.append((s, s, s))
                # keep neighbouring keys on one hemisphere: q and -q are the
                # same orientation, but LINEAR slerp between antipodes takes
                # the long way round and pops.
                if prev is not None and sum(a * b for a, b in zip(prev, q)) < 0:
                    q = (-q[0], -q[1], -q[2], -q[3])
                prev = q
                trans.append(t)
                rots.append(q)

            # WLD animation tracks use a different translation origin than the
            # bind track (e.g. PEBIP01 bind Y≈-26.5, anim frame-0 Y≈0).
            # Shift all frames so frame-0 matches the bind pose exactly,
            # keeping IBM × global_bone ≈ identity during animation.
            if trans:
                t_offset = shared_shift.get(bi) or tuple(bind_t[k] - trans[0][k] for k in range(3))
                if any(abs(d) > 1e-4 for d in t_offset):
                    trans = [tuple(v[k] + t_offset[k] for k in range(3))
                             for v in trans]

            for path, gtype, fmt in _PATHS:
                vals = {"translation": trans, "rotation": rots,
                        "scale": scales}[path]
                bind = {"translation": bind_t, "rotation": bind_q,
                        "scale": bind_s}[path]
                ts = times
                if not opts.no_collapse_static and _const(vals, eps):
                    if all(abs(x - y) <= eps for x, y in zip(vals[0], bind)):
                        stats["skipped"] += 1
                        continue
                    if path == "rotation" and all(abs(x + y) <= eps
                                                  for x, y in zip(vals[0],
                                                                  bind)):
                        stats["skipped"] += 1
                        continue
                    vals, ts = vals[:1], times[:1]
                in_acc = builder.add_accessor(
                    b"".join(struct.pack("<f", t) for t in ts),
                    5126, "SCALAR", len(ts),
                    minmax=([min(ts)], [max(ts)]), dedupe=True)
                out_acc = builder.add_accessor(
                    b"".join(struct.pack(fmt, *v) for v in vals),
                    5126, gtype, len(vals), dedupe=True)
                samplers.append({"input": in_acc, "output": out_acc,
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": bi, "path": path}})
                n_keys += len(vals)

        if not channels:
            log("  ! animation %s has no usable channels, skipped" % code)
            continue
        dur = max(len(t.frames) for t in tracks.values()) * step
        out.append({"name": code, "channels": channels, "samplers": samplers,
                    "extras": {"duration": round(dur, 4),
                               "frame_ms": round(step * 1000)}})
        stats["keys"] += n_keys
        stats["channels"] += len(channels)
        log("  anim %-5s %6.3f s  %4d channels  %6d keys"
            % (code, dur, len(channels), n_keys))
    return out, stats


# ==========================================================================
# input handling
# ==========================================================================

def quail_convert(s3d_path, out_dir, log):
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.environ.get("TWEEQ_QUAIL"), os.path.join(here, "..", "bin", "quail.exe"),
             os.path.join(here, "..", "bin", "quail"), os.path.expanduser("~/go/bin/quail"), shutil.which("quail")]
    quail = next((c for c in cands if c and os.path.isfile(c)), None)
    if not quail:
        raise SystemExit("quail not found (set TWEEQ_QUAIL, or install it as bin/quail.exe or ~/go/bin/quail)")
    log("  quail convert %s -> %s" % (os.path.basename(s3d_path), out_dir))
    r = subprocess.run([quail, "convert", s3d_path, out_dir],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise SystemExit("quail failed:\n%s\n%s" % (r.stdout, r.stderr))
    return out_dir


def find_model_dir(quail_dir, want=None):
    """The character lives in <quail>/<name>/<name>.wce.

    Shared archives such as global_chr.s3d hold dozens of actors side by side,
    so --actor picks one; without it the first is used and the rest listed.
    """
    found = []
    for entry in sorted(os.listdir(quail_dir)):
        d = os.path.join(quail_dir, entry)
        if not os.path.isdir(d) or entry in ("assets", ".vscode"):
            continue
        wce = os.path.join(d, entry + ".wce")
        if os.path.isfile(wce):
            found.append((entry, d, wce))
    if not found:
        raise SystemExit("no <name>/<name>.wce found under %s" % quail_dir)
    if want:
        for f in found:
            if f[0].lower() == want.lower():
                return f, found
        raise SystemExit("actor %r not in %s (have: %s)"
                         % (want, quail_dir,
                            ", ".join(f[0] for f in found)))
    return found[0], found


def load_model(quail_dir, opts, log):
    (name, mdir, wce), found = find_model_dir(quail_dir, opts.actor)
    if len(found) > 1:
        log("  %d actors in this archive, converting %r (--actor to pick "
            "another: %s)" % (len(found), name,
                              ", ".join(f[0] for f in found)))
    log("  model %s  <- %s" % (name, wce))
    model = CharModel()
    defs, insts = load_blocks(parse_wce(wce), model, log)
    attach_base_tracks(model, defs, insts, log)
    log("  skeleton %s: %d bones, %d attached skin(s), %d materials"
        % (model.skeleton_tag or "-", len(model.bones), len(model.skins),
           len(model.materials)))
    scaled = [(b.name, b.track.frames[0][2]) for b in model.bones
              if b.track and b.track.frames
              and abs(b.track.frames[0][2] - 1.0) > 1e-6]
    if scaled:
        log("  %d bone(s) carry a WLD shift_denominator scale (subtree "
            "scaling, inherited by children): %s"
            % (len(scaled), ", ".join("%s=%.4f" % s for s in scaled)))

    anim_dir = os.path.join(mdir, "animations")
    if not opts.no_anims and os.path.isdir(anim_dir):
        for fn in sorted(os.listdir(anim_dir)):
            if not fn.lower().endswith(".wce") or fn.startswith("_"):
                continue
            code, tracks = load_animation(os.path.join(anim_dir, fn), model,
                                          log)
            if tracks:
                model.animations[code] = tracks
    return name, model


# ==========================================================================
# CLI
# ==========================================================================

def main(argv):
    ap = argparse.ArgumentParser(
        description="Convert an old-world EQ WLD/S3D character to binary glTF")
    ap.add_argument("input", help="<model>_chr.s3d, or a quail output dir")
    ap.add_argument("output", help="destination .glb")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--z-up", action="store_true",
                    help="keep EQ's Z-up axes instead of converting to Y-up")
    ap.add_argument("--flip-v", action="store_true",
                    help="flip the V texture coordinate")
    ap.add_argument("--actor", default=None,
                    help="which actor to convert from a shared archive")
    ap.add_argument("--mesh", action="append", default=[], dest="meshes",
                    help="convert only this DMSPRITEDEF2 (repeatable)")
    ap.add_argument("--all-meshes", action="store_true",
                    help="convert every mesh, including unattached variants")
    ap.add_argument("--raw-quat", dest="conj_quat", action="store_false",
                    default=False,
                    help="use track quaternions as stored (WLD default)")
    ap.add_argument("--conj-quat", dest="conj_quat", action="store_true",
                    help="conjugate track quaternions (MDS/ANI convention)")
    ap.add_argument("--no-anims", action="store_true")
    ap.add_argument("--no-textures", action="store_true",
                    help="reference images by filename instead of embedding")
    ap.add_argument("--texture-dir", action="append", default=[],
                    dest="texture_dirs",
                    help="searched before assets/ (repeatable)")
    ap.add_argument("--dump-textures", metavar="DIR", default=None)
    ap.add_argument("--anim-epsilon", type=float, default=1e-5)
    ap.add_argument("--no-collapse-static", action="store_true")
    ap.add_argument("--keep-quail", metavar="DIR", default=None,
                    help="write the quail conversion here instead of a tempdir")
    ap.add_argument("--quiet", action="store_true")
    opts = ap.parse_args(argv)

    log = (lambda *a: None) if opts.quiet else (lambda *a: print(*a))
    tmp = None
    src = os.path.abspath(opts.input)
    try:
        if os.path.isdir(src):
            quail_dir = src
        else:
            if opts.keep_quail:
                quail_dir = os.path.abspath(opts.keep_quail)
                os.makedirs(os.path.dirname(quail_dir) or ".", exist_ok=True)
            else:
                tmp = tempfile.mkdtemp(prefix="wce_chr_")
                quail_dir = os.path.join(
                    tmp, os.path.splitext(os.path.basename(src))[0] + ".quail")
            quail_convert(src, quail_dir, log)

        opts.assets = os.path.join(quail_dir, "assets")
        name, model = load_model(quail_dir, opts, log)
        gltf, binary = convert(model, opts, log)

        out = os.path.abspath(opts.output)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(pack_glb(gltf, binary))
        log("  wrote %s (%d KB)" % (out, os.path.getsize(out) // 1024))
        validate_glb(out, quiet=opts.quiet)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
