#!/usr/bin/env python3
"""
EQG .mds (skinned model) binary reader/writer.

Reverse engineered from quail's Go implementation
(github.com/xackery/quail, raw/mds_read.go, raw/mds_write.go, raw/eqg_name.go)
and validated by byte-exact round-trip against cth.mds from cth.eqg.

--------------------------------------------------------------------------
FILE LAYOUT  (all integers little-endian)
--------------------------------------------------------------------------

HEADER (24 bytes)
    char[4]  magic          "EQGS"   (EverQuest Graphics Skinned)
    uint32   version        1 for old-world Luclin/PoP models (cth.mds = 1)
    uint32   name_length    byte length of the name blob that follows
    uint32   material_count
    uint32   bone_count
    uint32   model_count

NAME BLOB (name_length bytes)
    Back-to-back NUL-terminated ASCII strings. Every "name" field elsewhere
    in the file is a BYTE OFFSET into this blob, not an index. Offset 0 is
    the first string. quail takes abs() of the offset, so negative values
    are treated as positive (the writer emits negatives in some paths); we
    preserve the raw signed value so round-trips are exact.

MATERIALS  x material_count
    int32    id
    int32    name_offset          -> material name, e.g. "CTH"
    int32    shader_name_offset   -> e.g. "Chroma_MaxCBS1.fx"
    uint32   property_count
    PROPERTIES x property_count
        int32   name_offset       -> e.g. "e_TextureDiffuse0"
        uint32  type              0 = float, 1 = int, 2 = texture(name offset),
                                  3 = color(int)
        then 4 bytes whose meaning depends on type:
            type 0 -> float32 value
            type 2 -> int32 name offset (a .dds filename)
            else   -> int32 value

BONES  x bone_count
    int32    name_offset          -> e.g. "ROOT_BONE", "CHEST_CHEST01"
    int32    next                 sibling bone index, -1 = none
    uint32   children_count
    int32    child_index          index of first child, -1 = none
    float32  pivot[3]             bind-pose translation, relative to parent
    float32  quaternion[4]        bind-pose rotation (x, y, z, w)
    float32  scale[3]             bind-pose scale
    -> 56 bytes per bone. The hierarchy is a first-child / next-sibling tree,
       NOT a parent-index array. Bone 0 is the root.

MODELS  x model_count
    uint32   main_piece           1 = this is the main body piece
    int32    name_offset
    uint32   vertex_count
    uint32   face_count
    uint32   bone_assign_count    quail calls this "BoneCount", but it is
                                  really the number of WEIGHT RECORDS that
                                  follow. Verified across 196 models in the
                                  live EQ install: it is ALWAYS == vertex_count
                                  (0 differ). If you add vertices you MUST
                                  update it -- serialize() warns if you don't.

    VERTICES x vertex_count
        float32 position[3]
        float32 normal[3]
        uint8   tint[4]           ONLY IF version > 2 (else implicit 128,128,128,255)
        float32 uv[2]
        float32 uv2[2]            ONLY IF version > 2 (else implicit 0,0)
        -> version <= 2: 32 bytes/vertex.  version > 2: 44 bytes/vertex.

    FACES x face_count
        uint32  index[3]          vertex indices
        int32   material_id       -1 = no material
        uint32  flags             bit 0x01 passable, 0x02 transparent, ...
        -> 20 bytes per face

    WEIGHTS x vertex_count -- normally present IF the file-level bone_count > 0,
    but the block is OPTIONAL: generic_robe_red_iksar_male.mds in
    frostfellelfhat.eqg declares 161 bones and then simply ends after the face
    list, with no weight block at all (an unskinned attachment piece that still
    carries a skeleton). quail crashes on that file; we detect it by checking
    the remaining byte count and set Model.has_weights=False.
        int32   count             number of ACTIVE influences (0..4)
        4 x { int32 bone_index; float32 value }   <- always 4 slots, padded
        -> 36 bytes per vertex. Unused slots are (0, 0.0).

TRAILER
    Optionally 4 zero bytes at end of file. cth.mds has none.

--------------------------------------------------------------------------
NOTES / GOTCHAS
--------------------------------------------------------------------------
* quail's own reader has an inverted weight filter
  (`if j < int(count) { continue }` drops the ACTIVE weights and keeps the
  padding). We keep all 4 raw slots plus the count, and expose
  `Vertex.active_weights()` for the correct subset.
* quail's writer rebuilds the name blob without deduplication and in its own
  order, so quail cannot byte-round-trip a file. This module keeps the
  original name blob by default (`rebuild_names=False`) and therefore DOES
  round-trip byte-exactly.
* Material ids are not necessarily equal to their array index. Faces store
  the material *id*; index by `Mds.material_by_id()`.
* version <= 2 files have no vertex color and no second UV set. Writing a
  vertex with a tint into a version-1 file silently drops the tint.
* Every .mds in the live EQ install is version 1, so the version > 2 vertex
  layout (tint + uv2) is implemented from quail's source but is UNVERIFIED
  against real data.

--------------------------------------------------------------------------
VALIDATION
--------------------------------------------------------------------------
Round-tripped every .mds in the installed EQ client: 491 models across 2678
.eqg archives, all byte-exact, zero mismatches, zero parse errors. All are
version 1. `serialize(rebuild_names=True)` also reproduces cth.mds exactly,
which confirms the name blob is stored in natural read order with dedup.

--------------------------------------------------------------------------
TYPICAL USE -- swap geometry, keep the skeleton
--------------------------------------------------------------------------
    import sys; sys.path.insert(0, "tools")
    import s3d, mds

    m = mds.load_from_eqg(".../cth.eqg")
    model = m.models[0]
    # m.bones and m.bone_index("HEAD_HEAD") are untouched, so the .ani files
    # keep working -- animations bind to bones by index, not to geometry.
    model.vertices = new_vertices     # each with .set_weights([(bone_i, w), ...])
    model.faces = new_faces           # material_id must match an existing material
    model.sync_counts()
    data = mds.serialize(m)

Repacking the .eqg: tools/s3d.py is READ-ONLY (no PFS writer), so round-trip
the container through quail instead -- verified end to end on cth.eqg:

    ~/go/bin/quail unzip cth.eqg workdir/     # NOT `convert` -- that wants a
                                              # .eqg/.pfs output extension
    python3 ... # rewrite workdir/cth.mds via this module
    ~/go/bin/quail zip workdir/ cth_new.eqg

The 13 .ani files and the .lay/.pts pass through untouched, and the rebuilt
archive re-reads cleanly with both this module and `quail inspect`.
"""

import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAGIC = b"EQGS"

# Material property types
PARAM_FLOAT = 0
PARAM_INT = 1
PARAM_TEXTURE = 2
PARAM_COLOR = 3

# Face flags (from quail ModFaceFlag)
FACE_PASSABLE = 0x01
FACE_TRANSPARENT = 0x02
FACE_COLLISION_REQUIRED = 0x04
FACE_CULLED = 0x08
FACE_DEGENERATE = 0x10


# --------------------------------------------------------------------------
# Name blob
# --------------------------------------------------------------------------

class NameTable:
    """The MDS string blob. Names are referenced by byte offset into it."""

    def __init__(self, data: bytes = b""):
        self.data = bytearray(data)
        self._by_offset = {}
        self._parse()

    def _parse(self):
        self._by_offset = {}
        start = 0
        for i, b in enumerate(self.data):
            if b == 0:
                self._by_offset[start] = self.data[start:i].decode(
                    "latin-1")
                start = i + 1

    def get(self, offset: int) -> str:
        """Resolve a name offset. Negative offsets are abs()'d, matching quail."""
        if offset < 0:
            offset = -offset
        return self._by_offset.get(offset, "!UNK(%d)" % offset)

    def offset_of(self, name: str) -> int:
        for off, n in self._by_offset.items():
            if n == name:
                return off
        return -1

    def add(self, name: str) -> int:
        """Append a name if absent (deduplicating). Returns its byte offset."""
        if name == "":
            # Offset 0 only means "" if the blob actually starts with a NUL.
            existing = self._by_offset.get(0)
            if existing == "":
                return 0
        off = self.offset_of(name)
        if off != -1:
            return off
        off = len(self.data)
        self.data.extend(name.encode("latin-1"))
        self.data.append(0)
        self._by_offset[off] = name
        return off

    def __len__(self):
        return len(self.data)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class MaterialParam:
    name: str
    type: int
    value: object          # float for type 0, str for type 2, int otherwise
    _name_off: int = 0
    _value_off: int = 0    # only meaningful for type 2


@dataclass
class Material:
    id: int
    name: str
    shader_name: str
    params: List[MaterialParam] = field(default_factory=list)
    _name_off: int = 0
    _shader_off: int = 0

    def texture(self, slot: str = "e_TextureDiffuse0") -> Optional[str]:
        for p in self.params:
            if p.name == slot:
                return p.value
        return None


@dataclass
class Bone:
    name: str
    next: int              # sibling index, -1 = none
    children_count: int
    child_index: int       # first-child index, -1 = none
    pivot: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    quaternion: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    _name_off: int = 0


@dataclass
class Vertex:
    position: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    uv: Tuple[float, float]
    tint: Tuple[int, int, int, int] = (128, 128, 128, 255)
    uv2: Tuple[float, float] = (0.0, 0.0)
    # Skinning: always 4 raw slots + a count of how many are active.
    weight_count: int = 0
    weights: List[Tuple[int, float]] = field(default_factory=list)

    def active_weights(self) -> List[Tuple[int, float]]:
        """The (bone_index, value) pairs that actually influence this vertex."""
        return self.weights[:self.weight_count]

    def set_weights(self, pairs):
        """Set skinning influences from a list of (bone_index, weight)."""
        pairs = list(pairs)[:4]
        self.weight_count = len(pairs)
        self.weights = pairs + [(0, 0.0)] * (4 - len(pairs))


@dataclass
class Face:
    indices: Tuple[int, int, int]
    material_id: int
    flags: int = 0


@dataclass
class Model:
    name: str
    main_piece: int = 1
    # Really the weight-record count; always equals len(vertices) in every
    # shipped EQ model. Call sync_counts() after changing geometry.
    bone_count: int = 0
    vertices: List[Vertex] = field(default_factory=list)
    faces: List[Face] = field(default_factory=list)
    # False for the rare model that declares bones but ships no weight block.
    has_weights: bool = True
    _name_off: int = 0

    def sync_counts(self):
        self.bone_count = len(self.vertices)


@dataclass
class Mds:
    version: int = 1
    materials: List[Material] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    models: List[Model] = field(default_factory=list)
    names: NameTable = field(default_factory=NameTable)
    trailer: bytes = b""   # trailing bytes after the last model (usually b"" or 4 zeros)

    # -- convenience ------------------------------------------------------
    @property
    def has_tint(self) -> bool:
        return self.version > 2

    def material_by_id(self, mid: int) -> Optional[Material]:
        for m in self.materials:
            if m.id == mid:
                return m
        return None

    def bone_index(self, name: str) -> int:
        for i, b in enumerate(self.bones):
            if b.name == name:
                return i
        return -1

    def bone_parents(self) -> List[int]:
        """Convert the first-child/next-sibling tree into a parent-index array."""
        parents = [-1] * len(self.bones)
        for i, b in enumerate(self.bones):
            if b.child_index is None or b.child_index < 0:
                continue
            c = b.child_index
            for _ in range(b.children_count):
                if c < 0 or c >= len(self.bones):
                    break
                parents[c] = i
                c = self.bones[c].next
        return parents

    def summary(self) -> str:
        out = ["MDS version %d, %d names (%d bytes), %d materials, %d bones, "
               "%d models" % (self.version, len(self.names._by_offset),
                              len(self.names), len(self.materials),
                              len(self.bones), len(self.models))]
        for m in self.materials:
            out.append("  material %d %s shader=%s params=%d" %
                       (m.id, m.name, m.shader_name, len(m.params)))
            for p in m.params:
                out.append("    %s type=%d value=%r" % (p.name, p.type, p.value))
        for i, m in enumerate(self.models):
            out.append("  model %d %r main_piece=%d verts=%d faces=%d bone_count=%d"
                       % (i, m.name, m.main_piece, len(m.vertices),
                          len(m.faces), m.bone_count))
        return "\n".join(out)


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]
        if len(b) != n:
            raise ValueError("unexpected EOF at %d (wanted %d bytes)" % (self.p, n))
        self.p += n
        return b

    def u32(self) -> int:
        return struct.unpack_from("<I", self.d, self._adv(4))[0]

    def i32(self) -> int:
        return struct.unpack_from("<i", self.d, self._adv(4))[0]

    def f32(self) -> float:
        return struct.unpack_from("<f", self.d, self._adv(4))[0]

    def fixed(self, n: int, fmt: str):
        return struct.unpack_from("<" + fmt, self.d, self._adv(struct.calcsize("<" + fmt)))

    def _adv(self, n: int) -> int:
        p = self.p
        if p + n > len(self.d):
            raise ValueError("unexpected EOF at %d (wanted %d bytes)" % (p, n))
        self.p = p + n
        return p


def parse(data: bytes) -> Mds:
    """Parse an MDS binary blob into an Mds."""
    r = _Reader(data)
    magic = r.take(4)
    if magic != MAGIC:
        raise ValueError("invalid header %r, wanted %r" % (magic, MAGIC))

    mds = Mds()
    mds.version = r.u32()
    name_length = r.u32()
    material_count = r.u32()
    bone_count = r.u32()
    model_count = r.u32()

    mds.names = NameTable(r.take(name_length))
    N = mds.names

    for _ in range(material_count):
        mid = r.i32()
        noff = r.i32()
        soff = r.i32()
        mat = Material(id=mid, name=N.get(noff), shader_name=N.get(soff),
                       _name_off=noff, _shader_off=soff)
        for _ in range(r.u32()):
            poff = r.i32()
            ptype = r.u32()
            if ptype == PARAM_FLOAT:
                val, voff = r.f32(), 0
            else:
                raw = r.i32()
                if ptype == PARAM_TEXTURE:
                    val, voff = N.get(raw), raw
                else:
                    val, voff = raw, 0
            mat.params.append(MaterialParam(name=N.get(poff), type=ptype,
                                            value=val, _name_off=poff,
                                            _value_off=voff))
        mds.materials.append(mat)

    for _ in range(bone_count):
        noff = r.i32()
        nxt = r.i32()
        ccount = r.u32()
        cidx = r.i32()
        vals = r.fixed(10, "10f")
        mds.bones.append(Bone(
            name=N.get(noff), next=nxt, children_count=ccount, child_index=cidx,
            pivot=vals[0:3], quaternion=vals[3:7], scale=vals[7:10],
            _name_off=noff))

    has_tint = mds.version > 2

    for _ in range(model_count):
        main_piece = r.u32()
        noff = r.i32()
        vcount = r.u32()
        fcount = r.u32()
        mbones = r.u32()
        model = Model(name=N.get(noff), main_piece=main_piece,
                      bone_count=mbones, _name_off=noff)

        for _ in range(vcount):
            pos = r.fixed(3, "3f")
            nrm = r.fixed(3, "3f")
            tint = r.fixed(4, "4B") if has_tint else (128, 128, 128, 255)
            uv = r.fixed(2, "2f")
            uv2 = r.fixed(2, "2f") if has_tint else (0.0, 0.0)
            model.vertices.append(Vertex(position=pos, normal=nrm, uv=uv,
                                         tint=tint, uv2=uv2))

        for _ in range(fcount):
            idx = r.fixed(3, "3I")
            mid = r.i32()
            flags = r.u32()
            model.faces.append(Face(indices=idx, material_id=mid, flags=flags))

        # Weights normally exist when the FILE has bones (not the model's own
        # count), but the block is optional -- verify the bytes are actually
        # there before consuming them.
        need = len(model.vertices) * 36
        model.has_weights = bone_count > 0 and (len(data) - r.p) >= need
        if bone_count > 0 and not model.has_weights:
            sys.stderr.write(
                "mds: model %r declares %d bones but has no weight block "
                "(%d bytes left, needed %d)\n"
                % (model.name, bone_count, len(data) - r.p, need))
        if model.has_weights:
            for v in model.vertices:
                v.weight_count = r.i32()
                v.weights = []
                for _ in range(4):
                    bi = r.i32()
                    val = r.f32()
                    v.weights.append((bi, val))

        mds.models.append(model)

    mds.trailer = bytes(data[r.p:])
    if mds.trailer and mds.trailer != b"\x00\x00\x00\x00":
        sys.stderr.write("mds: warning, %d unexpected trailing bytes\n"
                         % len(mds.trailer))
    return mds


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

def _build_names(mds: Mds) -> NameTable:
    """Rebuild the name blob from scratch, deduplicating, in read order."""
    N = NameTable()
    for mat in mds.materials:
        mat._name_off = N.add(mat.name)
        mat._shader_off = N.add(mat.shader_name)
        for p in mat.params:
            p._name_off = N.add(p.name)
            if p.type == PARAM_TEXTURE:
                p._value_off = N.add(p.value)
    for b in mds.bones:
        b._name_off = N.add(b.name)
    for m in mds.models:
        m._name_off = N.add(m.name)
    return N


def serialize(mds: Mds, rebuild_names: bool = False) -> bytes:
    """
    Serialize an Mds back to binary.

    rebuild_names=False (default) reuses the name blob and the exact offsets
    read from the source file, which gives a byte-exact round trip. Use
    rebuild_names=True after renaming things or adding new materials/bones.
    """
    if rebuild_names:
        N = _build_names(mds)
    else:
        N = mds.names
        # Names that were added since parse still need offsets.
        for mat in mds.materials:
            if N.get(mat._name_off) != mat.name:
                mat._name_off = N.add(mat.name)
            if N.get(mat._shader_off) != mat.shader_name:
                mat._shader_off = N.add(mat.shader_name)
            for p in mat.params:
                if N.get(p._name_off) != p.name:
                    p._name_off = N.add(p.name)
                if p.type == PARAM_TEXTURE and N.get(p._value_off) != p.value:
                    p._value_off = N.add(p.value)
        for b in mds.bones:
            if N.get(b._name_off) != b.name:
                b._name_off = N.add(b.name)
        for m in mds.models:
            if N.get(m._name_off) != m.name:
                m._name_off = N.add(m.name)

    name_data = bytes(N.data)
    out = bytearray()
    out += MAGIC
    out += struct.pack("<5I", mds.version, len(name_data), len(mds.materials),
                       len(mds.bones), len(mds.models))
    out += name_data

    for mat in mds.materials:
        out += struct.pack("<3i I", mat.id, mat._name_off, mat._shader_off,
                           len(mat.params))
        for p in mat.params:
            out += struct.pack("<iI", p._name_off, p.type)
            if p.type == PARAM_FLOAT:
                out += struct.pack("<f", float(p.value))
            elif p.type == PARAM_TEXTURE:
                out += struct.pack("<i", p._value_off)
            else:
                out += struct.pack("<i", int(p.value))

    for b in mds.bones:
        out += struct.pack("<iiIi", b._name_off, b.next, b.children_count,
                           b.child_index)
        out += struct.pack("<10f", *b.pivot, *b.quaternion, *b.scale)

    has_tint = mds.version > 2
    for m in mds.models:
        if mds.bones and m.has_weights and m.bone_count != len(m.vertices):
            sys.stderr.write(
                "mds: warning, model %r bone_count=%d but has %d vertices; "
                "every shipped EQ model has these equal. Call "
                "Model.sync_counts() if you changed geometry.\n"
                % (m.name, m.bone_count, len(m.vertices)))
        out += struct.pack("<IiIII", m.main_piece, m._name_off, len(m.vertices),
                           len(m.faces), m.bone_count)
        for v in m.vertices:
            out += struct.pack("<6f", *v.position, *v.normal)
            if has_tint:
                out += struct.pack("<4B", *v.tint)
            out += struct.pack("<2f", *v.uv)
            if has_tint:
                out += struct.pack("<2f", *v.uv2)
        for f in m.faces:
            out += struct.pack("<3IiI", *f.indices, f.material_id, f.flags)
        if len(mds.bones) > 0 and m.has_weights:
            for v in m.vertices:
                w = list(v.weights)
                while len(w) < 4:
                    w.append((0, 0.0))
                out += struct.pack("<i", v.weight_count)
                for bi, val in w[:4]:
                    out += struct.pack("<if", bi, val)

    out += mds.trailer
    return bytes(out)


# --------------------------------------------------------------------------
# File / EQG helpers
# --------------------------------------------------------------------------

def load(path: str) -> Mds:
    with open(path, "rb") as f:
        return parse(f.read())


def save(mds: Mds, path: str, rebuild_names: bool = False):
    with open(path, "wb") as f:
        f.write(serialize(mds, rebuild_names=rebuild_names))


def load_from_eqg(eqg_path: str, member: str = None) -> Mds:
    """Extract and parse the .mds inside an .eqg PFS archive."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import s3d
    files = s3d.load(eqg_path)
    if member is None:
        cands = [k for k in files if k.lower().endswith(".mds")]
        if len(cands) != 1:
            raise ValueError("expected exactly one .mds, found %r" % cands)
        member = cands[0]
    return parse(files[member])


# --------------------------------------------------------------------------
# CLI: round-trip test / inspection
# --------------------------------------------------------------------------

def _main(argv):
    if len(argv) < 2:
        print("usage: mds.py <file.mds|file.eqg> [--obj out.obj]")
        return 1
    path = argv[1]
    if path.lower().endswith(".eqg"):
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import s3d
        files = s3d.load(path)
        name = [k for k in files if k.lower().endswith(".mds")][0]
        raw = files[name]
        print("from %s: %s (%d bytes)" % (path, name, len(raw)))
    else:
        raw = open(path, "rb").read()

    mds = parse(raw)
    print(mds.summary())

    out = serialize(mds)
    if out == raw:
        print("ROUND TRIP: byte-exact (%d bytes)" % len(out))
    else:
        print("ROUND TRIP: MISMATCH len %d vs %d" % (len(raw), len(out)))
        n = min(len(raw), len(out))
        for i in range(n):
            if raw[i] != out[i]:
                print("  first diff at offset %d: %02x vs %02x" % (i, raw[i], out[i]))
                break
        return 2

    # skeleton sanity
    parents = mds.bone_parents()
    roots = [i for i, p in enumerate(parents) if p == -1]
    print("skeleton: %d bones, roots=%s" % (len(mds.bones),
                                            [mds.bones[i].name for i in roots]))
    if mds.models and mds.bones:
        v = mds.models[0].vertices[0]
        print("vertex[0] weights: %s" %
              [(mds.bones[b].name, round(w, 4)) for b, w in v.active_weights()])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
