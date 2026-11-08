"""A donor car from a DLC package whose own database has no primary-key declarations (seen with cars imported from Forza Horizon 2 by
another tool) used to give 39 'UNIQUE constraint failed' problems: the copied rows kept the donor's Id values."""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'toolkit'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clone_car as cc
import dlc_builder as dlcb
import fh1_sources as src
from test_part_transfer import GAME


def strip_keys(blob: bytes) -> bytes:
    f = tempfile.NamedTemporaryFile(suffix='.slt', delete=False)
    f.write(blob)
    f.close()
    con = sqlite3.connect(f.name)
    for (t,) in con.execute("select name from sqlite_master where type='table'").fetchall():
        con.execute(f'create table "{t}__x" as select * from "{t}"')
        con.execute(f'drop table "{t}"')
        con.execute(f'alter table "{t}__x" rename to "{t}"')
    con.commit()
    con.close()
    data = Path(f.name).read_bytes()
    os.unlink(f.name)
    return data


def test_cols_takes_missing_keys_from_the_reference():
    a, ref = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    a.execute('create table T (Id INTEGER, Name TEXT, Other)')
    ref.execute('create table T (Id INTEGER PRIMARY KEY, Name TEXT, Other INTEGER)')
    assert cc._cols(a, 'T') == [('Id', 0, 'INTEGER'), ('Name', 0, 'TEXT'), ('Other', 0, '')]
    assert cc._cols(a, 'T', ref) == [('Id', 1, 'INTEGER'), ('Name', 0, 'TEXT'), ('Other', 0, 'INTEGER')]
    b = sqlite3.connect(':memory:')                         # a donor that does declare its keys is left alone
    b.execute('create table T (Id INTEGER, Name TEXT PRIMARY KEY)')
    assert cc._cols(b, 'T', ref) == cc._cols(b, 'T')
    assert cc._cols(a, 'Missing', ref) == []


def test_clone_from_a_package_donor_without_primary_keys(monkeypatch):
    if not GAME.exists():
        pytest.skip('Local FH1 integration fixture is unavailable')
    game = src.open_fh1_game(GAME)
    media = Path(next(n for n in game.car_media_names() if 'ALF_8C' in n.upper())).stem
    out = Path(tempfile.mkdtemp(prefix='fpt_keys_'))
    try:
        used = src.scan_used_ids(game, [], log=lambda *_: None)
        first = cc.same_length_name(media, used['media'])
        res = cc.clone_car(game, media, first, 9500, [], log=lambda *_: None)
        assert not res['report']['problems']
        pkg = src.DLCPackage(dlcb.write_xenia_folder(res['files'], str(out), 9500, 'Keys test'))
        original = src.DLCPackage.merges
        monkeypatch.setattr(src.DLCPackage, 'merges', lambda self: [strip_keys(b) for b in original(self)])
        used = src.scan_used_ids(game, [pkg], log=lambda *_: None)
        second = cc.same_length_name(first, used['media'])
        res2 = cc.clone_car(game, first, second, 9501, [pkg], donor_package=pkg, log=lambda *_: None)
        assert res2['report']['problems'] == []
        assert res2['report']['ids']['car'] != res['report']['ids']['car']
    finally:
        shutil.rmtree(out, ignore_errors=True)
