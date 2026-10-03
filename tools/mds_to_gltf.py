#!/usr/bin/env python3
"""
EQG .mds (skinned model) -> binary glTF (.glb).

Pure standard library: struct / zlib / json only. No Pillow, no numpy.

--------------------------------------------------------------------------
WHAT IT EMITS
--------------------------------------------------------------------------
* One glTF mesh per MDS model, one primitive per material used by that model.
* POSITION / NORMAL / TEXCOORD_0 / JOINTS_0 / WEIGHTS_0 (+ TANGENT when a
  normal map is attached).
* A skin holding every bone in the file as a joint node, with the full
  first-child/next-sibling hierarchy rebuilt as parent/children glTF nodes and
  local TRS taken straight from the bone's pivot / quaternion / scale.
* Inverse bind matrices computed as inverse(global bind matrix) per joint, so
  the bind pose reproduces the stored vertex positions exactly.
* PbrMetallicRoughness material with the diffuse .dds embedded as PNG in the
  GLB binary chunk. The e_TextureNormal0 slot is embedded as a glTF
  normalTexture as well (disable with --no-normal-map).

--------------------------------------------------------------------------
COORDINATE SYSTEM
--------------------------------------------------------------------------
EQ/MDS is Z-up, glTF/Godot is Y-up:

    godot_x =  eq_x
    godot_y =  eq_z
    godot_z = -eq_y

which is exactly a -90 degree rotation about X, i.e. the quaternion
(-sqrt(2)/2, 0, 0, sqrt(2)/2).

It is applied ONCE, in two consistent places:
  * every vertex position and normal is multiplied by it, and
  * the ROOT bone's local transform is pre-multiplied by it.
Child bones are untouched -- their locals are already expressed in the
(now rotated) parent space. Inverse bind matrices are then derived from the
rotated hierarchy, so mesh and skeleton cannot drift apart.
Pass --z-up to skip the rotation and keep raw EQ axes.

--------------------------------------------------------------------------
BONE QUATERNIONS ARE STORED CONJUGATED  (empirically determined)
--------------------------------------------------------------------------
The 4 floats in an MDS bone are (x, y, z, w) of the CONJUGATE of the rotation
you want, i.e. the local bind matrix is

    T(pivot) @ R(-x, -y, -z, w) @ S(scale)

Taking them at face value builds a skeleton that has nothing to do with the
mesh. Measured on cth.mds by comparing every bone's world bind position
against the weighted centroid of the vertices it drives (83 bones with
weights, model is ~6 units tall):

    quaternion as-is   mean error 2.406 u      <- skeleton is scrambled
    conjugated         mean error 0.200 u      <- joints sit inside the limbs

e.g. HEAD_HEAD lands at eq z = 4.80 against a head-vertex centroid of 4.85
when conjugated, versus z = 2.18 when not. --raw-quat disables the conjugate.

Because the inverse bind matrices are derived from whatever hierarchy we
build, the bind pose always renders the stored vertices exactly either way --
but only the conjugated skeleton is usable for posing or for future .ani work,
and only it looks right in Godot's skeleton view.

--------------------------------------------------------------------------
UV ORIENTATION  (empirically determined, do not "fix" this)
--------------------------------------------------------------------------
MDS texture coordinates are OpenGL-style (V=0 at the BOTTOM of the image),
while the .dds pixel rows are stored top-down and glTF expects V=0 at the TOP.
So the exporter writes TEXCOORD_0 = (u, 1 - v) and leaves the image rows in
stored order -- an extracted PNG therefore looks exactly like the shipped
.dds and can be edited and fed straight back into the EQ pipeline.

Proof (cth.eqg, 3135 faces): sampling the diffuse atlas at each face's UV
centroid, 26.2% of centroids land on the atlas's black (unused) background
without the flip versus 7.5% with it, and mean luminance rises 147 -> 168.
Use --no-flip-v only if a future model turns out to be authored the other way.

--------------------------------------------------------------------------
ANIMATIONS  (--animate)
--------------------------------------------------------------------------
The .ani files that sit beside the .mds in the same .eqg become glTF
animations, one per file, named after the animation slot (idle, walk, nrun,
...). See tools/ani.py for the binary format and the evidence behind these
two conventions:

  * an ANI keyframe is an ABSOLUTE parent-relative local TRS -- it REPLACES
    the bone's bind local transform rather than composing with it, so the
    keyframe values drop straight into glTF's node-local TRS channels;
  * ANI rotations use the SAME conjugated storage as MDS bone quaternions,
    so x, y, z are negated before writing. Measured on cth.eqg by posing the
    skeleton at frame 0 of idle and comparing joints against the weighted
    centroids of the vertices they drive: 0.96 u conjugated versus 2.62 u
    raw (bind pose itself scores 0.200 u).

The Z-up -> Y-up rotation is applied exactly where it is for the bind pose:
only the ROOT bone's channels are rewritten (translation through to_yup(),
rotation pre-multiplied by ZUP_TO_YUP_QUAT). Every other bone is already
expressed in its rotated parent's space.

Tracks whose bone name is not in the .mds are dropped (some cth animations
carry leftover "Bone09"-style tracks from the authoring rig). Bones with no
track keep their bind pose. A channel whose value never changes is written as
a single keyframe, and one that never changes AND equals the bind value is
omitted entirely -- that alone takes cth from 38730 raw keys down to a
fraction of the buffer.

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
    python3 tools/mds_to_gltf.py cth.eqg out.glb
    python3 tools/mds_to_gltf.py --animate cth.eqg out.glb
    python3 tools/mds_to_gltf.py cth_subdiv1.eqg out.glb
    python3 tools/mds_to_gltf.py model.mds textures/ out.glb

Options:
    --animate            embed every .ani found next to the .mds
    --ani-dir DIR        also read loose .ani files from here
    --no-collapse-static do not compress constant animation channels
    --texture-dir DIR    look here for replacement .png/.dds first
    --no-textures        do not embed images, reference by filename instead
    --no-normal-map      skip e_TextureNormal0 (also skips TANGENT)
    --flip-normal-green  invert the normal map's green channel (DirectX -> OpenGL)
    --z-up               keep EQ axes, do not rotate to Y-up
    --no-flip-v          write UVs unmodified
    --scale F            uniform scale applied to positions and bone pivots
    -q / --quiet
"""

import argparse
import json
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ani as animod  # noqa: E402
import mds as mdsmod  # noqa: E402
import s3d            # noqa: E402


# ==========================================================================
# small 4x4 / quaternion maths (row-major, row vectors are NOT used --
# points are column vectors, so  p' = M @ p)
# ==========================================================================

IDENTITY4 = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)


def mat_mul(a, b):
    out = [0.0] * 16
    for r in range(4):
        ar = r * 4
        for c in range(4):
            out[ar + c] = (a[ar + 0] * b[c] +
                           a[ar + 1] * b[4 + c] +
                           a[ar + 2] * b[8 + c] +
                           a[ar + 3] * b[12 + c])
    return tuple(out)


def mat_from_quat(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n > 0.0:
        x, y, z, w = x / n, y / n, z / n, w / n
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (1 - 2 * (yy + zz), 2 * (xy - wz),     2 * (xz + wy),     0.0,
            2 * (xy + wz),     1 - 2 * (xx + zz), 2 * (yz - wx),     0.0,
            2 * (xz - wy),     2 * (yz + wx),     1 - 2 * (xx + yy), 0.0,
            0.0,               0.0,               0.0,               1.0)


def mat_from_trs(t, q, s):
    m = list(mat_from_quat(q))
    for r in range(3):
        for c in range(3):
            m[r * 4 + c] *= s[c]
    m[3], m[7], m[11] = t[0], t[1], t[2]
    return tuple(m)


def mat_inverse(m):
    """General 4x4 inverse by Gauss-Jordan. These matrices are tiny and few."""
    a = [list(m[r * 4:r * 4 + 4]) + [1.0 if r == c else 0.0 for c in range(4)]
         for r in range(4)]
    for col in range(4):
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise ValueError("singular bone matrix (degenerate scale?)")
        a[col], a[piv] = a[piv], a[col]
        d = a[col][col]
        a[col] = [v / d for v in a[col]]
        for r in range(4):
            if r == col:
                continue
            f = a[r][col]
            if f:
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return tuple(v for r in range(4) for v in a[r][4:])


def mat_to_gltf(m):
    """Row-major -> glTF's column-major flat list."""
    return [m[0], m[4], m[8],  m[12],
            m[1], m[5], m[9],  m[13],
            m[2], m[6], m[10], m[14],
            m[3], m[7], m[11], m[15]]


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


# EQ Z-up -> glTF/Godot Y-up:  (x, y, z) -> (x, z, -y)
ZUP_TO_YUP_QUAT = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
ZUP_TO_YUP_MAT = mat_from_quat(ZUP_TO_YUP_QUAT)


def to_yup(v):
    return (v[0], v[2], -v[1])


# ==========================================================================
# DDS decoding -> raw RGBA8
# ==========================================================================

DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000


def _mask_shift(mask):
    if mask == 0:
        return 0, 0
    shift = 0
    m = mask
    while not (m & 1):
        m >>= 1
        shift += 1
    bits = 0
    while m & 1:
        m >>= 1
        bits += 1
    return shift, bits


def _expand(val, bits):
    if bits == 8:
        return val
    if bits == 0:
        return 255
    return (val * 255) // ((1 << bits) - 1)


def _bc_colors(c0, c1, opaque_four_colour):
    def rgb565(c):
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)

    a = rgb565(c0)
    b = rgb565(c1)
    if opaque_four_colour or c0 > c1:
        c2 = tuple((2 * a[i] + b[i]) // 3 for i in range(3)) + (255,)
        c3 = tuple((a[i] + 2 * b[i]) // 3 for i in range(3)) + (255,)
        return [a + (255,), b + (255,), c2, c3]
    c2 = tuple((a[i] + b[i]) // 2 for i in range(3)) + (255,)
    return [a + (255,), b + (255,), c2, (0, 0, 0, 0)]


def _decode_bc(data, w, h, mode):
    """mode: 1 = BC1/DXT1, 2 = BC2/DXT3, 3 = BC3/DXT5. Returns RGBA bytearray."""
    out = bytearray(w * h * 4)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    block = 8 if mode == 1 else 16
    p = 0
    for by in range(bh):
        for bx in range(bw):
            if p + block > len(data):
                return out
            alpha = None
            q = p
            if mode == 2:
                bits = int.from_bytes(data[q:q + 8], "little")
                alpha = [_expand((bits >> (4 * i)) & 0xF, 4) for i in range(16)]
                q += 8
            elif mode == 3:
                a0, a1 = data[q], data[q + 1]
                bits = int.from_bytes(data[q + 2:q + 8], "little")
                if a0 > a1:
                    tbl = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    tbl = ([a0, a1] +
                           [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] +
                           [0, 255])
                alpha = [tbl[(bits >> (3 * i)) & 0x7] for i in range(16)]
                q += 8
            c0, c1 = struct.unpack_from("<HH", data, q)
            idx = int.from_bytes(data[q + 4:q + 8], "little")
            cols = _bc_colors(c0, c1, mode != 1)
            for i in range(16):
                px, py = bx * 4 + (i & 3), by * 4 + (i >> 2)
                if px >= w or py >= h:
                    continue
                r, g, b, a = cols[(idx >> (2 * i)) & 0x3]
                if alpha is not None:
                    a = alpha[i]
                o = (py * w + px) * 4
                out[o] = r
                out[o + 1] = g
                out[o + 2] = b
                out[o + 3] = a
            p += block
    return out


def dds_to_rgba(data):
    """Decode a .dds blob to (width, height, RGBA8 bytes). Top mip only."""
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    pf_flags, fourcc = struct.unpack_from("<I4s", data, 80)
    bit_count, rm, gm, bm, am = struct.unpack_from("<5I", data, 88)
    offset = 128
    if fourcc == b"DX10":
        dxgi = struct.unpack_from("<I", data, 128)[0]
        offset = 148
        # 71/72 = BC1, 74/75 = BC2, 77/78 = BC3, 28/29 = R8G8B8A8
        if dxgi in (71, 72):
            return width, height, bytes(_decode_bc(data[offset:], width, height, 1))
        if dxgi in (74, 75):
            return width, height, bytes(_decode_bc(data[offset:], width, height, 2))
        if dxgi in (77, 78):
            return width, height, bytes(_decode_bc(data[offset:], width, height, 3))
        if dxgi in (28, 29):
            px = data[offset:offset + width * height * 4]
            return width, height, px
        raise ValueError("unsupported DXGI format %d" % dxgi)

    if pf_flags & DDPF_FOURCC:
        mode = {b"DXT1": 1, b"DXT2": 2, b"DXT3": 2,
                b"DXT4": 3, b"DXT5": 3}.get(fourcc)
        if mode is None:
            raise ValueError("unsupported DDS fourcc %r" % fourcc)
        return width, height, bytes(_decode_bc(data[offset:], width, height, mode))

    # Uncompressed. cth.eqg ships A8R8G8B8 (fourcc 0, 32bpp,
    # masks R=00ff0000 G=0000ff00 B=000000ff A=ff000000) -> bytes are B,G,R,A.
    bpp = bit_count // 8
    if bpp not in (1, 2, 3, 4):
        raise ValueError("unsupported DDS bit count %d" % bit_count)
    if pf_flags & DDPF_LUMINANCE and not (pf_flags & DDPF_RGB):
        gm = bm = rm
    rs, rb = _mask_shift(rm)
    gs, gb = _mask_shift(gm)
    bs, bb = _mask_shift(bm)
    as_, ab = _mask_shift(am)
    src = data[offset:]
    out = bytearray(width * height * 4)
    need = width * height * bpp
    if len(src) < need:
        raise ValueError("DDS truncated: %d bytes, need %d" % (len(src), need))

    if bpp == 4 and rm == 0x00FF0000 and gm == 0x0000FF00 and bm == 0x000000FF:
        # fast path for the common A8R8G8B8 / X8R8G8B8 layout
        has_alpha = am == 0xFF000000
        for i in range(width * height):
            o, s = i * 4, i * 4
            out[o] = src[s + 2]
            out[o + 1] = src[s + 1]
            out[o + 2] = src[s]
            out[o + 3] = src[s + 3] if has_alpha else 255
        return width, height, bytes(out)

    for i in range(width * height):
        v = int.from_bytes(src[i * bpp:(i + 1) * bpp], "little")
        o = i * 4
        out[o] = _expand((v >> rs) & ((1 << rb) - 1) if rb else 0, rb)
        out[o + 1] = _expand((v >> gs) & ((1 << gb) - 1) if gb else 0, gb)
        out[o + 2] = _expand((v >> bs) & ((1 << bb) - 1) if bb else 0, bb)
        out[o + 3] = _expand((v >> as_) & ((1 << ab) - 1), ab) if ab else 255
    return width, height, bytes(out)


# ==========================================================================
# PNG encode / decode (stdlib only)
# ==========================================================================

def png_encode(width, height, rgba):
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                       # filter type 0 (None)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
            chunk(b"IEND", b""))


def png_decode(data):
    """Minimal PNG reader for 8-bit RGB/RGBA/grey, no interlace."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    p, idat, w = 8, bytearray(), None
    depth = ctype = interlace = 0
    palette = trns = None
    while p < len(data):
        ln = struct.unpack_from(">I", data, p)[0]
        tag = data[p + 4:p + 8]
        body = data[p + 8:p + 8 + ln]
        p += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if depth != 8 or interlace != 0:
        raise ValueError("unsupported PNG (bit depth %d, interlace %d)"
                         % (depth, interlace))
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if nch is None:
        raise ValueError("unsupported PNG colour type %d" % ctype)
    raw = zlib.decompress(bytes(idat))
    stride = w * nch
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ft != 0:
            raise ValueError("bad PNG filter %d" % ft)
        for x in range(w):
            o, s = (y * w + x) * 4, x * nch
            if ctype == 6:
                out[o:o + 4] = line[s:s + 4]
            elif ctype == 2:
                out[o:o + 3] = line[s:s + 3]
                out[o + 3] = 255
            elif ctype == 0:
                g = line[s]
                out[o] = out[o + 1] = out[o + 2] = g
                out[o + 3] = 255
            elif ctype == 4:
                g = line[s]
                out[o] = out[o + 1] = out[o + 2] = g
                out[o + 3] = line[s + 1]
            else:  # palette
                i = line[s]
                out[o] = palette[i * 3]
                out[o + 1] = palette[i * 3 + 1]
                out[o + 2] = palette[i * 3 + 2]
                out[o + 3] = trns[i] if trns and i < len(trns) else 255
        prev = line
    return w, h, bytes(out)


# ==========================================================================
# texture resolution
# ==========================================================================

class TextureSource:
    """Finds a texture by MDS name across an EQG archive and/or a directory."""

    def __init__(self, archive=None, directory=None, verbose=True):
        self.archive = {k.lower(): v for k, v in (archive or {}).items()}
        self.directory = directory
        self.verbose = verbose
        self.disk = {}
        if directory and os.path.isdir(directory):
            for fn in os.listdir(directory):
                self.disk[fn.lower()] = os.path.join(directory, fn)
        self._cache = {}

    def _candidates(self, name):
        base = os.path.splitext(os.path.basename(name))[0].lower()
        # a hand-edited .png in the override dir beats the shipped .dds
        return [base + ".png", base + ".dds", base + ".tga", base + ".bmp"]

    def load(self, name):
        """-> (width, height, rgba bytes, origin string) or None."""
        if name in self._cache:
            return self._cache[name]
        result = None
        for cand in self._candidates(name):
            blob = origin = None
            if cand in self.disk:
                with open(self.disk[cand], "rb") as f:
                    blob = f.read()
                origin = self.disk[cand]
            elif cand in self.archive:
                blob = self.archive[cand]
                origin = "archive:" + cand
            if blob is None:
                continue
            try:
                if blob[:8] == b"\x89PNG\r\n\x1a\n":
                    w, h, px = png_decode(blob)
                elif blob[:4] == b"DDS ":
                    w, h, px = dds_to_rgba(blob)
                else:
                    continue
            except Exception as exc:                       # noqa: BLE001
                sys.stderr.write("  ! %s: %s\n" % (origin, exc))
                continue
            result = (w, h, px, origin)
            break
        self._cache[name] = result
        return result


# ==========================================================================
# glTF buffer assembly
# ==========================================================================

COMP_BYTE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GlbBuilder:
    def __init__(self):
        self.bin = bytearray()
        self.views = []
        self.accessors = []
        self._view_cache = {}       # (bytes, target) -> view index
        self._acc_cache = {}        # accessor identity -> accessor index

    def _align(self):
        while len(self.bin) % 4:
            self.bin.append(0)

    def add_view(self, data, target=None, dedupe=False):
        data = bytes(data)
        if dedupe:
            key = (data, target)
            hit = self._view_cache.get(key)
            if hit is not None:
                return hit
        self._align()
        off = len(self.bin)
        self.bin += data
        view = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.views.append(view)
        idx = len(self.views) - 1
        if dedupe:
            self._view_cache[(data, target)] = idx
        return idx

    def add_accessor(self, data, comp_type, gl_type, count,
                     target=None, minmax=None, normalized=False,
                     dedupe=False):
        """
        dedupe=True reuses an identical accessor. Animation data is extremely
        repetitive -- hundreds of bones hold a constant unit scale and share
        the same timestamp array -- so this is what keeps the 13 cth
        animations down to a sane buffer size.
        """
        if dedupe:
            key = (bytes(data), comp_type, gl_type, count, target,
                   repr(minmax), normalized)
            hit = self._acc_cache.get(key)
            if hit is not None:
                return hit
        view = self.add_view(data, target, dedupe=dedupe)
        acc = {"bufferView": view, "componentType": comp_type,
               "count": count, "type": gl_type}
        if normalized:
            acc["normalized"] = True
        if minmax:
            acc["min"], acc["max"] = minmax
        self.accessors.append(acc)
        idx = len(self.accessors) - 1
        if dedupe:
            self._acc_cache[key] = idx
        return idx


def pack_glb(gltf_json, binary):
    js = json.dumps(gltf_json, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bs = bytes(binary)
    bs += b"\x00" * ((4 - len(bs) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bs)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A) + js
    out += struct.pack("<II", len(bs), 0x004E4942) + bs
    return bytes(out)


# ==========================================================================
# tangents
# ==========================================================================

def build_tangents(positions, normals, uvs, indices):
    n = len(positions)
    tan = [[0.0, 0.0, 0.0] for _ in range(n)]
    bit = [[0.0, 0.0, 0.0] for _ in range(n)]
    for i in range(0, len(indices), 3):
        i0, i1, i2 = indices[i], indices[i + 1], indices[i + 2]
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        w0, w1, w2 = uvs[i0], uvs[i1], uvs[i2]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        du1, dv1 = w1[0] - w0[0], w1[1] - w0[1]
        du2, dv2 = w2[0] - w0[0], w2[1] - w0[1]
        det = du1 * dv2 - du2 * dv1
        if abs(det) < 1e-12:
            continue
        r = 1.0 / det
        t = ((e1[0] * dv2 - e2[0] * dv1) * r,
             (e1[1] * dv2 - e2[1] * dv1) * r,
             (e1[2] * dv2 - e2[2] * dv1) * r)
        b = ((e2[0] * du1 - e1[0] * du2) * r,
             (e2[1] * du1 - e1[1] * du2) * r,
             (e2[2] * du1 - e1[2] * du2) * r)
        for idx in (i0, i1, i2):
            for k in range(3):
                tan[idx][k] += t[k]
                bit[idx][k] += b[k]

    out = []
    for i in range(n):
        nx, ny, nz = normals[i]
        tx, ty, tz = tan[i]
        d = nx * tx + ny * ty + nz * tz
        tx, ty, tz = tx - nx * d, ty - ny * d, tz - nz * d
        ln = math.sqrt(tx * tx + ty * ty + tz * tz)
        if ln < 1e-9:
            # degenerate: any vector perpendicular to the normal will do
            tx, ty, tz = (0.0, 0.0, 1.0) if abs(nx) > 0.9 else (1.0, 0.0, 0.0)
            d = nx * tx + ny * ty + nz * tz
            tx, ty, tz = tx - nx * d, ty - ny * d, tz - nz * d
            ln = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
        tx, ty, tz = tx / ln, ty / ln, tz / ln
        cx, cy, cz = (ny * tz - nz * ty, nz * tx - nx * tz, nx * ty - ny * tx)
        bx, by, bz = bit[i]
        w = -1.0 if (cx * bx + cy * by + cz * bz) < 0.0 else 1.0
        out.append((tx, ty, tz, w))
    return out


# ==========================================================================
# animations
# ==========================================================================

# Path -> (glTF accessor type, component count, struct format)
_PATHS = (("translation", "VEC3", 3, "<3f"),
          ("rotation", "VEC4", 4, "<4f"),
          ("scale", "VEC3", 3, "<3f"))


def _const(values, eps):
    """True when every entry of `values` equals the first within eps."""
    first = values[0]
    for v in values[1:]:
        for a, b in zip(first, v):
            if abs(a - b) > eps:
                return False
    return True


def build_animations(m, anims, locals_, parents, builder, opts, log):
    """
    Turn {filename: ani.Ani} into a list of glTF animation objects.

    Bone i is glTF node i (bones are emitted first), so channel targets are
    just the MDS bone index. Returns (animations, stats dict).
    """
    yup = not opts.z_up
    scale = opts.scale
    eps = opts.anim_epsilon
    collapse = not opts.no_collapse_static

    out = []
    stats = {"keys": 0, "channels": 0, "dropped_tracks": 0,
             "skipped_channels": 0, "renamed_tracks": 0}

    for fn in sorted(anims, key=str.lower):
        a = anims[fn]
        channels, samplers = [], []
        n_keys = 0

        # NOT a {bone.name: index} lookup -- .ani track names lie. cth's
        # `stnd` clip names the secondary chest-arms exactly like the primary
        # arms, so a name lookup pointed 35 tracks at bones that already had
        # a channel and folded the model's arms over its head. See
        # ani.resolve_tracks.
        assigned, unknown = animod.resolve_tracks(a, m)
        stats["dropped_tracks"] += len(unknown)
        renamed = [(a.bones[ti].name, m.bones[bi].name)
                   for ti, bi in sorted(assigned.items())
                   if a.bones[ti].name != m.bones[bi].name]
        stats["renamed_tracks"] += len(renamed)

        for ti, bi in sorted(assigned.items()):
            track = a.bones[ti]
            if not track.frames:
                continue
            is_root = parents[bi] == -1
            bind_t, bind_q, bind_s = locals_[bi]

            times = [f.milliseconds / 1000.0 for f in track.frames]
            trans, rots, scales = [], [], []
            prev_q = None
            for f in track.frames:
                t = tuple(c * scale for c in f.translation)
                # ANI stores the conjugate of the rotation, exactly like the
                # MDS bind quaternion (see the module docstring).
                q = f.rotation
                if not opts.raw_quat:
                    q = (-q[0], -q[1], -q[2], q[3])
                if is_root and yup:
                    t = to_yup(t)
                    q = quat_mul(ZUP_TO_YUP_QUAT, q)
                q = _norm_quat(q)
                # Keep the track on one hemisphere. q and -q are the same
                # orientation, but glTF LINEAR rotation is slerp, so an
                # antipodal neighbour makes the joint spin the long way round
                # for one keyframe interval. cth ships 48 such pairs out of
                # 37503 -- rare, but each one is a visible pop.
                if prev_q is not None and sum(a * b for a, b in
                                              zip(prev_q, q)) < 0.0:
                    q = (-q[0], -q[1], -q[2], -q[3])
                prev_q = q
                trans.append(t)
                rots.append(q)
                scales.append(tuple(f.scale))

            for path, gtype, ncomp, fmt in _PATHS:
                vals = {"translation": trans, "rotation": rots,
                        "scale": scales}[path]
                bind = {"translation": bind_t, "rotation": bind_q,
                        "scale": bind_s}[path]
                ts = times
                if collapse and _const(vals, eps):
                    # constant channel: drop it when it already matches the
                    # node's rest transform, otherwise one keyframe says it all
                    if all(abs(x - y) <= eps for x, y in zip(vals[0], bind)):
                        stats["skipped_channels"] += 1
                        continue
                    if path == "rotation" and all(
                            abs(x + y) <= eps for x, y in zip(vals[0], bind)):
                        stats["skipped_channels"] += 1   # q and -q are equal
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
            log("  ! %s has no usable channels, skipped" % fn)
            continue

        # A node/path pair may carry at most one channel. Two would be
        # invalid glTF and viewers silently keep whichever came last -- which
        # is exactly how the stnd arm bug rendered.
        seen = set()
        for c in channels:
            key = (c["target"]["node"], c["target"]["path"])
            if key in seen:
                raise ValueError("%s: two channels drive node %d.%s"
                                 % (fn, key[0], key[1]))
            seen.add(key)

        name = a.anim_code or os.path.splitext(fn)[0]
        out.append({"name": name, "channels": channels, "samplers": samplers,
                    "extras": {"file": fn, "label": a.label,
                               "duration": round(a.duration, 4)}})
        stats["keys"] += n_keys
        stats["channels"] += len(channels)
        notes = []
        if renamed:
            notes.append("%d track(s) rebound by shape: %s"
                         % (len(renamed),
                            ", ".join("%s->%s" % r for r in renamed[:3])
                            + (", ..." if len(renamed) > 3 else "")))
        if unknown:
            notes.append("dropped %d unknown track(s)" % len(unknown))
        log("  anim %-6s %-22s %6.3f s  %4d channels  %6d keys%s"
            % (name, a.label, a.duration, len(channels), n_keys,
               ("  (" + "; ".join(notes) + ")") if notes else ""))
    return out, stats


def _norm_quat(q):
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)


# ==========================================================================
# bind-pose sanity check
# ==========================================================================

def bind_pose_error(m, globals_, yup, scale):
    """
    How far each joint's world bind position sits from the weighted centroid of
    the vertices it drives. A correct bind pose puts joints inside their limbs,
    so on cth.mds (~6 units tall) this comes out at 0.200 u; a wrong quaternion
    convention gives 2.4 u. Returns (mean, worst, worst_bone_name, n) or None.
    """
    acc = {}
    for model in m.models:
        if not model.has_weights:
            continue
        for v in model.vertices:
            p = tuple(c * scale for c in v.position)
            if yup:
                p = to_yup(p)
            for bi, w in v.active_weights():
                if w <= 0.0 or bi < 0 or bi >= len(globals_):
                    continue
                a = acc.setdefault(bi, [0.0, 0.0, 0.0, 0.0])
                a[0] += p[0] * w
                a[1] += p[1] * w
                a[2] += p[2] * w
                a[3] += w
    if not acc:
        return None
    total, worst, worst_name = 0.0, 0.0, ""
    for bi, a in acc.items():
        c = (a[0] / a[3], a[1] / a[3], a[2] / a[3])
        g = globals_[bi]
        b = (g[3], g[7], g[11])
        d = math.sqrt(sum((c[k] - b[k]) ** 2 for k in range(3)))
        total += d
        if d > worst:
            worst, worst_name = d, m.bones[bi].name
    return total / len(acc), worst, worst_name, len(acc)


# ==========================================================================
# main conversion
# ==========================================================================

def convert(m, textures, opts, anims=None):
    """m: mds.Mds, textures: TextureSource -> (gltf dict, binary bytes).

    anims: optional {filename: ani.Ani} to embed as glTF animations.
    """
    log = (lambda *a: None) if opts.quiet else (
        lambda *a: print(*a))

    bones = m.bones
    parents = m.bone_parents()
    scale = opts.scale
    yup = not opts.z_up

    # ---------------------------------------------------------------- bones
    locals_ = []
    for i, b in enumerate(bones):
        t = tuple(v * scale for v in b.pivot)
        q = tuple(b.quaternion)
        if not opts.raw_quat:
            # MDS stores the CONJUGATE of the bind rotation -- see docstring.
            q = (-q[0], -q[1], -q[2], q[3])
        s = tuple(b.scale)
        if parents[i] == -1 and yup:
            t = to_yup(t)
            q = quat_mul(ZUP_TO_YUP_QUAT, q)
        locals_.append((t, q, s))

    globals_ = [None] * len(bones)
    order = [i for i, p in enumerate(parents) if p == -1]
    seen = list(order)
    while order:
        i = order.pop(0)
        lm = mat_from_trs(*locals_[i])
        p = parents[i]
        globals_[i] = lm if p == -1 else mat_mul(globals_[p], lm)
        for c, pc in enumerate(parents):
            if pc == i and c not in seen:
                seen.append(c)
                order.append(c)
    for i, g in enumerate(globals_):
        if g is None:                       # orphan / cycle guard
            globals_[i] = mat_from_trs(*locals_[i])
            parents[i] = -1

    ibms = [mat_inverse(g) for g in globals_]

    stat = bind_pose_error(m, globals_, yup, scale)
    if stat:
        mean, worst, worst_name, n = stat
        log("  bind-pose check: %d weighted bones, mean joint-vs-vertex-centroid "
            "offset %.3f u (worst %.3f on %s)" % (n, mean, worst, worst_name))
        # Rigid props with one or two weighted bones legitimately park the root
        # away from the mesh, so only judge properly articulated skeletons.
        if mean > 1.0 and n >= 4:
            log("  ! bind pose looks wrong -- skeleton does not line up with the "
                "mesh (try toggling --raw-quat)")

    # ------------------------------------------------------------ materials
    gltf = {
        "asset": {"version": "2.0",
                  "generator": "eq mds_to_gltf.py (EQ HD upscale project)"},
        "scene": 0,
    }
    builder = GlbBuilder()
    images, samplers, gtextures, gmaterials = [], [], [], []
    mat_index = {}

    def add_image(name, want_normal_green_flip=False):
        got = textures.load(name)
        base = os.path.splitext(os.path.basename(name))[0]
        if got is None:
            log("  ! texture not found: %s (referenced by name)" % name)
            images.append({"uri": base + ".png", "name": base})
            return len(images) - 1

        w, h, px, origin = got
        if want_normal_green_flip:
            px = bytearray(px)
            for i in range(1, len(px), 4):
                px[i] = 255 - px[i]
            px = bytes(px)
        png = png_encode(w, h, px)

        if opts.dump_textures:
            os.makedirs(opts.dump_textures, exist_ok=True)
            dump = os.path.join(opts.dump_textures, base + ".png")
            with open(dump, "wb") as fh:
                fh.write(png)
            log("  dumped  %-21s %dx%d  -> %s" % (base, w, h, dump))

        if opts.no_textures:
            # side-car reference; Godot/Blender resolve it next to the .glb
            images.append({"uri": base + ".png", "name": base})
            return len(images) - 1

        view = builder.add_view(png)
        images.append({"bufferView": view, "mimeType": "image/png", "name": base})
        log("  texture %-22s %dx%d  %d KB png  <- %s"
            % (base, w, h, len(png) // 1024, origin))
        return len(images) - 1

    if not samplers:
        samplers.append({"magFilter": 9729, "minFilter": 9987,
                         "wrapS": 10497, "wrapT": 10497})

    has_normal_map = False
    for mat in m.materials:
        diffuse = mat.texture("e_TextureDiffuse0")
        normal = mat.texture("e_TextureNormal0") if not opts.no_normal_map else None
        g = {"name": mat.name or ("material_%d" % mat.id),
             "doubleSided": False,
             "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                      "roughnessFactor": 0.85}}
        if diffuse:
            img = add_image(diffuse)
            gtextures.append({"sampler": 0, "source": img})
            g["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": len(gtextures) - 1}
        if normal:
            img = add_image(normal, opts.flip_normal_green)
            gtextures.append({"sampler": 0, "source": img})
            g["normalTexture"] = {"index": len(gtextures) - 1}
            has_normal_map = True
        mat_index[mat.id] = len(gmaterials)
        gmaterials.append(g)

    # ----------------------------------------------------------- geometry
    nodes = []
    for i, b in enumerate(bones):
        t, q, s = locals_[i]
        node = {"name": b.name or ("bone_%d" % i)}
        if any(abs(v) > 1e-9 for v in t):
            node["translation"] = [float(v) for v in t]
        if abs(q[0]) + abs(q[1]) + abs(q[2]) > 1e-9 or abs(q[3] - 1.0) > 1e-9:
            node["rotation"] = [float(v) for v in q]
        if any(abs(v - 1.0) > 1e-9 for v in s):
            node["scale"] = [float(v) for v in s]
        nodes.append(node)
    for i, p in enumerate(parents):
        if p >= 0:
            nodes[p].setdefault("children", []).append(i)
    root_bones = [i for i, p in enumerate(parents) if p == -1]

    skin_index = None
    if bones:
        ibm_data = b"".join(struct.pack("<16f", *mat_to_gltf(x)) for x in ibms)
        acc = builder.add_accessor(ibm_data, 5126, "MAT4", len(ibms))
        skin = {"inverseBindMatrices": acc, "joints": list(range(len(bones)))}
        if len(root_bones) == 1:
            skin["skeleton"] = root_bones[0]
        gltf["skins"] = [skin]
        skin_index = 0

    meshes = []
    mesh_nodes = []
    total_v = total_f = 0

    for mi, model in enumerate(m.models):
        if not model.vertices or not model.faces:
            log("  skipping empty model %r" % model.name)
            continue

        positions, normals, uvs = [], [], []
        for v in model.vertices:
            p = tuple(c * scale for c in v.position)
            n = tuple(v.normal)
            if yup:
                p, n = to_yup(p), to_yup(n)
            ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
            if ln > 1e-9:
                n = (n[0] / ln, n[1] / ln, n[2] / ln)
            else:
                n = (0.0, 1.0, 0.0)
            u, vv = v.uv
            uvs.append((u, (1.0 - vv) if opts.flip_v else vv))
            positions.append(p)
            normals.append(n)

        # faces grouped by material so each primitive gets one glTF material
        groups = {}
        for f in model.faces:
            groups.setdefault(f.material_id, []).extend(f.indices)

        pos_data = b"".join(struct.pack("<3f", *p) for p in positions)
        nrm_data = b"".join(struct.pack("<3f", *n) for n in normals)
        uv_data = b"".join(struct.pack("<2f", *t) for t in uvs)
        pmin = [min(p[i] for p in positions) for i in range(3)]
        pmax = [max(p[i] for p in positions) for i in range(3)]

        attrs = {
            "POSITION": builder.add_accessor(pos_data, 5126, "VEC3",
                                             len(positions), 34962,
                                             (pmin, pmax)),
            "NORMAL": builder.add_accessor(nrm_data, 5126, "VEC3",
                                           len(normals), 34962),
            "TEXCOORD_0": builder.add_accessor(uv_data, 5126, "VEC2",
                                               len(uvs), 34962),
        }

        if has_normal_map:
            flat = [i for g in groups.values() for i in g]
            tans = build_tangents(positions, normals, uvs, flat)
            tan_data = b"".join(struct.pack("<4f", *t) for t in tans)
            attrs["TANGENT"] = builder.add_accessor(tan_data, 5126, "VEC4",
                                                    len(tans), 34962)

        skinned = bool(bones) and model.has_weights
        if skinned:
            nb = len(bones)
            j_data, w_data = bytearray(), bytearray()
            clamped = 0
            for v in model.vertices:
                pairs = [(b, wt) for b, wt in v.active_weights() if wt > 0.0]
                pairs.sort(key=lambda x: -x[1])
                pairs = pairs[:4]
                fixed = []
                for bi, wt in pairs:
                    if bi < 0 or bi >= nb:
                        clamped += 1
                        bi = 0
                    fixed.append((bi, wt))
                if not fixed:
                    fixed = [(0, 1.0)]
                tot = sum(wt for _, wt in fixed)
                if tot > 0:
                    fixed = [(bi, wt / tot) for bi, wt in fixed]
                while len(fixed) < 4:
                    fixed.append((0, 0.0))
                j_data += struct.pack("<4H", *[bi for bi, _ in fixed])
                w_data += struct.pack("<4f", *[wt for _, wt in fixed])
            if clamped:
                log("  ! %d weights referenced out-of-range bones" % clamped)
            attrs["JOINTS_0"] = builder.add_accessor(bytes(j_data), 5123,
                                                     "VEC4",
                                                     len(model.vertices), 34962)
            attrs["WEIGHTS_0"] = builder.add_accessor(bytes(w_data), 5126,
                                                      "VEC4",
                                                      len(model.vertices), 34962)

        prims = []
        big = len(positions) > 65535
        for mat_id, idx in sorted(groups.items(), key=lambda kv: kv[0]):
            if big:
                idata = b"".join(struct.pack("<I", i) for i in idx)
                ctype = 5125
            else:
                idata = b"".join(struct.pack("<H", i) for i in idx)
                ctype = 5123
            acc = builder.add_accessor(idata, ctype, "SCALAR", len(idx), 34963)
            prim = {"attributes": attrs, "indices": acc, "mode": 4}
            if mat_id in mat_index:
                prim["material"] = mat_index[mat_id]
            prims.append(prim)

        meshes.append({"name": model.name or ("model_%d" % mi),
                       "primitives": prims})
        node = {"name": model.name or ("model_%d" % mi),
                "mesh": len(meshes) - 1}
        if skinned and skin_index is not None:
            node["skin"] = skin_index
        nodes.append(node)
        mesh_nodes.append(len(nodes) - 1)
        total_v += len(positions)
        total_f += len(model.faces)
        log("  model %-10s %5d verts  %5d faces  %d primitive(s)  skinned=%s"
            % (model.name, len(positions), len(model.faces), len(prims), skinned))

    # --------------------------------------------------------------- anims
    if anims:
        if not bones:
            log("  ! %d animation(s) ignored: the model has no skeleton"
                % len(anims))
        else:
            gl_anims, astat = build_animations(m, anims, locals_, parents,
                                               builder, opts, log)
            if gl_anims:
                gltf["animations"] = gl_anims
                log("  animations: %d, %d channels, %d keys"
                    "  (%d constant channels omitted, %d unmatched tracks "
                    "dropped)"
                    % (len(gl_anims), astat["channels"], astat["keys"],
                       astat["skipped_channels"], astat["dropped_tracks"]))

    gltf["nodes"] = nodes
    gltf["meshes"] = meshes
    gltf["scenes"] = [{"nodes": root_bones + mesh_nodes}]
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
        % (len(bones), total_v, total_f, len(builder.bin) // 1024))
    return gltf, builder.bin


# ==========================================================================
# validation
# ==========================================================================

def validate_glb(path, quiet=False):
    """Re-read the written file and check its structure. Raises on failure."""
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("bad GLB magic %08x" % magic)
    if version != 2:
        raise ValueError("bad GLB version %d" % version)
    if length != len(data):
        raise ValueError("GLB header length %d != file size %d"
                         % (length, len(data)))
    p, chunks = 12, {}
    while p < len(data):
        clen, ctype = struct.unpack_from("<II", data, p)
        chunks[ctype] = data[p + 8:p + 8 + clen]
        p += 8 + clen
    if 0x4E4F534A not in chunks:
        raise ValueError("missing JSON chunk")
    if 0x004E4942 not in chunks:
        raise ValueError("missing BIN chunk")
    g = json.loads(chunks[0x4E4F534A].decode("utf-8"))
    binary = chunks[0x004E4942]
    declared = g["buffers"][0]["byteLength"]
    if declared != len(binary):
        raise ValueError("buffer byteLength %d != BIN chunk %d"
                         % (declared, len(binary)))
    for i, v in enumerate(g["bufferViews"]):
        end = v["byteOffset"] + v["byteLength"]
        if end > len(binary):
            raise ValueError("bufferView %d overruns buffer (%d > %d)"
                             % (i, end, len(binary)))
    for i, a in enumerate(g["accessors"]):
        need = COMP_BYTE[a["componentType"]] * TYPE_COUNT[a["type"]] * a["count"]
        have = g["bufferViews"][a["bufferView"]]["byteLength"]
        if need > have:
            raise ValueError("accessor %d needs %d bytes, view has %d"
                             % (i, need, have))
        if g["bufferViews"][a["bufferView"]]["byteOffset"] % 4:
            raise ValueError("accessor %d bufferView is not 4-byte aligned" % i)
    n_nodes = len(g["nodes"])
    for i, n in enumerate(g["nodes"]):
        for c in n.get("children", []):
            if c >= n_nodes:
                raise ValueError("node %d child %d out of range" % (i, c))
    for sk in g.get("skins", []):
        n_joints = len(sk["joints"])
        ibm = g["accessors"][sk["inverseBindMatrices"]]
        if ibm["count"] != n_joints:
            raise ValueError("skin has %d joints but %d inverse bind matrices"
                             % (n_joints, ibm["count"]))
        for mesh in g["meshes"]:
            for prim in mesh["primitives"]:
                ja = prim["attributes"].get("JOINTS_0")
                if ja is None:
                    continue
                view = g["bufferViews"][g["accessors"][ja]["bufferView"]]
                blob = binary[view["byteOffset"]:
                              view["byteOffset"] + view["byteLength"]]
                mx = max(struct.unpack("<%dH" % (len(blob) // 2), blob))
                if mx >= n_joints:
                    raise ValueError("joint index %d >= joint count %d"
                                     % (mx, n_joints))

    # ------------------------------------------------------------ animations
    path_type = {"translation": "VEC3", "scale": "VEC3",
                 "rotation": "VEC4", "weights": "SCALAR"}
    anim_keys = anim_channels = 0
    longest = 0.0
    for ai, anim in enumerate(g.get("animations", [])):
        if not anim.get("channels"):
            raise ValueError("animation %d has no channels" % ai)
        if not anim.get("samplers"):
            raise ValueError("animation %d has no samplers" % ai)
        for si, s in enumerate(anim["samplers"]):
            if s["input"] >= len(g["accessors"]) or \
                    s["output"] >= len(g["accessors"]):
                raise ValueError("animation %d sampler %d accessor out of range"
                                 % (ai, si))
            inp = g["accessors"][s["input"]]
            outp = g["accessors"][s["output"]]
            if inp["type"] != "SCALAR" or inp["componentType"] != 5126:
                raise ValueError("animation %d sampler %d input must be float "
                                 "SCALAR, got %s/%d"
                                 % (ai, si, inp["type"], inp["componentType"]))
            if "min" not in inp or "max" not in inp:
                raise ValueError("animation %d sampler %d input accessor is "
                                 "missing min/max (required by the spec)"
                                 % (ai, si))
            if inp["count"] < 1:
                raise ValueError("animation %d sampler %d has no keyframes"
                                 % (ai, si))
            expect = inp["count"]
            if s.get("interpolation") == "CUBICSPLINE":
                expect *= 3
            if outp["count"] != expect:
                raise ValueError("animation %d sampler %d: %d timestamps but "
                                 "%d output values"
                                 % (ai, si, inp["count"], outp["count"]))
            if outp["componentType"] != 5126:
                raise ValueError("animation %d sampler %d output must be float"
                                 % (ai, si))
            longest = max(longest, inp["max"][0])
            anim_keys += inp["count"]
        # glTF forbids two channels on the same node/path; viewers just keep
        # the last one, so the model animates from data it should ignore.
        targets = set()
        for ci, c in enumerate(anim["channels"]):
            if c["sampler"] >= len(anim["samplers"]):
                raise ValueError("animation %d channel %d sampler %d out of "
                                 "range" % (ai, ci, c["sampler"]))
            tgt = c["target"]
            key = (tgt.get("node"), tgt.get("path"))
            if key in targets:
                raise ValueError("animation %d (%s) has two channels on "
                                 "node %r path %r"
                                 % (ai, anim.get("name", "?"), key[0], key[1]))
            targets.add(key)
            node = tgt.get("node")
            if node is None or node >= n_nodes:
                raise ValueError("animation %d channel %d targets node %r "
                                 "(only %d nodes)" % (ai, ci, node, n_nodes))
            want = path_type.get(tgt["path"])
            if want is None:
                raise ValueError("animation %d channel %d bad path %r"
                                 % (ai, ci, tgt["path"]))
            got = g["accessors"][anim["samplers"][c["sampler"]]["output"]]
            if got["type"] != want:
                raise ValueError("animation %d channel %d path %s wants %s, "
                                 "sampler output is %s"
                                 % (ai, ci, tgt["path"], want, got["type"]))
            anim_channels += 1

    if not quiet:
        print("  VALID: %d bytes, JSON %d B, BIN %d B, %d nodes, %d meshes, "
              "%d accessors, %d images"
              % (len(data), len(chunks[0x4E4F534A]), len(binary), n_nodes,
                 len(g["meshes"]), len(g["accessors"]), len(g.get("images", []))))
        if g.get("animations"):
            print("  VALID: %d animations, %d channels, %d keyframes, "
                  "longest %.3f s  [%s]"
                  % (len(g["animations"]), anim_channels, anim_keys, longest,
                     ", ".join(a.get("name", "?") for a in g["animations"])))
    return g


# ==========================================================================
# CLI
# ==========================================================================

def main(argv):
    ap = argparse.ArgumentParser(
        description="Convert an EQG .mds skinned model to binary glTF (.glb)")
    ap.add_argument("args", nargs="+", metavar="ARG",
                    help="<in.eqg> <out.glb>   or   <in.mds> <texdir> <out.glb>")
    ap.add_argument("--texture-dir", default=None,
                    help="directory searched first for replacement textures")
    ap.add_argument("--no-textures", action="store_true",
                    help="reference images by filename instead of embedding")
    ap.add_argument("--dump-textures", metavar="DIR", default=None,
                    help="also write every referenced texture out as a PNG "
                         "here, for editing and feeding back via --texture-dir")
    ap.add_argument("--animate", action="store_true",
                    help="embed every .ani found alongside the .mds as a glTF "
                         "animation")
    ap.add_argument("--ani-dir", metavar="DIR", default=None,
                    help="also load loose .ani files from this directory "
                         "(implies --animate)")
    ap.add_argument("--no-collapse-static", action="store_true",
                    help="keep every keyframe even on channels that never "
                         "change (much larger file, useful for debugging)")
    ap.add_argument("--anim-epsilon", type=float, default=1e-6,
                    help="tolerance for calling an animation channel constant")
    ap.add_argument("--no-normal-map", action="store_true")
    ap.add_argument("--flip-normal-green", action="store_true",
                    help="invert normal-map green (DirectX -> OpenGL)")
    ap.add_argument("--raw-quat", action="store_true",
                    help="use bone quaternions verbatim instead of conjugating "
                         "them (produces a skeleton that does not match the "
                         "mesh -- see the module docstring)")
    ap.add_argument("--z-up", action="store_true",
                    help="keep raw EQ axes instead of rotating to Y-up")
    ap.add_argument("--no-flip-v", dest="flip_v", action="store_false",
                    default=True, help="write UVs unmodified")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("-q", "--quiet", action="store_true")
    opts = ap.parse_args(argv[1:])

    a = opts.args
    tex_dir = opts.texture_dir
    if len(a) == 2:
        src, out = a
    elif len(a) == 3:
        src, tex_dir2, out = a
        tex_dir = tex_dir or tex_dir2
    else:
        ap.error("expected <in> <out.glb> or <in.mds> <texdir> <out.glb>")
    if not out.lower().endswith((".glb", ".gltf")):
        ap.error("output must end in .glb")

    log = (lambda *x: None) if opts.quiet else (lambda *x: print(*x))

    archive = None
    if src.lower().endswith((".eqg", ".s3d", ".pfs", ".pak")):
        files = s3d.load(src)
        names = [k for k in files if k.lower().endswith(".mds")]
        if not names:
            sys.stderr.write("no .mds inside %s\n" % src)
            return 1
        if len(names) > 1:
            log("note: %d .mds in archive, using %s" % (len(names), names[0]))
        raw = files[names[0]]
        archive = files
        log("%s :: %s (%d bytes)" % (os.path.basename(src), names[0], len(raw)))
    else:
        with open(src, "rb") as f:
            raw = f.read()
        log("%s (%d bytes)" % (src, len(raw)))

    m = mdsmod.parse(raw)
    log("  MDS v%d: %d materials, %d bones, %d models"
        % (m.version, len(m.materials), len(m.bones), len(m.models)))

    anims = None
    if opts.animate or opts.ani_dir:
        anims = {}
        if archive:
            anims.update(animod.load_all_from_files(archive))
        if opts.ani_dir:
            for fn in sorted(os.listdir(opts.ani_dir)):
                if fn.lower().endswith(".ani"):
                    anims[fn] = animod.load(os.path.join(opts.ani_dir, fn))
        if not anims:
            sys.stderr.write("warning: --animate but no .ani files found "
                             "(archive=%s, ani-dir=%s)\n"
                             % (bool(archive), opts.ani_dir))
        else:
            log("  %d .ani file(s): %s"
                % (len(anims), ", ".join(sorted(a.anim_code or k
                                                for k, a in anims.items()))))
            for k, a in sorted(anims.items()):
                missing, _ = animod.check_bones(a, m)
                if missing:
                    log("  ! %s: %d track(s) name bones the .mds does not "
                        "have (%s) -- they will be dropped"
                        % (k, len(missing), ", ".join(missing[:5])))

    textures = TextureSource(archive, tex_dir, verbose=not opts.quiet)
    gltf, binary = convert(m, textures, opts, anims=anims)

    d = os.path.dirname(os.path.abspath(out))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "wb") as f:
        f.write(pack_glb(gltf, binary))
    log("wrote %s" % out)
    validate_glb(out, quiet=opts.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
