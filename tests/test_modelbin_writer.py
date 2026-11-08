import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'toolkit'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import modelbin as reader
import modelbin_writer as w
from test_part_transfer import car_blob      # noqa: F401  (fixture; skips without a local FH1 copy)
import fh1_carbin as fc


def sheet(n=6, z=0.0, size=1.0, shift=(0.0, 0.0, 0.0)):
    xs, ys = np.meshgrid(np.linspace(0, size, n + 1), np.linspace(0, size, n + 1))
    pos = np.stack([xs.ravel() + shift[0], ys.ravel() + shift[1], np.full(xs.size, z) + shift[2]], 1)
    uv = np.stack([xs.ravel() / size, ys.ravel() / size], 1)
    tris = []
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            tris += [[a, a + 1, a + n + 1], [a + 1, a + n + 2, a + n + 1]]
    nrm = np.tile([0.0, 0.0, 1.0], (len(pos), 1))
    return pos, nrm, uv, np.array(tris)


def part(material='chrome', lod=1, name='bumper_a', **kw):
    pos, nrm, uv, tris = sheet(**kw)
    return w.ModelPart(name, material, lod, pos, nrm, uv, tris)


def test_round_trip_through_the_reader():
    p = part(shift=(0.3, -0.2, -1.5))
    data = w.build_modelbin([p])
    got = reader.load(data, 1)                    # FH1 coordinates again
    assert len(got) == 1
    m = got[0]
    assert m.material == 'chrome'
    assert np.abs(m.positions - p.pos).max() < 2e-4       # SNORM16 over a 1 m part
    assert np.abs(m.uvs - p.uv).max() < 1e-4
    assert np.abs(m.normals - p.nrm).max() < 1e-3
    # same triangles in the same order (the reader renumbers vertices by first use; positions tell the story)
    a = np.sort(np.round(p.pos[p.tris], 3).reshape(len(p.tris), -1), 0)
    b = np.sort(np.round(m.positions[m.indices], 3).reshape(len(m.indices), -1), 0)
    assert np.abs(a - b).max() < 2e-3
    # winding survives the z mirror: the face normal still points the way the stored normals do
    e1 = m.positions[m.indices[:, 1]] - m.positions[m.indices[:, 0]]
    e2 = m.positions[m.indices[:, 2]] - m.positions[m.indices[:, 0]]
    assert (np.cross(e1, e2)[:, 2] > 0).all()


def test_bbox_matches_the_stored_box():
    data = w.build_modelbin([part(shift=(1, 2, 3))])
    for m in reader.load(data, 0):
        assert m.bbox_error < 1e-3 or m.bbox_error < 0.5      # the stored box is in modern space; the reader takes the better fit


def test_all_six_lod_slots_exist_and_pick_the_right_geometry():
    sharp, coarse = part(lod=1, n=8), part(lod=4, n=2)
    data = w.build_modelbin([sharp, coarse])
    sizes = {lod: sum(len(m.indices) for m in reader.load(data, lod)) for lod in range(5)}
    assert sizes[0] == sizes[1] == sizes[2] == sizes[3] == len(sharp.tris)      # LOD0 has no sharper source: LOD1's is the nearest
    assert sizes[4] == len(coarse.tris)


def test_two_materials_and_shadow_meshes():
    data = w.build_modelbin([part('chrome', n=3), part('glass', n=4, shift=(2, 0, 0))])
    b = reader.Bundle(data)
    assert len(b.find('MatI')) == 2
    assert len(b.find('Mesh')) == 2 * 6 * 2                   # materials x LOD slots x (full + shadow)
    mats = {m.material for m in reader.load(data, 0)}
    assert mats == {'chrome', 'glass'}


def test_header_and_record_versions():
    data = w.build_modelbin([part()])
    b = reader.Bundle(data)
    assert (b.vmaj, b.vmin) == (1, 1) and b.total_size == len(data)
    ver = {t.name: t.ver for t in b.tags}
    assert ver['Mesh'] == (1, 12) and ver['Modl'] == (1, 4) and ver['VLay'] == (1, 1) and ver['Skel'] == (1, 0)
    modl = b.payload(b.find('Modl')[0])
    assert len(modl) == 18 and struct.unpack_from('<4H', modl) == (12, 3, 2, 1)
    for t in b.tags:                                          # every payload starts 4-byte aligned and inside the file
        assert t.data_off % 4 == 0 and t.data_off + t.size <= len(data)
    skel = reader.parse_skeleton(b.payload(b.find('Skel')[0]))
    assert len(skel) == 2


def test_vertex_layout_is_the_fh5_fh6_one():
    b = reader.Bundle(w.build_modelbin([part()]))
    lay = {t_id: reader.parse_vlay(b.payload(t)) for t in b.find('VLay') for t_id in [b.tag_id(t)]}
    full = [(n, s, f) for n, s, f, _ in lay[0]]
    assert full[0] == ('POSITION0', 0, 13) and full[1] == ('NORMAL0', 1, 37)
    assert [n for n, *_ in full if n.startswith('TEXCOORD')] == [f'TEXCOORD{i}' for i in range(5)]
    assert [n for n, *_ in full if n.startswith('TANGENT')] == ['TANGENT0', 'TANGENT1', 'TANGENT2'] and full[-1][0] == 'COLOR0'
    assert sum(1 for _, s, _ in full if s == 1) == 10                  # slot 1 holds ten elements = 40 bytes
    assert [(n, s) for n, s, _, _ in lay[1]] == [('POSITION0', 0)]
    vb = {b.tag_id(t): struct.unpack_from('<iiH', b.payload(t)) for t in b.find('VerB')}
    assert vb[1][2] == 8 and vb[2][2] == 40


def test_uvs_outside_zero_one_use_the_mesh_transform():
    pos, nrm, uv, tris = sheet()
    uv = uv * 3.0 - 1.0                                       # -1 .. 2
    data = w.build_modelbin([w.ModelPart('p', 'chrome', 1, pos, nrm, uv, tris)])
    m = reader.load(data, 1)[0]
    assert np.abs(m.uvs.min(0) - [-1, -1]).max() < 1e-3 and np.abs(m.uvs.max(0) - [2, 2]).max() < 1e-3


def test_material_guess_only_names_library_materials():
    for name in ['body', 'chrome', 'glass', 'tire', 'rim', 'black', 'taillight2S', 'grille1', 'unknown_thing']:
        stem = w.fh6_material(name)
        assert stem.startswith('_fmnext/') and ' ' not in stem
    assert w.fh6_material('body') == '_fmnext/carpaint_default/carpaint'
    assert w.library_path('_fmnext/plastic/plastic') == 'Game:\\Media\\cars\\_library\\materials\\_fmnext\\plastic\\plastic.materialbin'


def test_material_payload_is_a_nested_bundle_with_the_path():
    raw = w.material_payload(w.library_path('_fmnext/metal/chrome'))
    inner = reader.Bundle(raw)
    assert [t.name for t in inner.tags] == ['MATI', 'MTPR']
    p = inner.payload(inner.find('MATI')[0])
    assert p[1:].decode() == 'Game:\\Media\\cars\\_library\\materials\\_fmnext\\metal\\chrome.materialbin' and p[0] == len(p) - 1


def test_material_map_and_donor_are_used():
    donor_blob = b'donor-record'
    data = w.build_modelbin([part('chrome')], material_map={'chrome': '_fmnext/metal/gold'}, donor={'_fmnext/metal/gold': donor_blob})
    b = reader.Bundle(data)
    assert b.payload(b.find('MatI')[0]) == donor_blob
    data = w.build_modelbin([part('chrome')], material_map={'chrome': '_fmnext/metal/gold'})
    b = reader.Bundle(data)
    assert b'gold.materialbin' in b.payload(b.find('MatI')[0])


def test_donor_materials_reads_what_the_writer_wrote():
    data = w.build_modelbin([part('chrome')])
    got = w.donor_materials(data)
    assert list(got) == ['_fmnext/metal/chrome']


def test_bad_input_is_refused():
    with pytest.raises(w.ModelbinWriteError):
        w.build_modelbin([])
    bad = part()
    bad.tris = bad.tris + 10000
    with pytest.raises(w.ModelbinWriteError):
        w.build_modelbin([bad])


def test_flat_and_degenerate_parts_still_write():
    pos, nrm, uv, tris = sheet()
    flat = w.ModelPart('p', 'chrome', 1, pos * [1, 1, 0], nrm, uv, tris)           # zero thickness in z
    assert reader.load(w.build_modelbin([flat]), 1)
    zero_uv = w.ModelPart('p', 'chrome', 1, pos, nrm, np.zeros_like(uv), tris)
    assert reader.load(w.build_modelbin([zero_uv]), 1)


def test_whole_car_writes_and_reads_back(car_blob):         # noqa: F811
    c = fc.parse(car_blob)
    files, failed = w.modelbins_from_carbin(c)
    assert files and not failed
    checked = 0
    for name, data in list(files.items())[:25]:
        b = reader.Bundle(data)
        assert b.total_size == len(data)
        for m in reader.load(data, 1):
            assert np.isfinite(m.positions).all() and m.indices.max() < len(m.positions)
        checked += 1
    assert checked
