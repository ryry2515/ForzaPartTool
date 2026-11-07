"""Reader for the .modelbin models of modern Forza games (Forza Horizon 5/6 and the Forza Motorsport 2023 family).

A .modelbin is a "Grub" bundle (little endian, magic `burG`): a tag table, then the tag payloads. The tags used here:
  Mesh  one drawable (material, LOD mask, bone, index range, vertex streams, position scale and translate)
  VerB  vertex buffer   IndB  index buffer   VLay  vertex layout   MatI  material names   Skel  bones
Two Mesh record versions have been seen (1.12 and 1.9); they hold the same information at different offsets.
Every mesh decoded here is checked against the bounding box stored in the file (`Mesh.bbox_error`).

`to_obj` turns the sharpest LOD into OBJ text in FH1 coordinates, which is what the editor's "Custom model" import reads.
Experimental: checked on car parts of FH6 and on a standalone part (a drag chute); other games' files may use record versions that are not known yet.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

# LOD mask bits of a Mesh record: bit0 = shadow/LODS, bit1 = LOD0 (the sharpest), bit2 = LOD1 ...
LOD_BIT = {0: 1 << 1, 1: 1 << 2, 2: 1 << 3, 3: 1 << 4, 4: 1 << 5}

_DXGI_SIZE = {6: 12, 10: 8, 13: 8, 24: 4, 28: 4, 35: 4, 37: 4, 2: 16, 16: 8, 41: 4}

# byte offsets inside a Mesh record, by record version: IndB id, first index, base vertex, index count, vertex-buffer entry size
_MESH_LAYOUT = {(1, 12): dict(ind=26, start=34, base=38, count=42, vb_entry=20),
                (1, 9): dict(ind=23, start=31, base=35, count=39, vb_entry=16)}


class ModelbinError(ValueError):
    pass

@dataclass
class Tag:
    name: str
    ver: tuple
    meta_count: int
    meta_off: int
    data_off: int
    comp_size: int
    size: int


class Bundle:
    """A parsed .modelbin."""

    def __init__(self, data: bytes):
        if len(data) < 0x14 or data[:4] != b"burG":
            raise ModelbinError("not a .modelbin model file (missing the 'burG' header)")
        _, self.vmaj, self.vmin, _, self.header_size, self.total_size, count = struct.unpack_from("<4sBBHIII", data, 0)
        self.data = data
        self.tags = []
        for i in range(count):
            t, ma, mi, nm, mo, do, cs, us = struct.unpack_from("<4sBBHIIII", data, 0x14 + i * 0x18)
            self.tags.append(Tag(t[::-1].decode("ascii"), (ma, mi), nm, mo, do, cs, us))

    def find(self, name: str) -> list[Tag]:
        return [t for t in self.tags if t.name == name]

    def meta(self, tag: Tag) -> dict:
        out = {}
        for i in range(tag.meta_count):
            p = tag.meta_off + i * 8
            t, sv, off = struct.unpack_from("<4sHH", self.data, p)
            out[t[::-1].decode("ascii").strip()] = self.data[p + off:p + off + (sv >> 4)]
        return out

    def tag_id(self, tag: Tag) -> int:
        raw = self.meta(tag).get("Id")
        return struct.unpack("<i", raw)[0] if raw else 0

    def payload(self, tag: Tag) -> bytes:
        if tag.comp_size != tag.size:
            raise ModelbinError(f"compressed {tag.name} data is not supported")
        return self.data[tag.data_off:tag.data_off + tag.size]


def parse_vlay(raw: bytes) -> list:
    """-> [(semantic+index, slot, dxgi format, byte offset inside the slot's vertex)]"""
    p = 0
    nnames, = struct.unpack_from("<H", raw, p); p += 2
    names = []
    for _ in range(nnames):
        ln, = struct.unpack_from("<I", raw, p); p += 4
        names.append(raw[p:p + ln].decode("ascii")); p += ln
    nel, = struct.unpack_from("<H", raw, p); p += 2
    els = []
    for _ in range(nel):
        ni, si, slot, _pad, fmt = struct.unpack_from("<HHHHI", raw, p); p += 20
        els.append((f"{names[ni]}{si}", slot, fmt))
    out, offs = [], {}
    for nm, slot, fmt in els:
        o = offs.get(slot, 0)
        out.append((nm, slot, fmt, o))
        offs[slot] = o + _DXGI_SIZE[fmt]
    return out


def parse_skeleton(raw: bytes) -> list:
    """World matrices (row-vector convention) of every bone."""
    n, = struct.unpack_from("<H", raw, 0)
    p, parents, local = 2, [], []
    for _ in range(n):
        ln, = struct.unpack_from("<I", raw, p); p += 4 + ln
        parent, _child, _sib = struct.unpack_from("<hhh", raw, p); p += 6
        local.append(np.array(struct.unpack_from("<16f", raw, p), np.float64).reshape(4, 4)); p += 64
        parents.append(parent)
    world = []
    for i in range(n):
        world.append(local[i] if parents[i] < 0 else local[i] @ world[parents[i]])
    return world


@dataclass
class Mesh:
    part: str                 # file stem, e.g. "bumperFSideMarkers_a"
    name: str                 # the mesh's own name, e.g. "bumperFSideMarkerL_a_LOD1"
    material: str
    lod_mask: int
    bone: int
    positions: np.ndarray     # (N,3) float32, source model space (car body front is +Z; library rims are local)
    normals: np.ndarray
    uvs: np.ndarray
    indices: np.ndarray       # (T,3)
    bbox_error: float = 0.0   # largest difference (m) from the bounding box stored in the file, before the bone transform
    extra: dict = field(default_factory=dict)


def decode_meshes(bundle: Bundle, lod_bit: int, part: str, with_shadow: bool = False) -> list:
    """Every Mesh whose LOD mask contains `lod_bit` (see LOD_BIT). Shadow-only meshes (no material) are skipped."""
    vlays = {bundle.tag_id(t): parse_vlay(bundle.payload(t)) for t in bundle.find("VLay")}
    bufs = {}
    for t in bundle.find("VerB") + bundle.find("IndB"):
        raw = bundle.payload(t)
        cnt, size, stride = struct.unpack_from("<IIH", raw)
        bufs[(t.name, bundle.tag_id(t))] = (raw[16:16 + size], cnt, stride)
    mats = [bundle.meta(t).get("Name", b"").decode("ascii", "replace") for t in bundle.find("MatI")]
    skel = parse_skeleton(bundle.payload(bundle.find("Skel")[0])) if bundle.find("Skel") else []

    out = []
    for t in bundle.find("Mesh"):
        lay = _MESH_LAYOUT.get(t.ver)
        if lay is None:
            raise ModelbinError(f"Mesh record version {t.ver[0]}.{t.ver[1]} is not known")
        m = bundle.payload(t)
        mat, = struct.unpack_from("<h", m, 2)
        lods, = struct.unpack_from("<H", m, 10)
        if not lods & lod_bit or (mat < 0 and not with_shadow):
            continue
        ib_id, = struct.unpack_from("<i", m, lay["ind"])
        start, base, icount = struct.unpack_from("<iiI", m, lay["start"])
        entry = lay["vb_entry"]
        tail = len(m) - 32 - 80 - 8 - 8
        nvb = next((n for n in range(1, 9) if tail - n * entry - 4 >= 0 and struct.unpack_from("<I", m, tail - n * entry - 4)[0] == n), None)
        if nvb is None:
            raise ModelbinError(f"{part}: cannot locate the vertex buffer list of a version {t.ver} mesh")
        q = tail - nvb * entry - 4
        layout_id, = struct.unpack_from("<i", m, q - 4)
        vbrefs = [struct.unpack_from("<iiii", m, q + 4 + i * entry) for i in range(nvb)]
        uvx = struct.unpack_from("<4f", m, len(m) - 32 - 80)
        scale = np.array(struct.unpack_from("<3f", m, len(m) - 32), np.float32)
        trans = np.array(struct.unpack_from("<3f", m, len(m) - 16), np.float32)

        idata, _, istride = bufs[("IndB", ib_id)]
        idx = np.frombuffer(idata, "<u4" if istride == 4 else "<u2", icount, start * istride).astype(np.int64) + base
        used = np.unique(idx)
        if not len(used) or int(used.max()) - min(int(used.min()), 0) > (1 << 24):
            raise ModelbinError(f"{part}: a mesh refers to an impossible vertex range (damaged or unsupported file)")
        lo = min(int(used.min()), 0)
        remap = np.full(int(used.max()) + 1 - lo, -1, np.int64)
        remap[used - lo] = np.arange(len(used))

        streams = {}
        for vb_id, slot, stride, off in vbrefs:
            data, cnt, _ = bufs[("VerB", vb_id)]
            arr = np.frombuffer(data, np.uint8).reshape(cnt, stride)
            streams[slot] = arr[used + off // stride, :]
        layout = {nm: (slot, fmt, o) for nm, slot, fmt, o in vlays[layout_id]}

        def field_of(nm, dt, n):
            slot, _fmt, o = layout[nm]
            raw = np.ascontiguousarray(streams[slot][:, o:o + np.dtype(dt).itemsize * n])
            return raw.view(dt).reshape(-1, n)

        pos4 = field_of("POSITION0", "<i2", 4).astype(np.float32) / 32767.0
        local = pos4[:, :3] * scale + trans
        if "NORMAL0" in layout and layout["NORMAL0"][1] == 37:
            nxy = field_of("NORMAL0", "<i2", 2).astype(np.float32) / 32767.0
            nrm = np.column_stack([pos4[:, 3], nxy])
        else:
            nrm = np.tile([0, 1, 0], (len(local), 1)).astype(np.float32)
        pos = local
        bone, = struct.unpack_from("<h", m, 8)
        if 0 <= bone < len(skel):                           # rigid parts (doors, mirrors) are stored bone-local
            M = skel[bone]
            pos = pos @ M[:3, :3] + M[3, :3]
            nrm = nrm @ M[:3, :3]
        nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
        if "TEXCOORD0" in layout:
            uv = field_of("TEXCOORD0", "<u2", 2).astype(np.float32) / 65535.0
            uv = np.column_stack([uv[:, 0] * uvx[1] + uvx[0], uv[:, 1] * uvx[3] + uvx[2]])
        else:
            uv = np.zeros((len(pos), 2), np.float32)
        meta = bundle.meta(t)
        error = 0.0
        box = meta.get("BBox")
        if box and len(box) >= 24:
            b = np.frombuffer(box[:24], "<f4")
            # the stored box is either in the mesh's own space or in car space (after the bone transform): the better fit counts
            error = float(min(max(np.abs(c.min(0) - b[:3]).max(), np.abs(c.max(0) - b[3:6]).max()) for c in (local, pos)))
        out.append(Mesh(part=part, name=meta.get("Name", b"").decode("ascii", "replace").split("|")[0],
                        material=mats[mat] if 0 <= mat < len(mats) else "", lod_mask=lods, bone=bone,
                        positions=pos.astype(np.float32), normals=nrm.astype(np.float32), uvs=uv.astype(np.float32),
                        indices=remap[idx - lo].reshape(-1, 3).astype(np.uint32), bbox_error=error,
                        extra={'position_scale': scale.tolist(),
                               'stored_bbox': np.frombuffer(box[:24], '<f4').tolist() if box and len(box) >= 24 else None}))
    return out


def bbox_audit(meshes, tolerance: float = .001) -> dict:
    """Separate archive-authored zero-scale contradictions from unresolved box mismatches.

    A stored box is an audit reference, not permission to stretch edited geometry
    to fit it. Other mismatches remain unresolved; they are not called decode errors.
    """
    meshes = list(meshes)
    mismatches = [m for m in meshes if m.bbox_error > tolerance]
    zero = [m for m in mismatches if m.extra.get('position_scale') is not None and
            not np.any(np.asarray(m.extra['position_scale']))]
    return {'meshes': len(meshes), 'mismatches': len(mismatches),
            'zero_scale_mismatches': len(zero), 'unresolved_mismatches': len(mismatches) - len(zero),
            'max_error': max((m.bbox_error for m in meshes), default=0.0)}


def to_fh1(mesh: Mesh) -> Mesh:
    """Modern Forza has the car's front at +z, FH1 at -z (x stays). That is a mirror, so the triangle winding flips too."""
    flip = np.array([1.0, 1.0, -1.0], np.float32)
    return Mesh(mesh.part, mesh.name, mesh.material, mesh.lod_mask, mesh.bone, mesh.positions * flip, mesh.normals * flip,
                mesh.uvs, mesh.indices[:, [0, 2, 1]].copy(), mesh.bbox_error, mesh.extra)


def load(data: bytes, lod: int = 0, part: str = "model") -> list:
    """Meshes of one LOD (0 = sharpest) of a .modelbin, in FH1 coordinates."""
    if lod not in LOD_BIT:
        raise ModelbinError("lod must be 0 to 4")
    try:
        meshes = decode_meshes(Bundle(data), LOD_BIT[lod], part)
    except ModelbinError:
        raise
    except (struct.error, IndexError, KeyError, ValueError, MemoryError) as exc:
        raise ModelbinError(f"the file is damaged, truncated or uses a record layout that is not supported yet ({type(exc).__name__}: {exc})") from exc
    if not meshes:
        raise ModelbinError(f"the file has no drawable meshes at LOD{lod}")
    return [to_fh1(m) for m in meshes]


def to_obj(data: bytes, lod: int = 0) -> str:
    """OBJ text (groups per mesh, `usemtl` per material, v/vt/vn) of one LOD, in FH1 coordinates; the V axis is flipped for the OBJ convention."""
    out = ["# converted from a modern Forza .modelbin (experimental)"]
    base = 1
    for m in load(data, lod):
        out.append(f"o {m.name or m.part}")
        out.append(f"usemtl {m.material or 'material'}")
        out.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in m.positions)
        out.extend(f"vt {u:.6f} {1.0 - v:.6f}" for u, v in m.uvs)
        out.extend(f"vn {x:.5f} {y:.5f} {z:.5f}" for x, y, z in m.normals)
        out.extend(f"f {a + base}/{a + base}/{a + base} {b + base}/{b + base}/{b + base} {c + base}/{c + base}/{c + base}"
                   for a, b, c in m.indices)
        base += len(m.positions)
    return "\n".join(out) + "\n"