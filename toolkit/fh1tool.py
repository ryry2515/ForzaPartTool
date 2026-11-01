"""fh1tool.py - command-line access to Forza Horizon 1 game files, for people and for AI assistants.

GAME is the user's own Forza Horizon 1 install: the Games on Demand folder (the one that holds 00007000 and is named 4D5309C9), an extracted folder, or an .iso.
Paths use forward slashes and are matched case-insensitively. Inside an archive use  archive.zip::member/path  (for example media/UI.zip::ResPack.xml).

  python fh1tool.py GAME ls [REGEX]                    list top-level files (path, size) whose path matches REGEX
  python fh1tool.py GAME zipls ARCHIVE [REGEX]         list the members of a zip archive in the game
  python fh1tool.py GAME cat PATH [--out FILE]         print or save a file (text is printed, binary is saved with --out)
  python fh1tool.py GAME db "SQL"  [--limit N]         run read-only SQL on the base game database (gamedb.slt)
  python fh1tool.py GAME tables [TABLE]                list tables with row counts, or the columns of one table
  python fh1tool.py GAME str LANG TABLE [KEY]          read a string table (media/stringtables/<lang>.zip): all keys, or one key; LANG is en, fr, ...
  python fh1tool.py GAME car MEDIANAME                 list the files of one car archive and its database rows (Data_Car, Data_Engine, ...)
  python fh1tool.py carbin FILE_OR_GAME_PATH [--game GAME]   summarise a .carbin model (sections, sub-meshes, vertex pools)
  python fh1tool.py xds FILE [--png OUT.png]           describe an .xds texture, optionally decode the base level to PNG
  python fh1tool.py packages CONTENT_DIR               list DLC packages in an Xenia content folder (offer number, files, merge databases)
  python fh1tool.py check GAME MERGE.slt [--package P]...   check a merge database for primary-key collisions with the game and other packages

Read-only: nothing here writes into the game or into Xenia.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fh1_sources as srcmod  # noqa: E402
import lsb2  # noqa: E402
import xlzx  # noqa: E402


def open_game(path):
    return srcmod.open_fh1_game(Path(path))


def read_path(game, spec: str) -> bytes:
    if "::" in spec:
        arc, member = spec.split("::", 1)
        z = game.zip(arc)
        names = {n.lower(): n for n in z.namelist()}
        if member.lower() not in names:
            raise SystemExit(f"{member} is not in {arc}")
        return xlzx.read_member(z, names[member.lower()])
    return game.sv.read(spec)


def human(n):
    return f"{n / 1048576:.1f} MB" if n > 1048576 else f"{n / 1024:.1f} KB" if n > 1024 else f"{n} B"


def db_of(game) -> sqlite3.Connection:
    blob = game.gamedb() if callable(game.gamedb) else game.gamedb
    return srcmod.open_db(blob)


def cmd_ls(a):
    game = open_game(a.game)
    rx = re.compile(a.regex or ".", re.I)
    rows = sorted((p, b) for p, _, b in game.sv.entries.values() if rx.search(p))
    for p, b in rows:
        print(f"{p}\t{b}")
    print(f"# {len(rows)} files", file=sys.stderr)


def cmd_zipls(a):
    game = open_game(a.game)
    z = game.zip(a.archive)
    rx = re.compile(a.regex or ".", re.I)
    n = 0
    for i in z.infolist():
        if rx.search(i.filename):
            print(f"{i.filename}\t{i.file_size}")
            n += 1
    print(f"# {n} members", file=sys.stderr)


def cmd_cat(a):
    data = read_path(open_game(a.game), a.path)
    if a.out:
        Path(a.out).write_bytes(data)
        print(f"wrote {a.out} ({len(data)} bytes)")
        return
    try:
        sys.stdout.write(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise SystemExit(f"{a.path} is binary ({len(data)} bytes); use --out FILE")


def cmd_db(a):
    con = db_of(open_game(a.game))
    cur = con.execute(a.sql)
    if cur.description:
        print("\t".join(d[0] for d in cur.description))
    for i, row in enumerate(cur):
        if i >= a.limit:
            print(f"# stopped at {a.limit} rows (use --limit)", file=sys.stderr)
            break
        print("\t".join("" if v is None else str(v) for v in row))


def cmd_tables(a):
    con = db_of(open_game(a.game))
    if a.table:
        for cid, name, typ, notnull, dflt, pk in con.execute(f'pragma table_info("{a.table}")'):
            print(f"{name}\t{typ}\t{'PK' if pk else ''}")
        return
    for (t,) in con.execute("select name from sqlite_master where type='table' order by name"):
        print(f"{t}\t{con.execute(f'select count(*) from {chr(34)}{t}{chr(34)}').fetchone()[0]}")


def cmd_str(a):
    game = open_game(a.game)
    z = game.zip(f"media/stringtables/{a.lang}.zip")
    names = {n.lower(): n for n in z.namelist()}
    member = f"{a.table}.str".lower()
    if member not in names:
        raise SystemExit(f"no {a.table}.str in {a.lang}.zip; tables: {', '.join(sorted(n for n in names))[:600]}")
    entries = lsb2.read(xlzx.read_member(z, names[member]))
    for e in entries:
        if a.key is None or str(e.key) == a.key or e.name == a.key:
            print(f"{e.key}\t{e.name}\t{e.text}")


def cmd_car(a):
    game = open_game(a.game)
    arc = f"media/cars/{a.media}.zip"
    z = game.zip(arc)
    print(f"# {arc}: {len(z.namelist())} members")
    for i in z.infolist():
        print(f"{i.filename}\t{i.file_size}")
    con = db_of(game)
    row = con.execute("select * from Data_Car where MediaName=?", (a.media,)).fetchone()
    if not row:
        return
    cols = [c[1] for c in con.execute("pragma table_info(Data_Car)")]
    car = dict(zip(cols, row))
    print("# Data_Car:", {k: car[k] for k in cols if k in ("Id", "MediaName", "MakeID", "ClassID", "Year", "Thumbnail", "IsUnicorn", "FamilyModelID", "BaseCost")})
    for (t,) in con.execute("select name from sqlite_master where type='table'"):
        tcols = [c[1] for c in con.execute(f'pragma table_info("{t}")')]
        for key in ("CarId", "CarID", "Id", "ID"):
            if key in tcols and t.startswith(("Data_", "List_Upgrade")) and t != "Data_Car":
                n = con.execute(f'select count(*) from "{t}" where "{key}"=?', (car["Id"],)).fetchone()[0]
                if n:
                    print(f"#   {t}.{key} = {car['Id']}: {n} row(s)")
                break


def cmd_carbin(a):
    import fh1_carbin as cb
    data = read_path(open_game(a.game), a.path) if a.game else Path(a.path).read_bytes()
    c = cb.parse(data)
    print(f"sections: {len(c.sections)}")
    for i, s in enumerate(c.sections):
        print(f"  [{i}] {s.name!r} version {s.version} subs {len(s.subs)} lod pool {s.lod.count} x {s.lod.stride} B, lod0 pool {s.lod0.count} x {s.lod0.stride} B")
        for j, sub in enumerate(s.subs[:6]):
            print(f"      sub {j}: {sub.name!r} lod {sub.lod} indices {len(sub.indices)} ({'strip' if sub.index_type == 6 else 'list'})")
        if len(s.subs) > 6:
            print(f"      ... {len(s.subs) - 6} more")
    print("round trip identical:", cb.build(c) == data)


def cmd_xds(a):
    import xds
    data = Path(a.file).read_bytes()
    print(xds.info(data))
    if a.png:
        xds.to_png(data, a.png)
        print("wrote", a.png)


def cmd_packages(a):
    for p in srcmod.list_packages(Path(a.content)):
        merges = p.merges()
        print(f"{p.path.name}\toffer {p.offer}\t{len(p.names())} files\t{len(merges)} merge db(s)\t{p.kind}")


def cmd_check(a):
    import db_check
    existing = {"FH1 base game": db_of(open_game(a.game))}
    for pk in a.package:
        for i, blob in enumerate(srcmod.DLCPackage(Path(pk)).merges()):
            existing[f"{Path(pk).name}#{i}"] = srcmod.open_db(blob)
    new = {Path(m).name: sqlite3.connect(m) for m in a.merges}
    probs = db_check.collisions(new, existing)
    print("\n".join(probs) if probs else f"OK: no key collisions in {len(new)} database(s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, **kw):
        p = sub.add_parser(name)
        for spec, opts in args:
            p.add_argument(*spec if isinstance(spec, tuple) else (spec,), **opts)
        p.set_defaults(fn=fn)
        return p
    G = ("game", {})
    add("ls", cmd_ls, G, ("regex", {"nargs": "?"}))
    add("zipls", cmd_zipls, G, ("archive", {}), ("regex", {"nargs": "?"}))
    add("cat", cmd_cat, G, ("path", {}), (("--out",), {}))
    add("db", cmd_db, G, ("sql", {}), (("--limit",), {"type": int, "default": 200}))
    add("tables", cmd_tables, G, ("table", {"nargs": "?"}))
    add("str", cmd_str, G, ("lang", {}), ("table", {}), ("key", {"nargs": "?"}))
    add("car", cmd_car, G, ("media", {}))
    add("carbin", cmd_carbin, ("path", {}), (("--game",), {}))
    add("xds", cmd_xds, ("file", {}), (("--png",), {}))
    add("packages", cmd_packages, ("content", {}))
    add("check", cmd_check, G, ("merges", {"nargs": "+"}), (("--package",), {"action": "append", "default": []}))
    # the first positional of every game command is the game folder, so `fh1tool.py GAME ls` is accepted too
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[1] in sub.choices and argv[0] not in sub.choices:
        argv = [argv[1], argv[0]] + argv[2:]
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
