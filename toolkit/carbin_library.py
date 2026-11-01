"""carbin_library.py - find car models in the two places they live, read them, and save an edited one back.

Two kinds of location:
  game   the base game. Readable from a Games on Demand folder or an .iso, but only an EXTRACTED game folder can be written to
         (files such as <game>/media/cars/<Car>.zip). Edits to a base-game car only take effect when saved into the extracted game:
         the game always prefers its own files over a DLC's files of the same path (guide section 3).
  dlc    installed or built DLC packages (folders; STFS packages are readable but not writable). A car that a DLC adds lives in
         <package>/Media/DLCZips/*.zip under media/cars/<Media>/.
Saving replaces ONE member of the zip. Every other member is copied byte for byte (compressed data, order and the Xbox LZX marker field are kept),
a backup <zip>.bak is made once, and the new file is checked before it replaces the old one.
Nothing is written while Xenia is running (the guide's rule: never change an installed pack under a running emulator).
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import fh1_sources as src
import xlzx


def xenia_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15).stdout.lower()
    except Exception:  # noqa: BLE001
        return False
    return any(line.startswith('"xenia') for line in out.splitlines())


def replace_zip_member(zip_path: Path, member: str, data: bytes, backup: bool = True) -> dict:
    """Write a copy of `zip_path` in which `member` holds `data` (deflated) and every other member is copied untouched; then replace the file."""
    zip_path = Path(zip_path)
    tmp = zip_path.with_name(zip_path.name + ".new")
    with zipfile.ZipFile(zip_path) as zr:
        infos = zr.infolist()
        names = [i.filename for i in infos]
        if member not in names:
            raise KeyError(f"{member} is not in {zip_path.name}")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zw:
            zw.comment = zr.comment
            for zi in infos:
                if zi.filename == member:
                    ni = zipfile.ZipInfo(zi.filename, time.localtime()[:6])
                    ni.compress_type, ni.external_attr, ni.create_system = zipfile.ZIP_DEFLATED, zi.external_attr, zi.create_system
                    zw.writestr(ni, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
                    continue
                zr.fp.seek(zi.header_offset)
                head = zr.fp.read(30)
                if head[:4] != b"PK\x03\x04":
                    raise ValueError(f"bad local header for {zi.filename}")
                nlen, elen = struct.unpack_from("<HH", head, 26)
                local_extra = zr.fp.read(nlen + elen)[nlen:]
                raw = zr.fp.read(zi.compress_size)
                out = zipfile.ZipInfo(zi.filename, zi.date_time)
                out.compress_type, out.CRC = zi.compress_type, zi.CRC
                out.compress_size, out.file_size = zi.compress_size, zi.file_size
                out.external_attr, out.create_system = zi.external_attr, zi.create_system
                out.flag_bits = zi.flag_bits & ~0x08
                out.extra = local_extra                       # the local header keeps its own extra field (Xbox LZX marker for method 21)
                out.header_offset = zw.fp.tell()
                zw.fp.write(out.FileHeader())
                zw.fp.write(raw)
                out.extra = zi.extra                          # the central directory keeps the central one
                zw.filelist.append(out)
                zw.NameToInfo[out.filename] = out
                zw.start_dir = zw.fp.tell()
                zw._didModify = True
    # check the new archive before it replaces the old one
    with zipfile.ZipFile(zip_path) as zr, zipfile.ZipFile(tmp) as zn:
        a, b = zr.infolist(), zn.infolist()
        if [i.filename for i in a] != [i.filename for i in b]:
            tmp.unlink()
            raise RuntimeError("member list or order changed")
        for x, y in zip(a, b):
            if x.filename == member:
                continue
            if (x.CRC, x.file_size, x.compress_size, x.compress_type, x.extra) != (y.CRC, y.file_size, y.compress_size, y.compress_type, y.extra):
                tmp.unlink()
                raise RuntimeError(f"{x.filename} differs after the copy")
        if zn.read(member) != data:
            tmp.unlink()
            raise RuntimeError("the new member does not read back identically")
    bak = Path(str(zip_path) + ".bak")
    if backup and not bak.exists():
        shutil.copy2(zip_path, bak)
    os.replace(tmp, zip_path)                                  # a fresh file: hard links to an older build stay as they were
    return {"zip": str(zip_path), "member": member, "backup": str(bak) if bak.exists() else "", "bytes": len(data)}


class Library:
    """The places carbins live. sources(): [{id, kind, label, writable, why}]; cars(id); members(id, car); read(...); write(...)."""

    def __init__(self, game: str | None = None, content_dirs: list[str] | None = None):
        self.game_path = Path(game) if game else None
        self.game = src.open_fh1_game(self.game_path) if game else None
        self.content_dirs = [Path(p) for p in (content_dirs or [])]
        self._pk: dict[str, src.DLCPackage] = {}
        for d in self.content_dirs:
            for p in src.list_packages(d):
                self._pk[p.path.name] = p
        self._cars: dict[str, dict[str, list[tuple[str, str]]]] = {}

    # ------------------------------------------------------------------ sources
    def sources(self) -> list[dict]:
        out = []
        if self.game:
            ext = isinstance(self.game.sv, src.FolderReader)
            out.append({"id": "game", "kind": "game", "label": "Game", "writable": ext,
                        "why": "" if ext else "this game is a Games on Demand / disc image: read only. Point the editor at an extracted copy to save into the game."})
        for name, p in sorted(self._pk.items()):
            if any(n.lower().startswith("media/dlczips/") and n.lower().endswith(".zip") for n in p.names()):
                out.append({"id": "dlc:" + name, "kind": "dlc", "label": f"DLC {name} (offer {p.offer})", "writable": p.kind == "folder",
                            "why": "" if p.kind == "folder" else "STFS console package: read only"})
        return out

    def _index(self, sid: str) -> dict[str, list[tuple[str, str]]]:
        """car media name -> [(archive id, member)] of its .carbin files."""
        if sid in self._cars:
            return self._cars[sid]
        idx: dict[str, list[tuple[str, str]]] = {}
        if sid == "game":
            for path in self.game.car_media_names():
                car = Path(path).stem
                z = self.game.zip(path)
                idx[car] = [(path, n) for n in z.namelist() if n.lower().endswith(".carbin") and not n.lower().startswith("stripped")]
        else:
            p = self._pk[sid.split(":", 1)[1]]
            for zn, z in p.zips.items():
                for n in z.namelist():
                    m = re.match(r"(?i)media/cars/([^/]+)/([^/]+\.carbin)$", n)
                    if m and not m.group(2).lower().startswith("stripped"):
                        idx.setdefault(m.group(1), []).append((zn, n))
        self._cars[sid] = idx
        return idx

    def cars(self, sid: str) -> list[str]:
        return sorted(self._index(sid))

    def members(self, sid: str, car: str) -> list[dict]:
        return [{"archive": a, "member": m, "label": Path(m).name} for a, m in sorted(self._index(sid).get(car, []), key=lambda x: x[1])]


    def archive_zip(self, sid: str, archive: str) -> zipfile.ZipFile:
        """The open zip behind an archive id (game archive or a DLC package's zip)."""
        if sid == "game":
            return self.game.zip(archive)
        return self._pk[sid.split(":", 1)[1]].zips[archive]

    def game_zip(self, path: str) -> zipfile.ZipFile:
        if not self.game:
            raise ValueError("no game folder is set (Open / Save tab)")
        return self.game.zip(path)
    # ------------------------------------------------------------------ read / write
    def read(self, sid: str, archive: str, member: str) -> bytes:
        if sid == "game":
            return xlzx.read_member(self.game.zip(archive), member)
        p = self._pk[sid.split(":", 1)[1]]
        return xlzx.read_member(p.zips[archive], member)

    def zip_path(self, sid: str, archive: str) -> Path:
        if sid == "game":
            if not isinstance(self.game.sv, src.FolderReader):
                raise PermissionError("the game is not an extracted folder, so it cannot be written to")
            return self.game.sv.entries[archive.lower()][1]
        p = self._pk[sid.split(":", 1)[1]]
        if p.kind != "folder":
            raise PermissionError("STFS packages are read only")
        if archive not in p.zips:                          # only archives the package really has: no "../" paths from a client
            raise KeyError(f"{archive} is not an archive of this package")
        return p.path / archive

    def write(self, sid: str, archive: str, member: str, data: bytes, ignore_xenia: bool = False) -> dict:
        if xenia_running() and not ignore_xenia:
            raise PermissionError("Xenia is running: close it first (a running emulator can crash or keep the old file)")
        zp = self.zip_path(sid, archive)
        if sid != "game":                                  # close the reader's handles on this package's archives before replacing the file
            p = self._pk[sid.split(":", 1)[1]]
            for z in p.zips.values():
                z.close()
            p.__dict__.pop("zips", None)
        res = replace_zip_member(zp, member, data)
        self._cars.pop(sid, None)
        return res
