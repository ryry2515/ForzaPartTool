"""Writer for `.modelbin` models of modern Forza (the layout Forza Horizon 6 uses). EXPERIMENTAL: not tested in the game.

What it writes, and where that comes from:
  * The bundle layout ("Grub" header, tag table, metadata, payloads), the record versions (bundle 1.1, Modl 1.4, Mesh 1.12, VLay 1.1),
    the vertex packing (position SNORM16 with per-mesh scale and translate, normal x kept in the position w, normal y/z SNORM16,
    five UV sets UNORM16, three tangents 10:10:10:2, vertex colour) and the way a model is assembled (a skeleton, a morph name,
    one material record per material, a full and a shadow-only mesh per LOD, one shared index buffer and position buffer) follow the
    open-source ForzaTech Studio by D3FEKT (MIT licence, https://github.com/D3FEKT/ForzaTechStudio), its "Modelbin Creator".
  * Each value was also compared with a real FH6 car part (read through toolkit/modelbin.py); the files written here are checked by
    reading them back with that reader. That proves the file is consistent, not that the game accepts it.

What it does NOT do: it writes no textures. A material is only a name that the game looks up in its own material library
(Media\\cars\\_library\\materials\\...). `fh6_material` guesses a library material from an FH1 material name; the guess can be wrong, and
a path that does not exist in the library is a game error. Pass `material_map` to choose exactly, or `donor_materials` (see
`donor_materials`) to reuse the real material records of a modelbin you own.

Coordinates in and out: FH1 (car front is -z). Modern Forza has the front at +z, so z is mirrored and the triangle winding flips.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

# DXGI formats used by the vertex layout
R16G16B16A16_SNORM = 13
R16G16_SNORM = 37
R16G16_UNORM = 35
R10G10B10A2_UNORM = 24
R8G8B8A8_UNORM = 28
R32_UINT = 42

# "packed" format ids stored next to each layout element (values seen in real files)
_PACKED = {R16G16B16A16_SNORM: 49, R16G16_SNORM: 52, R16G16_UNORM: 46, R10G10B10A2_UNORM: 48, R8G8B8A8_UNORM: 22}

SLOT1_STRIDE = 40            # normal 4 + 5 UV sets 20 + 3 tangents 12 + colour 4
LOD_SLOTS = 6                # LOD0 .. LOD5
_LOD_FLAG = {0: 0x03, 1: 0x04, 2: 0x08, 3: 0x10, 4: 0x20, 5: 0x40}
MAX_VERTICES = 1 << 24


class ModelbinWriteError(ValueError):
    pass


# --------------------------------------------------------------------------------------------------- bundle container

@dataclass
class Blob:
    tag: str                                  # four characters, e.g. "Mesh"
    ver: tuple
    data: bytes
    meta: list = field(default_factory=list)  # [(tag, version, bytes)]


def _tag(name: str) -> bytes:
    raw = name.encode("ascii")
    if len(raw) != 4:
        raise ModelbinWriteError(f"tag {name!r} is not four characters")
    return raw[::-1]                          # stored reversed: the uint32 'Mesh' is the bytes 'hseM'


def _pad4(buf: bytearray) -> None:
    buf += bytes(-len(buf) % 4)


def pack_bundle(blobs: list, ver=(1, 1)) -> bytes:
    """Serialise blobs as a Grub bundle (header, one 0x18-byte record per blob, per-blob metadata, 4-byte aligned payloads)."""
    n = len(blobs)
    out = bytearray(0x14 + 0x18 * n)
    meta_at = []
    for b in blobs:
        start = len(out)
        k = len(b.meta)
        out += bytes(8 * k)
        for j, (tag, version, data) in enumerate(b.meta):
            if len(data) >= 4096:
                raise ModelbinWriteError(f"metadata {tag!r} is too large ({len(data)} bytes)")
            at = len(out)
            out += data
            h = start + 8 * j
            if at - h > 0xFFFF:
                raise ModelbinWriteError("metadata offset does not fit 16 bits")
            struct.pack_into("<4sHH", out, h, _tag(tag), (len(data) << 4) | (version & 15), at - h)
        meta_at.append((k, start))
    _pad4(out)
    header_size = len(out)
    for i, b in enumerate(blobs):
        at = len(out)
        out += b.data
        size = len(b.data)
        _pad4(out)
        k, mo = meta_at[i]
        struct.pack_into("<4sBBHIIII", out, 0x14 + 0x18 * i, _tag(b.tag), b.ver[0], b.ver[1], k, mo, at, size, size)
    struct.pack_into("<4sBBHIII", out, 0, b"burG", ver[0], ver[1], 0, header_size, len(out), n)
    return bytes(out)


# --------------------------------------------------------------------------------------------------- small records

def _str32(s: str) -> bytes:
    raw = s.encode("ascii", "replace")
    return struct.pack("<I", len(raw)) + raw


def _str7(s: str) -> bytes:
    raw = s.encode("utf-8")
    n, out = len(raw), bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out) + raw


def skeleton_blob() -> Blob:
    """`<root>` and `root`, identity matrices: enough for a part whose meshes use bone 1, as the real files do."""
    ident = struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    bones = [("<root>", -1, 1, -1), ("root", 0, -1, -1)]
    data = struct.pack("<H", len(bones))
    for name, parent, child, nxt in bones:
        data += _str32(name) + struct.pack("<hhh", parent, child, nxt) + ident
    return Blob("Skel", (1, 0), data + struct.pack("<I", 0))


def morph_blob() -> Blob:
    return Blob("Mrph", (0, 0), struct.pack("<H", 1) + _str32("weight[0]"))


def material_payload(path: str) -> bytes:
    """The nested bundle of a material record: the library path of a .materialbin and an empty parameter table."""
    mati = Blob("MATI", (1, 0), _str7(path), [("Name", 0, path.rsplit("\\", 1)[-1].rsplit(".", 1)[0].encode("ascii", "replace")),
                                             ("ATST", 2, b"\x00\x00")])
    mtpr = Blob("MTPR", (2, 0), b"\x00" + struct.pack("<III", 0, 0, 0))
    return pack_bundle([mati, mtpr])


LIBRARY = "Game:\\Media\\cars\\_library\\materials\\"


def library_path(stem: str) -> str:
    """'_fmnext/plastic/plastic' -> 'Game:\\Media\\cars\\_library\\materials\\_fmnext\\plastic\\plastic.materialbin'."""
    stem = stem.replace("/", "\\").strip("\\")
    return LIBRARY + (stem if stem.lower().endswith(".materialbin") else stem + ".materialbin")


# FH1 material name (or a part of it) -> a material that exists in FH6's library (checked against its Materials.zip). A guess by looks.
_MATERIAL_GUESS = [
    ("blur_lip", "_fmnext/specialcase/wheelblur_lip"), ("blur_rim", "_fmnext/specialcase/wheelblur"),
    ("inner_rim", "_fmnext/wheel/wheelpaint"), ("chrome", "_fmnext/metal/chrome"), ("emblem", "_fmnext/metal/chrome"),
    ("badge", "_fmnext/metal/chrome"), ("rim", "_fmnext/wheel/wheelpaint"), ("tire", "_fmnext/tires/tires_pg"),
    ("textured_reflector", "_fmnext/reflector/reflector"), ("reflector", "_fmnext/reflector/reflector"),
    ("grille", "_fmnext/detail/grille"), ("undercarriage", "_fmnext/specialcase/undercarriage_ch1"),
    ("black_glass", "_fmnext/glass/glass_limotint"), ("detail_glass", "_fmnext/glass/glass_detailed"),
    ("window", "_fmnext/glass/glass"), ("glass", "_fmnext/glass/glass"), ("light", "_fmnext/glass/glass_detailed"),
    ("head", "_fmnext/glass/glass_detailed"), ("indicator", "_fmnext/glass/glass_detailed"), ("fog", "_fmnext/glass/glass_detailed"),
    ("drl", "_fmnext/glass/glass_detailed"), ("numplate", "_fmnext/plastic/plastic"), ("matte_colors", "_fmnext/paint/paintedmetal"),
    ("body", "_fmnext/carpaint_default/carpaint"), ("carbon", "_fmnext/plastic/plastic"),
]
_DEFAULT_MATERIAL = "_fmnext/plastic/plastic"


def fh6_material(fh1_name: str) -> str:
    """The library material (without the folder prefix or extension) that stands in for an FH1 material."""
    low = fh1_name.lower()
    for key, stem in _MATERIAL_GUESS:
        if key in low:
            return stem
    return _DEFAULT_MATERIAL


def donor_materials(modelbin: bytes) -> dict:
    """Material records of a modelbin you own -> {library stem lower-case: the record's payload}; give it to build_modelbin so those
    materials keep their real shader parameters instead of an empty table."""
    import modelbin as reader
    b = reader.Bundle(modelbin)
    out = {}
    for t in b.find("MatI"):
        raw = b.payload(t)
        try:
            inner = reader.Bundle(raw)
            mati = inner.find("MATI")[0]
            p = inner.payload(mati)
            n, shift, i = 0, 0, 0
            while True:
                c = p[i]; i += 1
                n |= (c & 0x7F) << shift
                shift += 7
                if not c & 0x80:
                    break
            path = p[i:i + n].decode("utf-8", "replace")
        except Exception:                                   # a record in some other shape: skip it, it is only a convenience
            continue
        low = path.replace("/", "\\").lower()
        if "_library\\materials\\" in low:
            stem = low.split("_library\\materials\\", 1)[1].rsplit(".", 1)[0].replace("\\", "/")
            out[stem] = raw
    return out


# --------------------------------------------------------------------------------------------------- geometry

@dataclass
class ModelPart:
    """One drawable surface: a material at one LOD, in FH1 coordinates."""
    name: str
    material: str                 # FH1 material name
    lod: int
    pos: np.ndarray               # (N,3)
    nrm: np.ndarray               # (N,3)
    uv: np.ndarray                # (N,2), V as FH1 stores it (the same value the reader returns)
    tris: np.ndarray              # (T,3)


def _vertex_streams(p: ModelPart):
    """-> position stream (N,8 bytes), slot-1 stream (N,40 bytes), index array (T*3,), scale, translate, uv transform, bbox.
    Everything is converted to modern coordinates first (z mirrored, winding flipped)."""
    pos = np.asarray(p.pos, np.float64) * [1, 1, -1]
    nrm = np.asarray(p.nrm, np.float64) * [1, 1, -1]
    tris = np.asarray(p.tris, np.int64)[:, [0, 2, 1]]
    uv = np.nan_to_num(np.asarray(p.uv, np.float64))
    n = len(pos)
    if n == 0 or not len(tris):
        raise ModelbinWriteError(f"{p.name}: no geometry")
    if n >= MAX_VERTICES or tris.max() >= n or tris.min() < 0:
        raise ModelbinWriteError(f"{p.name}: triangle indices outside the vertex list")
    norm_len = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.where(norm_len > 1e-9, nrm / np.maximum(norm_len, 1e-12), [0, 1, 0])

    lo, hi = pos.min(0), pos.max(0)
    center = (lo + hi) / 2
    half = np.maximum((hi - lo) / 2 * 1.02, 1e-3)
    q = np.clip((pos - center) / half, -1, 1)
    pos_stream = np.empty((n, 4), "<i2")
    pos_stream[:, :3] = np.rint(q * 32767)
    pos_stream[:, 3] = np.rint(np.clip(nrm[:, 0], -1, 1) * 32767)

    # tangent from the UVs, in the FH1 frame, then mirrored like everything else
    import carbin_geom as cg
    tan = cg.uv_tangents(np.asarray(p.pos, np.float64), np.asarray(p.nrm, np.float64), uv, np.asarray(p.tris, np.int64)) * [1, 1, -1]
    tan = np.nan_to_num(tan)

    umin, vmin = uv.min(0)
    umax, vmax = uv.max(0)
    if umin >= -1e-6 and vmin >= -1e-6 and umax <= 1 + 1e-6 and vmax <= 1 + 1e-6:
        uvx = (0.0, 1.0, 0.0, 1.0)
    else:                                                       # outside 0..1: store a per-mesh offset and scale, as the real files do
        uvx = (float(umin), float(max(umax - umin, 1e-6)), float(vmin), float(max(vmax - vmin, 1e-6)))
    su = np.clip((uv[:, 0] - uvx[0]) / uvx[1], 0, 1)
    sv = np.clip((uv[:, 1] - uvx[2]) / uvx[3], 0, 1)
    uv16 = np.empty((n, 2), "<u2")
    uv16[:, 0] = np.rint(su * 65535)
    uv16[:, 1] = np.rint(sv * 65535)

    def pack1010102(v):
        x = np.rint(np.clip(v[:, 0] * 0.5 + 0.5, 0, 1) * 1023).astype(np.uint32)
        y = np.rint(np.clip(v[:, 1] * 0.5 + 0.5, 0, 1) * 1023).astype(np.uint32)
        z = np.rint(np.clip(v[:, 2] * 0.5 + 0.5, 0, 1) * 1023).astype(np.uint32)
        return x | (y << 10) | (z << 20) | (np.uint32(3) << 30)   # w = +1: right-handed tangent frame

    s1 = np.zeros(n, [("n", "<i2", 2), ("uv", "<u2", (5, 2)), ("t", "<u4", 3), ("c", "<u4")])
    s1["n"] = np.rint(np.clip(nrm[:, 1:3], -1, 1) * 32767)
    s1["uv"] = uv16[:, None, :]
    s1["t"] = pack1010102(tan)[:, None]
    s1["c"] = 0xFFFFFFFF
    assert s1.dtype.itemsize == SLOT1_STRIDE
    scale = np.append(half, 1.0)
    trans = np.append(center, 0.0)
    return pos_stream.tobytes(), s1.tobytes(), tris.reshape(-1).astype("<u4"), scale, trans, uvx, (lo, hi)


def _vlay_blob(ident: int, full: bool) -> Blob:
    names = ["POSITION", "NORMAL", "TEXCOORD", "TANGENT", "COLOR"] if full else ["POSITION"]
    els = [(0, 0, 0, R16G16B16A16_SNORM)]
    if full:
        els.append((1, 0, 1, R16G16_SNORM))
        els += [(2, i, 1, R16G16_UNORM) for i in range(5)]
        els += [(3, i, 1, R10G10B10A2_UNORM) for i in range(3)]
        els.append((4, 0, 1, R8G8B8A8_UNORM))
    data = struct.pack("<H", len(names)) + b"".join(_str32(s) for s in names) + struct.pack("<H", len(els))
    for ni, si, slot, fmt in els:
        data += struct.pack("<hhhhiii", ni, si, slot, 0, fmt, -1, 0)
    data += b"".join(struct.pack("<i", _PACKED[fmt]) for *_, fmt in els)
    data += struct.pack("<I", 0x4FF if full else 0)
    return Blob("VLay", (1, 1), data, [("Id  ", 0, struct.pack("<I", ident))])


def _buffer_blob(tag: str, ident: int, count: int, stride: int, fmt: int, data: bytes) -> Blob:
    head = struct.pack("<iiHBBi", count, len(data), stride, 1, 0, fmt)
    return Blob(tag, (1, 1) if tag == "VerB" else (1, 0), head + data, [("Id  ", 0, struct.pack("<I", ident))])


def _mesh_blob(name: str, material: int, lod: int, shadow: bool, ib_draw: int, index_count: int, vcount: int,
               pos_offset: int, norm_vb: int, uvx, scale, trans, bbox) -> Blob:
    lo, hi = bbox
    if shadow:
        bucket = 0x01 | 0x08                                  # opaque + shadow
    else:
        bucket = 0x01 | 0x10                                  # opaque + not-shadow
    r = struct.pack("<hhhh", -1, -1 if shadow else material, -1, -1)
    r += struct.pack("<hH", 1, _LOD_FLAG[lod])
    r += struct.pack("<BBHBBIBB", 0, 255, bucket, 0, 0, 1, 1, 1)         # lod bytes, buckets, order, skin elements, morph targets, morph damage, 32-bit
    r += struct.pack("<H", 4)                                              # triangle list
    r += struct.pack("<iiiiii", 0, 0, ib_draw, 0, index_count, index_count // 3)
    r += struct.pack("<fII", 0.65, vcount, 0)                              # ACMR, referenced vertices, referenced-vertex list (empty)
    r += struct.pack("<i", 1 if shadow else 0)                             # vertex layout id
    vbs = [(1, 0, 8, pos_offset)] + ([] if shadow else [(norm_vb, 1, SLOT1_STRIDE, 0)])
    r += struct.pack("<i", len(vbs)) + b"".join(struct.pack("<iIIII", i, s, st, off, 0) for i, s, st, off in vbs)
    r += struct.pack("<iiiI", -1, -1, 0, 0)                                # morph buffer, skin buffer, no constant buffers, source mesh
    r += struct.pack("<20f", *([0, 1, 0, 1] if shadow else uvx), *([0, 1, 0, 1] * 4))
    r += struct.pack("<8f", *scale, *trans)
    meta = [("Name", 0, name.encode("ascii", "replace")), ("BBox", 0, struct.pack("<6f", *lo, *hi))]
    return Blob("Mesh", (1, 12), r, meta)


def build_modelbin(parts: list, material_map: dict | None = None, donor: dict | None = None) -> bytes:
    """Parts (ModelPart, FH1 coordinates) -> the bytes of a `.modelbin`.

    Each (material, source LOD) becomes one geometry entry; the six LOD slots use the sharpest geometry that is not sharper than
    the slot (or the nearest sharper one when the model has no such LOD), each with a full mesh and a shadow-only mesh.
    material_map: FH1 material name -> library stem (e.g. '_fmnext/paint/paintedmetal'); donor: see donor_materials."""
    parts = [p for p in parts if len(p.tris)]
    if not parts:
        raise ModelbinWriteError("the model has no triangles")
    by_mat: dict[str, dict[int, ModelPart]] = {}
    for p in parts:
        slot = by_mat.setdefault(p.material, {})
        if p.lod in slot:                                      # two pieces of the same material and LOD: one geometry
            a = slot[p.lod]
            slot[p.lod] = ModelPart(a.name, a.material, a.lod, np.vstack([a.pos, p.pos]), np.vstack([a.nrm, p.nrm]),
                                    np.vstack([a.uv, p.uv]), np.vstack([a.tris, np.asarray(p.tris) + len(a.pos)]))
        else:
            slot[p.lod] = p
    mat_names = list(by_mat)
    stems = [((material_map or {}).get(m) or fh6_material(m)) for m in mat_names]

    # geometry entries
    entries, pos_data, idx_data = {}, bytearray(), []
    pos_count = idx_count = 0
    norm_blobs = []
    for m in mat_names:
        for lod, p in sorted(by_mat[m].items()):
            ps, s1, idx, scale, trans, uvx, bbox = _vertex_streams(p)
            vc = len(ps) // 8
            if len(idx) == 0:
                raise ModelbinWriteError(f"{p.name}: empty index list")
            vb_id = 2 + len(norm_blobs)
            norm_blobs.append(_buffer_blob("VerB", vb_id, vc, SLOT1_STRIDE, R16G16_SNORM, s1))
            entries[(m, lod)] = dict(vb=vb_id, ib=idx_count, icount=len(idx), vcount=vc, pos_off=pos_count * 8,
                                     scale=scale, trans=trans, uvx=uvx, bbox=bbox)
            pos_data += ps
            idx_data.append(idx)
            pos_count += vc
            idx_count += len(idx)

    blobs = [skeleton_blob(), morph_blob()]
    for i, (m, stem) in enumerate(zip(mat_names, stems)):
        payload = (donor or {}).get(stem.lower()) or material_payload(library_path(stem))
        blobs.append(Blob("MatI", (1, 0), payload, [("Name", 0, m.encode("ascii", "replace")), ("Id  ", 0, struct.pack("<I", i))]))

    n_mesh = 0
    lo_all = np.full(3, np.inf)
    hi_all = np.full(3, -np.inf)
    for mi, m in enumerate(mat_names):
        have = sorted(by_mat[m])
        base = by_mat[m][have[0]].name
        for slot in range(LOD_SLOTS):
            src = max((l for l in have if l <= slot), default=have[0])    # sharpest geometry that is not sharper than the slot
            e = entries[(m, src)]
            for shadow in (False, True):
                nm = "Shadow" if shadow else f"{base}_LOD{slot}"
                blobs.append(_mesh_blob(nm, mi, slot, shadow, e["ib"], e["icount"], e["vcount"], e["pos_off"], e["vb"],
                                        e["uvx"], e["scale"], e["trans"], e["bbox"]))
                n_mesh += 1
            lo_all = np.minimum(lo_all, e["bbox"][0])
            hi_all = np.maximum(hi_all, e["bbox"][1])

    blobs.append(_buffer_blob("IndB", 0, idx_count, 4, R32_UINT, np.concatenate(idx_data).astype("<u4").tobytes()))
    blobs += [_vlay_blob(0, True), _vlay_blob(1, False)]
    blobs.append(_buffer_blob("VerB", 1, pos_count, 8, R16G16B16A16_SNORM, bytes(pos_data)))
    blobs += norm_blobs
    modl = struct.pack("<HHHH", n_mesh, 2 + len(norm_blobs), 2, len(mat_names))
    modl += struct.pack("<BbbBHBBBB", 1, 5, -1, 0, 0x7F, 1, 0, 0, 1)
    blobs.append(Blob("Modl", (1, 4), modl, [("BBox", 0, struct.pack("<6f", *lo_all, *hi_all))]))
    return pack_bundle(blobs)


# --------------------------------------------------------------------------------------------------- from a carbin

def parts_from_carbin(carbin) -> dict:
    """{section (part) name: [ModelPart]} for every section that has triangles. UV set as in the OBJ export ('auto')."""
    import carbin_geom as cg
    out = {}
    for sec in carbin.sections:
        pool = cg.active_pool(sec)
        if not pool.count:
            continue
        d = cg.decode_pool(sec, pool)
        for k, sb in enumerate(sec.subs):
            tris = cg.sub_triangles(sb, pool.count)
            if not len(tris):
                continue
            uvs = cg.final_uv(d.uv1, sb.uvxf[4:8]) if cg.is_swatch(sb) else cg.final_uv(d.uv0, sb.uvxf[0:4])
            used = np.unique(tris)
            remap = np.full(used.max() + 1, -1, np.int64)
            remap[used] = np.arange(len(used))
            out.setdefault(sec.name, []).append(ModelPart(sec.name, sb.name, int(sb.lod), d.pos[used], d.nrm[used],
                                                          np.nan_to_num(uvs[used]), remap[tris]))
    return out


def modelbins_from_carbin(carbin, material_map: dict | None = None, donor: dict | None = None) -> dict:
    """{'<part>.modelbin': bytes}; a part that cannot be written is left out and named in the second result."""
    files, failed = {}, {}
    for name, parts in parts_from_carbin(carbin).items():
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "part"
        try:
            files[f"{safe}.modelbin"] = build_modelbin(parts, material_map, donor)
        except ModelbinWriteError as exc:
            failed[name] = str(exc)
    return files, failed
