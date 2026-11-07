import importlib.util
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolkit'))
SPEC = importlib.util.spec_from_file_location('editor_server', ROOT / 'toolkit/carbin_editor/server.py')
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


def test_rejected_game_path_keeps_previous_library(tmp_path, monkeypatch):
    server.S = server.Session()
    previous = object()
    server.S.lib = previous
    server.S.cfg = {'game': 'previous', 'dlc': []}
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'settings.json')
    try:
        server.set_library(str(tmp_path / 'missing-game'), [])
    except (ValueError, FileNotFoundError, OSError):
        pass
    assert server.S.cfg['game'] == 'previous'
    assert server.S.lib is previous
    assert not server.SETTINGS.exists()


def test_no_game_requires_settings():
    server.S = server.Session()
    assert server.library_info()['needs_setup'] is True
    assert server.library_info()['config'] == {'game': '', 'dlc': []}


def test_malformed_remembered_settings_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'settings.json')
    server.SETTINGS.write_text('["not", "settings"]')
    assert server.load_settings() == {}


def test_custom_dlc_output_folder_is_discovered(tmp_path):
    package = tmp_path / 'exports' / '4D5309C900009000'
    (package / 'Media').mkdir(parents=True)
    packages = server.fsrc.list_packages(package.parent)
    assert [p.path for p in packages] == [package]


def test_settings_write_failure_is_reported(tmp_path, monkeypatch):
    server.S = server.Session()
    blocked = tmp_path / 'not-a-folder'
    blocked.write_text('existing file')
    monkeypatch.setattr(server, 'SETTINGS', blocked / 'settings.json')
    with pytest.raises(OSError):
        server.save_settings()


def test_settings_round_trip_keeps_game_dlc_and_export_paths(tmp_path, monkeypatch):
    server.S = server.Session()
    server.S.cfg = {'game': 'C:/My Game', 'dlc': ['D:/DLC']}
    server.S.export_dirs = ['D:/Edited Cars']
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'prefs' / 'settings.json')
    server.save_settings()
    assert server.load_settings() == {'game': 'C:/My Game', 'dlc': ['D:/DLC'], 'export_dirs': ['D:/Edited Cars'], 'output_dir': ''}


def test_game_path_accepts_extracted_default_xex(tmp_path):
    (tmp_path / 'media/db').mkdir(parents=True)
    (tmp_path / 'media/cars').mkdir()
    (tmp_path / 'media/db/gamedb.slt').write_bytes(b'SQLite format 3\x00')
    (tmp_path / 'media/cars/Example.zip').write_bytes(b'PK')
    executable = tmp_path / 'default.xex'
    executable.write_bytes(b'XEX2')
    game = server.fsrc.open_fh1_game(executable)
    assert game.car_media_names() == ['media/cars/Example.zip']


def test_game_path_accepts_god_data_folder():
    from test_part_transfer import GAME
    data = Path(str(GAME) + '.data')
    if not data.is_dir():
        pytest.skip('Local FH1 GoD fixture unavailable')
    game = server.fsrc.open_fh1_game(data)
    assert len(game.car_media_names()) > 100


@pytest.mark.parametrize('select_child', [False, True])
def test_xenia_title_content_folder_explains_where_dlc_belongs(tmp_path, select_child):
    title = tmp_path / '4D5309C9'
    dlc = title / '00000002'
    dlc.mkdir(parents=True)
    with pytest.raises(ValueError) as error:
        server.fsrc.open_fh1_game(dlc if select_child else title)
    assert 'DLC content folders' in str(error.value)
    assert str(dlc) in str(error.value)
    assert 'base game' in str(error.value)


@pytest.mark.parametrize('select_child', [False, True])
def test_selecting_dlc_as_game_keeps_valid_game_and_routes_dlc(tmp_path, monkeypatch, select_child):
    from test_part_transfer import GAME
    if not GAME.exists():
        pytest.skip('Local FH1 GoD fixture unavailable')
    monkeypatch.setattr(server, 'SETTINGS', tmp_path / 'settings.json')
    server.S = server.Session()
    server.set_library(str(GAME), [])
    title = tmp_path / '4D5309C9'
    dlc = title / '00000002'
    dlc.mkdir(parents=True)
    server.set_library(str(dlc if select_child else title), [])
    assert server.S.cfg == {'game': str(GAME), 'dlc': [str(dlc)]}
    assert server.S.lib.game
    assert server.library_info()['path_notice']
    assert server.load_settings()['game'] == str(GAME)

