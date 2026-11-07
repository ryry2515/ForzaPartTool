"""Bugs found by sweeping every car and fuzzing the edit operations (see the release notes of test build 10)."""
import json
import struct

import numpy as np
import pytest

from test_custom_model_save import new_part_request
from test_editor_api import editor, server
from test_part_actions_api import triangle_request
import carbin_geom as cg
import fh1_carbin as fc


def open_car(editor, wanted, suffix=''):
    cars = editor('/api/library/cars?source=game')['cars']
    car = next((c for c in cars if wanted.upper() in c.upper()), None)
    if car is None:
        pytest.skip(f'{wanted} is not in the local FH1 fixture')
    members = editor('/api/library/members?source=game&car=' + car)['members']
    main = next(m for m in members if m['member'].lower() == car.lower() + suffix + '.carbin')
    request = {'source': 'game', 'archive': main['archive'], 'member': main['member']}
    return car, editor('/api/library/open', request)


def section_index(name):
    return next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == name.lower())


@pytest.mark.parametrize('bad', ['x y', 'é', '', '   ', 'a' * 300, '日本'])
def test_rename_rejects_names_the_file_cannot_hold(editor, bad):
    open_car(editor, 'ALF_8C')
    before = fc.build(server.S.carbin)
    with pytest.raises(ValueError):
        server.OPS['rename_section']({'section': 0, 'name': bad})
    assert fc.build(server.S.carbin) == before


def test_rename_accepts_a_good_name_and_keeps_the_file_readable(editor):
    open_car(editor, 'ALF_8C')
    editor('/api/op', {'op': 'rename_section', 'section': 0, 'name': 'my_part-2'})
    assert fc.parse(fc.build(server.S.carbin)).sections[0].name == 'my_part-2'
    other = server.S.carbin.sections[1].name
    with pytest.raises(ValueError, match='already called'):
        server.OPS['rename_section']({'section': 0, 'name': other.upper()})


def test_new_part_name_with_spaces_is_cleaned_instead_of_corrupting_the_file(editor):
    open_car(editor, 'ALF_8C')
    target = section_index('body')
    editor('/api/op', dict(new_part_request(target, name='my wing é'), both_models=False))
    names = [s.name for s in fc.parse(fc.build(server.S.carbin)).sections]
    assert 'my_wing' in names[-1] or names[-1].startswith('my_wing')
    assert all(32 < ord(c) < 127 for c in names[-1])


def test_paired_move_skips_parts_the_close_up_model_does_not_have(editor):
    open_car(editor, 'ALF_8C')
    hood = section_index('hooda')
    lod0_names = {s.name.lower() for s in fc.parse(server.S.original).sections}      # only a sanity anchor
    assert 'hooda' in lod0_names
    before = list(server.S.carbin.sections[hood].offset)
    summary = editor('/api/op', {'op': 'transform', 'sections': [hood], 'translate': [0, 0.2, 0], 'move_pivot': True, 'both_models': True})
    assert summary['sections'][hood]['offset'][1] == pytest.approx(before[1] + 0.2, abs=1e-4)


def test_replacing_two_parts_gives_each_its_own_resource_key(editor):
    open_car(editor, 'ALF_8C')
    for name in ('body', 'trunk'):
        editor('/api/op', dict(triangle_request(section_index(name)), mode='replace', both_models=False, auto_lods=False))
    assert not [p for p in cg.validate(server.S.carbin) if 'resource key' in p]


def test_light_and_exhaust_indexes_are_checked(editor):
    open_car(editor, 'ALF_8C')
    before = fc.build(server.S.carbin)
    count = len(cg.lights(server.S.carbin))
    for bad in (-1, count, 999):
        with pytest.raises(ValueError):
            server.OPS['light_set']({'index': bad, 'fields': {'glow': 3}})
        with pytest.raises(ValueError):
            server.OPS['light_remove']({'index': bad})
    with pytest.raises(ValueError):
        server.OPS['exhaust_set']({'index': 99, 'pos': [0, 0, 0]})
    assert fc.build(server.S.carbin) == before


def test_problems_a_stock_file_already_had_do_not_block_saving(editor, tmp_path):
    """48 stock files share a vertex-buffer key and ~60 have a flat part with a zero extent; saving them used to fail with 'validation failed'."""
    car, _ = open_car(editor, 'AUD_A4Avant_04')
    assert cg.validate(server.S.carbin), 'the fixture car is expected to carry a stock validation problem'
    assert server.new_problems(server.S.carbin, server.S.baseline) == []
    editor('/api/op', {'op': 'transform', 'sections': [0], 'translate': [0, 0.01, 0], 'both_models': False})
    result = server.export_replacement_zip(str(tmp_path))
    assert result['zip'].endswith('.zip')


def test_geometry_never_sends_nan_uvs(editor):
    """Unused LOD placeholders carry a 'no transform' marker (FLT_MAX, -inf) that made every UV NaN."""
    open_car(editor, 'AUD_A4Avant_04')
    checked = 0
    for index in range(len(server.S.carbin.sections)):
        blob = server.geometry_blob(index)
        head_len = struct.unpack_from('<I', blob, 0)[0]
        assert (4 + head_len) % 4 == 0
        head = json.loads(blob[4:4 + head_len])
        base = 4 + head_len
        for sub in head['subs']:
            if head['verts'] and 'uvOffset' in sub:
                uv = np.frombuffer(blob, dtype='<f4', offset=base + sub['uvOffset'], count=head['verts'] * 2)
                assert np.isfinite(uv).all(), (head['name'], sub['material'])
                checked += 1
    assert checked

def test_wheel_layout_puts_four_wheels_at_the_corners(editor):
    open_car(editor, 'FOR_Fiesta')
    layout = editor('/api/wheel_layout')
    assert layout['has'] and len(layout['wheels']) == 4
    front = [w for w in layout['wheels'] if w['z'] < 0]
    rear = [w for w in layout['wheels'] if w['z'] > 0]
    assert len(front) == len(rear) == 2
    assert {w['flip'] for w in layout['wheels']} == {True, False}
    assert all(abs(w['y']) < 0.1 for w in layout['wheels'])                  # axles are at height 0 in the car's own frame
    assert layout['ground'] == pytest.approx(-layout['radius'], abs=0.12)


def test_cockpit_preview_lists_the_sharp_interior_parts(editor):
    open_car(editor, 'FOR_Fiesta')
    info = editor('/api/cockpit_info')
    assert info['has']
    assert {'interior', 'seatL', 'steering_wheel'} <= set(info['replaces'])
    blob = editor_blob(editor, info['sections'][0]['index'])
    assert len(blob) > 100


def editor_blob(editor, index):
    return server.cockpit_geometry(index)
