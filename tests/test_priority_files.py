"""Priority files: a package whose files replace base-game files by path (Media/DLCZips/<offer>_pri_100001/<game path>)."""
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))
import dlc_builder as b  # noqa: E402
import priority_pack as pp  # noqa: E402


def _fake_game(root: Path) -> Path:
    """A tiny extracted game folder: one loose file, one file inside media/effects.zip, one inside media/ui/textures/Horizon.zip."""
    (root / "media" / "ui").mkdir(parents=True)
    (root / "media" / "ui" / "LoadingDefs.xml").write_bytes(b"<x/>")
    with zipfile.ZipFile(root / "media" / "effects.zip", "w") as z:
        z.writestr("Smoke.xml", "<Effect/>")
    (root / "media" / "ui" / "textures").mkdir()
    with zipfile.ZipFile(root / "media" / "ui" / "textures" / "Horizon.zip", "w") as z:
        z.writestr("Controller/A_Button.xds", b"xds")
    return root


def test_default_priority_is_the_one_seen_before_the_base_game():
    assert b.OVERRIDE_PRIORITY == 100001


def test_layout_of_a_priority_package():
    files = b.build_priority_files(9500, {"media/effects/Smoke.xml": b"a", "media/ui/x.txt": b"b"})
    assert files["Media/9500.puboffer"] == b.PUBOFFER
    assert files["Media/DLCZips/9500_pri_100001/media/effects/Smoke.xml"] == b"a"
    assert files["Media/DLCZips/9500_pri_100001/media/ui/x.txt"] == b"b"
    assert len(files) == 3
    assert not any("_pri_99" in n for n in files)


def test_priority_can_be_chosen():
    files = b.build_priority_files(9500, {"a/b.txt": b"x"}, priority=100500)
    assert "Media/DLCZips/9500_pri_100500/a/b.txt" in files


@pytest.mark.parametrize("bad", [0, -5, 1.5, "100001"])
def test_priority_must_be_a_positive_whole_number(bad):
    with pytest.raises(ValueError):
        b.build_priority_files(9500, {"a/b.txt": b"x"}, priority=bad)


def test_paths_are_cleaned():
    assert b.clean_game_path("game:\\Media\\effects\\Smoke.xml") == "Media/effects/Smoke.xml"
    assert b.clean_game_path("/media//ui/./x.txt") == "media/ui/x.txt"


@pytest.mark.parametrize("bad", ["", "/", "../x", "media/../../x", "C:\\x.txt", "media/a:b.txt"])
def test_paths_that_could_leave_the_folder_are_refused(bad):
    with pytest.raises(ValueError):
        b.clean_game_path(bad)


def test_two_paths_that_differ_only_by_case_are_refused():
    with pytest.raises(ValueError, match="only by case"):
        b.build_priority_files(9500, {"media/Effects/Smoke.xml": b"a", "media/effects/smoke.xml": b"b"})


def test_nothing_to_package_is_refused():
    with pytest.raises(ValueError):
        b.build_priority_files(9500, {})


def test_game_has_path_finds_loose_files_and_zip_members_ignoring_case(tmp_path):
    game = _fake_game(tmp_path)
    assert b.game_has_path(game, "media/ui/loadingdefs.xml")
    assert b.game_has_path(game, "media/effects/smoke.xml")
    assert b.game_has_path(game, "media/ui/textures/horizon/controller/a_button.xds")
    assert not b.game_has_path(game, "media/effects/nothing.xml")
    assert not b.game_has_path(game, "media/ui/textures/horizon/controller")      # a folder, not a file
    assert not b.game_has_path(game, "media/camera/cameraphysics.xml")
    assert b.missing_in_game(game, ["media/effects/Smoke.xml", "media/effects/Typo.xml"]) == ["media/effects/Typo.xml"]


def test_cli_writes_a_package_that_verifies(tmp_path, capsys):
    src = tmp_path / "files" / "media" / "effects"
    src.mkdir(parents=True)
    (src / "Smoke.xml").write_bytes(b"<Effect/>")
    game = _fake_game(tmp_path / "game")
    out = tmp_path / "out"
    code = pp.main(["--offer", "9500", "--name", "Test", "--out", str(out), "--dir", str(tmp_path / "files"), "--game", str(game)])
    assert code == 0
    folder = out / "xenia" / b.package_name(9500)
    assert (folder / "Media" / "DLCZips" / "9500_pri_100001" / "media" / "effects" / "Smoke.xml").read_bytes() == b"<Effect/>"
    assert (out / "xenia" / "Headers" / "00000002" / (b.package_name(9500) + ".header")).is_file()
    assert b.verify_folder(folder, allow_no_database=True) == []
    assert b.verify_folder(folder)            # without the flag a pack that has no database change is still reported
    assert "warning" not in capsys.readouterr().out


def test_cli_warns_about_a_low_priority_and_a_path_the_game_lacks(tmp_path, capsys):
    game = _fake_game(tmp_path / "game")
    f = tmp_path / "mine.xml"
    f.write_bytes(b"<x/>")
    code = pp.main(["--offer", "9501", "--name", "Test", "--out", str(tmp_path / "out"), "--priority", "99", "--game", str(game),
                    f"media/effects/Typo.xml={f}"])
    text = capsys.readouterr().out
    assert code == 0
    assert "below 100000" in text and "no file media/effects/Typo.xml" in text


def test_cli_refuses_a_path_outside_the_game_tree(tmp_path):
    f = tmp_path / "mine.xml"
    f.write_bytes(b"<x/>")
    with pytest.raises(SystemExit):
        pp.main(["--offer", "9502", "--name", "Test", "--out", str(tmp_path / "out"), f"../escape.xml={f}"])


def test_cli_can_also_write_a_console_package(tmp_path):
    f = tmp_path / "mine.xml"
    f.write_bytes(b"<x/>")
    out = tmp_path / "out"
    assert pp.main(["--offer", "9503", "--name", "Test", "--out", str(out), "--stfs", f"media/effects/Smoke.xml={f}"]) == 0
    assert (out / "console" / b.package_name(9503)).is_file()


GAME = os.environ.get("FH1_GAME")


@pytest.mark.skipif(not GAME, reason="set FH1_GAME to an extracted game folder")
def test_the_a_button_test_pack_inverts_the_colours_of_the_real_icon(tmp_path):
    import numpy as np
    import xds
    import xlzx
    files = pp.make_test_files(Path(GAME))
    assert list(files) == [pp.TEST_PATH]
    assert b.game_has_path(GAME, pp.TEST_PATH)
    with zipfile.ZipFile(Path(GAME) / pp.TEST_ZIP_MEMBER[0]) as zf:
        before = xds.decode(xlzx.read_member(zf, pp.TEST_ZIP_MEMBER[1]))
    after = xds.decode(files[pp.TEST_PATH])
    assert after.shape == before.shape
    opaque = before[..., 3] > 200
    assert opaque.any()
    # block compression is lossy, so compare means: the colour channels moved about as far as an inversion moves them
    delta = np.abs((255 - before[opaque, :3].astype(int)).mean() - after[opaque, :3].astype(int).mean())
    assert delta < 12
