import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolkit'))
import fh1_sources as sources
import fh1_carbin as fc
import carbin_geom as cg
import xlzx

# your own FH1 game (a folder, .iso or Games-on-Demand container); the integration tests skip without it
GAME = Path(os.environ.get('FH1_GAME', 'FH1_GAME_not_set'))


@pytest.fixture(scope='module')
def car_blob():
    if not GAME.exists():
        pytest.skip('Local FH1 integration fixture is unavailable')
    game = sources.open_fh1_game(GAME)
    archive = next(n for n in game.car_media_names() if 'ALF_8C' in n.upper())
    z = game.zip(archive)
    member = next(n for n in z.namelist() if n.lower() == Path(archive).stem.lower() + '.carbin')
    return xlzx.read_member(z, member)


def clipboard_type():
    import carbin_parts
    return carbin_parts.PartClipboard


def test_copy_is_independent_and_paste_survives_rebuild(car_blob):
    source = fc.parse(car_blob)
    dest = fc.parse(car_blob)
    si = next(i for i, s in enumerate(source.sections) if s.lod.count and s.subs)
    clipboard = clipboard_type()()
    original_dest = fc.build(dest)
    clipboard.copy(source, si, 'donor car')
    expected = cg.decode_pool(source.sections[si], cg.active_pool(source.sections[si])).pos.copy()
    cg.transform_section(source, si, cg.trs_matrix(translate=(8, 0, 0)))
    assert fc.build(dest) == original_dest
    pasted = clipboard.paste(dest)
    assert pasted == len(dest.sections) - 1
    assert dest.sections[pasted].name != dest.sections[si].name
    rebuilt = fc.parse(fc.build(dest))
    actual = cg.decode_pool(rebuilt.sections[pasted], cg.active_pool(rebuilt.sections[pasted])).pos
    # Encoding may compact/reorder vertices by material. Compare bounds, independent of that order.
    np.testing.assert_allclose(actual.min(0), expected.min(0), atol=0.001)
    np.testing.assert_allclose(actual.max(0), expected.max(0), atol=0.001)
    assert not cg.validate(rebuilt)
    assert any(s == pasted for s, _ in cg.drawn_at(rebuilt))
    pasted_again = clipboard.paste(dest)
    assert dest.sections[pasted_again].name != dest.sections[pasted].name
    assert not cg.validate(dest)


def test_invalid_copy_preserves_clipboard(car_blob):
    car = fc.parse(car_blob)
    clipboard = clipboard_type()()
    clipboard.copy(car, 0, 'first')
    before = clipboard.info()
    with pytest.raises(ValueError):
        clipboard.copy(car, -1, 'bad')
    assert clipboard.info() == before


def test_unknown_material_mapping_does_not_mutate_destination(car_blob):
    car = fc.parse(car_blob)
    clipboard = clipboard_type()()
    clipboard.copy(car, 0, 'donor')
    before = fc.build(car)
    names = clipboard.info()['materials']
    with pytest.raises(ValueError, match='material'):
        clipboard.paste(car, material_map={names[0]: 'does_not_exist'})
    assert fc.build(car) == before


def test_paste_into_lod0_remaps_to_visible_lod(car_blob):
    car = fc.parse(car_blob)
    clipboard = clipboard_type()()
    clipboard.copy(car, 0, 'donor')
    # A target body with the same format but one LOD is a deterministic cross-pool fixture.
    for sec in car.sections:
        sec.lod0, sec.lod = sec.lod, fc.Pool(0, 0, [0], b'')
        for sub in sec.subs:
            sub.lod = 0
    arrays = cg.draw_lists(car.header)[1]
    entries = list(dict.fromkeys(e for a in arrays for e in a))
    car.header = cg.set_draw_lists(car.header, [[], entries])
    pasted = clipboard.paste(car)
    assert all(s.lod == 0 for s in car.sections[pasted].subs)
    assert car.sections[pasted].lod0.count > 0
    assert not cg.validate(car)


def test_copy_preserves_effective_detail_texture_uvs(car_blob):
    car = fc.parse(car_blob)
    clipboard = clipboard_type()()
    clipboard.copy(car, 0, 'donor')
    original = car.sections[0]
    decoded = cg.decode_pool(original, cg.active_pool(original))
    index = clipboard.paste(car)
    pasted = car.sections[index]
    rebuilt = cg.decode_pool(pasted, cg.active_pool(pasted))
    for sub in original.subs:
        used = np.unique(cg.sub_triangles(sub, len(decoded.pos)))
        if not len(used):
            continue
        other = next(s for s in pasted.subs if (s.lod, s.name) == (sub.lod, sub.name))
        other_used = np.unique(cg.sub_triangles(other, len(rebuilt.pos)))
        expected = cg.final_uv(decoded.uv1, sub.uvxf[4:8])[used]
        actual = cg.final_uv(rebuilt.uv1, other.uvxf[4:8])[other_used]
        np.testing.assert_allclose(actual.min(0), expected.min(0), atol=0.002)
        np.testing.assert_allclose(actual.max(0), expected.max(0), atol=0.002)
