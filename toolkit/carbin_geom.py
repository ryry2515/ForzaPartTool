"""carbin_geom.py - geometry-level editing of Forza Horizon 1 `.carbin` models (builds on fh1_carbin.py).

Verified encodings (round-trip checked on the game's own cars, see selftest.py):
  position  : local = pos - section.offset; v = local / |bmax|; xyz = v/|v| (SNORM16), w = |v|  ->  world = xyz * w * |bmax| + offset ; bmin = -bmax
  uv0, uv1  : UNORM16 pairs; final = raw * scale + offset, with (off_u, scale_u, off_v, scale_v) per sub in sub.uvxf[0:4] (uv0) and [4:8] (uv1)
              uv0 addresses the car's baked atlas (scale 0 = one swatch texel); uv1 is the detail / tangent-space mapping
  frame     : SNORM16 quaternion; columns of the rotation matrix are normal, tangent, bitangent
  tail      : 4 bytes of baked lighting (AO + SH), fitted from the donor's own vertices (SHModel)
  indices   : triangle strips (index_type 6, restart 0xFFFF for 16-bit, 0x00FFFFFF for 32-bit) or lists (4)
  axes      : x left/right (negative = left), y up, mesh z NEGATIVE = front (light records use +z = front)
A section is a named part. Its sub-meshes (material name + LOD) index into one vertex pool; main carbins use the `lod` pool (LOD 1 and up),
`_lod0` and `_cockpit` carbins use the `lod0` pool.
Header draw lists decide which sub-meshes are drawn at each LOD; every operation that changes a section's sub-meshes keeps them consistent.

Everything here is untested in the game except through the round trips named above; test every edited car in the game.
"""
from __future__ import annotations

import copy
import random
import struct
from dataclasses import dataclass

import numpy as np

import fh1_carbin as fc

RESTART32 = 0x00FFFFFF
EXHAUST_ENTRY = 0x8C
TRANSPARENT_HINT = ("glass", "window", "light", "lens", "indicator", "turn")


# ----------------------------------------------------------------------------------------------------------------- decoding

def bscale(sec: fc.Section) -> float:
    return float(np.linalg.norm(sec.bmax))


def active_pool(sec: fc.Section, prefer: str | None = None) -> fc.Pool:
    """The vertex pool the section's sub-meshes index into."""
    if prefer == "lod0" and sec.lod0.count:
        return sec.lod0
    if prefer == "lod" and sec.lod.count:
        return sec.lod
    return sec.lod if sec.lod.count else sec.lod0


def pool_name_of(c: fc.Carbin) -> str:
    """'lod' for a main carbin (LOD 1 and up), 'lod0' for `_lod0` / `_cockpit` style files."""
    a = sum(s.lod.count for s in c.sections)
    b = sum(s.lod0.count for s in c.sections)
    return "lod" if a >= b else "lod0"


@dataclass
class Decoded:
    pos: np.ndarray       # (N,3) world
    nrm: np.ndarray       # (N,3)
    tan: np.ndarray       # (N,3)
    bit: np.ndarray       # (N,3)
    uv0: np.ndarray       # (N,2) raw 0..1
    uv1: np.ndarray       # (N,2) raw 0..1
    sh: np.ndarray        # (N,4) uint8


def rot_columns(q: np.ndarray):
    """SNORM quaternion (x,y,z,w) -> normal, tangent, bitangent (columns of the rotation matrix)."""
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
    x, y, z, w = q.T
    n = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)], 1)
    t = np.stack([2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)], 1)
    b = np.stack([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)], 1)
    return n, t, b


def decode_pool(sec: fc.Section, pool: fc.Pool) -> Decoded:
    if not pool.count:
        z3 = np.zeros((0, 3))
        return Decoded(z3, z3, z3, z3, np.zeros((0, 2)), np.zeros((0, 2)), np.zeros((0, 4), np.uint8))
    if pool.stride != 28:
        raise ValueError(f"vertex stride {pool.stride} is not FH1's 28 bytes")
    v = pool.vertices()
    raw = np.ascontiguousarray(v[:, :8]).view(">i2").reshape(-1, 4).astype(np.float64) / 32767.0
    pos = raw[:, :3] * raw[:, 3:4] * bscale(sec) + np.asarray(sec.offset, np.float64)
    uv0 = np.ascontiguousarray(v[:, 8:12]).view(">u2").reshape(-1, 2).astype(np.float64) / 65535.0
    uv1 = np.ascontiguousarray(v[:, 12:16]).view(">u2").reshape(-1, 2).astype(np.float64) / 65535.0
    q = np.ascontiguousarray(v[:, 16:24]).view(">i2").reshape(-1, 4).astype(np.float64) / 32767.0
    n, t, b = rot_columns(q)
    return Decoded(pos, n, t, b, uv0, uv1, np.ascontiguousarray(v[:, 24:28]))


def sub_triangles(sub: fc.Sub, vertex_count: int) -> np.ndarray:
    idx = np.asarray(sub.indices, np.int64)
    restart = 0xFFFF if sub.index_size == 2 else RESTART32
    if sub.index_type == 6:
        tris = fc.strip_to_list(idx, restart)
    else:
        tris = idx[: len(idx) // 3 * 3].reshape(-1, 3)
    tris = np.asarray(tris, np.int64).reshape(-1, 3)
    return tris[(tris < vertex_count).all(1)]


def final_uv(raw: np.ndarray, xf4) -> np.ndarray:
    ou, su, ov, sv = xf4
    return np.column_stack([raw[:, 0] * su + ou, raw[:, 1] * sv + ov])


def is_swatch(sub: fc.Sub) -> bool:
    return abs(sub.uvxf[1]) < 0.01 and abs(sub.uvxf[3]) < 0.01


# ----------------------------------------------------------------------------------------------------------------- encoding

def frame_quaternions(nrm: np.ndarray, tan: np.ndarray) -> np.ndarray:
    """Rows [N, T, B=N x T] -> quaternion (x,y,z,w) such that rot_columns(q)[0] == N."""
    n = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    t = tan - n * np.sum(n * tan, 1, keepdims=True)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    b = np.cross(n, t)
    R = np.stack([n, t, b], 2)                           # columns n, t, b
    m00, m01, m02 = R[:, 0, 0], R[:, 0, 1], R[:, 0, 2]
    m10, m11, m12 = R[:, 1, 0], R[:, 1, 1], R[:, 1, 2]
    m20, m21, m22 = R[:, 2, 0], R[:, 2, 1], R[:, 2, 2]
    tr = m00 + m11 + m22
    q = np.zeros((len(n), 4))
    c0 = tr > 0
    s = np.sqrt(np.maximum(tr[c0] + 1.0, 1e-12)) * 2
    q[c0] = np.stack([(m21 - m12)[c0] / s, (m02 - m20)[c0] / s, (m10 - m01)[c0] / s, 0.25 * s], 1)
    c1 = ~c0 & (m00 > m11) & (m00 > m22)
    s = np.sqrt(np.maximum(1.0 + m00[c1] - m11[c1] - m22[c1], 1e-12)) * 2
    q[c1] = np.stack([0.25 * s, (m01 + m10)[c1] / s, (m02 + m20)[c1] / s, (m21 - m12)[c1] / s], 1)
    c2 = ~c0 & ~c1 & (m11 > m22)
    s = np.sqrt(np.maximum(1.0 + m11[c2] - m00[c2] - m22[c2], 1e-12)) * 2
    q[c2] = np.stack([(m01 + m10)[c2] / s, 0.25 * s, (m12 + m21)[c2] / s, (m02 - m20)[c2] / s], 1)
    c3 = ~c0 & ~c1 & ~c2
    s = np.sqrt(np.maximum(1.0 + m22[c3] - m00[c3] - m11[c3], 1e-12)) * 2
    q[c3] = np.stack([(m02 + m20)[c3] / s, (m12 + m21)[c3] / s, 0.25 * s, (m10 - m01)[c3] / s], 1)
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def uv_tangents(pos, nrm, uv, tris) -> np.ndarray:
    with np.errstate(divide='ignore', invalid='ignore'):
        return _uv_tangents(pos, nrm, uv, tris)


def _uv_tangents(pos, nrm, uv, tris) -> np.ndarray:
    v0, v1, v2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    w0, w1, w2 = uv[tris[:, 0]], uv[tris[:, 1]], uv[tris[:, 2]]
    e1, e2, d1, d2 = v1 - v0, v2 - v0, w1 - w0, w2 - w0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1.0, 1.0 / r)[:, None]
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    tan = np.zeros_like(pos, dtype=np.float64)
    for k in range(3):
        np.add.at(tan, tris[:, k], sdir)
    tan -= nrm * np.sum(nrm * tan, 1, keepdims=True)
    bad = np.linalg.norm(tan, axis=1) < 1e-8
    if bad.any():
        alt = np.where(np.abs(nrm[bad, 0:1]) < 0.9, [[1, 0, 0]], [[0, 1, 0]])
        tan[bad] = alt - nrm[bad] * np.sum(nrm[bad] * alt, 1, keepdims=True)
    return tan / np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-12)


class SHModel:
    """Linear fit of the 4 baked-lighting bytes of a vertex as a function of its normal, from a carbin's own vertices."""

    def __init__(self, donor: fc.Carbin):
        X, Y = [], []
        for s in donor.sections:
            for pool in (s.lod, s.lod0):
                if pool.count < 16 or pool.stride < 28:
                    continue
                v = pool.vertices()
                q = np.ascontiguousarray(v[:, 16:24]).view(">i2").reshape(-1, 4).astype(np.float64) / 32767
                X.append(fc.quat_normal(q))
                Y.append(v[:, 24:28].astype(np.float64))
        if X:
            X, Y = np.vstack(X), np.vstack(Y)
            A = np.column_stack([np.ones(len(X)), X])
            self.coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
        else:
            self.coef = np.array([[255.0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])

    def __call__(self, nrm: np.ndarray) -> np.ndarray:
        A = np.column_stack([np.ones(len(nrm)), nrm])
        return np.clip(np.round(A @ self.coef), 0, 255).astype(np.uint8)


def encode_vertices(pos, nrm, tan, uv0, uv1, offset, bmax, sh_bytes) -> bytes:
    local = pos - np.asarray(offset, np.float64)
    v = local / max(np.linalg.norm(bmax), 1e-9)
    w = np.linalg.norm(v, axis=1)
    xyz = np.where(w[:, None] > 1e-9, v / np.maximum(w[:, None], 1e-12), [[1.0, 0, 0]])
    out = np.zeros((len(pos), 28), np.uint8)
    p4 = np.column_stack([xyz, np.minimum(w, 1.0)])
    out[:, 0:8] = np.ascontiguousarray(np.round(np.clip(p4, -1, 1) * 32767).astype(">i2")).view(np.uint8).reshape(-1, 8)
    out[:, 8:12] = np.ascontiguousarray(np.round(np.clip(uv0, 0, 1) * 65535).astype(">u2")).view(np.uint8).reshape(-1, 4)
    out[:, 12:16] = np.ascontiguousarray(np.round(np.clip(uv1, 0, 1) * 65535).astype(">u2")).view(np.uint8).reshape(-1, 4)
    out[:, 16:24] = np.ascontiguousarray(np.round(np.clip(frame_quaternions(nrm, tan), -1, 1) * 32767).astype(">i2")).view(np.uint8).reshape(-1, 8)
    out[:, 24:28] = sh_bytes
    return out.tobytes()


def to_strip(tris: np.ndarray) -> np.ndarray:
    """Triangle list -> strip with restarts (greedy joining of edge-adjacent consecutive triangles)."""
    out = []
    i, n = 0, len(tris)
    while i < n:
        a, b, c = (int(x) for x in tris[i])
        strip = [a, b, c]
        i += 1
        while i < n:
            p, q = strip[-2], strip[-1]
            t = [int(x) for x in tris[i]]
            odd = len(strip) % 2 == 1
            nxt = None
            for k in range(3):
                x0, x1, x2 = t[k], t[(k + 1) % 3], t[(k + 2) % 3]
                if odd and (x1, x0) == (p, q):
                    nxt = x2
                elif not odd and (x0, x1) == (p, q):
                    nxt = x2
                if nxt is not None:
                    break
            if nxt is None:
                break
            strip.append(nxt)
            i += 1
        out += strip + [RESTART32]
    return np.array(out[:-1] if out else [], np.uint32)


def orient(pos, nrm, tris):
    """Make each triangle's winding agree with its vertex normals (FH1: cross(b-a, c-a) ~ +n)."""
    fn = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    flip = np.sum(fn * nrm[tris].sum(1), 1) < 0
    tris = tris.copy()
    tris[flip] = tris[flip][:, ::-1]
    return tris


def smooth_normals(pos, tris):
    n = np.zeros_like(pos, dtype=np.float64)
    fn = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


# ----------------------------------------------------------------------------------------------------------------- pieces

@dataclass
class Piece:
    """One imported surface chunk: float geometry for a (LOD, material) group of a section."""
    material: str
    lod: int
    pos: np.ndarray        # (N,3)
    nrm: np.ndarray        # (N,3)
    uv0: np.ndarray        # (N,2) final atlas uv, or a swatch point
    uv1: np.ndarray        # (N,2)
    tris: np.ndarray       # (T,3)
    swatch: bool = False   # uv0 is a single swatch texel: the sub gets scale 0


def donor_swatches(c: fc.Carbin) -> dict[str, tuple[float, float]]:
    """material -> (u, v) swatch texel the car already uses for it (the flat-colour entries of its atlas)."""
    from collections import Counter
    sw: dict[str, Counter] = {}
    for s in c.sections:
        for sb in s.subs:
            if is_swatch(sb):
                sw.setdefault(sb.name, Counter())[(round(sb.uvxf[0] + sb.uvxf[1] / 2, 5), round(sb.uvxf[2] + sb.uvxf[3] / 2, 5))] += 1
    return {m: cnt.most_common(1)[0][0] for m, cnt in sw.items()}


def _encode_groups(pieces, offset, bmax, sh, base, tpl_sub):
    """Encode pieces grouped by (LOD, material): -> (vertex byte blocks, new sub-meshes, vertex count). Vertex indexes start at `base`."""
    vbytes, subs = [], []
    groups: dict[tuple[int, str], list[Piece]] = {}
    for p in pieces:
        groups.setdefault((p.lod, p.material), []).append(p)
    for (lod, mat), ps in sorted(groups.items()):
        idx_all = []
        u1 = np.vstack([p.uv1 for p in ps])
        u1min, u1rng = u1.min(0), np.maximum(np.ptp(u1, 0), 1e-6)
        u0 = np.vstack([p.uv0 for p in ps])
        if all(p.swatch for p in ps):
            u0min, u0rng, u0div = u0[0], np.zeros(2), np.ones(2)
        else:
            u0min, u0rng = u0.min(0), np.ptp(u0, 0)
            u0div = np.where(u0rng > 1e-6, u0rng, 1.0)
            u0rng = np.where(u0rng > 1e-6, u0rng, 0.0)
        for p in ps:
            used = np.unique(p.tris)
            remap = np.full(used.max() + 1, -1, np.int64)
            remap[used] = np.arange(len(used))
            tris = remap[p.tris]
            pos, nrm = p.pos[used].astype(np.float64), p.nrm[used].astype(np.float64)
            tris = orient(pos, nrm, tris)
            uv0 = np.zeros((len(used), 2)) if p.swatch else (p.uv0[used] - u0min) / u0div
            uv1 = (p.uv1[used] - u1min) / u1rng
            tan = uv_tangents(pos, nrm, p.uv1[used], tris)
            vbytes.append(encode_vertices(pos, nrm, tan, uv0, uv1, offset, bmax, sh(nrm)))
            idx_all.append(tris + base)
            base += len(used)
        sb = copy.deepcopy(tpl_sub)
        sb.name, sb.lod, sb.index_type, sb.index_size = mat, lod, 6, 4
        sb.uvxf = [float(u0min[0]), float(u0rng[0]), float(u0min[1]), float(u0rng[1]),
                   float(u1min[0]), float(u1rng[0]), float(u1min[1]), float(u1rng[1])]
        sb.indices = to_strip(np.vstack(idx_all))
        subs.append(sb)
    return vbytes, subs, base


def rebuild_section(c: fc.Carbin, si: int, pieces: list[Piece], sh: SHModel, seed: int = 1, pool: str | None = None, draw: bool | None = None) -> None:
    """Replace section `si`'s geometry with `pieces` (in place) and fix the header draw lists. Sub-meshes are grouped by (LOD, material)."""
    rng = random.Random(seed)
    sec = c.sections[si]
    placement = capture_placement(c, si)
    pool = pool or pool_name_of(c)
    tpl_sub = sec.subs[0] if sec.subs else next(sb for s in c.sections for sb in s.subs)
    allp = np.vstack([p.pos[np.unique(p.tris)] for p in pieces])
    offset = np.array(sec.offset, np.float64)
    bmax = np.maximum(np.abs(allp - offset).max(0), 1e-3) * 1.0005
    sec.bmin, sec.bmax = list(-bmax), list(bmax)
    vbytes, subs, base = _encode_groups(pieces, offset, bmax, sh, 0, tpl_sub)
    newpool = fc.Pool(base, 28, [1, rng.randrange(1, 0xFFFFFFFF)], b"".join(vbytes))
    empty = fc.Pool(0, 0, [0], b"")
    if pool == "lod":
        sec.lod, sec.lod0 = newpool, (sec.lod0 if sec.lod0.count == 0 else empty)
    else:
        sec.lod, sec.lod0 = (sec.lod if sec.lod.count == 0 else empty), newpool
    sec.subs = subs
    sec.perms = struct.pack(">I", 0)
    sec.cnt2 = struct.pack(">I", 0)
    sec.streams = [fc.Stream(3, 0, 0, [0], b""), fc.Stream(3, 0, 0, [0], b"")]
    sec.tail5 = sec.tail5[:4] + b"\x00"
    if hasattr(sec, "fraw"):
        del sec.fraw
    refresh_draw_lists(c, si, placement, draw)


def append_pieces(c: fc.Carbin, si: int, pieces: list[Piece], sh: SHModel, seed: int = 1, pool: str | None = None) -> int:
    """Add `pieces` to section `si` WITHOUT touching its existing sub-meshes: the old vertices keep their bytes (only the position
    words are re-encoded when the bounds grow), the new vertices and sub-meshes are appended, and the new sub-meshes are added to the
    draw lists (transparent list for glass-like materials). The section's per-vertex damage streams are cleared because their length
    must match the vertex count. Returns the number of sub-meshes added."""
    sec = c.sections[si]
    pool = pool or pool_name_of(c)
    old = active_pool(sec, pool)
    if not old.count:
        rebuild_section(c, si, pieces, sh, seed, pool, draw=True)
        return len(sec.subs)
    odec = decode_pool(sec, old)
    offset = np.array(sec.offset, np.float64)
    allp = np.vstack([odec.pos] + [p.pos[np.unique(p.tris)] for p in pieces])
    bmax = np.maximum(np.abs(allp - offset).max(0), 1e-3) * 1.0005
    ov = old.vertices().copy()
    local = odec.pos - offset
    v = local / max(np.linalg.norm(bmax), 1e-9)
    w = np.linalg.norm(v, axis=1)
    xyz = np.where(w[:, None] > 1e-9, v / np.maximum(w[:, None], 1e-12), [[1.0, 0, 0]])
    p4 = np.column_stack([xyz, np.minimum(w, 1.0)])
    ov[:, 0:8] = np.ascontiguousarray(np.round(np.clip(p4, -1, 1) * 32767).astype(">i2")).view(np.uint8).reshape(-1, 8)
    tpl = sec.subs[0] if sec.subs else next(sb for s in c.sections for sb in s.subs)
    vbytes, subs, count = _encode_groups(pieces, offset, bmax, sh, old.count, tpl)
    newpool = fc.Pool(count, 28, list(old.bone), ov.tobytes() + b"".join(vbytes))
    first = len(sec.subs)
    if old is sec.lod:
        sec.lod = newpool
    else:
        sec.lod0 = newpool
    sec.subs = sec.subs + subs
    sec.bmin, sec.bmax = list(-bmax), list(bmax)
    sec.streams = [fc.Stream(3, 0, 0, [0], b"") for _ in (sec.streams or [0, 0])]
    if hasattr(sec, "fraw"):
        del sec.fraw
    if has_body_header(c):
        _, arrays, _ = draw_lists(c.header)
        if arrays:
            lods = array_lods(c, arrays)
            half = len(arrays) // 2
            transparent = transparent_materials(c)
            for k in range(first, len(sec.subs)):
                sb = sec.subs[k]
                want_t = sb.name in transparent or any(h in sb.name.lower() for h in TRANSPARENT_HINT)
                for ai in (range(0, half) if want_t else range(half, 2 * half)):
                    if lods[ai] == sb.lod:
                        arrays[ai].append((si << 16) | k)
            c.header = set_draw_lists(c.header, arrays)
    return len(subs)

# ----------------------------------------------------------------------------------------------------------------- LOD generation

def decimate(p: Piece, target_tris: int) -> Piece:
    """Simplify a piece to about `target_tris` triangles by vertex clustering: vertices in the same grid cell merge (positions, normals and uvs are
    averaged), degenerate and duplicate triangles are dropped, and the cell size is searched until the budget is met. Fast and robust; meant for the
    lower LODs (the silhouette is kept, small detail is not)."""
    tris = p.tris
    if len(tris) <= target_tris or len(tris) < 4:
        return p
    lo, hi = p.pos.min(0), p.pos.max(0)
    span = float(np.max(hi - lo)) or 1.0

    def cluster(cell: float):
        key = np.floor((p.pos - lo) / cell).astype(np.int64)
        k = key[:, 0] + key[:, 1] * 100003 + key[:, 2] * 10000019
        _, inv = np.unique(k, return_inverse=True)
        t = inv[tris]
        keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        t = t[keep]
        if len(t):
            srt = np.sort(t, axis=1)
            _, first = np.unique(srt, axis=0, return_index=True)
            t = t[np.sort(first)]
        return inv, t

    a, b = span / 4096.0, span / 2.0
    best = None
    for _ in range(18):
        mid = (a + b) / 2
        inv, t = cluster(mid)
        if len(t) > target_tris:
            a = mid
        else:
            best, b = (inv, t), mid
    if best is None:
        best = cluster(b)
    inv, t = best
    n = int(inv.max()) + 1
    cnt = np.bincount(inv, minlength=n).astype(np.float64)[:, None]

    def avg(a_):
        out = np.zeros((n, a_.shape[1]))
        np.add.at(out, inv, a_)
        return out / cnt
    used = np.unique(t)
    remap = np.full(n, -1, np.int64)
    remap[used] = np.arange(len(used))
    pos, nrm, uv0, uv1 = avg(p.pos)[used], avg(p.nrm)[used], avg(p.uv0)[used], avg(p.uv1)[used]
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return Piece(p.material, p.lod, pos, nrm, uv0, uv1, remap[t], p.swatch)


def lod_ratios(c: fc.Carbin, si: int, lods) -> dict[int, float]:
    """Triangle budget of each LOD relative to the lowest one asked for, taken from the section's own sub-meshes when it has several LODs
    (otherwise a default ladder). Used to size the simplified copies of an added model."""
    sec = c.sections[si]
    pool = active_pool(sec)
    have: dict[int, int] = {}
    for sb in sec.subs:
        have[sb.lod] = have.get(sb.lod, 0) + len(sub_triangles(sb, pool.count)) if pool.count else 0
    default = {0: 1.0, 1: 1.0, 2: 0.5, 3: 0.3, 4: 0.18, 5: 0.08}
    lods = sorted(lods)
    top = lods[0]
    out = {}
    for l in lods:
        if top in have and l in have and have[top] > 0:
            out[l] = min(1.0, have[l] / have[top])
        else:
            out[l] = min(1.0, default.get(l, 0.1) / max(default.get(top, 1.0), 1e-9))
    return out


def lod_pieces(pieces_at_top: list[Piece], lods, ratios: dict[int, float], min_tris: int = 12) -> list[Piece]:
    """One set of pieces per LOD: the lowest LOD keeps the full model, the others get decimate()d copies sized by `ratios`."""
    out = []
    top = min(lods)
    for l in sorted(lods):
        for p in pieces_at_top:
            q = p if l == top else decimate(p, max(min_tris, int(len(p.tris) * ratios.get(l, 0.1))))
            out.append(Piece(q.material, l, q.pos, q.nrm, q.uv0, q.uv1, q.tris, q.swatch))
    return out


# ----------------------------------------------------------------------------------------------------------------- draw lists

def has_body_header(c: fc.Carbin) -> bool:
    h = c.header
    return len(h) > 0x160 and struct.unpack_from(">I", h, 0)[0] == 5


def draw_lists(header: bytes):
    """-> (offset of the D block, [arrays of (section << 16 | sub)], end offset). Two arrays per LOD: transparent first, then opaque."""
    u = lambda o: struct.unpack_from(">I", header, o)[0]
    n = u(0x154)
    base = 0x15C + 32 * n
    ex = u(base + 8)
    d0 = base + 0x10 + ex * EXHAUST_ENTRY + 0x340
    p = d0 + 4
    arrays = []
    for _ in range(u(d0) * 2):
        k = u(p)
        arrays.append(list(struct.unpack_from(f">{k}I", header, p + 4)))
        p += 4 + 4 * k
    return d0, arrays, p


def set_draw_lists(header: bytes, arrays) -> bytes:
    d0, _, end = draw_lists(header)
    blob = struct.pack(">I", len(arrays) // 2) + b"".join(struct.pack(f">I{len(a)}I", len(a), *a) for a in arrays)
    return header[:d0] + blob + header[end:]


def array_lods(c: fc.Carbin, arrays) -> list[int]:
    """Which sub-mesh LOD each draw-list array holds (by the entries it already has; fallback by position)."""
    from collections import Counter
    half = len(arrays) // 2
    out = []
    for ai in range(len(arrays)):
        pair = arrays[ai % half] + arrays[half + ai % half]
        cnt = Counter()
        for e in pair:
            s, k = e >> 16, e & 0xFFFF
            if s < len(c.sections) and k < len(c.sections[s].subs):
                cnt[c.sections[s].subs[k].lod] += 1
        out.append(cnt.most_common(1)[0][0] if cnt else (0 if half == 1 else ai % half + 1))
    return out


def transparent_materials(c: fc.Carbin) -> set[str]:
    """Materials the carbin already draws in the transparent lists more often than in the opaque ones."""
    from collections import Counter
    _, arrays, _ = draw_lists(c.header)
    half = len(arrays) // 2
    t, o = Counter(), Counter()
    for ai, a in enumerate(arrays):
        for e in a:
            s, k = e >> 16, e & 0xFFFF
            if s < len(c.sections) and k < len(c.sections[s].subs):
                (t if ai < half else o)[c.sections[s].subs[k].name] += 1
    return {m for m in t if t[m] > o[m]}


def capture_placement(c: fc.Carbin, si: int) -> dict[tuple[int, str], list[int]]:
    """(LOD, material) -> draw-list array indexes the section's sub-meshes are drawn in now. Take this BEFORE you change the section."""
    out: dict[tuple[int, str], list[int]] = {}
    for (s, k), ais in drawn_at(c).items():
        if s == si and k < len(c.sections[si].subs):
            sb = c.sections[si].subs[k]
            out.setdefault((sb.lod, sb.name), [])
            for ai in ais:
                if ai not in out[(sb.lod, sb.name)]:
                    out[(sb.lod, sb.name)].append(ai)
    return out


def refresh_draw_lists(c: fc.Carbin, si: int, placement: dict | None = None, draw: bool | None = None) -> None:
    """After section `si`'s sub-meshes changed: drop its old entries and add one per sub-mesh.
    A sub-mesh whose (LOD, material) was drawn before goes into the same arrays again (`placement`, from capture_placement).
    Others go to the array of their LOD: the transparent list for materials the car draws as transparent or named like glass or lights, else the opaque list.
    A section that was not drawn at all before stays hidden unless draw=True."""
    if not has_body_header(c):
        return
    _, arrays, _ = draw_lists(c.header)
    if not arrays:
        return
    placement = placement or {}
    if draw is None and not placement:
        draw = False
    transparent = transparent_materials(c)
    lods = array_lods(c, arrays)
    half = len(arrays) // 2
    arrays = [[e for e in a if (e >> 16) != si] for a in arrays]
    if draw is False:
        c.header = set_draw_lists(c.header, arrays)
        return
    for k, sb in enumerate(c.sections[si].subs):
        key = (sb.lod, sb.name)
        if key in placement:
            targets = placement[key]
        else:
            want_t = sb.name in transparent or any(h in sb.name.lower() for h in TRANSPARENT_HINT)
            targets = [ai for ai in (range(0, half) if want_t else range(half, 2 * half)) if lods[ai] == sb.lod]
        for ai in targets:
            arrays[ai].append((si << 16) | k)
    c.header = set_draw_lists(c.header, arrays)

def hide_section(c: fc.Carbin, si: int) -> None:
    """Remove the section from every draw list (it stays in the file but is never drawn)."""
    if not has_body_header(c):
        return
    _, arrays, _ = draw_lists(c.header)
    c.header = set_draw_lists(c.header, [[e for e in a if (e >> 16) != si] for a in arrays])


def drawn_at(c: fc.Carbin) -> dict[tuple[int, int], list[int]]:
    """(section, sub) -> list of draw-list array indexes that draw it."""
    if not has_body_header(c):
        return {}
    _, arrays, _ = draw_lists(c.header)
    out: dict[tuple[int, int], list[int]] = {}
    for ai, a in enumerate(arrays):
        for e in a:
            out.setdefault((e >> 16, e & 0xFFFF), []).append(ai)
    return out


def remove_subs(c: fc.Carbin, si: int, ks) -> None:
    """Delete sub-meshes `ks` of section `si` and renumber the draw lists. The vertices stay in the pool (they are simply unused)."""
    sec = c.sections[si]
    ks = set(ks)
    keep = [k for k in range(len(sec.subs)) if k not in ks]
    remap = {old: new for new, old in enumerate(keep)}
    if has_body_header(c):
        _, arrays, _ = draw_lists(c.header)
        new = []
        for a in arrays:
            na = []
            for e in a:
                s, k = e >> 16, e & 0xFFFF
                if s != si:
                    na.append(e)
                elif k in remap:
                    na.append((si << 16) | remap[k])
            new.append(na)
        c.header = set_draw_lists(c.header, new)
    sec.subs = [sec.subs[k] for k in keep]


def set_sub(c: fc.Carbin, si: int, k: int, material: str | None = None, lod: int | None = None) -> None:
    """Rename a sub-mesh's material and/or change its LOD. A LOD change moves its draw-list entries to the arrays of the new LOD
    (same transparent/opaque half); if the car has no array for that LOD the sub-mesh is no longer drawn."""
    sb = c.sections[si].subs[k]
    if material is not None:
        sb.name = material
    if lod is not None and lod != sb.lod and has_body_header(c):
        _, arrays, _ = draw_lists(c.header)
        half = len(arrays) // 2
        lods = array_lods(c, arrays)
        entry = (si << 16) | k
        halves = [0 if ai < half else 1 for ai, a in enumerate(arrays) if entry in a]
        arrays = [[e for e in a if e != entry] for a in arrays]
        for h in halves:
            for ai in range(h * half, (h + 1) * half):
                if lods[ai] == lod:
                    arrays[ai].append(entry)
        c.header = set_draw_lists(c.header, arrays)
    if lod is not None:
        sb.lod = lod


def show_section(c: fc.Carbin, si: int) -> None:
    """Make a hidden section drawn again: every sub-mesh goes to the arrays of its LOD."""
    refresh_draw_lists(c, si, None, True)

# ----------------------------------------------------------------------------------------------------------------- transforms

def transform_section(c: fc.Carbin, si: int, matrix: np.ndarray, move_pivot: bool = False) -> None:
    """Apply a 4x4 matrix (acting on world coordinates, row-vector form not used: p' = M @ [p,1]) to section `si` in place.
    The vertex frames (normal/tangent) are rotated with the matrix' upper 3x3 (assumed rotation times uniform or non-uniform scale; mirror flips winding).
    move_pivot: also move the section's `offset` (pivot) by the matrix."""
    sec = c.sections[si]
    M = np.asarray(matrix, np.float64)
    A = M[:3, :3]
    det = np.linalg.det(A)
    ninv = np.linalg.inv(A).T if abs(det) > 1e-12 else np.eye(3)
    decs = {}
    for name in ("lod", "lod0"):
        pool = getattr(sec, name)
        if pool.count:
            decs[name] = decode_pool(sec, pool)
    if not decs:
        return
    new_offset = (M @ np.append(sec.offset, 1.0))[:3] if move_pivot else np.asarray(sec.offset, np.float64)
    tp = {}
    for name, d in decs.items():
        pos = (A @ d.pos.T).T + M[:3, 3]
        n = d.nrm @ ninv.T
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        t = d.tan @ A.T
        t -= n * np.sum(n * t, 1, keepdims=True)
        t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
        tp[name] = (pos, n, t)
    allp = np.vstack([v[0] for v in tp.values()])
    bmax = np.maximum(np.abs(allp - new_offset).max(0), 1e-3) * 1.0005
    for name, (pos, n, t) in tp.items():
        pool = getattr(sec, name)
        d = decs[name]
        raw = np.frombuffer(encode_vertices(pos, n, t, d.uv0, d.uv1, new_offset, bmax, d.sh), np.uint8).reshape(-1, 28).copy()
        # keep the stored uv bytes exactly (they were only decoded to floats): copy them back
        v0 = pool.vertices()
        raw[:, 8:16] = v0[:, 8:16]
        raw[:, 24:28] = v0[:, 24:28]
        pool.data = raw.tobytes()
    if det < 0:
        for sb in sec.subs:                                   # a mirror flips winding
            idx = np.asarray(sb.indices, np.int64)
            if sb.index_type == 4:
                tr = idx[: len(idx) // 3 * 3].reshape(-1, 3)[:, ::-1]
                sb.indices = tr.reshape(-1).astype(idx.dtype)
            else:
                tris = sub_triangles(sb, max(p.count for p in (sec.lod, sec.lod0)))[:, ::-1]
                sb.indices = to_strip(tris)
                sb.index_type, sb.index_size = 6, 4
    sec.offset = [float(x) for x in new_offset]
    sec.bmin, sec.bmax = list(-bmax), list(bmax)
    if hasattr(sec, "fraw"):
        del sec.fraw


def trs_matrix(translate=(0, 0, 0), rotate_deg=(0, 0, 0), scale=(1, 1, 1), pivot=(0, 0, 0)) -> np.ndarray:
    """Scale, then rotate (X, then Y, then Z, degrees) about `pivot`, then translate."""
    rx, ry, rz = np.radians(rotate_deg)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    A = Rz @ Ry @ Rx @ np.diag(scale)
    p = np.asarray(pivot, np.float64)
    M = np.eye(4)
    M[:3, :3] = A
    M[:3, 3] = p - A @ p + np.asarray(translate, np.float64)
    return M


# ----------------------------------------------------------------------------------------------------------------- OBJ

def export_obj(c: fc.Carbin, sections: list[int] | None = None, lods: set[int] | None = None, uv: str = "auto", flip_v: bool = True) -> str:
    """Wavefront OBJ of the chosen sections (default all) and LODs (default all). One object per section; `usemtl` = the sub-mesh's material name.
    uv: 'uv0' (atlas), 'uv1' (detail mapping) or 'auto' (uv0 unless the sub reads a single swatch texel, then uv1). Coordinates are FH1's (front = -z)."""
    lines = ["# Forza Horizon 1 carbin export (front is -z, left is -x)"]
    vbase = 1
    for si, sec in enumerate(c.sections):
        if sections is not None and si not in sections:
            continue
        pool = active_pool(sec)
        if not pool.count:
            continue
        d = decode_pool(sec, pool)
        wrote = False
        for k, sb in enumerate(sec.subs):
            if lods is not None and sb.lod not in lods:
                continue
            tris = sub_triangles(sb, pool.count)
            if not len(tris):
                continue
            use = uv if uv != "auto" else ("uv1" if is_swatch(sb) else "uv0")
            uvs = final_uv(d.uv0, sb.uvxf[0:4]) if use == "uv0" else final_uv(d.uv1, sb.uvxf[4:8])
            if flip_v:
                uvs = uvs * [1, -1] + [0, 1]
            if not wrote:
                lines.append(f"o {sec.name}")
                wrote = True
            used = np.unique(tris)
            remap = np.full(used.max() + 1, -1, np.int64)
            remap[used] = np.arange(len(used))
            lines.append(f"g {sec.name}_{k}_L{sb.lod}")
            lines.append(f"usemtl {sb.name}")
            lines += [f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" for p in d.pos[used]]
            lines += [f"vt {t[0]:.6f} {t[1]:.6f}" for t in uvs[used]]
            lines += [f"vn {n[0]:.5f} {n[1]:.5f} {n[2]:.5f}" for n in d.nrm[used]]
            for t in remap[tris]:
                a, b, cc = (int(x) + vbase for x in t)
                lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {cc}/{cc}/{cc}")
            vbase += len(used)
    return "\n".join(lines) + "\n"


def parse_obj(text: str, flip_v: bool = True) -> dict[str, dict[int | None, dict[str, dict]]]:
    """-> {object name: {lod or None: {material: {pos, nrm, uv, tris}}}} with one vertex per (v, vt, vn) combination. Polygons are fan-triangulated.
    The LOD comes from a group name ending in _L<n> (what export_obj writes); None when the OBJ does not say."""
    import re
    P, T, N = [], [], []
    objs: dict[str, dict[int | None, dict[str, dict]]] = {}
    cur_obj, cur_mat, cur_lod = "object", "default", None
    bufs: dict[tuple, dict] = {}

    def buf():
        key = (cur_obj, cur_lod, cur_mat)
        if key not in bufs:
            bufs[key] = {"pos": [], "nrm": [], "uv": [], "tris": [], "map": {}}
        return bufs[key]

    for raw in text.splitlines():
        s = raw.strip()
        if not s or s[0] == "#":
            continue
        parts = s.split()
        k = parts[0]
        if k == "v":
            P.append([float(x) for x in parts[1:4]])
        elif k == "vt":
            T.append([float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0])
        elif k == "vn":
            N.append([float(x) for x in parts[1:4]])
        elif k == "o":
            cur_obj = " ".join(parts[1:]) or "object"
        elif k == "g":
            gname = " ".join(parts[1:])
            m = re.search(r"_L(\d+)$", gname)
            cur_lod = int(m.group(1)) if m else None
            if cur_obj == "object" and not m:
                cur_obj = gname or "object"
        elif k == "usemtl":
            cur_mat = " ".join(parts[1:]) or "default"
        elif k == "f":
            b = buf()
            ids = []
            for tok in parts[1:]:
                f = tok.split("/")
                vi = int(f[0]); vi = vi - 1 if vi > 0 else len(P) + vi
                ti = (int(f[1]) if len(f) > 1 and f[1] else 0)
                ti = (ti - 1 if ti > 0 else len(T) + ti) if ti else -1
                ni = (int(f[2]) if len(f) > 2 and f[2] else 0)
                ni = (ni - 1 if ni > 0 else len(N) + ni) if ni else -1
                key = (vi, ti, ni)
                if key not in b["map"]:
                    b["map"][key] = len(b["pos"])
                    b["pos"].append(P[vi])
                    b["uv"].append(T[ti] if ti >= 0 else [0.0, 0.0])
                    b["nrm"].append(N[ni] if ni >= 0 else None)
                ids.append(b["map"][key])
            for i in range(1, len(ids) - 1):
                b["tris"].append([ids[0], ids[i], ids[i + 1]])
    for (o, lod, m), b in bufs.items():
        if not b["tris"]:
            continue
        pos = np.array(b["pos"], np.float64)
        tris = np.array(b["tris"], np.int64)
        uv = np.array(b["uv"], np.float64)
        if flip_v:
            uv = uv * [1, -1] + [0, 1]
        if any(n is None for n in b["nrm"]):
            nrm = smooth_normals(pos, tris)
        else:
            nrm = np.array(b["nrm"], np.float64)
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        objs.setdefault(o, {}).setdefault(lod, {})[m] = {"pos": pos, "nrm": nrm, "uv": uv, "tris": tris}
    return objs


def pieces_from_obj(by_lod: dict[int | None, dict[str, dict]], default_lod: int, swatches: dict[str, tuple[float, float]],
                    material_map: dict[str, str] | None = None, uv0_mode: str = "swatch", lod_map: dict[int, int] | None = None) -> list[Piece]:
    """One object of parse_obj() -> pieces. material_map renames OBJ materials to FH1 material names; lod_map renames LODs; groups with no LOD use default_lod.
    uv0_mode: 'swatch' (materials the car already draws as flat colour keep a swatch texel; others use the OBJ uv as atlas uv) or 'obj' (always the OBJ uv)."""
    out = []
    for lod, mats in by_lod.items():
        lod_val = default_lod if lod is None else (lod_map or {}).get(lod, lod)
        for mat, g in mats.items():
            name = (material_map or {}).get(mat, mat)
            sw = swatches.get(name) if uv0_mode == "swatch" else None
            uv0 = np.tile(np.array(sw), (len(g["pos"]), 1)) if sw else g["uv"]
            out.append(Piece(name, lod_val, g["pos"], g["nrm"], uv0, g["uv"], g["tris"], swatch=bool(sw)))
    return out

# ----------------------------------------------------------------------------------------------------------------- header: lights, exhausts

LIGHT_SIZE = 32


def lights(c: fc.Carbin) -> list[dict]:
    """The 32-byte light records of a main carbin header: position, direction, role bytes, colour, glow size."""
    if not has_body_header(c):
        return []
    h = c.header
    n = struct.unpack_from(">I", h, 0x154)[0]
    out = []
    for i in range(n):
        r = h[0x158 + i * LIGHT_SIZE:0x158 + (i + 1) * LIGHT_SIZE]
        out.append({"index": i, "pos": list(struct.unpack_from(">3f", r, 0)), "dir": list(struct.unpack_from(">3h", r, 12)),
                    "b18": r[18], "role": r[19], "b20": r[20], "b21": r[21], "rgb": [r[22], r[23], r[24]], "glow": r[25],
                    "tail": r[26:32].hex(), "head6": r[12:18].hex()})
    return out


def set_light(c: fc.Carbin, i: int, **kw) -> None:
    h = bytearray(c.header)
    o = 0x158 + i * LIGHT_SIZE
    if "pos" in kw:
        struct.pack_into(">3f", h, o, *[float(x) for x in kw["pos"]])
    if "dir" in kw:
        struct.pack_into(">3h", h, o + 12, *[int(x) for x in kw["dir"]])
    for key, off in (("b18", 18), ("role", 19), ("b20", 20), ("b21", 21), ("glow", 25)):
        if key in kw:
            h[o + off] = int(kw[key]) & 0xFF
    if "rgb" in kw:
        h[o + 22:o + 25] = bytes(int(x) & 0xFF for x in kw["rgb"])
    c.header = bytes(h)


def add_light(c: fc.Carbin, like: int | None = None) -> int:
    """Append a light record (copy of record `like`, or a plain white one) and return its index."""
    n = struct.unpack_from(">I", c.header, 0x154)[0]
    rec = c.header[0x158 + like * LIGHT_SIZE:0x158 + (like + 1) * LIGHT_SIZE] if like is not None and like < n else bytes(LIGHT_SIZE)
    if like is None:
        rec = bytearray(LIGHT_SIZE)
        rec[22:25] = b"\xff\xff\xff"
        rec[25] = 4
        rec = bytes(rec)
    ins = 0x158 + n * LIGHT_SIZE
    c.header = c.header[:0x154] + struct.pack(">I", n + 1) + c.header[0x158:ins] + rec + c.header[ins:]
    return n


def remove_light(c: fc.Carbin, i: int) -> None:
    n = struct.unpack_from(">I", c.header, 0x154)[0]
    o = 0x158 + i * LIGHT_SIZE
    c.header = c.header[:0x154] + struct.pack(">I", n - 1) + c.header[0x158:o] + c.header[o + LIGHT_SIZE:]


def exhausts(c: fc.Carbin) -> list[dict]:
    """Exhaust / backfire emitters: position and side flag (2 = left, 3 = right). Count and flags must not change."""
    if not has_body_header(c):
        return []
    h = c.header
    n = struct.unpack_from(">I", h, 0x154)[0]
    base = 0x15C + 32 * n
    ex = struct.unpack_from(">I", h, base + 8)[0]
    out = []
    for i in range(ex):
        o = base + 0x10 + i * EXHAUST_ENTRY
        out.append({"index": i, "pos": list(struct.unpack_from(">3f", h, o + 14 * 4)), "flag": struct.unpack_from(">I", h, o + 34 * 4)[0]})
    return out


def set_exhaust_pos(c: fc.Carbin, i: int, pos) -> None:
    h = bytearray(c.header)
    n = struct.unpack_from(">I", h, 0x154)[0]
    base = 0x15C + 32 * n
    struct.pack_into(">3f", h, base + 0x10 + i * EXHAUST_ENTRY + 14 * 4, *[float(x) for x in pos])
    c.header = bytes(h)


# ----------------------------------------------------------------------------------------------------------------- summary / checks

def summary(c: fc.Carbin) -> dict:
    drawn = drawn_at(c)
    secs = []
    for si, s in enumerate(c.sections):
        pool = active_pool(s)
        subs = []
        for k, sb in enumerate(s.subs):
            tr = len(sub_triangles(sb, pool.count)) if pool.count else 0
            subs.append({"index": k, "material": sb.name, "lod": sb.lod, "tris": tr, "swatch": is_swatch(sb),
                         "index_type": "strip" if sb.index_type == 6 else "list", "drawn_in": drawn.get((si, k), [])})
        secs.append({"index": si, "name": s.name, "offset": list(s.offset), "bmax": list(s.bmax), "verts": pool.count,
                     "pool": "lod" if pool is s.lod else "lod0", "subs": subs, "version": s.version})
    return {"sections": secs, "pool": pool_name_of(c), "lights": lights(c), "exhausts": exhausts(c), "body_header": has_body_header(c),
            "draw_arrays": len(draw_lists(c.header)[1]) if has_body_header(c) else 0,
            "array_lods": array_lods(c, draw_lists(c.header)[1]) if has_body_header(c) else []}


def validate(c: fc.Carbin) -> list[str]:
    """Consistency checks an edited carbin should pass before it is saved. Returns problems (empty = ok)."""
    probs = []
    for si, s in enumerate(c.sections):
        pool = active_pool(s)
        for k, sb in enumerate(s.subs):
            idx = np.asarray(sb.indices, np.int64)
            restart = 0xFFFF if sb.index_size == 2 else RESTART32
            real = idx[(idx != restart) & (idx < 0xFFFFFF)]
            if len(real) and real.max() >= pool.count:
                probs.append(f"{s.name} sub {k}: index {real.max()} is outside the {pool.count}-vertex pool")
        if pool.count and pool.stride != 28:
            probs.append(f"{s.name}: vertex stride {pool.stride}")
        if len(s.bmax) == 3 and min(s.bmax) <= 0:
            probs.append(f"{s.name}: non-positive bmax {s.bmax}")
    if has_body_header(c):
        _, arrays, _ = draw_lists(c.header)
        for ai, a in enumerate(arrays):
            for e in a:
                si, k = e >> 16, e & 0xFFFF
                if si >= len(c.sections) or k >= len(c.sections[si].subs):
                    probs.append(f"draw list {ai}: entry ({si},{k}) points at a sub-mesh that does not exist")
                    break
    keys = [s.lod.bone[1] for s in c.sections if len(s.lod.bone) > 1 and s.lod.count] + [s.lod0.bone[1] for s in c.sections if len(s.lod0.bone) > 1 and s.lod0.count]
    if len(keys) != len(set(keys)):
        probs.append("two vertex buffers share a resource key (give edited copies their own key)")
    try:
        fc.parse(fc.build(c))
    except Exception as e:  # noqa: BLE001
        probs.append(f"rebuilt file does not parse: {e}")
    return probs
