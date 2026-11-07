import http.client
import json

from test_editor_api import editor, open_alfa, server
import fh1_carbin as fc


def test_unload_clears_car_but_keeps_library_and_clipboard(editor):
    open_alfa(editor)
    editor('/api/parts/copy', {'section': 0})
    library = server.S.lib
    config = dict(server.S.cfg)
    result = editor('/api/unload', {})
    assert result['open'] is False
    assert server.S.carbin is None and server.S.origin is None and server.S.path is None
    assert not server.S.staged and not server.S.body_over and not server.S.undo and not server.S.redo
    assert server.S.original == b'' and server.S.name == '' and server.S.baseline == set()
    assert server.S.lib is library and server.S.cfg == config
    assert editor('/api/status')['clipboard']['name']
    assert editor('/api/library/cars?source=game')['cars']
    assert editor('/api/unload', {})['open'] is False
    open_alfa(editor)
    assert server.S.carbin is not None


def test_unload_requires_discard_for_pending_models_and_body_settings(editor):
    open_alfa(editor)
    before = fc.build(server.S.carbin)
    server.S.staged['pending.carbin'] = before
    server.S.body_over['ModelWheelbase'] = 2.8
    connection = http.client.HTTPConnection('127.0.0.1', editor.port)
    connection.request('POST', '/api/unload', '{}', {'Cookie': f'{server.COOKIE}={server.TOKEN}'})
    response = connection.getresponse()
    status, result = response.status, json.loads(response.read())
    connection.close()
    assert status == 409 and result['requires_discard']
    assert fc.build(server.S.carbin) == before
    assert server.S.staged and server.S.body_over
    assert editor('/api/unload', {'discard': True})['open'] is False
    assert not server.S.staged and not server.S.body_over
