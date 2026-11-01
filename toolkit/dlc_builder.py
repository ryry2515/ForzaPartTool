"""dlc_builder.py - assemble an installable Forza Horizon 1 DLC package from files you provide.

What it writes (layout measured from a working multi-part pack):
    Media/<offer>.puboffer                                          one space + CRLF
    Media/DLCZips/<offer>_pri_99.zip, <offer>part2_pri_99.zip ...   your car files (stored zip: no compression needed)
    Media/DLCZips/<offer>_pri_99/Media/db/patch/<offer>00_merge.slt rows merged into the game database at boot
    Media/DLCZips/StringTables_pri_99<offer>/<LANG>/<Table>.str     string tables (superset of the base game's)
    Media/DLCZips/stringtables.xml, zipmount.xml                    which tables the pack overrides and where they are mounted
    plus any loose files you pass (for example engine sound banks under Media/DLCZips/<offer>_pri_99/Media/audio/...)

The result can be written as a folder for Xenia (with the .header file Xenia needs) and/or as an STFS file for a modded console.

Typical use:
    from dlc_builder import *
    db = new_merge_db(base_gamedb_bytes, ["ContentOffers", "Data_Car"])     # empty tables with the game's own schema
    db.execute("insert into ContentOffers ...")                              # add your rows
    files = build_files(3070, merge_db=finish_merge_db(db), car_zips={"cars": {"media/cars/XXX/...": b"..."}}, string_tables={...})
    folder = write_xenia_folder(files, "out", 3070, "My pack")               # then copy into Xenia (xenia_install.py) when Xenia is closed
    write_stfs(files, "out/console/" + package_name(3070), "My pack")        # for a modded 360
    verify_folder(folder)

Nothing here installs anything. Close Xenia before installing.
"""
from __future__ import annotations

import io
import sqlite3
import struct
import tempfile
import zipfile
from pathlib import Path

TITLE_ID = 0x4D5309C9
PUBOFFER = b" \r\n"
# Language folder names as found in a working pack (and in the base game's string table archives).
LANGUAGES = ["CHT", "DA", "DE", "DEV", "EN", "ES", "FI", "FR", "GB", "HU", "IT", "JP", "KO", "LOC", "MX", "NB", "PL", "RU", "SV", "br", "cz", "nl"]


def package_name(offer: int) -> str:
    return f"4D5309C9{offer:08d}"


def offer_id_text(offer: int) -> str:
    """ContentOffers.ID for offer number `offer` (for example 3060 -> '4D5309C93060FFFF')."""
    return f"4D5309C9{offer:04d}FFFF"


def offer_id_int(offer: int) -> int:
    """ContentOffers.OfferId: the same value as a 64-bit integer."""
    return int(offer_id_text(offer), 16)


def content_offer_row(offer: int, display_name_ref: str, description_ref: str = "") -> dict:
    """A ContentOffers row shaped like the one in a working pack (IsDLC 1, installed and purchased).
    display_name_ref / description_ref are '_&N' string references."""
    return {"ID": offer_id_text(offer), "OfferId": offer_id_int(offer), "DisplayName": display_name_ref, "Description": description_ref,
            "PubOfferingID": offer, "LicenseLevel": 2, "LicenseMask": 0, "IsDLC": 1, "ImagePath": "", "ThumbPath": "",
            "IsInstalled": 1, "IsPurchased": 1, "IsVIP": 0, "Hidden": 0, "IsALaCarte": 0, "SeasonPassOffer": 0}


def make_zip(members: dict[str, bytes]) -> bytes:
    """Stored (uncompressed) zip with the members in the order given. Keep the order of an existing archive when you rewrite one."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data)
    return buf.getvalue()


def stringtables_xml(tables: list[str]) -> bytes:
    body = "".join(f'   <table Name="{t}" /> \r\n' for t in tables)
    return f'<?xml version="1.0" ?> \r\n<stringtables> \r\n{body}</stringtables> \r\n'.encode()


def zipmount_xml(offer: int, extra_mounts: list[tuple[str, str]] = (), strings: bool = True) -> bytes:
    """Mount the string table folder, plus optional extra mounts [(zip/folder name, game path)] such as engine tuning folders."""
    rows = [f'   <zip Name="StringTables_pri_99{offer}" Mount="game:\\Media\\StringTables\\" AltRootPath="game:\\Media\\StringTables\\" ShouldCache="0"  />'] if strings else []
    rows += [f'   <zip Name="{n}" Mount="{m}" AltRootPath="same" ShouldCache="0"/>' for n, m in extra_mounts]
    return ('<?xml version="1.0" ?> \r\n<zipmount> \r\n' + " \r\n".join(rows) + ' \r\n</zipmount> \r\n').encode()


def new_merge_db(base_gamedb: bytes, tables: list[str]) -> sqlite3.Connection:
    """An empty database that has only the named tables, created with the base game's own CREATE statements.
    Insert your rows, then call finish_merge_db(). Only add rows: a primary key the game already has breaks the boot-time merge."""
    f = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
    f.write(base_gamedb)
    f.close()
    src = sqlite3.connect(f.name)
    out = sqlite3.connect(tempfile.NamedTemporaryFile(suffix=".slt", delete=False).name)
    for t in tables:
        row = src.execute("select sql from sqlite_master where type='table' and name=?", (t,)).fetchone()
        if not row:
            raise KeyError(f"table {t} is not in the game database")
        out.execute(row[0])
    src.close()
    return out


def insert(db: sqlite3.Connection, table: str, row: dict) -> None:
    cols = list(row)
    db.execute(f'insert into "{table}" ({",".join(chr(34) + c + chr(34) for c in cols)}) values ({",".join("?" * len(cols))})', [row[c] for c in cols])


def finish_merge_db(db: sqlite3.Connection) -> bytes:
    db.commit()
    path = db.execute("pragma database_list").fetchone()[2]
    db.close()
    return Path(path).read_bytes()


def build_files(offer: int, *, merge_db: bytes, car_zips: dict[str, dict[str, bytes]] | None = None,
                string_tables: dict[str, dict[str, bytes]] | None = None, loose_files: dict[str, bytes] | None = None,
                extra_mounts: list[tuple[str, str]] = ()) -> dict[str, bytes]:
    """Return {path inside the package: bytes}.

    car_zips      {label: {member path: bytes}} - the first becomes <offer>_pri_99.zip, the next <offer>part2_pri_99.zip, ... Keep one car's files in one zip, each zip about 900 MB or less.
    string_tables {table name: {LANG: bytes of a .str file}} - build the bytes with lsb2.write(). A mounted table replaces the game's table of that name, so it must contain every key the game uses.
    loose_files   {path relative to Media/DLCZips/<offer>_pri_99/ : bytes}
    """
    files: dict[str, bytes] = {f"Media/{offer}.puboffer": PUBOFFER}
    for i, (_, members) in enumerate((car_zips or {}).items()):
        files[f"Media/DLCZips/{offer}_pri_99.zip" if i == 0 else f"Media/DLCZips/{offer}part{i + 1}_pri_99.zip"] = make_zip(members)
    files[f"Media/DLCZips/{offer}_pri_99/Media/db/patch/{offer}00_merge.slt"] = merge_db
    tables = ["ContentOffers", "Data_Car"]
    for table, langs in (string_tables or {}).items():
        if table not in tables:
            tables.append(table)
        for lang, data in langs.items():
            files[f"Media/DLCZips/StringTables_pri_99{offer}/{lang}/{table}.str"] = data
    if string_tables or extra_mounts:                  # a pack that ships no strings must not declare string tables (the game would look for ours and find nothing)
        if string_tables:
            files["Media/DLCZips/stringtables.xml"] = stringtables_xml(tables)
        files["Media/DLCZips/zipmount.xml"] = zipmount_xml(offer, extra_mounts, strings=bool(string_tables))
    for rel, data in (loose_files or {}).items():
        files[f"Media/DLCZips/{offer}_pri_99/{rel}"] = data
    return files


def xenia_header(name: str, display: str, title_id: int = TITLE_ID) -> bytes:
    """The 332-byte .header file Xenia needs next to an installed package."""
    h = bytearray(332)
    struct.pack_into(">II", h, 0, 1, 2)
    d = display.encode("utf-16-be")[:254]
    h[0x8:0x8 + len(d)] = d
    n = name.encode("ascii")[:42]
    h[0x108:0x108 + len(n)] = n
    struct.pack_into(">I", h, 0x140, title_id)
    return bytes(h)


def write_xenia_folder(files: dict[str, bytes], out_dir, offer: int, display: str) -> Path:
    """Write <out_dir>/xenia/<package name>/ (the package) and <out_dir>/xenia/Headers/00000002/<package name>.header.
    To install, copy the package folder to <xenia>/content/0000000000000000/4D5309C9/00000002/ and the header to .../4D5309C9/Headers/00000002/ with Xenia closed."""
    name = package_name(offer)
    root = Path(out_dir) / "xenia"
    folder = root / name
    for n, data in files.items():
        p = folder / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    hdr = root / "Headers" / "00000002" / f"{name}.header"
    hdr.parent.mkdir(parents=True, exist_ok=True)
    hdr.write_bytes(xenia_header(name, display))
    return folder


def write_stfs(files: dict[str, bytes], out_path, display: str, description: str = "") -> Path:
    """Console package (STFS). A file inside must stay under 4 GB and the whole package under about 3.8 GiB; split larger packs into more offers."""
    import stfs
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    p = stfs.build_stfs(files, Path(out_path), display_name=display, description=description)
    r = stfs.STFSReader(p)
    errs = r.verify()
    same = set(r.entries()) == set(files) and all(r.read(n) == d for n, d in files.items())
    r.close()
    if errs or not same:
        raise RuntimeError(f"STFS check failed: {errs}, identical={same}")
    return p


def verify_folder(folder) -> list[str]:
    """Offline checks of a built package folder. Returns a list of problems (empty = looks right). This does not run the game."""
    import fh1_sources as srcmod
    problems = []
    pkg = srcmod.DLCPackage(Path(folder))
    if pkg.offer is None:
        problems.append("no Media/<offer>.puboffer")
    for n, z in pkg.zips.items():
        bad = z.testzip()
        if bad:
            problems.append(f"{n}: bad member {bad}")
        low = [m.lower() for m in z.namelist()]
        if len(low) != len(set(low)):
            problems.append(f"{n}: two members differ only by case (crashes the game)")
    merges = pkg.merges()
    if len(merges) != 1:
        problems.append(f"expected one merge database, found {len(merges)}")
    for blob in merges:
        con = srcmod.open_db(blob)
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            problems.append("merge database fails the SQLite integrity check")
        offers = [r[0] for r in con.execute("select ID from ContentOffers")] if con.execute("select 1 from sqlite_master where name='ContentOffers'").fetchone() else []
        if pkg.offer is not None and offer_id_text(pkg.offer) not in offers:
            problems.append(f"ContentOffers has no row with ID {offer_id_text(pkg.offer)}")
    names = {n.lower() for n in pkg.names()}
    if any(n.startswith("media/dlczips/stringtables_pri_99") for n in names) and "media/dlczips/stringtables.xml" not in names:
        problems.append("string tables are shipped but stringtables.xml is missing")
    return problems
