"""
fm4_carbin.py - lossless reader/writer for Forza Motorsport 4 .carbin files (type 1/2/3).

Section/sub layout is FH1's (fh1_carbin.py) minus FH1's per-buffer storage blocks:
  Pool   : u32 count | u32 stride | data                     (FH1 adds u32 flag [+u32 key])
  Sub    : ... raw56 | u32 version | u32 count | u32 size ...  (FH1 adds a u32 flag after version)
  Tail   : raw5 | 2 x (u32 version, u32 count, u32 stride, data)
Vertices are 32 bytes (FH1: 28): bytes 0-23 identical, then 4 x i16 lighting terms (FH1: 4 x u8).
Header skip logic follows FM4CarModelTools (fm4carbin/parser.py).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from fh1_carbin import CarbinError, Pool, Section, Stream, Sub, _R

BE = struct.Struct(">I")


@dataclass
class FM4Carbin:
    type_id: int
    header: bytes
    sections: list
    footer: bytes


def _header_len(d: bytes) -> int:
    r = _R(d, 0)
    t = r.u32()
    if t == 1:
        r.p += 0x35C; n = r.u32(); r.p += n * 4 + 8
    elif t == 2:
        r.p += 0x15C; n = r.u32(); r.p += 4 + n * 0x8C + 0x340
        for _ in range(r.u32() * 2):
            k = r.u32(); r.p += k * 4
        r.p += 4
    elif t == 3:
        r.p += 0x398
        for _ in range(r.u32() * 2):
            k = r.u32(); r.p += k * 4
        r.p += 4
    else:
        raise CarbinError(f"unknown FM4 carbin type {t}")
    return r.p


def _pool(r: _R) -> Pool:
    count = max(r.i32(), 0)
    stride = r.u32()
    return Pool(count, stride, [], r.raw(count * stride))


def _sub(r: _R) -> Sub:
    raw5 = r.raw(5); uvxf = r.f32s(8); raw36 = r.raw(36)
    name = r.raw(r.u32()).decode("latin-1")
    lod, itype = r.u32(), r.u32()
    raw56 = r.raw(56); rawver = r.raw(4)
    count, size = r.u32(), r.u32()
    idx = np.frombuffer(r.raw(count * size), ">u2" if size == 2 else ">u4").copy()
    return Sub(raw5, uvxf, raw36, name, lod, itype, raw56, rawver, size, idx, r.raw(4))


def _section(r: _R) -> Section:
    marker = r.u32()
    if marker not in (2, 5):
        raise CarbinError(f"bad marker {marker}")
    fraw = r.raw(36); vals = list(struct.unpack(">9f", fraw))
    raw28 = r.raw(28)
    pc = r.u32(); perms = BE.pack(pc) + r.raw(pc * 16)
    raw4a = r.raw(4)
    c2 = r.u32(); cnt2 = BE.pack(c2) + r.raw(c2 * 2)
    raw12 = r.raw(12)
    name = r.raw(r.u8()).decode("latin-1")
    version = r.u32()
    lod = _pool(r)
    raw4b = r.raw(4)
    subs = [_sub(r) for _ in range(r.u32())]
    raw4c = r.raw(4)
    lod0 = _pool(r)
    tail5 = r.raw(5)
    streams = []
    for _ in range(2):
        v, c, s = r.u32(), r.u32(), r.u32()
        streams.append(Stream(v, c, s, [], r.raw(c * s)))
    sec = Section(marker, vals[0:3], vals[3:6], vals[6:9], raw28, perms, raw4a, cnt2, raw12, name, version,
                  lod, raw4b, subs, raw4c, lod0, tail5, streams, b"")
    sec.fraw = fraw
    return sec


def _try(data: bytes, hl: int):
    n = BE.unpack_from(data, hl)[0]
    if not 1 <= n <= 255:
        raise CarbinError("bad part count")
    r = _R(data, hl + 4)
    secs = [_section(r) for _ in range(n)]
    for s in secs:
        if not s.name or not all(32 < ord(ch) < 127 for ch in s.name):
            raise CarbinError("bad section name")
    return secs, r.p


def parse(data: bytes) -> FM4Carbin:
    t = BE.unpack_from(data, 0)[0]
    cands = []
    try:
        cands.append(_header_len(data))
    except Exception:                                  # noqa: BLE001
        pass
    cands += list(range(8, min(len(data) - 64, 0x4000), 4))   # fallback: scan for the part table
    for hl in cands:
        try:
            secs, end = _try(data, hl)
        except Exception:                              # noqa: BLE001
            continue
        return FM4Carbin(t, data[:hl], secs, data[end:])
    raise CarbinError("no section table found")


def _w_sub(s: Sub) -> bytes:
    nm = s.name.encode("latin-1")
    idx = np.asarray(s.indices, ">u2" if s.index_size == 2 else ">u4").tobytes()
    return (s.raw5 + struct.pack(">8f", *s.uvxf) + s.raw36 + BE.pack(len(nm)) + nm
            + struct.pack(">II", s.lod, s.index_type) + s.raw56 + s.rawver
            + struct.pack(">II", len(s.indices), s.index_size) + idx + s.raw4)


def build(c: FM4Carbin) -> bytes:
    out = [c.header, BE.pack(len(c.sections))]
    for s in c.sections:
        nm = s.name.encode("latin-1")
        out += [BE.pack(s.marker), s.fraw, s.raw28, s.perms, s.raw4a, s.cnt2, s.raw12, bytes([len(nm)]), nm,
                BE.pack(s.version), struct.pack(">iI", s.lod.count, s.lod.stride), s.lod.data, s.raw4b,
                BE.pack(len(s.subs))]
        out += [_w_sub(x) for x in s.subs]
        out += [s.raw4c, struct.pack(">iI", s.lod0.count, s.lod0.stride), s.lod0.data, s.tail5]
        for st in s.streams:
            out.append(struct.pack(">III", st.version, st.count, st.stride) + st.data)
    out.append(c.footer)
    return b"".join(out)
