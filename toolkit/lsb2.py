"""
lsb2.py - read and write Forza .str string tables (LSB2), as used by FH1 and FM4.

Layout (big-endian): "LSB2", u32 0x01000000, u32 0x02000000, u32 0x24 (header size), u32 4, then 4 chunk offsets:
  text   size, count, count x (u16 key, u32 offset in UTF-16 units), sentinel (0xFFFF, end), UTF-16 pool
  names  size, count, count x (u16 key, u16 ordinal, u16 0, u16 byte offset), ASCII identifier pool
  (unused, offset 0)
  order  size, count, count x u16 key in ordinal order
Keys are sorted ascending in the text and names chunks. A "_&N" database reference uses key N & 0xFFFF.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass
class Entry:
    key: int
    text: str
    name: str          # source identifier, e.g. IDS_DisplayName_41
    ordinal: int


def read(data: bytes) -> list[Entry]:
    assert data[:4] == b"LSB2"
    hsize, n = struct.unpack_from(">II", data, 12)
    offs = struct.unpack_from(f">{n}I", data, 20)
    c0, c1 = offs[0], offs[1]
    count = struct.unpack_from(">I", data, c0 + 4)[0]
    ents = [struct.unpack_from(">HI", data, c0 + 8 + 6 * i) for i in range(count)]
    pool = c0 + 8 + 6 * (count + 1)
    text = {}
    for key, off in ents:
        s = e = pool + 2 * off
        while data[e:e + 2] != b"\0\0":
            e += 2
        text[key] = data[s:e].decode("utf-16-be")
    count1 = struct.unpack_from(">I", data, c1 + 4)[0]
    pool1 = c1 + 8 + 8 * count1
    out = []
    for i in range(count1):
        key, ordinal, _, off = struct.unpack_from(">HHHH", data, c1 + 8 + 8 * i)
        o = pool1 + off
        out.append(Entry(key, text.get(key, ""), data[o:data.index(b"\0", o)].decode("ascii"), ordinal))
    return out


def _chunk(body: bytes) -> bytes:
    """size word (counting itself) + body."""
    return struct.pack(">I", 4 + len(body)) + body


def write(entries: list[Entry]) -> bytes:
    ents = sorted(entries, key=lambda e: e.key)
    # text chunk
    pool, offs = bytearray(), []
    for e in ents:
        offs.append(len(pool) // 2)
        pool += e.text.encode("utf-16-be") + b"\0\0"
    body = b"".join(struct.pack(">HI", e.key, o) for e, o in zip(ents, offs))
    body += struct.pack(">HI", 0xFFFF, len(pool) // 2)
    c0 = _chunk(struct.pack(">I", len(ents)) + body + bytes(pool) + b"\0\0")   # pool ends with an empty string
    # names chunk
    npool, noffs = bytearray(), {}
    for e in sorted(ents, key=lambda e: e.ordinal):        # identifiers are pooled in ordinal order
        noffs[e.key] = len(npool)
        npool += e.name.encode("ascii") + b"\0"
    body = b"".join(struct.pack(">HHHH", e.key, e.ordinal, 0, noffs[e.key]) for e in ents)
    c1 = _chunk(struct.pack(">I", len(ents)) + body + bytes(npool))
    # order chunk
    by_ord = sorted(ents, key=lambda e: e.ordinal)
    c2 = _chunk(struct.pack(">I", len(by_ord)) + struct.pack(f">{len(by_ord)}H", *(e.key for e in by_ord)))
    o0 = 0x24
    o1 = o0 + len(c0)
    o2 = o1 + len(c1)
    head = b"LSB2" + struct.pack(">IIII", 0x01000000, 0x02000000, 0x24, 4) + struct.pack(">4I", o0, o1, 0, o2)
    return head + c0 + c1 + c2
