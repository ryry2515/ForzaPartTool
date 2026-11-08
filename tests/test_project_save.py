import io
import json
import zipfile

import pytest

from test_desktop_settings import server
from test_part_transfer import car_blob
import fh1_carbin as fc

ORIGIN = {'source': 'game', 'archive': 'media/cars/X.zip', 'member': 'media/cars/X/X.carbin'}


@pytest.fixture
def session(car_blob):
    server.S = server.Session()
    server.S.load(car_blob, 'X.carbin', None, ORIGIN)
    return server.S


def test_project_round_trip_restores_everything(session):
    session.staged['nodamage.xds'] = b'atlas bytes'
    session.staged['LiveryMasks/front.tga'] = b'mask'
    session.body_over = {'ModelWheelbase': 2.75}
    model = fc.build(session.carbin)
    data = server.project_bytes()
    server.S = server.Session()                    # a new session, as after restarting the tool
    info = server.project_open(data)
    restored = server.S
    assert restored.name == 'X.carbin' and restored.origin == ORIGIN
    assert fc.build(restored.carbin) == model
    assert restored.staged == {'nodamage.xds': b'atlas bytes', 'LiveryMasks/front.tga': b'mask'}
    assert restored.body_over == {'ModelWheelbase': 2.75}
    assert info['name'] == 'X.carbin'


def test_project_rejects_files_that_are_not_projects(session):
    before = fc.build(session.carbin)
    with pytest.raises(ValueError):
        server.project_open(b'not a zip at all')
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, 'w') as z:
        z.writestr('hello.txt', 'x')
    with pytest.raises(ValueError):
        server.project_open(empty.getvalue())
    assert fc.build(server.S.carbin) == before     # the open car was not touched


def _with_extra(good, name, raw=b'x'):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(buf, 'w') as out:
        for item in src.namelist():
            out.writestr(item, src.read(item))
        out.writestr(zipfile.ZipInfo(name), raw)       # a ZipInfo keeps the name exactly as given
    return buf.getvalue()


@pytest.mark.parametrize('bad', ['staged/../evil.txt', 'staged//abs.txt', 'staged/C:/x.txt'])
def test_project_rejects_unsafe_names(session, bad):
    before = fc.build(session.carbin)
    with pytest.raises(ValueError):
        server.project_open(_with_extra(server.project_bytes(), bad))
    assert fc.build(server.S.carbin) == before


def test_project_from_a_newer_tool_is_refused(session):
    good = server.project_bytes()
    before = fc.build(session.carbin)
    newer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(newer, 'w') as out:
        for item in src.namelist():
            raw = src.read(item)
            if item == 'project.json':
                meta = json.loads(raw)
                meta['version'] = 99
                raw = json.dumps(meta).encode()
            out.writestr(item, raw)
    with pytest.raises(ValueError):
        server.project_open(newer.getvalue())
    assert fc.build(server.S.carbin) == before