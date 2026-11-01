"""physdef.py - read and edit a Forza Horizon 1 car's physics files: `physicsdefinition.bin` (what the game loads) and `Physics/MAXData.xml` (its readable cousin).

physicsdefinition.bin (big-endian; measured on all 178 base cars, guide section 40):
    u32 version (1), u32 13, u32 1
    30 floats   header: [0] = 0.01, [1:10] a symmetric 3x3 and [10:19] a second 3x3 (inertia-like), [19:30] eleven more floats (meaning not decoded)
    u32 n       number of collision spheres, then u32 0
    n x 22 B    n - 1 spheres, each: u16 flags (2 = body sphere, 1 = small corner sphere, 0/3 occur), f32 x, y, z, radius, u32 0; the last of the n records is not a sphere
                (it is the start of the table below) and is kept as it is
    tail        opaque: a block of zeros, a table of offsets (relative to 18 bytes before the end of the n records, so they survive a change of n),
                the convex hull (usually 24 points of 4 floats x, y, z, 0), face and index tables
Everything this module changes keeps the tail byte for byte; it edits the header floats, the spheres and the hull points.

Coordinates: the physics files use +z = front like the light records (guide section 8); carbin meshes use -z = front.

    pd = parse(data)
    pd.spheres[0].r *= 1.2
    new_bytes = pd.build()
"""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

SPHERE = struct.Struct(">H4fI")           # flags, x, y, z, r, zero
HEAD = 0x0C                              # first header float
COUNT_AT = 0x84
SPHERES_AT = 0x8C


@dataclass
class Sphere:
    flags: int
    x: float
    y: float
    z: float
    r: float
    tail: int = 0


@dataclass
class PhysDef:
    raw: bytes
    header: list[float]
    spheres: list[Sphere]
    hull_at: int | None                   # file offset of the first hull point (None: no hull found)
    hull: list[list[float]] = field(default_factory=list)   # [x, y, z, w]
    version: tuple = (1, 13, 1)

    def build(self) -> bytes:
        """The file with the edited header floats, spheres and hull points. The tail keeps its bytes (and moves if the number of spheres changed)."""
        old_n = struct.unpack_from(">I", self.raw, COUNT_AT)[0]
        old_end = SPHERES_AT + SPHERE.size * old_n
        out = bytearray(self.raw[:SPHERES_AT])
        struct.pack_into(">30f", out, HEAD, *self.header)
        struct.pack_into(">I", out, COUNT_AT, len(self.spheres) + 1)
        for s in self.spheres:
            out += SPHERE.pack(s.flags, s.x, s.y, s.z, s.r, s.tail)
        out += self.raw[old_end - SPHERE.size:old_end]                 # the n-th record: start of the offset table
        tail = bytearray(self.raw[old_end:])
        if self.hull_at is not None:
            at = self.hull_at - old_end
            for i, v in enumerate(self.hull):
                struct.pack_into(">4f", tail, at + 16 * i, *v)
        return bytes(out + tail)


def _hull_at(b: bytes, se: int) -> tuple[int | None, int]:
    """(offset, count) of the hull points. Uses the offset table when it is there (24 points in 124 of 178 cars), otherwise looks for the first run of 16-byte records
    with real xyz and w == 0 after the sphere list."""
    pt = se + 0x3E
    if pt + 24 <= len(b):
        o0 = struct.unpack_from(">I", b, pt)[0]
        cnt = b[pt + 16]
        at = se - 0x12 + o0
        if 0 < cnt <= 64 and 0 < o0 < len(b) and at + 16 * cnt <= len(b) and all(struct.unpack_from(">f", b, at + 16 * i + 12)[0] == 0 for i in range(cnt)):
            return at, cnt
    for p in range(se, len(b) - 16 * 4):
        n = 0
        while p + 16 * (n + 1) <= len(b):
            x, y, z, w = struct.unpack_from(">4f", b, p + 16 * n)
            if not (w == 0 and abs(x) < 8 and abs(y) < 8 and abs(z) < 8 and (abs(x) + abs(y) + abs(z)) > 1e-3 and x == x and y == y and z == z):
                break
            n += 1
        if n >= 6:
            return p, n
    return None, 0


def parse(data: bytes) -> PhysDef:
    if len(data) < SPHERES_AT or struct.unpack_from(">3I", data, 0) != (1, 13, 1):
        raise ValueError("not a Forza Horizon 1 physicsdefinition.bin (expected the header 1, 13, 1, big-endian)")
    n = struct.unpack_from(">I", data, COUNT_AT)[0]
    if n > 200 or SPHERES_AT + SPHERE.size * n > len(data):
        raise ValueError(f"implausible sphere count {n}")
    header = list(struct.unpack_from(">30f", data, HEAD))
    spheres = [Sphere(*SPHERE.unpack_from(data, SPHERES_AT + SPHERE.size * i)) for i in range(max(n - 1, 0))]
    se = SPHERES_AT + SPHERE.size * n
    at, cnt = _hull_at(data, se)
    hull = [list(struct.unpack_from(">4f", data, at + 16 * i)) for i in range(cnt)] if at is not None else []
    return PhysDef(data, header, spheres, at, hull)


# ---------------------------------------------------------------------------------------------- edits
def transform(pd: PhysDef, scale=(1.0, 1.0, 1.0), offset=(0.0, 0.0, 0.0), radius_scale: float | None = None) -> None:
    """Scale then move every sphere and hull point (x, y, z). Radii are multiplied by `radius_scale` (default: the mean of the three scales)."""
    sx, sy, sz = scale
    rs = (sx + sy + sz) / 3.0 if radius_scale is None else radius_scale
    for s in pd.spheres:
        s.x, s.y, s.z, s.r = s.x * sx + offset[0], s.y * sy + offset[1], s.z * sz + offset[2], s.r * rs
    for v in pd.hull:
        v[0], v[1], v[2] = v[0] * sx + offset[0], v[1] * sy + offset[1], v[2] * sz + offset[2]


def bounds(pd: PhysDef) -> dict:
    """Axis-aligned bounds of the spheres (with their radii) and of the hull."""
    out = {}
    if pd.spheres:
        lo = [min(getattr(s, a) - s.r for s in pd.spheres) for a in "xyz"]
        hi = [max(getattr(s, a) + s.r for s in pd.spheres) for a in "xyz"]
        out["spheres"] = (lo, hi)
    if pd.hull:
        out["hull"] = ([min(v[i] for v in pd.hull) for i in range(3)], [max(v[i] for v in pd.hull) for i in range(3)])
    return out


def fit(pd: PhysDef, old_box, new_box) -> tuple[tuple, tuple]:
    """Map the physics shapes from the old bounding box (lo, hi) onto the new one, per axis (scale, then move). -> (scale, offset) that were applied."""
    (olo, ohi), (nlo, nhi) = old_box, new_box
    sc = tuple((nhi[i] - nlo[i]) / (ohi[i] - olo[i]) if ohi[i] - olo[i] > 1e-6 else 1.0 for i in range(3))
    off = tuple(nlo[i] - olo[i] * sc[i] for i in range(3))
    transform(pd, sc, off)
    return sc, off


# ---------------------------------------------------------------------------------------------- MAXData.xml
def read_maxdata(text: str) -> dict:
    """{'misc': {...}, 'bbox': {...}, 'spheres': [{'r','x','y','z','flags'}...], 'offset': {...}} from Physics/MAXData.xml (only some cars list all their spheres)."""
    t = ET.fromstring(text.strip().lstrip("﻿"))
    misc = t.find("Misc")
    bb = t.find("Collision/BoundingBox")
    cs = t.find("Collision/CollSpheres")
    spheres = []
    if cs is not None:
        for e in cs:
            if re.fullmatch(r"Sphere\d+", e.tag):
                spheres.append({"r": float(e.get("Radius")), "x": float(e.get("PosX")), "y": float(e.get("PosY")), "z": float(e.get("PosZ")), "flags": int(e.get("Flags", "0"))})
    return {"misc": dict(misc.attrib) if misc is not None else {}, "bbox": dict(bb.attrib) if bb is not None else {}, "spheres": spheres,
            "offset": dict(cs.attrib) if cs is not None else {}}


def patch_maxdata(text: str, spheres: list[dict] | None = None, bbox: dict | None = None, misc: dict | None = None) -> str:
    """Rewrite values in MAXData.xml in place (text edits, so everything else stays as it was). spheres: new values for Sphere0..; bbox: Min/Max/Bevel attributes; misc: Wheelbase, tracks, ride heights."""
    def setattrs(tag_re: str, values: dict, s: str) -> str:
        m = re.search(tag_re, s)
        if not m:
            return s
        block = m.group(0)
        for k, v in values.items():
            block = re.sub(rf'({k}=")[^"]*(")', lambda mm: f'{mm.group(1)}{v:.6f}{mm.group(2)}', block, count=1)
        return s[:m.start()] + block + s[m.end():]
    if misc:
        text = setattrs(r"<Misc [^>]*>", misc, text)
    if bbox:
        text = setattrs(r"<BoundingBox [^>]*/>", bbox, text)
    for i, sp in enumerate(spheres or []):
        text = setattrs(rf"<Sphere{i} [^>]*/>", {"Radius": sp["r"], "PosX": sp["x"], "PosY": sp["y"], "PosZ": sp["z"]}, text)
    return text


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="describe a physicsdefinition.bin")
    ap.add_argument("file")
    a = ap.parse_args()
    pd = parse(open(a.file, "rb").read())
    print(json.dumps({"spheres": len(pd.spheres), "hull_points": len(pd.hull), "hull_at": pd.hull_at, "bounds": bounds(pd), "header": [round(x, 4) for x in pd.header]}, indent=1))
    assert pd.build() == pd.raw, "round trip failed"
    print("round trip: identical")


if __name__ == "__main__":
    main()
