import http.client
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from test_desktop_settings import server
from test_part_transfer import GAME
import carbin_library as cl
import carbin_geom as cg
import fh1_carbin as fc
import xlzx


@pytest.fixture
def editor(tmp_path, monkeypatch):
    if not GAME.exists():
        pytest.skip('Local FH1 integration fixture is unavailable')
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'settings.json')
    server.S = server.Session()
    server.S.lib = cl.Library(str(GAME), [])
    server.S.cfg = {'game': str(GAME), 'dlc': []}
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def call(route, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=120)
        headers = {'Cookie': f'{server.COOKIE}={server.TOKEN}'}
        connection.request('POST' if body is not None else 'GET', route, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        assert response.status == 200, data.decode(errors='replace')
        return json.loads(data)

    call.port = httpd.server_port
    yield call
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def open_alfa(editor):
    cars = editor('/api/library/cars?source=game')['cars']
    car = next(c for c in cars if 'ALF_8C' in c.upper())
    members = editor('/api/library/members?source=game&car=' + car)['members']
    main = next(m for m in members if m['member'].lower() == car.lower() + '.carbin')
    request = {'source': 'game', 'archive': main['archive'], 'member': main['member']}
    return request, editor('/api/library/open', request)


def test_donor_copy_preserves_active_edits_and_paste_is_undoable(editor):
    request, summary = open_alfa(editor)
    editor('/api/op', {'op': 'transform', 'sections': [0], 'translate': [0.1, 0, 0]})
    server.S.staged['marker'] = b'keep'
    edited = fc.build(server.S.carbin)
    donor = editor('/api/parts/inspect', request)
    assert donor['sections']
    editor('/api/parts/copy', {**request, 'section': 0})
    assert fc.build(server.S.carbin) == edited
    assert server.S.staged['marker'] == b'keep'
    pasted = editor('/api/op', {'op': 'paste_part'})
    assert len(pasted['sections']) == len(summary['sections']) + 1
    assert pasted['undo'] == 2
    after = fc.build(server.S.carbin)
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == edited
    editor('/api/redo', {})
    assert fc.build(server.S.carbin) == after
    editor('/api/library/open', request)
    assert editor('/api/status')['clipboard']['name']


def test_pasted_geometry_exports_and_reopens_as_dlc(editor, tmp_path):
    _, summary = open_alfa(editor)
    editor('/api/parts/copy', {'section': 0})
    pasted = editor('/api/op', {'op': 'paste_part', 'translate': [0, 0.25, 0]})
    added_name = pasted['sections'][-1]['name']
    defaults = editor('/api/clone/defaults')
    result = editor('/api/clone', {'new_media': defaults['new_media'], 'offer': defaults['offer'],
                                   'out_dir': str(tmp_path), 'display_name': 'Desktop Editor Test', 'formats': ['xenia', 'stfs']})
    assert not result['problems']
    assert Path(result['folder']).is_dir()
    assert Path(result['header']).is_file()
    assert Path(result['stfs']).is_file()
    # exporting also writes the model for other tools: OBJ + MTL, and a model bin per part
    assert Path(result['obj']).is_file() and Path(result['obj']).with_suffix('.mtl').is_file()
    assert Path(result['project']).is_file()
    folder = Path(result['modelbin']['folder'])
    assert 'modelbin_error' not in result and result['modelbin']['parts'] > 10 and not result['modelbin']['skipped']
    assert (folder / 'README.txt').is_file() and (folder / 'body.modelbin').is_file()
    assert (folder / f'{added_name}.modelbin').is_file()          # the part pasted in this test is exported too
    library = cl.Library(str(GAME), [str(Path(result['folder']).parent)])
    source = next(s['id'] for s in library.sources() if s['kind'] == 'dlc')
    new_car = library.cars(source)[0]
    main = next(m for m in library.members(source, new_car) if Path(m['member']).name.lower() == new_car.lower() + '.carbin')
    car = fc.parse(library.read(source, main['archive'], main['member']))
    assert len(car.sections) == len(summary['sections']) + 1
    assert car.sections[-1].name == added_name
    assert not cg.validate(car)
    stripped = str(Path(main['member']).with_name('stripped_' + Path(main['member']).name)).replace('\\', '/')
    stripped = next(n for n in library.archive_zip(source, main['archive']).namelist() if n.lower() == stripped.lower())
    assert library.read(source, main['archive'], stripped) == fc.build(car, stripped=True)
    # Both emitted formats must expose the same new car model.
    console = cl.Library(str(GAME), [str(Path(result['stfs']).parent)])
    console_source = next(s['id'] for s in console.sources() if s['kind'] == 'dlc')
    assert console.cars(console_source) == [new_car]
    with pytest.raises(FileExistsError):
        server.do_clone({'new_media': defaults['new_media'], 'offer': defaults['offer'],
                         'out_dir': str(tmp_path), 'formats': ['xenia', 'stfs']})
    next_defaults = editor('/api/clone/defaults')
    assert next_defaults['offer'] != defaults['offer']
    assert next_defaults['new_media'] != defaults['new_media']
    second = editor('/api/clone', {'new_media': next_defaults['new_media'], 'offer': next_defaults['offer'],
                                   'out_dir': str(tmp_path / 'second'), 'formats': ['xenia']})
    assert second['car_id'] != result['car_id']


def test_export_rejects_invalid_model(editor):
    open_alfa(editor)
    for sec in server.S.carbin.sections:
        if sec.lod.count and len(sec.lod.bone) > 1:
            sec.lod.bone[1] = 123
    with pytest.raises(ValueError, match='validation'):
        server.do_clone({})


def test_texture_preview_reads_staged_edits(editor):
    open_alfa(editor)
    archive = server.origin_zip()
    member = server.atlas_member(archive, 'nodamage')
    original = xlzx.read_member(archive, member)
    import xds
    edited = xds.solid(original, 'black')
    server.S.staged[Path(member).name] = edited
    assert server.preview_texture_blob('nodamage') == edited
    # This changes only the preview/staged export, never the source archive.
    assert xlzx.read_member(archive, member) == original


def test_multiple_staged_texture_paints_keep_previous_images(editor):
    import base64
    import xds
    open_alfa(editor)
    for rect, color in [([0.70, 0.70, 0.72, 0.72], [255, 0, 0, 255]), ([0.80, 0.80, 0.82, 0.82], [0, 255, 0, 255])]:
        server.paint_atlases({'rgba': base64.b64encode(bytes(color)).decode(), 'w': 1, 'h': 1, 'rect': rect, 'atlases': ['nodamage'], 'stage_only': True})
    image = xds.decode(server.preview_texture_blob('nodamage'))
    y, x = int(image.shape[0] * 0.71), int(image.shape[1] * 0.71)
    assert image[y, x, 0] > 220 and image[y, x, 1] < 30, 'Second paint removed the first staged image'
