"""
fh1_sources.py - every place an FH1 donor car or ID can come from, behind one interface.

  BaseGame     the Games-on-Demand (SVOD) container: <...>/4D5309C9/00007000/<hash> + <hash>.data
  DLCPackage   an installed DLC, either an STFS file (console/USB layout, e.g. 4D5309C900000729)
               or an extracted folder (Xenia layout, <...>/00000002/<name>/Media/...)
  Donor        one donor car (base or DLC) exposing car files, wheel files, audio XMLs, DB rows
  scan_used_ids() collects car/engine/drivetrain/wheel/offer IDs and MediaNames from the base
               game and every installed DLC so a new car never collides.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import struct
import tempfile
import zipfile
from functools import cached_property
from pathlib import Path

import stfs
import xlzx


def zread(zf: zipfile.ZipFile, name: str) -> bytes:
    return xlzx.read_member(zf, name)          # handles method 21 (LZX) and stored/deflate


def open_db(blob: bytes) -> sqlite3.Connection:
    f = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
    f.write(blob); f.close()
    con = sqlite3.connect(f.name)

    return con


# ---------------------------------------------------------------------------
# base game (SVOD / Games on Demand)
# ---------------------------------------------------------------------------

class SVOD:
    """Reader for the GoD container (logic from tools/containers.py, validated on FH1 + FM4)."""

    def __init__(self, path):
        self.path = Path(path)
        h = self.path.read_bytes()
        self.start = int.from_bytes(h[0x395:0x398], "little") * 2
        self.files = [p.open("rb") for p in sorted(Path(str(path) + ".data").glob("Data*"))]
        self.shift = 2
        self.files[0].seek(0x2000); magic = self.files[0].read(36)
        if magic[:20] != b"MICROSOFT*XBOX*MEDIA":
            self.shift = 0; self.files[0].seek(0x12000); magic = self.files[0].read(36)
        if magic[:20] != b"MICROSOFT*XBOX*MEDIA":
            raise ValueError("not an Xbox media (GoD) container")
        self.entries = {}
        b, _ = struct.unpack_from("<II", magic, 20)
        self._walk(b, 0, "", set())

    def _offset(self, b):
        t = b - self.start + self.shift
        fi, fb = divmod(t, 0x14388)
        l0 = fb // 0x198 + 1
        off = fb * 2048 + (l0 + l0 // 0xA1C4 + 1) * 4096
        if off >= 0xA290000:
            fi += 1; off = off % 0xA290000 + 0x2000
        return fi, off

    def _read_at(self, b, n, skip=0):
        out = []
        while n:
            fi, off = self._offset(b)
            f = self.files[fi]; f.seek(off + skip)
            d = f.read(min(2048 - skip, n)); out.append(d); n -= len(d); b += 1; skip = 0
        return b"".join(out)

    def _walk(self, b, ordinal, parent, seen):
        if (b, ordinal) in seen:
            return
        seen.add((b, ordinal))
        pos = ordinal * 4
        data = self._read_at(b + pos // 2048, 269, pos % 2048)
        l, r, db, n, attr, nl = struct.unpack_from("<HHIIBB", data)
        name = data[14:14 + nl].decode("utf8", "replace")
        if l:
            self._walk(b, l, parent, seen)
        path = f"{parent}/{name}" if parent else name
        if attr & 16:
            if n:
                self._walk(db, 0, path, seen)
        else:
            self.entries[path.lower()] = (path, db, n)
        if r:
            self._walk(b, r, parent, seen)

    def read(self, path: str) -> bytes:
        p, b, n = self.entries[path.lower()]
        return self._read_at(b, n)

    def has(self, path: str) -> bool:
        return path.lower() in self.entries


def find_svod(folder: Path) -> Path | None:
    """Accepts the title folder (…/4D5309C9), its 00007000 subfolder, or the container file."""
    folder = Path(folder)
    if not folder.exists():
        return None
    cands = [folder] if folder.is_file() else list(folder.glob("**/*"))
    for p in cands:
        if p.is_file() and (p.parent / (p.name + ".data")).is_dir():
            return p
    return None


class ISOReader:
    """Xbox 360 disc image (.iso): XDVDFS, same directory format as the GoD container."""
    PARTITIONS = (0, 0xFD90000, 0x2080000, 0x18300000)          # plain/XISO, XGD3, XGD2, XGD1

    def __init__(self, path):
        self.path = Path(path)
        self.f = self.path.open("rb")
        for part in self.PARTITIONS:
            self.f.seek(part + 0x10000)
            magic = self.f.read(36)
            if magic[:20] == b"MICROSOFT*XBOX*MEDIA":
                self.part = part
                break
        else:
            raise ValueError("not an Xbox 360 disc image")
        root, size = struct.unpack_from("<II", magic, 20)
        self.entries = {}
        self._walk(root, 0, "", set())

    def _read(self, sector, n, skip=0):
        self.f.seek(self.part + sector * 2048 + skip)
        return self.f.read(n)

    def _walk(self, sector, ordinal, parent, seen):
        if (sector, ordinal) in seen:
            return
        seen.add((sector, ordinal))
        data = self._read(sector, 269, ordinal * 4)
        l, r, db, n, attr, nl = struct.unpack_from("<HHIIBB", data)
        if l == 0xFFFF:
            return
        name = data[14:14 + nl].decode("utf8", "replace")
        if l:
            self._walk(sector, l, parent, seen)
        path = f"{parent}/{name}" if parent else name
        if attr & 16:
            if n:
                self._walk(db, 0, path, seen)
        else:
            self.entries[path.lower()] = (path, db, n)
        if r:
            self._walk(sector, r, parent, seen)

    def read(self, path):
        p, db, n = self.entries[path.lower()]
        return self._read(db, n)

    def has(self, path):
        return path.lower() in self.entries


class FolderReader:
    """An extracted game folder (the one that contains media\\db\\gamedb.slt and default.xex)."""

    def __init__(self, root):
        self.root = Path(root)
        self.entries = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(self.root)).replace("\\", "/")
                self.entries[rel.lower()] = (rel, p, p.stat().st_size)

    def read(self, path):
        return self.entries[path.lower()][1].read_bytes()

    def has(self, path):
        return path.lower() in self.entries


def open_fh1_game(path) -> "BaseGame":
    """Games on Demand folder, an .iso, or an extracted game folder - whichever the user has."""
    p = Path(path) if path else None
    if not p or not p.exists():
        raise ValueError(f"FH1 game folder not found: {path}")
    if p.is_file() and p.suffix.lower() == ".iso":
        return BaseGame(ISOReader(p))
    cands = [p] if p.is_file() else [c for c in p.glob("**/*") if c.is_file() and (c.parent / (c.name + ".data")).is_dir()]
    cands.sort(key=lambda c: "4d5309c9" not in str(c).lower())      # FH1's title id first
    for c in cands:
        try:
            sv = SVOD(c)
        except Exception:                                  # noqa: BLE001 - other/partial containers
            continue
        if sv.has("media/db/gamedb.slt") and any(k.startswith("media/cars/") for k in sv.entries):
            return BaseGame(sv)
    if p.is_dir():
        hit = next(iter(p.glob("**/media/db/gamedb.slt")), None) or next(iter(p.glob("**/Media/db/gamedb.slt")), None)
        if hit:
            return BaseGame(FolderReader(hit.parents[2]))
        iso = next(iter(p.glob("**/*.iso")), None)
        if iso:
            return BaseGame(ISOReader(iso))
    raise ValueError(
        f"Couldn't find Forza Horizon game data in {p}.\n"
        "Point 'FH1 game' at one of these:\n"
        "  - the Games on Demand folder (…\\4D5309C9, containing 00007000)\n"
        "  - an extracted game folder (containing default.xex and media\\db\\gamedb.slt)\n"
        "  - a disc image (.iso)")


class BaseGame:
    def __init__(self, reader):
        self.sv = reader if hasattr(reader, "read") and hasattr(reader, "entries") else SVOD(reader)

    @cached_property
    def gamedb(self) -> bytes:
        return self.sv.read("media/db/gamedb.slt")

    def zip(self, path: str) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(self.sv.read(path)))

    @cached_property
    def audio_cmt(self):
        return self.zip("media/audio/cars/CarModelTuning.zip")

    @cached_property
    def audio_et(self):
        return self.zip("media/audio/cars/Engines/EngineTuning.zip")

    @cached_property
    def ui_textures(self):
        return self.zip("media/ui/Textures.zip")

    def car_media_names(self) -> list[str]:
        return sorted(p for (p, _, _) in self.sv.entries.values()
                      if re.match(r"media/cars/[^/]+\.zip$", p, re.I) and "Presets" not in p)


# ---------------------------------------------------------------------------
# installed DLC (STFS file or extracted folder)
# ---------------------------------------------------------------------------

class DLCPackage:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_file():
            self.kind = "stfs"
            self._r = stfs.STFSReader(self.path)
            self.title, self.display = self._r.title, self._r.display
            self._files = self._r.entries()
        else:
            self.kind = "folder"
            self.title, self.display = 0x4D5309C9, self.path.name
            self._files = None

    def names(self) -> list[str]:
        if self._files is not None:
            return list(self._files)
        return [str(p.relative_to(self.path)).replace("\\", "/") for p in self.path.rglob("*") if p.is_file()]

    def read(self, name: str) -> bytes:
        if self._files is not None:
            return self._r.read(name)
        return (self.path / name).read_bytes()

    @cached_property
    def zips(self) -> dict[str, zipfile.ZipFile]:
        out = {}
        for n in self.names():
            if n.lower().startswith("media/dlczips/") and n.lower().endswith(".zip"):
                try:
                    if self._files is None:                  # extracted folder: open in place
                        out[n] = zipfile.ZipFile(self.path / n)
                    else:
                        out[n] = zipfile.ZipFile(self._disk_copy(n))
                except zipfile.BadZipFile:
                    pass
        return out

    def _disk_copy(self, name: str) -> Path:
        """An inner zip of an STFS package, unpacked once to a shared cache file. Holding every DLC pack's
        zips in memory (several GB per process) ran 14 parallel build workers out of RAM."""
        import hashlib
        st = self.path.stat()
        tag = hashlib.sha1(f"{self.path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{name}".encode()).hexdigest()[:16]
        root = Path(os.environ.get("FH_PKG_CACHE") or Path(tempfile.gettempdir()) / "fh_pkg_cache")
        dst = root / f"{tag}_{Path(name).name}"
        if not dst.exists():
            root.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(f"{dst.name}.{os.getpid()}.part")
            part.write_bytes(self.read(name))
            try:
                if dst.exists():                            # another worker finished first: keep theirs
                    raise FileExistsError
                os.rename(part, dst)
            except OSError:
                part.unlink(missing_ok=True)
        for _ in range(50):                                 # Windows refuses opens while a rename is in flight
            try:
                with open(dst, "rb"):
                    return dst
            except PermissionError:
                import time
                time.sleep(0.2)
        return dst

    @cached_property
    def offer(self) -> int | None:
        for n in self.names():
            m = re.match(r"media/(\d+)\.puboffer$", n, re.I)
            if m:
                return int(m.group(1))
        return None

    def merges(self) -> list[bytes]:
        out = [self.read(n) for n in self.names() if re.search(r"/db/patch/[^/]+_merge\.slt$", n, re.I)]
        for zf in self.zips.values():
            out += [zread(zf, n) for n in zf.namelist() if re.search(r"db/patch/[^/]+_merge\.slt$", n, re.I)]
        return out

    def find_car_zip(self, media: str) -> zipfile.ZipFile | None:
        pre = f"media/cars/{media}/".lower()
        for zf in self.zips.values():
            if any(n.lower().startswith(pre) for n in zf.namelist()):
                return zf
        return None


def list_packages(content_dir: Path) -> list[DLCPackage]:
    """All FH1 DLC under …/4D5309C9/00000002 (STFS files and extracted folders)."""
    d = Path(content_dir)
    if d.name.lower() != "00000002":
        d = d / "00000002"
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        try:
            if p.is_file():
                with p.open("rb") as f:
                    if f.read(4) not in (b"LIVE", b"PIRS", b"CON "):
                        continue
            elif not (p / "Media").is_dir():
                continue
            out.append(DLCPackage(p))
        except Exception:                                  # noqa: BLE001 - skip unreadable packages
            continue
    return out


# ---------------------------------------------------------------------------
# donors
# ---------------------------------------------------------------------------

class Donor:
    """kind 'base' (from the game container) or 'dlc' (from an installed package)."""

    def __init__(self, media: str, *, base: BaseGame | None = None, package: DLCPackage | None = None):
        self.media, self.base, self.package = media, base, package
        self.kind = "dlc" if package else "base"
        if self.kind == "base":
            self.car_zip = base.zip(f"media/cars/{media}.zip")
            self._prefix = ""
        else:
            self.car_zip = package.find_car_zip(media)
            if self.car_zip is None:
                raise KeyError(f"{media} not found in {package.path.name}")
            self._prefix = f"media/cars/{media}/"

    # car folder ----------------------------------------------------------
    def car_files(self) -> list[str]:
        """Paths relative to the car folder."""
        pre = self._prefix.lower()
        return [n[len(pre):] for n in self.car_zip.namelist()
                if n.lower().startswith(pre) and not n.endswith("/") and len(n) > len(pre)]

    def read_car(self, rel: str) -> bytes:
        names = {n.lower(): n for n in self.car_zip.namelist()}
        return zread(self.car_zip, names[(self._prefix + rel).lower()])

    def has_car(self, rel: str) -> bool:
        return (self._prefix + rel).lower() in {n.lower() for n in self.car_zip.namelist()}

    # database ------------------------------------------------------------
    @cached_property
    def db_blob(self) -> bytes:
        if self.kind == "base":
            return self.base.gamedb
        for blob in self.package.merges():
            con = open_db(blob)
            try:
                if con.execute("select 1 from Data_Car where MediaName=? collate nocase", (self.media,)).fetchone():
                    return blob
            finally:
                con.close()
        raise KeyError(f"no merge DB in {self.package.path.name} defines {self.media}")

    @cached_property
    def row(self) -> dict:
        con = open_db(self.db_blob)
        cols = [c[1] for c in con.execute("pragma table_info(Data_Car)")]
        r = con.execute("select * from Data_Car where MediaName=? collate nocase", (self.media,)).fetchone()
        con.close()
        return dict(zip(cols, r))

    # wheels --------------------------------------------------------------
    def wheel_files(self) -> dict[str, bytes]:
        """{relative name: bytes} of the stock wheel model folder."""
        con = open_db(self.db_blob)
        r = con.execute("select MediaName from List_Wheels where ID=?", (self.row["StockWheelID"],)).fetchone()
        con.close()
        wmedia = r[0] if r else self.media
        if self.kind == "base":
            path = f"media/wheels/{wmedia}.zip"
            if not self.base.sv.has(path):
                return {}
            zf = self.base.zip(path)
            return {n: zread(zf, n) for n in zf.namelist() if not n.endswith("/")}
        pre = f"media/wheels/{wmedia}/".lower()
        for zf in self.package.zips.values():
            hits = [n for n in zf.namelist() if n.lower().startswith(pre) and not n.endswith("/")]
            if hits:
                return {n[len(pre):]: zread(zf, n) for n in hits}
        return {}

    # audio / ui ----------------------------------------------------------
    def audio_xml(self) -> dict[str, bytes]:
        """{'CarModelTuning/<media>_CMT.xml': .., 'Engines/EngineTuning/<engine>_ET.xml': ..}"""
        con = open_db(self.db_blob)
        emedia = con.execute("""select e.MediaName from Data_Engine e join List_UpgradeEngine u
                                on u.EngineID=e.EngineID where u.Ordinal=? and u.IsStock=1""",
                             (self.row["Id"],)).fetchone()
        con.close()
        want = {f"carmodeltuning/{self.media.lower()}_cmt.xml": f"CarModelTuning/{self.media}_CMT.xml"}
        if emedia:
            want[f"enginetuning/{emedia[0].lower()}_et.xml"] = f"Engines/EngineTuning/{emedia[0]}_ET.xml"
        out = {}
        if self.kind == "base":
            for zf in (self.base.audio_cmt, self.base.audio_et):
                for n in zf.namelist():
                    for key, dst in want.items():
                        if n.lower().endswith(key.split("/", 1)[1]):
                            out[dst] = zread(zf, n)
        else:
            for zf in self.package.zips.values():
                for n in zf.namelist():
                    low = n.lower()
                    for key, dst in want.items():
                        if low.endswith(key):
                            out[dst] = zread(zf, n)
            for n in self.package.names():                  # loose (UDLC-style) folders
                low = n.lower()
                for key, dst in want.items():
                    if low.endswith(key.split("/", 1)[1]) and dst not in out:
                        out[dst] = self.package.read(n)
        return out

    def thumbnail(self) -> tuple[bytes, str] | None:
        """(bytes, extension) of the Autoshow thumbnail; DLC uses .xds or .tga."""
        cid = self.row["Id"]
        zips = [self.base.ui_textures] if self.kind == "base" else list(self.package.zips.values())
        for ext in (".xds", ".tga"):
            for zf in zips:
                for n in zf.namelist():
                    if n.lower().endswith(f"thumbnail_{cid}{ext}"):
                        return zread(zf, n), ext
        return None


# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

def _ids_from_db(blob: bytes, used: dict):
    con = open_db(blob)
    try:
        tabs = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        q = lambda s: [r for r in con.execute(s)]
        if "Data_Car" in tabs:
            for i, m in q("select Id, MediaName from Data_Car"):
                used["car"].add(i); used["media"].add(str(m).lower())
        if "Data_Engine" in tabs:
            for (i, m) in q("select EngineID, MediaName from Data_Engine"):
                used["engine"].add(i); used["engine_media"].add(str(m).lower())
        if "Data_Drivetrain" in tabs:
            used["drivetrain"] |= {r[0] for r in q("select DrivetrainID from Data_Drivetrain")}
        if "List_Wheels" in tabs:
            for (i, m) in q("select ID, MediaName from List_Wheels"):
                used["wheel"].add(i); used["wheel_media"].add(str(m).lower())
        if "ContentOffers" in tabs:
            used["offer"] |= {r[0] for r in q("select PubOfferingID from ContentOffers")}
        # ids also live in rows whose main record is gone: FH1's own database keeps CameraOverrides /
        # CarExceptions rows for FM4 cars it dropped, and a DLC row reusing such a key breaks its merge
        kinds = {"ordinal": "car", "carid": "car", "engineid": "engine", "drivetrainid": "drivetrain"}
        for t in tabs:
            for col in (r[1] for r in con.execute(f'pragma table_info("{t}")')):
                kind = kinds.get(col.lower())
                if kind:
                    used[kind] |= {r[0] for r in con.execute(f'select distinct "{col}" from "{t}"') if isinstance(r[0], int)}
            if t == "List_TorqueCurve":                  # curve ids are engine*1000 + n
                used["engine"] |= {r[0] // 1000 for r in q("select TorqueCurveID from List_TorqueCurve")}
    finally:
        con.close()


def scan_used_ids(base: BaseGame | None, packages: list[DLCPackage], log=print) -> dict:
    used = {k: set() for k in ("car", "engine", "drivetrain", "wheel", "offer", "media",
                                "engine_media", "wheel_media")}
    if base:
        _ids_from_db(base.gamedb, used)
        used["media"] |= {Path(p).stem.lower() for p in base.car_media_names()}
    for pkg in packages:
        if pkg.offer is not None:
            used["offer"].add(pkg.offer)
        for blob in pkg.merges():
            try:
                _ids_from_db(blob, used)
            except Exception as e:                         # noqa: BLE001
                log(f"[ids] {pkg.path.name}: {e}")
    return used
