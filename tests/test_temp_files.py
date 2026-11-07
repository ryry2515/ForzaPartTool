"""The database helpers must not leave temporary .slt copies behind (they used to: 3-9 MB per call, tens of gigabytes in a day of sweeps)."""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolkit'))
import clone_car  # noqa: E402
import dlc_builder  # noqa: E402
import fh1_sources  # noqa: E402


def _database(path: Path, rows: int = 3) -> bytes:
    con = sqlite3.connect(path)
    con.execute('create table Data_Car (Id integer primary key, Name text)')
    con.execute('create table ContentOffers (OfferId integer primary key)')
    con.executemany('insert into Data_Car values (?, ?)', [(i, f'car{i}') for i in range(rows)])
    con.commit()
    con.close()
    return path.read_bytes()


def _slt_files() -> set:
    return {p.name for p in Path(tempfile.gettempdir()).glob('tmp*.slt')}


def test_open_db_leaves_no_temp_file_and_returns_the_data(tmp_path):
    blob = _database(tmp_path / 'game.slt')
    before = _slt_files()
    con = fh1_sources.open_db(blob)
    assert con.execute('select count(*) from Data_Car').fetchone()[0] == 3
    con.execute('insert into Data_Car values (99, "x")')          # a usable, writable in-memory copy
    con.close()
    assert _slt_files() == before


def test_merge_database_builder_cleans_up_after_itself(tmp_path):
    blob = _database(tmp_path / 'game.slt')
    before = _slt_files()
    db = dlc_builder.new_merge_db(blob, ['ContentOffers'])
    db.execute('insert into ContentOffers values (7)')
    merged = dlc_builder.finish_merge_db(db)
    assert _slt_files() == before
    out = tmp_path / 'merge.slt'
    out.write_bytes(merged)
    assert sqlite3.connect(out).execute('select OfferId from ContentOffers').fetchall() == [(7,)]


def test_a_missing_table_does_not_leave_files_behind(tmp_path):
    blob = _database(tmp_path / 'game.slt')
    before = _slt_files()
    try:
        dlc_builder.new_merge_db(blob, ['NoSuchTable'])
    except KeyError:
        pass
    assert _slt_files() == before


def test_check_merge_cleans_up_after_itself(tmp_path):
    base = _database(tmp_path / 'base.slt')
    merge_path = tmp_path / 'merge.slt'
    con = sqlite3.connect(merge_path)
    con.execute('create table Data_Car (Id integer primary key, Name text)')
    con.execute("insert into Data_Car values (100, 'new')")
    con.commit()
    con.close()
    before = _slt_files()
    assert clone_car.check_merge(base, merge_path.read_bytes()) == []
    assert _slt_files() == before
