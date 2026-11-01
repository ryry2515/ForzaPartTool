"""
fh1_carbin.py - lossless reader/writer for Forza Horizon 1 (Xbox 360) .carbin files.

Layout follows github.com/rustybusgaming/fh1_rewrite (crates/fh1-formats/src/carbin.rs);
every byte the reference skips is kept verbatim so parse() -> build() round-trips exactly.
All values are big-endian.

Section:  u32 marker(5|2) | f32 offset[3] | f32 bmin[3] | f32 bmax[3] | raw28
          | u32 perm_count, perm_count*16 | raw4 | u32 cnt2, cnt2*2 | raw12
          | u8 name_len, name | u32 version | Pool(lod) | raw4 | u32 sub_count, Sub*
          | raw4 | Pool(lod0) | tail
Pool:     u32 count | u32 stride | bone block (ver>=3: u32 n, [u32 id if n]) | count*stride
Sub:      raw5 | f32 uvxf[8] | raw36 | u32 name_len, name | u32 lod | u32 index_type(4 list,6 strip)
          | raw56 | raw4|8 | u32 count | u32 size | indices | raw4
Tail:     raw5 | 2 x Stream(u32 version, u32 count, u32 stride, bone block, data) | raw until next
Vertex (28/32 B): i16 pos[4] (xyz*w, SNORM, remapped to section bounds) | u16 uv0[2] | u16 uv1[2]
          | i16 tangent-frame quaternion[4] | u8[4] (SH/AO) | (+4 B when stride 32)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

BE_U32 = struct.Struct(">I")
FLT_MAX = 3.4028234663852886e38


class CarbinError(Exception):
    pass


class _R:
    def __init__(self, d: bytes, p: int = 0):
        self.d, self.p = d, p

    def need(self, n):
        if self.p + n > len(self.d):
            raise CarbinError(f"truncated at {self.p:#x} (+{n})")

    def raw(self, n) -> bytes:
        self.need(n)
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.raw(1)[0]

    def u32(self):
        return BE_U32.unpack(self.raw(4))[0]

    def i32(self):
        return struct.unpack(">i", self.raw(4))[0]

    def f32s(self, n):
        return list(struct.unpack(f">{n}f", self.raw(4 * n)))


@dataclass
class Pool:
    count: int
    stride: int
    bone: list[int]              # [] or [n] or [n, id]
    data: bytes

    def vertices(self) -> np.ndarray:
        return np.frombuffer(self.data, np.uint8).reshape(self.count, self.stride) if self.count else \
            np.zeros((0, self.stride or 28), np.uint8)


@dataclass
class Stream:
    version: int
    count: int
    stride: int
    bone: list[int]
    data: bytes


@dataclass
class Sub:
    raw5: bytes
    uvxf: list[float]
    raw36: bytes
    name: str
    lod: int
    index_type: int
    raw56: bytes
    rawver: bytes
    index_size: int
    indices: np.ndarray          # uint16/uint32 as stored (strip or list)
    raw4: bytes


@dataclass
class Section:
    marker: int
    offset: list[float]
    bmin: list[float]
    bmax: list[float]
    raw28: bytes
    perms: bytes                 # perm_count * 16
    raw4a: bytes
    cnt2: bytes                  # cnt2 * 2
    raw12: bytes
    name: str
    version: int
    lod: Pool
    raw4b: bytes
    subs: list[Sub]
    raw4c: bytes
    lod0: Pool
    tail5: bytes
    streams: list[Stream]
    trailer: bytes


@dataclass
class Carbin:
    header: bytes                # everything before the part-count word
    sections: list[Section]
    footer: bytes
    type_id: int = 0


def _bone(r: _R, version: int) -> list[int]:
    if version < 3:
        return []
    n = r.u32()
    return [n, r.u32()] if n else [n]


def _pool(r: _R, version: int) -> Pool:
    count = max(r.i32(), 0)
    stride = r.u32()
    bone = _bone(r, version)
    if count and stride not in (28, 32):
        raise CarbinError(f"odd vertex stride {stride}")
    return Pool(count, stride, bone, r.raw(count * stride))


def _sub(r: _R, version: int) -> Sub:
    raw5 = r.raw(5)
    uvxf = r.f32s(8)
    raw36 = r.raw(36)
    ln = r.u32()
    if ln > 256:
        raise CarbinError("sub name too long")
    name = r.raw(ln).decode("latin-1")
    lod = r.u32()
    itype = r.u32()
    if itype not in (4, 6):
        raise CarbinError(f"index type {itype}")
    raw56 = r.raw(56)
    rawver = r.raw(8 if version >= 3 else 4)
    count = r.u32()
    size = r.u32()
    if size not in (2, 4):
        raise CarbinError(f"index size {size}")
    idx = np.frombuffer(r.raw(count * size), ">u2" if size == 2 else ">u4").copy()
    return Sub(raw5, uvxf, raw36, name, lod, itype, raw56, rawver, size, idx, r.raw(4))


def _looks_like_section(d: bytes, p: int) -> bool:
    if p + 40 > len(d):
        return False
    if BE_U32.unpack_from(d, p)[0] not in (5, 2):
        return False
    f = struct.unpack_from(">9f", d, p + 4)
    return all(np.isfinite(f)) or (f[3] == FLT_MAX)


def _section(r: _R) -> Section:
    marker = r.u32()
    if marker not in (5, 2):
        raise CarbinError(f"bad marker {marker} at {r.p - 4:#x}")
    fraw = r.raw(36)
    vals = list(struct.unpack(">9f", fraw))
    offset, bmin, bmax = vals[0:3], vals[3:6], vals[6:9]
    raw28 = r.raw(28)
    pc = r.u32()
    if pc > 1_000_000:
        raise CarbinError("perm count")
    perms = BE_U32.pack(pc) + r.raw(pc * 16)
    raw4a = r.raw(4)
    c2 = r.u32()
    if c2 > 1_000_000:
        raise CarbinError("cnt2")
    cnt2 = BE_U32.pack(c2) + r.raw(c2 * 2)
    raw12 = r.raw(12)
    nb = r.raw(r.u8())
    if not nb or not all(32 < c < 127 for c in nb):
        raise CarbinError("section name not printable")
    name = nb.decode("latin-1")
    version = r.u32()
    lod = _pool(r, version)
    raw4b = r.raw(4)
    nsub = r.u32()
    if nsub > 1000:
        raise CarbinError("sub count")
    subs = [_sub(r, version) for _ in range(nsub)]
    raw4c = r.raw(4)
    lod0 = _pool(r, version)
    tail5 = r.raw(5)
    streams = []
    for _ in range(2):
        v, c, s = r.u32(), r.u32(), r.u32()
        bone = _bone(r, v)
        streams.append(Stream(v, c, s, bone, r.raw(c * s)))
    s = Section(marker, offset, bmin, bmax, raw28, perms, raw4a, cnt2, raw12, name, version,
                lod, raw4b, subs, raw4c, lod0, tail5, streams, b"")
    s.fraw = fraw
    return s


def parse(data: bytes) -> Carbin:
    type_id = BE_U32.unpack_from(data, 0)[0]
    if type_id not in (1, 2, 3, 5):
        raise CarbinError(f"unsupported carbin type {type_id}")
    start = 0x4DC if type_id == 5 and any(data[4:0x18]) else 0x8
    best = None
    for i in range(start, min(len(data) - 48, start + 0x2000), 4):
        n = BE_U32.unpack_from(data, i)[0]
        if not 1 <= n <= 255 or not _looks_like_section(data, i + 4):
            continue
        try:
            secs, end = _sections(data, i + 4, n)
        except CarbinError:
            continue
        best = Carbin(data[:i], secs, data[end:], type_id)
        break
    if best is None:
        raise CarbinError("no section table found")
    return best


def _sections(data: bytes, p: int, n: int):
    r = _R(data, p)
    secs = []
    for k in range(n):
        s = _section(r)
        # trailer: bytes up to the next section marker (0/4/8/12 B observed)
        if k + 1 < n:
            for skip in (0, 4, 8, 12, 16):
                if _looks_like_section(data, r.p + skip):
                    s.trailer = r.raw(skip)
                    break
            else:
                raise CarbinError(f"no next section after {s.name}")
        secs.append(s)
    return secs, r.p


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------

def _w_bone(bone, stripped=False):
    # Stripped files set bit 1 ("payload not present") on the buffer flag word: 1 -> 3.
    if stripped and bone and bone[0]:
        bone = [bone[0] | 2] + list(bone[1:])
    return b"".join(BE_U32.pack(x) for x in bone)


def _w_pool(p: Pool, stripped=False) -> bytes:
    return (struct.pack(">iI", p.count, p.stride) + _w_bone(p.bone, stripped)
            + (b"" if stripped else p.data))


def _w_sub(s: Sub, stripped=False) -> bytes:
    nm = s.name.encode("latin-1")
    idx = b"" if stripped else np.asarray(s.indices, ">u2" if s.index_size == 2 else ">u4").tobytes()
    rawver = s.rawver
    if stripped and len(rawver) == 8:            # index-buffer flag word: 0 -> 2
        rawver = rawver[:4] + BE_U32.pack(BE_U32.unpack(rawver[4:])[0] | 2)
    return (s.raw5 + struct.pack(">8f", *s.uvxf) + s.raw36 + BE_U32.pack(len(nm)) + nm
            + struct.pack(">II", s.lod, s.index_type) + s.raw56 + rawver
            + struct.pack(">II", len(s.indices), s.index_size) + idx + s.raw4)


def _w_section(s: Section, stripped=False) -> bytes:
    nm = s.name.encode("latin-1")
    floats = struct.pack(">9f", *s.offset, *s.bmin, *s.bmax)
    fraw = getattr(s, "fraw", None)
    if fraw is not None and struct.unpack(">9f", fraw) == tuple(s.offset + s.bmin + s.bmax):
        floats = fraw                            # unchanged: keep exact bits (NaN/-0/sentinels)
    out = [BE_U32.pack(s.marker), floats, s.raw28,
           s.perms, s.raw4a, s.cnt2, s.raw12, bytes([len(nm)]), nm, BE_U32.pack(s.version),
           _w_pool(s.lod, stripped), s.raw4b, BE_U32.pack(len(s.subs))]
    out += [_w_sub(x, stripped) for x in s.subs]
    out += [s.raw4c, _w_pool(s.lod0, stripped), s.tail5]
    for st in s.streams:
        out.append(struct.pack(">III", st.version, st.count, st.stride) + _w_bone(st.bone, stripped)
                   + (b"" if stripped else st.data))
    out.append(s.trailer)
    return b"".join(out)


def build(c: Carbin, stripped: bool = False) -> bytes:
    """stripped=True writes the companion stripped_<name>.carbin (all payloads omitted)."""
    return (c.header + BE_U32.pack(len(c.sections))
            + b"".join(_w_section(s, stripped) for s in c.sections) + c.footer)


# ---------------------------------------------------------------------------
# vertex helpers
# ---------------------------------------------------------------------------

def decode_positions(sec: Section, pool: Pool) -> np.ndarray:
    v = pool.vertices()
    if not len(v):
        return np.zeros((0, 3), np.float32)
    p = np.ascontiguousarray(v[:, :8]).view(">i2").reshape(-1, 4).astype(np.float32) / 32767.0
    xyz = p[:, :3] * p[:, 3:4]
    lo, hi = xyz.min(0), xyz.max(0)
    span = np.where(hi - lo > 0, hi - lo, 1)
    t = np.where(hi - lo > 0, (xyz - lo) / span, 0.5)
    bmin, bmax = np.array(sec.bmin), np.array(sec.bmax)
    return (bmin + t * (bmax - bmin)).astype(np.float32)


def quat_normal(q: np.ndarray) -> np.ndarray:
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-9)
    x, y, z, w = q.T
    return np.stack([1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)], 1)


def strip_to_list(idx: np.ndarray, restart: int) -> np.ndarray:
    tris, run = [], []
    for v in idx.tolist():
        if v == restart or v >= 0xFFFFFF:
            run = []
            continue
        run.append(v)
        if len(run) >= 3:
            a, b, c = run[-3:]
            if a != b and b != c and a != c:
                tris.append((a, b, c) if len(run) % 2 else (b, a, c))
    return np.array(tris, np.uint32).reshape(-1, 3)
