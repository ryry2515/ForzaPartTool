import numpy as np
import pytest

from test_editor_api import editor, open_alfa, server
from test_part_transfer import car_blob
import atlas_paint as ap
import carbin_geom as cg
import fh1_carbin as fc
import part_look as pl
import xds

SAMPLE = '''<ShaderSettings>
  <window>
    <GlassColor r="0.020000" g="0.020000" b="0.020000" a="0.800000"/>
    <FresnelIndex value="2.050002"/>
  </window>
  <tail_light>
    <BrakeColor r="1.88" g="0.0" b="0.0" a="0.0"/>
  </tail_light>
  <chrome>
    <CubeScale value="0.8"/>
  </chrome>
</ShaderSettings>
'''


# ---------------------------------------------------------------------------------------------------------- ShaderSettings.xml as text
def test_set_param_replaces_one_element_and_changes_nothing_else():
    new = pl.set_param(SAMPLE, 'window', 'GlassColor', {'r': 0.0, 'g': 0.0, 'b': 0.1, 'a': 0.5})
    assert pl.parse_settings(new)['window']['GlassColor'] == {'r': 0.0, 'g': 0.0, 'b': 0.1, 'a': 0.5}
    assert new.replace('<GlassColor r="0.000000" g="0.000000" b="0.100000" a="0.500000"/>',
                       '<GlassColor r="0.020000" g="0.020000" b="0.020000" a="0.800000"/>') == SAMPLE
    assert pl.wellformed(new)


def test_set_param_adds_to_a_material_and_adds_a_material_and_remove_undoes_both():
    one = pl.set_param(SAMPLE, 'window', 'GlassMaxOpacity', {'value': 0.9})
    assert pl.parse_settings(one)['window']['GlassMaxOpacity'] == {'value': 0.9}
    two = pl.set_param(one, 'detail_glass_red', 'GlassColor', {'r': 0.8, 'g': 0.0, 'b': 0.0, 'a': 0.7})
    assert pl.parse_settings(two)['detail_glass_red']['GlassColor']['a'] == 0.7
    assert pl.wellformed(two)
    assert pl.remove_param(pl.remove_param(two, 'detail_glass_red', 'GlassColor'), 'window', 'GlassMaxOpacity') == SAMPLE


def test_reset_puts_back_the_original_or_removes_what_was_not_there():
    edited = pl.set_param(SAMPLE, 'tail_light', 'BrakeColor', {'r': 0.0, 'g': 1.0, 'b': 0.0, 'a': 0.0})
    back = pl.reset_param(edited, SAMPLE, 'tail_light', 'BrakeColor')
    assert back != edited and pl.parse_settings(back) == pl.parse_settings(SAMPLE)
    added = pl.set_param(SAMPLE, 'detail_glass_amber', 'GlassColor', {'r': 1, 'g': 1, 'b': 1, 'a': 1})
    assert pl.reset_param(added, SAMPLE, 'detail_glass_amber', 'GlassColor') == SAMPLE


def test_values_are_checked_and_limited():
    assert pl.clean_values('GlassColor', {'r': 0.1, 'g': 0.2, 'b': 0.3, 'a': 7})['a'] == 1.0          # glass opacity cannot exceed 1
    assert pl.clean_values('BrakeColor', {'r': 99}, {'r': 1, 'g': 0, 'b': 0, 'a': 0})['r'] == 16.0      # lamp colours may be above 1, within reason
    assert pl.clean_values('BrakeColor', {'g': 0.5}, {'r': 1, 'g': 0, 'b': 0, 'a': 0}) == {'r': 1.0, 'g': 0.5, 'b': 0.0, 'a': 0.0}
    with pytest.raises(ValueError):
        pl.clean_values('GlassColor', {'r': float('nan')})
    with pytest.raises(ValueError):
        pl.clean_values('CubeScale', {'value': 1})                                                     # only the tint and lamp settings are editable here
    with pytest.raises(ValueError):
        pl.set_param(SAMPLE, 'bad name', 'GlassColor', {'r': 0, 'g': 0, 'b': 0, 'a': 0})


def test_an_edit_may_not_break_a_wellformed_file_but_a_broken_one_can_still_be_edited():
    with pytest.raises(ValueError):
        pl.edit_checked(SAMPLE, SAMPLE.replace('</window>', '</windows>'))
    broken = SAMPLE.replace('</chrome>', '</chrome2>')                              # about 3 % of the stock files are like this
    assert not pl.wellformed(broken)
    assert pl.edit_checked(broken, pl.set_param(broken, 'window', 'GlassColor', {'r': 0, 'g': 0, 'b': 0, 'a': 1})).count('GlassColor') == 1


def test_tints_and_rows_only_describe_what_changed():
    assert pl.tints(SAMPLE, SAMPLE) == {}
    edited = pl.set_param(pl.set_param(SAMPLE, 'window', 'GlassColor', {'r': 0, 'g': 0, 'b': 0, 'a': 0.9}), 'tail_light', 'BrakeColor', {'r': 0, 'g': 2, 'b': 0, 'a': 0})
    assert pl.tints(SAMPLE, edited) == {'window': {'GlassColor': [0.0, 0.0, 0.0, 0.9]}, 'tail_light': {'colour': [0.0, 2.0, 0.0]}}
    rows = pl.look_rows(SAMPLE, edited, {'window', 'detail_glass_red'})
    assert [(r['material'], r['param'], r['changed']) for r in rows][:2] == [('window', 'GlassColor', True), ('detail_glass_red', 'GlassColor', False)]
    assert next(r for r in rows if r['material'] == 'detail_glass_red')['present'] is False       # a glass the car uses but has no entry for: offered with a starting value
    assert not any(r['param'] == 'CubeScale' for r in rows)


# ---------------------------------------------------------------------------------------------------------- material groups on a real car
def group(car, part='hooda', material='black'):
    si = next(i for i, s in enumerate(car.sections) if s.name == part)
    return si, [k for k, sb in enumerate(car.sections[si].subs) if sb.name == material]


def tris(car, si, ks):
    pool = cg.active_pool(car.sections[si])
    return sum(len(cg.sub_triangles(car.sections[si].subs[k], pool.count)) for k in ks)


def test_split_makes_a_part_of_its_own_with_the_same_geometry(car_blob):
    car = fc.parse(car_blob)
    si, ks = group(car)
    assert ks and len(ks) < len(car.sections[si].subs)
    count, lods = tris(car, si, ks), sorted(car.sections[si].subs[k].lod for k in ks)
    box = cg.decode_pool(car.sections[si], cg.active_pool(car.sections[si])).pos
    n = len(car.sections)
    new = pl.split_material(car, si, 'black', 'hooda_black')
    rebuilt = fc.parse(fc.build(car))
    assert len(rebuilt.sections) == n + 1 and rebuilt.sections[new].name == 'hooda_black'
    assert not any(sb.name == 'black' for sb in rebuilt.sections[si].subs)
    assert {sb.name for sb in rebuilt.sections[new].subs} == {'black'}
    assert sorted(sb.lod for sb in rebuilt.sections[new].subs) == lods
    assert tris(rebuilt, new, range(len(rebuilt.sections[new].subs))) == count
    assert not cg.validate(rebuilt)
    assert any(s == new for s, _ in cg.drawn_at(rebuilt))
    pool = cg.active_pool(rebuilt.sections[new])
    pos = cg.decode_pool(rebuilt.sections[new], pool).pos[np.unique(np.concatenate([cg.sub_triangles(sb, pool.count) for sb in rebuilt.sections[new].subs]))]
    assert pos.min(0)[1] >= box.min(0)[1] - 0.01 and pos.max(0)[1] <= box.max(0)[1] + 0.01          # it stays where it was
    assert np.allclose(rebuilt.sections[new].offset, (pos.min(0) + pos.max(0)) / 2, atol=0.01)       # pivot in the middle of the group


def test_split_of_an_undrawn_part_stays_undrawn(car_blob):
    car = fc.parse(car_blob)
    si, _ = group(car)
    cg.hide_section(car, si)
    new = pl.split_material(car, si, 'black')
    assert not any(s == new for s, _ in cg.drawn_at(car))


def test_move_into_another_part_keeps_the_geometry(car_blob):
    car = fc.parse(car_blob)
    si, ks = group(car)
    target = next(i for i, s in enumerate(car.sections) if s.name == 'body')
    before = tris(car, target, [k for k, sb in enumerate(car.sections[target].subs) if sb.name == 'black'])
    count = tris(car, si, ks)
    pl.move_material(car, si, 'black', target)
    rebuilt = fc.parse(fc.build(car))
    assert not any(sb.name == 'black' for sb in rebuilt.sections[si].subs)
    assert tris(rebuilt, target, [k for k, sb in enumerate(rebuilt.sections[target].subs) if sb.name == 'black']) == before + count
    assert not cg.validate(rebuilt)
    with pytest.raises(ValueError):
        pl.move_material(car, target, 'black', target)


def test_rename_changes_every_lod_and_refuses_bad_names(car_blob):
    car = fc.parse(car_blob)
    si, ks = group(car)
    drawn = {k: cg.drawn_at(car).get((si, k)) for k in ks}
    assert pl.rename_material(car, si, 'black', 'plastic2') == len(ks)
    assert all(car.sections[si].subs[k].name == 'plastic2' for k in ks)
    assert {k: cg.drawn_at(car).get((si, k)) for k in ks} == drawn
    for bad in ('', 'has space', 'ünï'):
        with pytest.raises(ValueError):
            pl.rename_material(car, si, 'plastic2', bad)
    with pytest.raises(ValueError):
        pl.rename_material(car, si, 'no_such_material', 'x')


def test_a_swatch_reads_one_texel_wherever_the_vertices_are(car_blob):
    car = fc.parse(car_blob)
    si, ks = group(car)
    sub = car.sections[si].subs[ks[0]]
    pl.point_at(sub, 0.37, 0.62)
    assert cg.is_swatch(sub) and pl.swatch_point(sub) == pytest.approx((0.37, 0.62))
    pool = cg.active_pool(car.sections[si])
    uv = cg.final_uv(cg.decode_pool(car.sections[si], pool).uv0, sub.uvxf[:4])
    assert np.allclose(uv, [0.37, 0.62], atol=1e-6)
    assert pl.count_pointing(car, 0.37, 0.62) >= 1
    assert not cg.validate(fc.parse(fc.build(car)))


# ---------------------------------------------------------------------------------------------------------- through the editor's API
def hooda(summary):
    return next(i for i, s in enumerate(summary['sections']) if s['name'] == 'hooda')


def test_split_in_both_models_is_one_undo_step(editor):
    _, summary = open_alfa(editor)
    si = hooda(summary)
    before = fc.build(server.S.carbin)
    after = editor('/api/op', {'op': 'split_material', 'section': si, 'material': 'black', 'name': 'hooda_black', 'both_models': True})
    assert len(after['sections']) == len(summary['sections']) + 1 and after['last_pasted'] == len(summary['sections'])
    assert after['sections'][after['last_pasted']]['name'] == 'hooda_black'
    assert not cg.validate(server.S.carbin)
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == before and not server.S.staged
    editor('/api/redo', {})
    assert server.S.carbin.sections[-1].name == 'hooda_black'


def test_recolour_paints_a_block_in_every_body_atlas_and_points_the_group_at_it(editor):
    _, summary = open_alfa(editor)
    si = hooda(summary)
    out = editor('/api/op', {'op': 'recolour', 'section': si, 'material': 'black', 'rgb': [200, 30, 30], 'scope': 'part', 'both_models': True})
    look = out['looks']['hooda|black']
    assert look['rgb'] == [200, 30, 30]
    staged = {k: v for k, v in server.S.staged.items() if k.lower().endswith('.xds')}
    assert len(staged) >= 2
    u, v = pl.cell_centre(tuple(look['cell']), look['size'])
    for name, raw in staged.items():
        img = xds.decode(raw)
        h, w = img.shape[:2]
        px = img[int(v * h), int(u * w)]
        assert abs(int(px[0]) - 200) <= 10 and abs(int(px[1]) - 30) <= 10 and abs(int(px[2]) - 30) <= 10 and px[3] >= 250, (name, px)
    for k, sb in enumerate(server.S.carbin.sections[si].subs):
        if sb.name == 'black':
            assert pl.swatch_point(sb) == pytest.approx((u, v))
        else:
            assert pl.swatch_point(sb) != pytest.approx((u, v))
    assert not cg.validate(server.S.carbin)
    again = editor('/api/op', {'op': 'recolour', 'section': si, 'material': 'black', 'rgb': [20, 90, 220], 'scope': 'part', 'both_models': True})
    assert again['looks']['hooda|black']['cell'] == look['cell']                       # the same part again reuses its block
    editor('/api/undo', {})
    editor('/api/undo', {})
    assert not [k for k in server.S.staged if k.lower().endswith('.xds')] and not server.S.looks


def test_recolour_refuses_glass_and_lamps(editor):
    _, summary = open_alfa(editor)
    body = next(i for i, s in enumerate(summary['sections']) if s['name'] == 'body')
    before = fc.build(server.S.carbin)
    for material in ('black_glass', 'indicator_left'):
        with pytest.raises(ValueError):
            server.op_recolour({'section': body, 'material': material, 'rgb': [1, 2, 3]})
    assert fc.build(server.S.carbin) == before and not server.S.staged


def test_shader_ops_stage_the_file_and_reset_drops_it_again(editor):
    open_alfa(editor)
    rows = editor('/api/look')
    window = next(r for r in rows['rows'] if r['material'] == 'window' and r['param'] == 'GlassColor')
    assert rows['has'] and rows['window_presets'] and window['present'] is False and window['start'] == [0.02, 0.02, 0.02, 0.8]      # the Alfa draws windows but has no tint set
    out = editor('/api/op', {'op': 'shader_set', 'material': 'window', 'param': 'GlassColor', 'values': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.9}})
    assert out['tints']['window']['GlassColor'] == [0.0, 0.0, 0.0, 0.9]
    assert [k for k in server.S.staged if k.lower() == 'shadersettings.xml']
    glass = next(r for r in editor('/api/look')['rows'] if r['material'] == 'window' and r['param'] == 'GlassColor')
    assert glass['changed'] and glass['present'] and glass['values']['a'] == 0.9
    lamp = editor('/api/op', {'op': 'shader_set', 'material': 'tail_light2', 'param': 'BrakeColor', 'values': {'r': 0.0, 'g': 3.0}})
    assert lamp['tints']['tail_light2']['colour'] == [0.0, 3.0, 0.0]                      # r and g changed, b stays as the file has it
    out = editor('/api/op', {'op': 'shader_reset', 'material': 'window', 'param': 'GlassColor'})
    assert 'window' not in out['tints'] and 'tail_light2' in out['tints']
    out = editor('/api/op', {'op': 'shader_reset', 'material': 'tail_light2', 'param': 'BrakeColor'})
    assert out['tints'] == {} and not server.S.staged                                       # nothing differs from the car's file any more: nothing is staged
    editor('/api/undo', {})                                                                 # undo brings the lamp edit (and its staged file) back
    assert any(r['changed'] and r['material'] == 'tail_light2' for r in editor('/api/look')['rows'])
    assert [k for k in server.S.staged if k.lower() == 'shadersettings.xml']


def test_a_split_part_a_colour_and_a_tint_all_reach_the_exported_dlc_car(editor, tmp_path):
    import carbin_library as cl
    from test_part_transfer import GAME
    _, summary = open_alfa(editor)
    si = hooda(summary)
    editor('/api/op', {'op': 'split_material', 'section': si, 'material': 'black', 'name': 'hooda_black', 'both_models': True})
    split = server.S.carbin.sections[-1].name
    editor('/api/op', {'op': 'recolour', 'section': len(server.S.carbin.sections) - 1, 'material': 'black', 'rgb': [250, 10, 10], 'scope': 'part', 'both_models': True})
    editor('/api/op', {'op': 'shader_set', 'material': 'window', 'param': 'GlassColor', 'values': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.9}})
    defaults = editor('/api/clone/defaults')
    result = editor('/api/clone', {'new_media': defaults['new_media'], 'offer': defaults['offer'], 'out_dir': str(tmp_path), 'display_name': 'Look Test', 'formats': ['xenia']})
    assert not result['problems']
    library = cl.Library(str(GAME), [str(tmp_path / 'xenia')])
    source = next(s['id'] for s in library.sources() if s['kind'] == 'dlc')
    new_car = library.cars(source)[0]
    archive = library.members(source, new_car)[0]['archive']
    zipped = library.archive_zip(source, archive)

    def read(suffix):                                       # any file of the new car's archive, as the game would read it
        import xlzx
        return xlzx.read_member(zipped, next(n for n in zipped.namelist() if n.lower().endswith(suffix.lower())))
    shader = read('/ShaderSettings.xml').decode('latin-1')
    assert pl.parse_settings(shader)['window']['GlassColor'] == {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.9}
    car = fc.parse(read(f'/{new_car}.carbin'))
    assert car.sections[-1].name == split and not cg.validate(car)
    sub = car.sections[-1].subs[0]
    u, v = pl.swatch_point(sub)
    atlas = xds.decode(read('/nodamage.xds'))
    px = atlas[int(v * atlas.shape[0]), int(u * atlas.shape[1])]
    assert px[0] > 235 and px[1] < 30 and px[2] < 30 and px[3] > 245


def test_looks_survive_a_saved_project(editor):
    _, summary = open_alfa(editor)
    si = hooda(summary)
    editor('/api/op', {'op': 'recolour', 'section': si, 'material': 'black', 'rgb': [10, 200, 10], 'scope': 'part'})
    saved = server.project_bytes()
    server.S.looks = {}
    server.project_open(saved)
    assert server.S.looks['hooda|black']['rgb'] == [10, 200, 10]
