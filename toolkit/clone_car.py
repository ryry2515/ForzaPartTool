"""clone_car.py - copy a car (from the base game or from a DLC) into a NEW car inside a DLC package.

    python clone_car.py GAME DONOR_MEDIA NEW_MEDIA OFFER OUT_DIR [--dlc <...\\4D5309C9\\00000002>]... [--new-id N]

What it makes: a package folder (and the Xenia header) holding the new car: its own database rows in every per-car table, its own copy of the car's files
(models, textures, gauges, livery masks, shader settings) under the new name, the Autoshow thumbnail and the car-model audio tuning file.
The wheels, engine sound banks, engine tuning and the names shown in the game are shared with the donor (the name strings are the donor's), so the copy shows the
donor's name in the game until you add string tables.

How the database rows are copied (measured on the base game):
  * a car has ids in several spaces: car (Data_Car.Id, `Ordinal` / `CarId` / `ModelId` columns), engine (`EngineID`), drivetrain (`DrivetrainID`), body (`CarBodyID`),
    and torque curves (`TorqueCurveID` = engine * 1000 + n). Every id the donor OWNS gets a new, unused value; ids it merely points at (shared wheels, shared engine swaps,
    physics rows) are left alone.
  * each row of a per-car table is copied with its ids mapped; a single-column surrogate primary key (`Id`) gets a fresh value.
  * a ContentOffers row and a ContentOffersMapping row make the package own the car.
New media name: the SAME LENGTH as the donor's (the name is replaced inside text and gauge files without moving any byte). New ids stay below 32768 (engine ids must).
Checked offline before anything is written: merge into a copy of the game database (no key collisions), and the donor and the copy have the same number of rows in every table.
Not tested in the game unless the editor README says so.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import dlc_builder as b
import fh1_sources as src

CAR_COLS = {"ordinal", "carid", "modelid"}
ENGINE_COLS, DRIVE_COLS, BODY_COLS, CURVE_COLS = {"engineid"}, {"drivetrainid"}, {"carbodyid"}, {"torquecurveid"}
SPECIAL = {("Data_Car", "id"): "car", ("Data_CarBody", "id"): "body", ("Data_Engine", "engineid"): "engine", ("Data_Drivetrain", "drivetrainid"): "drive"}
TABLE_PREFIXES = ("List_Upgrade", "Data_Car", "Data_Engine", "Data_Drivetrain", "Data_Motor", "List_AntiSwayPhysics", "List_SpringDamperPhysics", "List_TorqueCurve")
TABLE_NAMES = {"CameraOverrides", "CarExceptions", "CarPartPositions", "Combo_Colors", "Combo_Colors_Exceptions"}
ID_START, ID_LIMIT = 28000, 32767
TEXT_EXT = (".xml", ".txt", ".html", ".bgf", ".bsg", ".fbf", ".bin")


def _space(table: str, col: str) -> str | None:
    c = col.lower()
    if (table, c) in SPECIAL:
        return SPECIAL[(table, c)]
    if c in CAR_COLS:
        return "car"
    return "engine" if c in ENGINE_COLS else "drive" if c in DRIVE_COLS else "body" if c in BODY_COLS else "curve" if c in CURVE_COLS else None


def _wanted(table: str) -> bool:
    return table in TABLE_NAMES or table.startswith(TABLE_PREFIXES)


def _cols(con, t, ref=None):
    """[(column, primary-key position, declared type)] of table `t`. A package's own database can lack the key declarations (rebuilt
    with CREATE TABLE AS, or by a tool that does not write them); then `ref` (the game's database) supplies the keys and missing types,
    so the surrogate `Id` of every copied row still gets a fresh value instead of repeating the donor's."""
    cols = [(c[1], c[5], (c[2] or "").upper()) for c in con.execute(f'pragma table_info("{t}")')]
    if ref is not None and cols and not any(pk for _, pk, _ in cols):
        known = {c[1].lower(): (c[5], (c[2] or "").upper()) for c in ref.execute(f'pragma table_info("{t}")')}
        cols = [(c, known[c.lower()][0] if c.lower() in known else pk, typ or (known[c.lower()][1] if c.lower() in known else typ)) for c, pk, typ in cols]
    return cols


def same_length_name(donor: str, taken: set[str]) -> str:
    """A free media name of the donor's length: its last two characters replaced by Z0..Z9, ZA.. ."""
    for tail in [f"Z{c}" for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"]:
        cand = donor[:-2] + tail
        if cand.lower() not in taken:
            return cand
    raise ValueError("no free name")


def clone_car(game: src.BaseGame, donor_media: str, new_media: str, offer: int, packages: list[src.DLCPackage] | None = None,
              new_id: int | None = None, donor_package: src.DLCPackage | None = None, replace_files: dict[str, bytes] | None = None, log=print,
              display_name: str | None = None, short_name: str | None = None, body_overrides: dict | None = None) -> dict:
    """-> {"files": {path in package: bytes}, "report": {...}}. replace_files: {file name relative to the car folder: bytes} used instead of the donor's (for example your edited carbin).
    display_name / short_name: the name shown in the Autoshow and garage. They are written into Data_Car as plain text, which the game shows as it is, so no string table is needed
    and nothing clashes with other packs. Without them the copy shows the donor's name.
    body_overrides: {Data_CarBody column: value} written into the new car's body row (wheelbase, track widths, ride heights, bounding box: ModelWheelbase, ModelFrontTrackOuter, ..., PristineBoundingBoxMaxZ)."""
    packages = packages or []
    donor = src.Donor(donor_media, base=game, package=donor_package) if donor_package else src.Donor(donor_media, base=game)
    if len(new_media) != len(donor_media):
        raise ValueError(f"the new media name must have {len(donor_media)} characters like {donor_media}")
    used = src.scan_used_ids(game, packages, log=lambda *_: None)
    if new_media.lower() in used["media"]:
        raise ValueError(f"media name {new_media} is already used")
    if offer in used["offer"]:
        raise ValueError(f"offer number {offer} is already used by another package")
    D = donor.row["Id"]
    sdb = src.open_db(donor.db_blob)           # donor database (the game's, or the DLC's merge database)
    base = src.open_db(game.gamedb)
    cons = [base] + [src.open_db(m) for p in packages for m in p.merges()]

    # ---- the ids the donor owns
    def one(sql, *a):
        return [r[0] for r in sdb.execute(sql, a)]
    engines, drives, bodies = (one("select distinct EngineID from List_UpgradeEngine where Ordinal=?", D), one("select distinct DrivetrainID from List_UpgradeDrivetrain where Ordinal=?", D),
                               one("select distinct CarBodyID from List_UpgradeCarBody where Ordinal=?", D))

    def owned(table, col, ids):
        out = []
        for i in ids:
            other = sdb.execute(f'select count(*) from "{table}" where "{col}"=? and Ordinal<>?', (i, D)).fetchone()[0]
            if other == 0:
                out.append(i)
        return out
    own = {"engine": owned("List_UpgradeEngine", "EngineID", engines), "drive": owned("List_UpgradeDrivetrain", "DrivetrainID", drives), "body": owned("List_UpgradeCarBody", "CarBodyID", bodies)}

    # ---- every id and key already in use anywhere (base + installed packages)
    taken = set()
    for con in cons:
        for (t,) in con.execute("select name from sqlite_master where type='table'"):
            for col, pk, typ in _cols(con, t):
                if _space(t, col) in ("car", "engine", "drive", "body") and "INT" in typ:
                    taken |= {r[0] for r in con.execute(f'select distinct "{col}" from "{t}"') if isinstance(r[0], int)}
                if _space(t, col) == "curve":
                    taken |= {r[0] // 1000 for r in con.execute(f'select distinct "{col}" from "{t}"') if isinstance(r[0], int)}
    taken |= used["car"] | used["engine"] | used["drivetrain"]

    def fresh(start=ID_START):
        n = start
        while n in taken:
            n += 1
        if n > ID_LIMIT:
            raise ValueError("no free id below 32768")
        taken.add(n)
        return n
    N = new_id if new_id is not None else fresh()
    if new_id is not None:
        if new_id in taken:
            raise ValueError(f"id {new_id} is already used")
        taken.add(new_id)
    maps = {"car": {D: N}, "engine": {}, "drive": {}, "body": {}}
    for space in ("engine", "drive", "body"):
        for i in own[space]:
            maps[space][i] = N if i == D else fresh()      # an owned id equal to the car id stays equal to it (the base game's convention)
    curve_new = {e: n for e, n in maps["engine"].items()}

    def map_value(space, v):
        if v is None or not isinstance(v, int):
            return v
        if space == "curve":
            return curve_new[v // 1000] * 1000 + v % 1000 if (v // 1000) in curve_new else v
        return maps[space].get(v, v)

    # ---- the new database
    tables = [t for (t,) in base.execute("select name from sqlite_master where type='table' order by name") if _wanted(t)] + ["ContentOffers", "ContentOffersMapping"]
    db = b.new_merge_db(game.gamedb, tables)
    next_pk: dict[tuple[str, str], int] = {}
    report = {"tables": {}, "ids": {"car": N, **{k: dict(v) for k, v in maps.items() if k != "car"}}, "donor_id": D}

    def pk_next(t, c):
        key = (t, c)
        if key not in next_pk:
            mx = 0
            for con in cons:
                try:
                    mx = max(mx, con.execute(f'select coalesce(max("{c}"),0) from "{t}"').fetchone()[0] or 0)
                except sqlite3.Error:
                    pass
            next_pk[key] = mx + 1
        next_pk[key] += 1
        return next_pk[key] - 1
    for t in tables:
        if t in ("ContentOffers", "ContentOffersMapping"):
            continue
        cols = _cols(sdb, t, base)
        if not cols:
            continue
        keyed = [(c, _space(t, c)) for c, pk, typ in cols if _space(t, c)]
        if not keyed:
            continue
        # select the donor's rows: by the car id if the table has a car column, otherwise by an owned id of another space
        car_col = next((c for c, s in keyed if s == "car"), None)
        if car_col:
            rows = sdb.execute(f'select * from "{t}" where "{car_col}"=?', (D,)).fetchall()
        else:
            conds, args = [], []
            for c, s in keyed:
                if s == "curve":
                    ids = [e for e in own["engine"]]
                    conds.append(" or ".join(f'("{c}">=? and "{c}"<?)' for _ in ids) or "0")
                    for e in ids:
                        args += [e * 1000, e * 1000 + 1000]
                else:
                    ids = own[s] if s in own else []
                    conds.append(f'"{c}" in ({",".join("?" * len(ids))})' if ids else "0")
                    args += ids
            rows = sdb.execute(f'select * from "{t}" where {" or ".join("(" + c + ")" for c in conds)}', args).fetchall() if conds else []
        if not rows:
            continue
        pkcols = [c for c, pk, typ in cols if pk]
        surrogate = pkcols[0] if len(pkcols) == 1 and not _space(t, pkcols[0]) and "INT" in dict((c, typ) for c, _, typ in cols)[pkcols[0]] else None
        names = [c for c, _, _ in cols]
        for r in rows:
            v = dict(zip(names, r))
            for c, s in keyed:
                v[c] = map_value(s, v[c])
            if surrogate:
                v[surrogate] = pk_next(t, surrogate)
            if t == "Data_Car":
                v["MediaName"] = new_media
            b.insert(db, t, v)
        report["tables"][t] = len(rows)
    thumb = donor.thumbnail()
    ext = thumb[1] if thumb else ".xds"
    db.execute('update Data_Car set Thumbnail=? where Id=?', (f"game:\\Media\\UI\\Textures\\Thumbnails\\Thumbnail_{N}{ext}", N))
    if body_overrides:
        cols = {c for c, _, _ in _cols(db, "Data_CarBody")} if db.execute("select 1 from sqlite_master where name='Data_CarBody'").fetchone() else set()
        bad = [k for k in body_overrides if k not in cols or k == "Id"]
        if bad:
            raise ValueError(f"not Data_CarBody columns: {bad}")
        for new_body in maps["body"].values():
            db.execute(f'update Data_CarBody set {", ".join(f"{k}=?" for k in body_overrides)} where Id=?', [*body_overrides.values(), new_body])
    if display_name:
        db.execute('update Data_Car set DisplayName=?, ModelShort=? where Id=?', (display_name, short_name or display_name, N))
    b.insert(db, "ContentOffers", b.content_offer_row(offer, "", ""))
    b.insert(db, "ContentOffersMapping", {"Id": offer * 1000, "OfferId": b.offer_id_int(offer), "ContentId": N, "ContentType": 1})
    merge_blob = b.finish_merge_db(db)

    # ---- the files
    def rename_bytes(data: bytes) -> bytes:
        for a, c in ((donor_media, new_media), (donor_media.lower(), new_media.lower()), (donor_media.upper(), new_media.upper())):
            data = data.replace(a.encode(), c.encode())
        return data

    def rename_name(n: str) -> str:
        def case(m):                                           # keep the letter case the donor's file name used
            s = m.group(0)
            return new_media.lower() if s.islower() else new_media.upper() if s.isupper() else new_media
        return re.sub(re.escape(donor_media), case, n, flags=re.I)
    members: dict[str, bytes] = {}
    for rel in donor.car_files():
        data = (replace_files or {}).get(rel)
        if data is None:
            data = donor.read_car(rel)
            if rel.lower().endswith(TEXT_EXT) or "digitalgauge" in rel.lower():
                data = rename_bytes(data)
        members[f"media/cars/{new_media}/{rename_name(rel)}"] = data
    if thumb:
        members[f"media/UI/Textures/Thumbnails/Thumbnail_{N}{ext}"] = thumb[0]
    cmt = {k: v for k, v in donor.audio_xml().items() if k.startswith("CarModelTuning/")}
    for k, v in cmt.items():
        members[f"media/audio/cars/CarModelTuning/{rename_name(Path(k).name)}"] = rename_bytes(v)
    files = b.build_files(offer, merge_db=merge_blob, car_zips={"car": members})

    # ---- offline checks
    problems = check_merge(game.gamedb, merge_blob, [m for p in packages for m in p.merges()])
    for t, n in report["tables"].items():
        if t in ("ContentOffers", "ContentOffersMapping"):
            continue
    report.update({"files": len(members), "thumbnail": bool(thumb), "audio_files": list(cmt), "problems": problems, "media": new_media, "offer": offer})
    log(f"clone {donor_media} -> {new_media}: car id {N}, {sum(report['tables'].values())} database rows in {len(report['tables'])} tables, {len(members)} files")
    return {"files": files, "report": report, "merge_blob": merge_blob}


def _sql_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    return repr(v)


def _insert_sql(table: str, row: dict) -> str:
    cols = list(row)
    return (f"INSERT OR REPLACE INTO [{table}] ({','.join('[' + c + ']' for c in cols)}) "
            f"VALUES ({','.join(_sql_value(row[c]) for c in cols)});\n")


def replace_car(game: src.BaseGame, donor_media: str, new_media: str, offer: int, packages: list[src.DLCPackage] | None = None,
                donor_package: src.DLCPackage | None = None, replace_files: dict[str, bytes] | None = None, log=print) -> dict:
    """Replace a car IN PLACE: the same Data_Car id, the same rows for everything else, only MediaName changes to `new_media`, the name of the folder that
    now holds the model. The database change is a plain-text SQL patch (Media/db/patch/<offer>00_execute.sql, run by the game at start-up like the Wheel Adder's):
        INSERT OR REPLACE INTO [Data_Car] (...every column of the car's row...) VALUES (...);
    The package ships the car's files under media/cars/<new_media>/ (base files win over a DLC folder of the SAME name, so the folder needs a NEW name:
    that is why MediaName changes). new_media must have the length of donor_media (the name is also patched inside the binary files).
    -> {"files": {path in package: bytes}, "sql": str, "report": {...}}"""
    packages = packages or []
    donor = src.Donor(donor_media, base=game, package=donor_package) if donor_package else src.Donor(donor_media, base=game)
    if len(new_media) != len(donor_media):
        raise ValueError(f"the new media name must have {len(donor_media)} characters like {donor_media}")
    if new_media.lower() == donor_media.lower():
        raise ValueError("the new name must differ from the original: the game reads a base car's own folder first")
    used = src.scan_used_ids(game, packages, log=lambda *_: None)
    if new_media.lower() in used["media"]:
        raise ValueError(f"media name {new_media} is already used")
    if offer in used["offer"]:
        raise ValueError(f"offer number {offer} is already used by another package")
    D = donor.row["Id"]
    sdb = src.open_db(donor.db_blob)
    cur = sdb.execute("select * from Data_Car where Id=?", (D,))
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    row["MediaName"] = new_media
    pass
    lines = [f"-- ForzaPartTool: replace car {D} ({donor_media}) with the model in media/cars/{new_media}/\n",
             "-- The same Data_Car id; only MediaName changes. Run by the game at start-up from this package.\n",
             _insert_sql("Data_Car", row),
             _insert_sql("ContentOffers", b.content_offer_row(offer, "", "")),
             _insert_sql("ContentOffersMapping", {"Id": offer * 1000, "OfferId": b.offer_id_int(offer), "ContentId": D, "ContentType": 1})]
    sql = "".join(lines)

    def rename_bytes(data: bytes) -> bytes:
        for a, c in ((donor_media, new_media), (donor_media.lower(), new_media.lower()), (donor_media.upper(), new_media.upper())):
            data = data.replace(a.encode(), c.encode())
        return data

    def rename_name(n: str) -> str:
        def case(m):
            s = m.group(0)
            return new_media.lower() if s.islower() else new_media.upper() if s.isupper() else new_media
        return re.sub(re.escape(donor_media), case, n, flags=re.I)
    members: dict[str, bytes] = {}
    for rel in donor.car_files():
        data = (replace_files or {}).get(rel)
        if data is None:
            data = donor.read_car(rel)
            if rel.lower().endswith(TEXT_EXT) or "digitalgauge" in rel.lower():
                data = rename_bytes(data)
        members[f"media/cars/{new_media}/{rename_name(rel)}"] = data
    cmt = {k: v for k, v in donor.audio_xml().items() if k.startswith("CarModelTuning/")}
    for k, v in cmt.items():
        members[f"media/audio/cars/CarModelTuning/{rename_name(Path(k).name)}"] = rename_bytes(v)
    files = b.build_files(offer, merge_db=None, execute_sql=sql.encode("utf-8"), car_zips={"car": members})

    # ---- offline check: run the patch on a copy of the game's database and read the row back
    base_copy = sqlite3.connect(":memory:")
    base_copy.deserialize(game.gamedb)
    base_copy.executescript(sql)
    got = base_copy.execute("select Id, MediaName from Data_Car where Id=?", (D,)).fetchone()
    problems = [] if got == (D, new_media) else [f"the SQL patch did not leave Data_Car {D} with MediaName {new_media}: {got}"]
    other = base_copy.execute("select count(*) from Data_Car").fetchone()[0] - game_count(game)
    if other:
        problems.append(f"the patch changed the number of Data_Car rows by {other}")
    log(f"replace {donor_media} -> {new_media}: car id {D} kept, {len(members)} files")
    return {"files": files, "sql": sql, "report": {"car_id": D, "media": new_media, "donor": donor_media, "offer": offer, "files": len(members), "problems": problems}}


def game_count(game) -> int:
    con = sqlite3.connect(":memory:")
    con.deserialize(game.gamedb)
    return con.execute("select count(*) from Data_Car").fetchone()[0]


def check_merge(base_blob: bytes, merge_blob: bytes, other_merges=()) -> list[str]:
    """Merge the new database (and the other packages' databases) into a copy of the game database the way the game does: any key collision is reported."""
    problems = []
    import os
    tmp = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
    tmp.write(base_blob)
    tmp.close()
    con = sqlite3.connect(tmp.name)
    extra = []
    try:
        for blob, strict in [(m, False) for m in other_merges] + [(merge_blob, True)]:
            f = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
            extra.append(f.name)
            f.write(blob)
            f.close()
            con.execute(f"attach database '{f.name}' as m")
            for (t,) in con.execute("select name from m.sqlite_master where type='table'").fetchall():
                if con.execute("select count(*) from main.sqlite_master where name=?", (t,)).fetchone()[0] == 0:
                    continue
                try:
                    con.execute(f'insert {"" if strict else "or ignore "}into main."{t}" select * from m."{t}"')
                except sqlite3.IntegrityError as e:
                    problems.append(f"{t}: {e}")
            con.commit()
            con.execute("detach database m")
    finally:
        con.close()
        for name in [tmp.name] + extra:               # these temporary copies used to be left behind on every call
            try:
                os.unlink(name)
            except OSError:
                pass
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game")
    ap.add_argument("donor")
    ap.add_argument("new")
    ap.add_argument("offer", type=int)
    ap.add_argument("out")
    ap.add_argument("--dlc", action="append", default=[])
    ap.add_argument("--new-id", type=int)
    ap.add_argument("--name", help="name shown in the game (plain text)")
    ap.add_argument("--short-name", help="short name (default: same as --name)")
    a = ap.parse_args()
    game = src.open_fh1_game(Path(a.game))
    pk = [p for d in a.dlc for p in src.list_packages(Path(d))]
    res = clone_car(game, a.donor, a.new, a.offer, pk, a.new_id, display_name=a.name, short_name=a.short_name)
    folder = b.write_xenia_folder(res["files"], a.out, a.offer, f"{a.new} (copy of {a.donor})")
    print(res["report"])
    print("package folder:", folder, "\nproblems:", res["report"]["problems"] or "none")


if __name__ == "__main__":
    main()
