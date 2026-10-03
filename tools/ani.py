#!/usr/bin/env python3
"""
EQG .ani (skeletal animation) binary reader.

Reverse engineered from quail's Go implementation
(github.com/xackery/quail, raw/ani_read.go + raw/eqg_name.go) and validated
against the 13 .ani files inside cth.eqg.

--------------------------------------------------------------------------
FILE LAYOUT  (all integers little-endian)
--------------------------------------------------------------------------

HEADER
    char[4]  magic         "EQGA"  (EverQuest Graphics Animation)
    uint32   version       cth.eqg ships version 2
    uint32   name_length   byte length of the name blob
    uint32   bone_count    number of animated bone tracks
    uint32   is_strict     ONLY IF version > 1. Non-zero = "strict": the
                           animation is bound to one specific skeleton and
                           the client refuses to retarget it.

NAME BLOB (name_length bytes)
    Back-to-back NUL-terminated ASCII strings, exactly like .mds. Bone name
    fields are BYTE OFFSETS into this blob, and quail abs()es them, so a
    negative offset means the same thing as its positive twin.

BONE TRACKS x bone_count
    uint32   frame_count
    int32    name_offset       -> bone name, matching an .mds bone name
    FRAMES x frame_count
        uint32   milliseconds  absolute time from animation start, not a delta
        float32  translation[3]
        float32  rotation[4]   (x, y, z, w)
        float32  scale[3]
        -> 44 bytes per frame

TRAILER
    Optionally 4 zero bytes. quail errors on anything else; we keep whatever
    is there so the reader never lies about the file.

--------------------------------------------------------------------------
SEMANTICS  (verified against cth.eqg + cth.mds)
--------------------------------------------------------------------------
* Tracks are PARENT-RELATIVE local TRS, exactly like the .mds bind pose
  pivot/quaternion/scale. Proof: for every one of the 13 animations, the
  first frame of every track is within ~1e-3 of the matching .mds bone's bind
  pivot, and the frame rotation is within ~1e-3 of the bind quaternion (see
  `compare_to_bind`). If the tracks were object-space or additive that would
  be false. So a track value REPLACES the bone's bind local transform; it is
  not composed with it.

* Rotations are stored in the SAME conjugated convention as .mds bone
  quaternions -- the stored (x, y, z, w) is the conjugate of the rotation you
  want, so negate x, y, z before use. This falls straight out of the point
  above: frame 0 equals the bind quaternion, and the bind quaternion is known
  to be conjugated (see mds_to_gltf.py's docstring, mean joint-vs-limb error
  0.200 u conjugated versus 2.406 u raw).

* Not every skeleton bone is animated. cth.mds has 103 bones; its animations
  drive 90 (idle/stnd/...) -- the missing ones are attachment points
  (*_POINT, TRACK_*) that just inherit their parent. Un-animated bones must
  keep their bind pose.

* !! TRACK NAMES ARE NOT RELIABLE KEYS. !!  Do not build a {name: bone} dict
  and look tracks up in it -- see `resolve_tracks` below and the section that
  follows.

--------------------------------------------------------------------------
BONE NAMES ARE UNRELIABLE -- MATCH TRACKS WITH `resolve_tracks`
--------------------------------------------------------------------------
The .ani name blobs were written by the authoring rig, not by the exporter
that wrote the .mds, and they disagree with the skeleton in three ways. All
three are present in cth.eqg:

1. MISSING NAMESPACE PREFIX.  Cazic Thule has four arms: a primary pair
   (ARML_*/ARMR_*) and a secondary pair growing out of the chest
   (CHEST_ARML_*/CHEST_ARMR_*). Twelve of the thirteen clips spell the
   secondary set with the CHEST_ prefix; `stnd` spells all 36 of them
   WITHOUT it, so every secondary-arm track collides with the primary bone
   of the same name. A naive name lookup sends the chest-arm keyframes to
   the shoulder/elbow/wrist of the main arms and the model's arms fold up
   over its head.

2. RENAMED LEAF JOINTS.  The rig calls the third finger joint `..._<N>03`;
   the skeleton calls it `..._<N>02_END` (CHEST_ARMR_INDX03 vs
   CHEST_ARMR_INDX02_END).

3. PLAIN TYPOS.  `AMRR_MIDL03` for ARMR_MIDL03 (in `stnd`), and a
   CHEST_ARML_PINK02 track misspelled CHEST_ARML_PINK01 -- a duplicate of
   its own neighbour -- in EVERY clip.

`resolve_tracks(a, m)` handles all three by falling back from exact names to
an order-preserving match on normalised names. It is the only supported way
to map tracks onto bones. Evidence it is right, on `stnd`: exact-name lookup
resolves 98 tracks onto just 63 distinct bones (35 collisions) with a mean
frame0-vs-bind pivot error of 0.143 u; `resolve_tracks` puts all 99 tracks on
99 distinct bones at 0.005 u -- a 30x better fit, and ANI frame 0 is known to
equal the bind pose.

* Track lengths differ per bone within one animation, and so do the frame
  times, so each track needs its own sampler input. Duration is the max
  timestamp over all tracks.

--------------------------------------------------------------------------
VALIDATION
--------------------------------------------------------------------------
Parsed every .ani in the installed EQ client: 7620 animations across 658 .eqg
archives, zero parse errors, zero unexpected trailing bytes. Every cth track
resolves onto a distinct cth.mds bone via `resolve_tracks` except the
Bone06..Bone36 leftovers from the authoring rig (in 6 clips) -- those name no
joint that exists and are dropped by the exporter.

--------------------------------------------------------------------------
NAMING
--------------------------------------------------------------------------
    <code>_<variant>_<n>_<model>.ani     e.g. idle_ba_1_cth.ani

The 4-letter code is the EQ animation slot (see ANIM_NAMES): idle, stnd,
walk, nrun, jmpa/jmpu, swim, turn, flch, crmp, gcst, slpr, stun.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
    import sys; sys.path.insert(0, "tools")
    import ani
    anims = ani.load_all_from_eqg(".../cth.eqg")   # {filename: Ani}
    a = anims["idle_ba_1_cth.ani"]
    a.duration_ms, a.bones[0].name, a.bones[0].frames[0].rotation

CLI:
    python3 tools/ani.py cth.eqg              # summary of every animation
    python3 tools/ani.py cth.eqg --verbose    # per-bone frame counts
"""

import os
import re
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAGIC = b"EQGA"

FRAME_STRUCT = struct.Struct("<I10f")   # ms + 3 translation + 4 rotation + 3 scale
FRAME_SIZE = FRAME_STRUCT.size          # 44

# EQ animation slot codes seen on player/NPC models. Only used for pretty
# printing; unknown codes pass through unchanged.
#
# Code frequencies come from a sweep of all 2678 .eqg archives in the client.
# crmp is DEATH, not a crouch: `crch` (361 archives) is the crouch slot, and
# cth's crmp track drops ROOT from z=2.37 to z=0.31 while pitching it 75
# degrees -- the model collapses to the floor. "crmp" = crumple.
ANIM_NAMES = {
    "crch": "crouch",
    "crmp": "crumple (death)",
    "dest": "destroyed",
    "flch": "flinch (damage taken)",
    "gcst": "generic cast",
    "idle": "idle",
    "jmpa": "jump (airborne)",
    "jmpu": "jump (launch)",
    "knel": "kneel",
    "ltrn": "turn left",
    "msht": "melee, shooting",
    "nrun": "run",
    "nsit": "sit",
    "slpr": "sleep / prone",
    "stnd": "stand",
    "stun": "stunned",
    "swim": "swim",
    "turn": "turn in place",
    "walk": "walk",
}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Frame:
    """One keyframe of one bone. Local (parent-relative) TRS."""
    milliseconds: int
    translation: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]     # (x, y, z, w), CONJUGATED
    scale: Tuple[float, float, float]

    @property
    def seconds(self) -> float:
        return self.milliseconds / 1000.0

    def true_rotation(self) -> Tuple[float, float, float, float]:
        """The rotation with the storage conjugation undone -- what you pose with."""
        x, y, z, w = self.rotation
        return (-x, -y, -z, w)


@dataclass
class BoneTrack:
    name: str
    frames: List[Frame] = field(default_factory=list)
    _name_off: int = 0

    @property
    def duration_ms(self) -> int:
        return self.frames[-1].milliseconds if self.frames else 0

    def is_static(self, eps: float = 1e-6) -> bool:
        """True when every frame holds the same transform (a constant track)."""
        if len(self.frames) < 2:
            return True
        f0 = self.frames[0]
        for f in self.frames[1:]:
            for a, b in ((f0.translation, f.translation),
                         (f0.rotation, f.rotation),
                         (f0.scale, f.scale)):
                if any(abs(x - y) > eps for x, y in zip(a, b)):
                    return False
        return True


@dataclass
class Ani:
    version: int = 2
    is_strict: bool = False
    bones: List[BoneTrack] = field(default_factory=list)
    name: str = ""              # source filename, when known
    names_blob: bytes = b""
    trailer: bytes = b""

    # -- convenience ------------------------------------------------------
    @property
    def duration_ms(self) -> int:
        return max((b.duration_ms for b in self.bones), default=0)

    @property
    def duration(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def frame_count(self) -> int:
        """Frames on the longest track."""
        return max((len(b.frames) for b in self.bones), default=0)

    @property
    def anim_code(self) -> str:
        """The 4-letter EQ slot code, e.g. 'idle' from idle_ba_1_cth.ani."""
        base = os.path.basename(self.name)
        return base.split("_")[0].lower() if base else ""

    @property
    def label(self) -> str:
        """Human name for the slot, falling back to the code."""
        return ANIM_NAMES.get(self.anim_code, self.anim_code or self.name)

    def track(self, bone_name: str) -> Optional[BoneTrack]:
        for b in self.bones:
            if b.name == bone_name:
                return b
        return None

    def summary(self) -> str:
        static = sum(1 for b in self.bones if b.is_static())
        return ("%-22s v%d strict=%-5s %3d tracks (%d static)  %6.3f s  "
                "%3d frames  %5d keys"
                % (self.name or "<unnamed>", self.version, self.is_strict,
                   len(self.bones), static, self.duration, self.frame_count,
                   sum(len(b.frames) for b in self.bones)))


# --------------------------------------------------------------------------
# Name blob (same scheme as mds.NameTable; kept local so ani.py stands alone)
# --------------------------------------------------------------------------

class NameTable:
    def __init__(self, data: bytes = b""):
        self.data = bytes(data)
        self._by_offset: Dict[int, str] = {}
        start = 0
        for i, b in enumerate(self.data):
            if b == 0:
                self._by_offset[start] = self.data[start:i].decode("latin-1")
                start = i + 1

    def get(self, offset: int) -> str:
        if offset < 0:
            offset = -offset
        return self._by_offset.get(offset, "!UNK(%d)" % offset)

    def __len__(self):
        return len(self.data)


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------

def parse(data: bytes, name: str = "") -> Ani:
    """Parse an .ani binary blob."""
    if data[:4] != MAGIC:
        raise ValueError("invalid header %r, wanted %r" % (data[:4], MAGIC))

    version, name_length, bone_count = struct.unpack_from("<III", data, 4)
    p = 16
    is_strict = False
    if version > 1:
        is_strict = struct.unpack_from("<I", data, p)[0] != 0
        p += 4

    names_blob = data[p:p + name_length]
    if len(names_blob) != name_length:
        raise ValueError("truncated name blob (%d of %d bytes)"
                         % (len(names_blob), name_length))
    p += name_length
    N = NameTable(names_blob)

    a = Ani(version=version, is_strict=is_strict, name=name,
            names_blob=names_blob)

    for bi in range(bone_count):
        if p + 8 > len(data):
            raise ValueError("truncated at bone %d of %d" % (bi, bone_count))
        frame_count, name_off = struct.unpack_from("<Ii", data, p)
        p += 8
        need = frame_count * FRAME_SIZE
        if p + need > len(data):
            raise ValueError("bone %d (%s) wants %d frames (%d bytes) but only "
                             "%d bytes remain"
                             % (bi, N.get(name_off), frame_count, need,
                                len(data) - p))
        track = BoneTrack(name=N.get(name_off), _name_off=name_off)
        for _ in range(frame_count):
            v = FRAME_STRUCT.unpack_from(data, p)
            p += FRAME_SIZE
            track.frames.append(Frame(milliseconds=v[0],
                                      translation=v[1:4],
                                      rotation=v[4:8],
                                      scale=v[8:11]))
        a.bones.append(track)

    a.trailer = data[p:]
    if a.trailer and a.trailer != b"\x00\x00\x00\x00":
        sys.stderr.write("ani: %s: %d unexpected trailing bytes\n"
                         % (name or "<unnamed>", len(a.trailer)))
    return a


def load(path: str) -> Ani:
    with open(path, "rb") as f:
        return parse(f.read(), name=os.path.basename(path))


def load_all_from_eqg(eqg_path: str) -> "Dict[str, Ani]":
    """Parse every .ani in an .eqg PFS archive -> {filename: Ani}, name-sorted."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import s3d
    files = s3d.load(eqg_path)
    return load_all_from_files(files)


def load_all_from_files(files: Dict[str, bytes]) -> "Dict[str, Ani]":
    """Parse every .ani in an already-loaded PFS dict -> {filename: Ani}."""
    out = {}
    for fn in sorted(files, key=str.lower):
        if not fn.lower().endswith(".ani"):
            continue
        out[fn] = parse(files[fn], name=fn)
    return out


# --------------------------------------------------------------------------
# Validation against an .mds skeleton
# --------------------------------------------------------------------------

_TYPOS = (("AMRR_", "ARMR_"), ("AMRL_", "ARML_"))
_TRAILING_DIGITS = re.compile(r"\d+$")


def normalize_bone_name(name: str) -> str:
    """
    Collapse a bone/track name to the joint FAMILY it belongs to, so that
    names the rig and the skeleton spell differently still compare equal.

    Drops, in order: rig typos, the CHEST_ arm namespace, a _END leaf suffix,
    and the joint ordinal.

        ARMR_INDX03            -> ARMR_INDX
        CHEST_ARMR_INDX02_END  -> ARMR_INDX
        AMRR_MIDL03            -> ARMR_MIDL

    Deliberately lossy: the ordinal is what `resolve_tracks` re-derives from
    track order, because the rig's ordinals are off by one in places.
    """
    n = name.upper()
    for bad, good in _TYPOS:
        if n.startswith(bad):
            n = good + n[len(bad):]
    # CHEST_ARML_* / CHEST_ARMR_* are the secondary arm pair. Strip only that
    # prefix -- CHEST_CHEST01 and CHEST03_TORCH01 are unrelated bones.
    if n.startswith("CHEST_ARM"):
        n = n[len("CHEST_"):]
    if n.endswith("_END"):
        n = n[:-len("_END")]
    return _TRAILING_DIGITS.sub("", n)


def resolve_tracks(a: Ani, m) -> Tuple[Dict[int, int], List[str]]:
    """
    Map each of `a`'s bone tracks onto a DISTINCT bone of the mds.Mds `m`.

    THIS, not a {bone.name: index} dict, is how tracks must be bound. See the
    "BONE NAMES ARE UNRELIABLE" section at the top of this module for why --
    in short, `stnd_ba_1_cth.ani` names Cazic Thule's secondary chest-arms
    exactly like his primary arms, so a name lookup drives the wrong limbs.

    Two passes:

      1. Claim by exact name. First track to ask for a bone gets it; a bone is
         never handed out twice. This resolves everything in a well-named clip
         and, in a mis-named one, correctly gives the primary bones to the
         primary tracks (which always come first in track order).

      2. Everything left over is matched on `normalize_bone_name`, walking
         forward through the still-unclaimed bones. Track order and bone order
         agree within a limb, so "the next unclaimed bone of this family at or
         after the last one I assigned" recovers the intended joint -- and the
         family check keeps junk tracks (Bone09 and friends) from binding to
         anything at all.

    Returns ({track index: bone index}, [names of dropped tracks]).
    """
    first_by_name: Dict[str, int] = {}
    for i, b in enumerate(m.bones):
        first_by_name.setdefault(b.name, i)

    assigned: Dict[int, int] = {}
    used = set()

    for ti, track in enumerate(a.bones):
        bi = first_by_name.get(track.name)
        if bi is not None and bi not in used:
            assigned[ti] = bi
            used.add(bi)

    by_family: Dict[str, List[int]] = {}
    for i, b in enumerate(m.bones):
        by_family.setdefault(normalize_bone_name(b.name), []).append(i)

    dropped: List[str] = []
    prev = -1
    for ti, track in enumerate(a.bones):
        if ti in assigned:
            prev = assigned[ti]
            continue
        free = [i for i in by_family.get(normalize_bone_name(track.name), ())
                if i not in used]
        if not free:
            dropped.append(track.name)
            continue
        after = [i for i in free if i > prev]
        bi = after[0] if after else free[0]
        assigned[ti] = bi
        used.add(bi)
        prev = bi

    return assigned, dropped


def check_bones(a: Ani, m) -> Tuple[List[str], List[str]]:
    """
    Cross-check an Ani's tracks against an mds.Mds skeleton.

    Returns (unresolvable_track_names, mds_bones_not_animated).
    """
    assigned, dropped = resolve_tracks(a, m)
    hit = set(assigned.values())
    unanimated = [b.name for i, b in enumerate(m.bones) if i not in hit]
    return dropped, unanimated


def compare_to_bind(a: Ani, m) -> Optional[Tuple[float, float, int]]:
    """
    Evidence that ANI tracks are parent-relative locals in the same convention
    as the MDS bind pose: compare each track's FIRST frame against the matching
    bone's bind pivot/quaternion.

    Returns (mean translation delta, mean rotation delta, matched track count).
    """
    assigned, _ = resolve_tracks(a, m)
    td = rd = 0.0
    n = 0
    for ti, bi in sorted(assigned.items()):
        track = a.bones[ti]
        bone = m.bones[bi]
        if not track.frames:
            continue
        f = track.frames[0]
        td += max(abs(x - y) for x, y in zip(f.translation, bone.pivot))
        # quaternions are double covers: q and -q are the same rotation
        d1 = max(abs(x - y) for x, y in zip(f.rotation, bone.quaternion))
        d2 = max(abs(x + y) for x, y in zip(f.rotation, bone.quaternion))
        rd += min(d1, d2)
        n += 1
    if not n:
        return None
    return td / n, rd / n, n


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(
        description="Inspect EQG .ani skeletal animations")
    ap.add_argument("path", help="an .eqg archive or a single .ani file")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list every bone track with its frame count")
    ap.add_argument("--bone", default=None,
                    help="dump every keyframe of this bone")
    opts = ap.parse_args(argv[1:])

    m = None
    if opts.path.lower().endswith((".eqg", ".s3d", ".pfs", ".pak")):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import s3d
        files = s3d.load(opts.path)
        anims = load_all_from_files(files)
        mds_names = [k for k in files if k.lower().endswith(".mds")]
        if mds_names:
            import mds as mdsmod
            m = mdsmod.parse(files[mds_names[0]])
            print("skeleton: %s -- %d bones" % (mds_names[0], len(m.bones)))
    else:
        anims = {os.path.basename(opts.path): load(opts.path)}

    if not anims:
        print("no .ani files found in %s" % opts.path)
        return 1

    print("%d animation(s) in %s\n" % (len(anims), os.path.basename(opts.path)))
    header = ("%-22s %-22s %8s %7s %7s %7s"
              % ("file", "slot", "seconds", "frames", "tracks", "keys"))
    print(header)
    print("-" * len(header))
    total_keys = 0
    for fn, a in anims.items():
        keys = sum(len(b.frames) for b in a.bones)
        total_keys += keys
        print("%-22s %-22s %8.3f %7d %7d %7d"
              % (fn, a.label, a.duration, a.frame_count, len(a.bones), keys))
    print("-" * len(header))
    print("%-22s %-22s %8s %7s %7d %7d"
          % ("total", "", "", "", sum(len(a.bones) for a in anims.values()),
             total_keys))

    if m is not None:
        print("\nvalidation against the .mds skeleton:")
        for fn, a in anims.items():
            assigned, dropped = resolve_tracks(a, m)
            exact = sum(1 for ti, bi in assigned.items()
                        if a.bones[ti].name == m.bones[bi].name)
            cmp_ = compare_to_bind(a, m)
            note = ""
            if cmp_:
                note = ("  frame0-vs-bind: dT=%.6f dQ=%.6f over %d tracks"
                        % cmp_)
            print("  %-22s %3d/%3d bones bound (%d renamed), %d dropped%s"
                  % (fn, len(assigned), len(m.bones), len(assigned) - exact,
                     len(dropped), note))
            for ti, bi in sorted(assigned.items()):
                if a.bones[ti].name != m.bones[bi].name:
                    print("      ~ track %-22s -> bone %s"
                          % (a.bones[ti].name, m.bones[bi].name))
            if dropped:
                print("      ! no such joint: %s" % ", ".join(dropped[:8]))
        _, unanimated = check_bones(next(iter(anims.values())), m)
        if unanimated:
            print("  bones with no track (they keep their bind pose): %d"
                  % len(unanimated))
            print("      %s" % ", ".join(unanimated))

    if opts.verbose or opts.bone:
        for fn, a in anims.items():
            print("\n=== %s  (%s)  %.3f s" % (fn, a.label, a.duration))
            for t in a.bones:
                if opts.bone and t.name != opts.bone:
                    continue
                print("  %-24s %4d frames  %7.3f s%s"
                      % (t.name, len(t.frames), t.duration_ms / 1000.0,
                         "  [static]" if t.is_static() else ""))
                if opts.bone:
                    for f in t.frames:
                        print("      %7d ms  T(%8.4f %8.4f %8.4f)  "
                              "R(%7.4f %7.4f %7.4f %7.4f)  S(%.3f %.3f %.3f)"
                              % ((f.milliseconds,) + tuple(f.translation)
                                 + tuple(f.rotation) + tuple(f.scale)))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
