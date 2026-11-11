"""Replace mode: the same Data_Car id, MediaName = the new folder name, the database change as a plain SQL file (execute.sql)."""
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))
import clone_car as cc  # noqa: E402
import dlc_builder as b  # noqa: E402
import fh1_sources as src  # noqa: E402

GAME = os.environ.get("FH1_GAME")
pytestmark = pytest.mark.skipif(not GAME, reason="set FH1_GAME to a game folder")
DONOR = "FER_F142_10"


@pytest.fixture(scope="module")
def built():
    game = src.open_fh1_game(Path(GAME))
    new = cc.same_length_name(DONOR, set())
    res = cc.replace_car(game, DONOR, new, 3999, [], log=lambda *_: None)
    return game, new, res


def test_sql_keeps_the_id_and_changes_only_media_name(built):
    game, new, res = built
    base = sqlite3.connect(":memory:")
    base.deserialize(game.gamedb)
    before = base.execute("select * from Data_Car where MediaName=?", (DONOR,)).fetchone()
    cols = [d[1] for d in base.execute("pragma table_info(Data_Car)")]
    n = base.execute("select count(*) from Data_Car").fetchone()[0]
    base.executescript(res["sql"])
    after = base.execute("select * from Data_Car where Id=?", (before[0],)).fetchone()
    assert base.execute("select count(*) from Data_Car").fetchone()[0] == n
    diff = [c for c, x, y in zip(cols, before, after) if x != y]
    assert diff == ["MediaName"] and after[cols.index("MediaName")] == new


def test_package_ships_sql_not_slt_and_the_renamed_folder(built):
    _, new, res = built
    names = list(res["files"])
    assert any(n.endswith("_execute.sql") for n in names) and not any(n.endswith(".slt") for n in names)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as out:
        folder = b.write_xenia_folder(res["files"], out, 3999, "t")
        assert b.verify_folder(folder) == []
        z = zipfile.ZipFile(next(Path(folder).rglob("*_pri_99.zip")))
        assert any(n.startswith(f"media/cars/{new}/") for n in z.namelist())
        assert not any(n.startswith(f"media/cars/{DONOR}/") for n in z.namelist())


def test_same_name_is_refused():
    game = src.open_fh1_game(Path(GAME))
    with pytest.raises(ValueError):
        cc.replace_car(game, DONOR, DONOR, 3999, [], log=lambda *_: None)
