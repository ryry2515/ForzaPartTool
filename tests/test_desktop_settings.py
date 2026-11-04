import importlib.util
import sys
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
