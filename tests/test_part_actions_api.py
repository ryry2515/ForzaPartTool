import base64
import io
import zipfile
import numpy as np
import pytest

from test_editor_api import editor, open_alfa, server
from test_part_transfer import car_blob
import fh1_carbin as fc
import carbin_geom as cg


def triangle_request(section):
    enc = lambda values, dtype: base64.b64encode(np.asarray(values, dtype=dtype).tobytes()).decode()
    return {'op': 'add_model', 'section': section, 'both_models': True, 'lods': [1],
            'mode': 'add', 'meshes': [{'material': 'body',
                'positions': enc([[0, 3, 0], [0.1, 3, 0], [0, 3.1, 0]], '<f4'),
                'indices': enc([[0, 1, 2]], '<u4')}]}


def test_paired_import_stages_both_models_and_undo_restores_them(editor):
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    before = fc.build(server.S.carbin)
    server.S.staged['keep.txt'] = b'keep'
    editor('/api/op', triangle_request(target))
    staged = dict(server.S.staged)
    main = server.origin_car()[1]
    paired = main[:-7] + '_lod0.carbin'
    assert main in staged and paired in staged
    other = fc.parse(staged[paired])
    assert not cg.validate(other)
    section = next(s for s in other.sections if s.name.lower() == 'body')
    assert any(s.lod == 0 for s in section.subs)
    assert cg.decode_pool(section, cg.active_pool(section)).pos[:, 1].max() > 3
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == before
    assert server.S.staged == {'keep.txt': b'keep'}
    editor('/api/redo', {})
    assert server.S.staged == staged


def test_missing_paired_part_is_added_as_a_new_part_in_the_other_model(editor):
    """The close-up/photo model often lacks a part the driving model has (a hood, say). Adding a model onto that part in both models used to fail
    ("the other model of this car has no part named ..."); the model now goes into the other model as a new part of its own, and a notice says so."""
    open_alfa(editor)
    server.S.carbin.sections[0].name = 'only_in_this_file'
    summary = editor('/api/op', triangle_request(0))
    assert 'only_in_this_file' in summary['notice'] and 'new part' in summary['notice']
    assert not cg.validate(server.S.carbin)
    assert cg.decode_pool(server.S.carbin.sections[0], cg.active_pool(server.S.carbin.sections[0])).pos[:, 1].max() > 3     # added onto the part in the open model
    main = server.origin_car()[1]
    other = fc.parse(server.S.staged[main[:-7] + '_lod0.carbin'])
    assert not cg.validate(other)
    made = next(s for s in other.sections if s.name == 'only_in_this_file_custom')                  # a new part of its own in the other model
    assert cg.decode_pool(made, cg.active_pool(made)).pos[:, 1].max() > 3
    assert not any(s.name == 'only_in_this_file' for s in other.sections)
    editor('/api/undo', {})                                                                          # one undo takes both models back
    assert not server.S.staged or main[:-7] + '_lod0.carbin' not in server.S.staged


def test_replace_in_both_models_with_a_missing_part_says_what_it_did(editor):
    open_alfa(editor)
    server.S.carbin.sections[0].name = 'only_in_this_file'
    request = dict(triangle_request(0), mode='replace')
    summary = editor('/api/op', request)
    assert 'in place of' in summary['notice'] and 'hide or delete' in summary['notice']


def test_several_paired_parts_with_the_same_name_still_refuse(editor):
    import copy
    from pathlib import Path
    open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    paired = server.origin_car()[1][:-7] + '_lod0.carbin'
    archive = server.origin_zip()
    member = server.find_member(archive, str(Path(server.S.origin['member']).with_name(paired)).replace('\\', '/'))
    other = fc.parse(server.xlzx.read_member(archive, member))
    other.sections.append(copy.deepcopy(next(s for s in other.sections if s.name.lower() == 'body')))     # a second part called body
    server.S.staged[paired] = fc.build(other)
    before = fc.build(server.S.carbin)
    with pytest.raises(ValueError, match='several parts'):
        server.op_add_model(triangle_request(target))
    assert fc.build(server.S.carbin) == before

def test_coordinate_api_is_undoable(editor):
    open_alfa(editor)
    before = fc.build(server.S.carbin)
    editor('/api/op', {'op': 'set_coordinates', 'section': 0, 'coordinates': [1, 2, 3]})
    np.testing.assert_allclose(server.S.carbin.sections[0].offset, [1, 2, 3])
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == before


def test_paired_staging_uses_actual_archive_member_case(car_blob, monkeypatch):
    session = server.Session()
    session.load(car_blob, 'Mixed_CASE.carbin', origin={'source': 'game', 'archive': 'Mixed_CASE.zip', 'member': 'mixed_case.carbin'})
    monkeypatch.setattr(server, 'S', session)
    monkeypatch.setattr(server, 'origin_car', lambda: ('Mixed_CASE', 'mixed_case.carbin', None))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mixed_case_lod0.carbin', car_blob)
    archive = zipfile.ZipFile(buffer)
    monkeypatch.setattr(server, 'origin_zip', lambda: archive)
    session.staged['MIXED_CASE_LOD0.carbin'] = car_blob
    target = next(i for i, s in enumerate(session.carbin.sections) if s.name.lower() == 'body')
    server.op_add_model(triangle_request(target))
    assert set(session.staged) == {'mixed_case.carbin', 'mixed_case_lod0.carbin'}
    archive.close()


def test_opening_paired_file_uses_staged_geometry(editor):
    request, _ = open_alfa(editor)
    target = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    editor('/api/op', triangle_request(target))
    member = next(key for key in server.S.staged if key.lower().endswith('_lod0.carbin'))
    expected = server.S.staged[member]
    editor('/api/library/open', {**request, 'member': member})
    assert fc.build(server.S.carbin) == expected
