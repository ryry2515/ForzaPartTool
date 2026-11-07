import base64
import zipfile
import struct

import numpy as np
import pytest

from test_part_actions_api import triangle_request
from test_editor_api import editor, open_alfa, server
from test_part_transfer import car_blob
import carbin_geom as cg
import carbin_library as cl
import fh1_carbin as fc


def test_double_sided_survives_fh1_encoding_in_both_models(editor):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    request = dict(triangle_request(target), mode='replace', double_sided=True, auto_lods=False)
    editor('/api/op', request)
    assert len(server.S.staged) == 2
    for raw in server.S.staged.values():
        car = fc.parse(raw)
        section = next(s for s in car.sections if s.name.lower() == 'body')
        pool = cg.active_pool(section)
        data = cg.decode_pool(section, pool)
        for sub in section.subs:
            triangles = cg.sub_triangles(sub, pool.count)
            assert len(triangles) == 2
            points = data.pos[triangles]
            normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
            assert np.dot(normals[0], normals[1]) < 0, 'Back face winding must survive export'
            assert np.dot(data.nrm[triangles[0]].mean(0), data.nrm[triangles[1]].mean(0)) < -0.9
        assert not cg.validate(car)


def test_atomic_zip_replaces_all_requested_members_and_keeps_others(tmp_path):
    path = tmp_path / 'car.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b'original archive'
        archive.writestr('car.carbin', b'old main')
        archive.writestr('car_lod0.carbin', b'old lod0')
        archive.writestr('other.dat', b'untouched payload' * 300)
    original = path.read_bytes()
    result = cl.replace_zip_members(path, {'car.carbin': b'new main', 'car_lod0.carbin': b'new lod0'})
    assert path.with_suffix('.zip.bak').read_bytes() == original
    assert result['members'] == ['car.carbin', 'car_lod0.carbin']
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ['car.carbin', 'car_lod0.carbin', 'other.dat']
        assert archive.comment == b'original archive'
        assert archive.read('car.carbin') == b'new main'
        assert archive.read('car_lod0.carbin') == b'new lod0'
        assert archive.read('other.dat') == b'untouched payload' * 300
    before = path.read_bytes()
    with pytest.raises(KeyError):
        cl.replace_zip_members(path, {'missing.carbin': b'bad'})
    assert path.read_bytes() == before


def test_same_car_save_includes_staging_and_stripped_companions(editor, tmp_path, monkeypatch):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    editor('/api/op', dict(triangle_request(target), double_sided=True))
    origin = server.S.origin
    source_archive = server.origin_zip()
    path = tmp_path / 'same-car.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for info in source_archive.infolist():
            import xlzx
            archive.writestr(info.filename, xlzx.read_member(source_archive, info.filename))
    expected = dict(server.S.staged)
    monkeypatch.setattr(server.S.lib, 'zip_path', lambda *_: path)
    monkeypatch.setattr(cl, 'xenia_running', lambda: False)
    result = editor('/api/library/save', {})
    assert server.S.origin == origin
    assert not server.S.staged
    with zipfile.ZipFile(path) as archive:
        for member, raw in expected.items():
            assert archive.read(member) == raw
            stripped = 'stripped_' + member
            stripped = next(n for n in archive.namelist() if n.lower() == stripped.lower())
            assert archive.read(stripped) == fc.build(fc.parse(raw), stripped=True)
    assert len(result['saved']['members']) >= 2


def test_paired_texture_import_uses_atlas_rectangle_and_paints_once(editor, monkeypatch):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    request = dict(triangle_request(target), mode='replace', auto_lods=False,
                   texture={'rect': [0.2, 0.3, 0.6, 0.8]})
    request['meshes'][0]['uvs'] = base64.b64encode(np.asarray([[0, 0], [1, 0], [0, 1]], dtype='<f4').tobytes()).decode()
    paints = []
    monkeypatch.setattr(server, 'paint_atlases', lambda texture: paints.append(texture))
    editor('/api/op', request)
    assert len(paints) == 1
    for raw in server.S.staged.values():
        model = fc.parse(raw)
        section = next(s for s in model.sections if s.name.lower() == 'body')
        data = cg.decode_pool(section, cg.active_pool(section))
        for sub in section.subs:
            indices = cg.sub_triangles(sub, len(data.pos)).ravel()
            uv = cg.final_uv(data.uv0, sub.uvxf[:4])[indices]
            np.testing.assert_allclose(uv.min(0), [0.2, 0.3], atol=0.001)
            np.testing.assert_allclose(uv.max(0), [0.6, 0.8], atol=0.001)


def test_replacement_zip_keeps_identity_and_original_game_unchanged(editor, tmp_path):
    request, _ = open_alfa(editor)
    original = server.S.lib.read(**{'sid': request['source'], 'archive': request['archive'], 'member': request['member']})
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    editor('/api/op', dict(triangle_request(target), double_sided=True))
    expected = dict(server.S.staged)
    result = editor('/api/library/export_zip', {'out_dir': str(tmp_path)})
    assert server.S.origin == request
    assert server.S.staged == expected, 'Export must retain pending edits for future saves'
    assert server.S.lib.read(request['source'], request['archive'], request['member']) == original
    with zipfile.ZipFile(result['saved']['zip']) as archive:
        for member, raw in expected.items():
            assert archive.read(member) == raw
        source = server.origin_zip()
        def compressed_payload(archive, info):
            archive.fp.seek(info.header_offset)
            header = archive.fp.read(30)
            name_len, extra_len = struct.unpack_from('<HH', header, 26)
            local = archive.fp.read(name_len + extra_len)
            return local[name_len:], archive.fp.read(info.compress_size)
        untouched = [info for info in source.infolist() if info.filename not in result['saved']['members']]
        assert untouched
        for info in untouched:
            copied = archive.getinfo(info.filename)
            assert (copied.compress_type, copied.extra, copied.CRC) == (info.compress_type, info.extra, info.CRC)
            assert compressed_payload(source, info) == compressed_payload(archive, copied)


def test_zip_swap_failure_keeps_original_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'car.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('main.carbin', b'original')
    original = path.read_bytes()
    def fail_swap(*args):
        raise OSError('simulated locked destination')
    monkeypatch.setattr(cl.os, 'replace', fail_swap)
    with pytest.raises(OSError, match='locked'):
        cl.replace_zip_members(path, {'main.carbin': b'edited'})
    assert path.read_bytes() == original
    assert path.with_suffix('.zip.bak').read_bytes() == original
    assert not list(tmp_path.glob('*.new'))


def test_failed_paired_texture_paint_restores_models_and_staging(editor, monkeypatch):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    original = fc.build(server.S.carbin)
    server.S.staged['keep.txt'] = b'keep'
    def fail_paint(texture):
        server.S.staged['nodamage.xds'] = b'incomplete'
        raise ValueError('bad texture')
    monkeypatch.setattr(server, 'paint_atlases', fail_paint)
    with pytest.raises(ValueError, match='bad texture'):
        server.op_add_model(dict(triangle_request(target), texture={'rect': [0, 0, 1, 1]}))
    assert fc.build(server.S.carbin) == original
    assert server.S.staged == {'keep.txt': b'keep'}


def new_part_request(section, name='my_wing'):
    return dict(triangle_request(section), mode='new', name=name, auto_lods=False)


def test_custom_model_as_new_part_stays_selectable_and_movable(editor):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    before = len(server.S.carbin.sections)
    body_before = fc.build(server.S.carbin)
    summary = editor('/api/op', new_part_request(target))
    index = summary['last_pasted']
    assert index == before and len(summary['sections']) == before + 1
    assert summary['sections'][index]['name'] == 'my_wing'
    # the body itself is untouched: the model is a part of its own, not merged into it
    body = fc.parse(body_before).sections[target]
    assert [s.name for s in server.S.carbin.sections][:before] == [s.name for s in fc.parse(body_before).sections]
    assert len(server.S.carbin.sections[target].subs) == len(body.subs)
    # both models (driving + photo) carry the same part under the same name
    assert len(server.S.staged) == 2
    for raw in server.S.staged.values():
        car = fc.parse(raw)
        assert 'my_wing' in [s.name for s in car.sections]
        assert not cg.validate(car)
        part = next(s for s in car.sections if s.name == 'my_wing')
        assert cg.decode_pool(part, cg.active_pool(part)).pos[:, 1].max() > 3
        assert any(si == car.sections.index(part) for si, _ in cg.drawn_at(car))
    # it can be moved again, like any part
    moved = editor('/api/op', {'op': 'transform', 'sections': [index], 'translate': [0, 1, 0]})
    part = server.S.carbin.sections[index]
    assert cg.decode_pool(part, cg.active_pool(part)).pos[:, 1].max() > 4
    assert moved['sections'][index]['name'] == 'my_wing'
    # a second model with the same name does not clash
    again = editor('/api/op', new_part_request(target))
    assert again['sections'][again['last_pasted']]['name'] == 'my_wing_2'
    # undo takes the part away again
    editor('/api/undo', {})
    editor('/api/undo', {})
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == body_before


def test_new_part_survives_save_and_reopen(editor, tmp_path):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    editor('/api/op', new_part_request(target, 'reopen_me'))
    main_member = server.origin_car()[1]
    raw = server.S.staged[main_member]
    reopened = fc.parse(raw)
    names = [s.name for s in reopened.sections]
    assert 'reopen_me' in names
    index = names.index('reopen_me')
    assert not cg.validate(reopened)
    cg.transform_section(reopened, index, cg.trs_matrix(translate=(0, 0, 0.5)), move_pivot=True)      # still movable after a round trip
    assert not cg.validate(fc.parse(fc.build(reopened)))


def test_output_folder_is_remembered(editor, tmp_path):
    open_alfa(editor)
    assert editor('/api/clone/defaults')['out_dir'] == str(server.default_out_dir())
    folder = tmp_path / 'my exports'
    editor('/api/library/export_zip', {'out_dir': str(folder)})
    assert server.S.output_dir == str(folder.resolve())
    assert server.load_settings()['output_dir'] == str(folder.resolve())
    assert editor('/api/clone/defaults')['out_dir'] == str(folder.resolve())
    assert editor('/api/library')['output_dir'] == str(folder.resolve())