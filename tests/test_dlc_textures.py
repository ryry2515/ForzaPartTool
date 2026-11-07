import io
from pathlib import Path
import zipfile

import pytest

from test_editor_api import editor, server
from test_part_transfer import GAME
import carbin_library as cl
import carbin_geom as cg
import fh1_carbin as fc
import xlzx
import xds


DLC = Path('C:/Users/ryan/Downloads/xenia_canary_windows/content/0000000000000000/4D5309C9/00000002')


@pytest.mark.parametrize('lod0', [False, True])
def test_fm4_focus_st_dlc_preview_reads_its_own_textures(editor, lod0):
    if not DLC.is_dir():
        pytest.skip('Local FM4 DLC fixture unavailable')
    server.S.lib = cl.Library(str(GAME), [str(DLC)])
    source = next((s['id'] for s in server.S.lib.sources() if s['kind'] == 'dlc'
                   and 'FOR_FocusST_06' in server.S.lib.cars(s['id'])), None)
    if not source:
        pytest.skip('FM4-imported Focus ST fixture unavailable')
    media = 'FOR_FocusST_06'
    members = server.S.lib.members(source, media)
    filename = media + ('_lod0' if lod0 else '') + '.carbin'
    chosen = next(m for m in members if Path(m['member']).name.lower() == filename.lower())
    editor('/api/library/open', {'source': source, **chosen})
    archive = server.origin_zip()
    for atlas in ('nodamage', 'damage', 'lights', 'interior'):
        expected = next(n for n in archive.namelist() if n.lower() ==
                        f'media/cars/{media}/{atlas}{"_lod0" if lod0 or atlas == "interior" else ""}.xds'.lower())
        blob = server.preview_texture_blob(atlas)
        assert blob == xlzx.read_member(archive, expected)
        pixels = xds.decode(blob)
        assert pixels.shape[0] > 1 and pixels.shape[1] > 1
    member = server.atlas_member(archive, 'nodamage')
    server.S.staged[Path(member).name.upper()] = b'staged texture edit'
    assert server.preview_texture_blob('nodamage') == b'staged texture edit'


def test_main_model_can_use_only_available_lod0_interior(editor):
    car = 'FOR_FocusRS500_10'
    members = server.S.lib.members('game', car)
    chosen = next(m for m in members if m['member'].lower() == (car + '.carbin').lower())
    editor('/api/library/open', {'source': 'game', **chosen})
    archive = server.origin_zip()
    assert 'interior.xds' not in {n.lower() for n in archive.namelist()}
    assert server.preview_texture_blob('interior') == xlzx.read_member(archive, 'interior_LOD0.xds')


def test_dlc_lookup_does_not_borrow_other_cars_or_zip_root(editor, monkeypatch):
    monkeypatch.setattr(server, 'origin_car', lambda: ('Active', 'Active.carbin', object()))
    server.S.origin = {'source': 'dlc:test'}
    monkeypatch.setattr(cg, 'pool_name_of', lambda _: 'lod')
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('nodamage.xds', b'wrong root')
        archive.writestr('media/cars/Other/nodamage.xds', b'wrong car')
    with zipfile.ZipFile(data) as archive:
        with pytest.raises(KeyError):
            server.atlas_member(archive, 'nodamage')
