"""
stfs.py - write and verify Xbox 360 STFS "LIVE" content packages (the format UDLC ships in).

Layout matches UDLCv1.2's working FH1 packages (verified field-by-field):
  * magic LIVE, content type 0x00000002 (marketplace/DLC), title 4D5309C9
  * one license entry 0xFFFFFFFFFFFFFFFF ("unlocked" - accepted by modded consoles and Xenia)
  * block separation 1 -> a single hash table per level ("read-only" package)
  * header hash @0x32C = SHA1(header[0x344 : aligned header end])
  * L0 hash entry = SHA1(4KB block) + 0x80 + next block (24-bit BE, 0xFFFFFF = end)
  * L1/L2 entry   = SHA1(lower table) + 00000000 ; top table SHA1 -> volume descriptor
  * physical position of data block b:  b + sum over levels of (b // 170**k + 1), as long as
    b >= 170**(k)  (tables for a group precede it; L1/L2 tables follow the first group)
The RSA signature area is left zero: RGH/JTAG kernels and Xenia do not verify it, which is
exactly how UDLC is installed ("modded console only").
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

BLOCK = 0x1000
HDR_SIZE_V1 = 0x971A
DATA_START_V1 = 0xA000            # (0x971A + 0xFFF) & ~0xFFF
PER_TABLE = 170


def phys_index(b: int) -> int:
    p, base = b, PER_TABLE
    for _ in range(3):
        p += (b + base) // base
        if b < base:
            break
        base *= PER_TABLE
    return p


def _utf16(s: str, size: int) -> bytes:
    return s.encode("utf-16-be")[: size - 2].ljust(size, b"\0")


def _fat_time() -> bytes:
    import time
    t = time.localtime()
    d = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    tm = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    return struct.pack(">HH", d, tm) * 2


def build_stfs(files: dict[str, bytes], out_path: Path, *, display_name: str, description: str = "",
               title_id: int = 0x4D5309C9, title_name: str = "Forza Horizon",
               thumbnail_png: bytes | None = None) -> Path:
    """files: {"Media/3000.puboffer": b"...", "Media/DLCZips/x.zip": b"...", ...}"""
    # ---- directory tree -> file table entries (DFS, directories before their contents)
    entries: list[dict] = []
    dir_index: dict[str, int] = {"": 0xFFFF}

    def ensure_dir(path: str) -> int:
        if path in dir_index:
            return dir_index[path]
        parent, _, name = path.rpartition("/")
        pi = ensure_dir(parent)
        dir_index[path] = len(entries)
        entries.append({"name": name, "dir": True, "parent": pi, "data": b""})
        return dir_index[path]

    for path in sorted(files, key=lambda p: (p.count("/"), p.lower())):
        parent, _, name = path.replace("\\", "/").rpartition("/")
        if len(name.encode()) > 40:
            raise ValueError(f"STFS names are limited to 40 bytes: {name}")
        pi = ensure_dir(parent)
        entries.append({"name": name, "dir": False, "parent": pi, "data": files[path]})
    if len(entries) > 0xFFFF:
        raise ValueError("too many entries")

    ft_blocks = max(1, -(-len(entries) // 64))
    next_block = ft_blocks
    for e in entries:
        n = -(-len(e["data"]) // BLOCK)
        e["start"], e["blocks"] = (next_block, n) if n else (0, 0)
        next_block += n
    total = next_block

    # ---- logical data blocks + next pointers
    ts = _fat_time()
    ft = bytearray(ft_blocks * BLOCK)
    for i, e in enumerate(entries):
        nm = e["name"].encode()
        flags = len(nm) | 0x40 | (0x80 if e["dir"] else 0)
        rec = (nm.ljust(40, b"\0") + bytes([flags]) + e["blocks"].to_bytes(3, "little") * 2
               + e["start"].to_bytes(3, "little") + struct.pack(">HI", e["parent"], len(e["data"])) + ts)
        ft[i * 64:(i + 1) * 64] = rec
    blocks: list[bytes] = [bytes(ft[i * BLOCK:(i + 1) * BLOCK]) for i in range(ft_blocks)]
    nexts = [i + 1 for i in range(ft_blocks - 1)] + [0xFFFFFF]
    for e in entries:
        d = e["data"]
        for k in range(e["blocks"]):
            blocks.append(d[k * BLOCK:(k + 1) * BLOCK].ljust(BLOCK, b"\0"))
            nexts.append(e["start"] + k + 1 if k + 1 < e["blocks"] else 0xFFFFFF)
    assert len(blocks) == total

    # ---- hash tables
    def table(entries_):
        t = bytearray(BLOCK)
        for i, ent in enumerate(entries_):
            t[i * 24:(i + 1) * 24] = ent
        return bytes(t)

    l0 = [table([hashlib.sha1(blocks[b]).digest() + b"\x80" + nexts[b].to_bytes(3, "big")
                 for b in range(g, min(g + PER_TABLE, total))]) for g in range(0, total, PER_TABLE)]
    levels = [l0]
    while len(levels[-1]) > 1:
        lower = levels[-1]
        levels.append([table([hashlib.sha1(lower[j]).digest() + b"\0\0\0\0"
                              for j in range(g, min(g + PER_TABLE, len(lower)))])
                       for g in range(0, len(lower), PER_TABLE)])
    if len(levels) > 3:
        raise ValueError("package too large for 3 hash levels")
    top = levels[-1][0]

    # ---- physical layout
    nphys = phys_index(total - 1) + 1
    phys: list[bytes | None] = [None] * nphys
    for b in range(total):
        phys[phys_index(b)] = blocks[b]
    for g, t in enumerate(l0):
        phys[phys_index(g * PER_TABLE) - 1] = t
    if len(levels) > 1:
        for k, t in enumerate(levels[1]):
            phys[phys_index(max(k * PER_TABLE ** 2, PER_TABLE)) - 2] = t
    if len(levels) > 2:
        phys[phys_index(PER_TABLE ** 2) - 3] = levels[2][0]
    assert all(p is not None for p in phys), "layout hole"

    # ---- header (metadata version 1)
    h = bytearray(DATA_START_V1)
    h[0:4] = b"LIVE"
    h[0x22C:0x23C] = b"\xFF" * 8 + b"\0" * 8
    struct.pack_into(">III", h, 0x340, HDR_SIZE_V1, 2, 1)          # header size, content type, meta ver
    struct.pack_into(">Q", h, 0x34C, nphys * BLOCK)
    struct.pack_into(">I", h, 0x360, title_id)
    vd = (bytes([0x24, 0, 1]) + ft_blocks.to_bytes(2, "little") + (0).to_bytes(3, "little") + hashlib.sha1(top).digest()
          + struct.pack(">II", total, 0))
    h[0x379:0x379 + 0x24] = vd
    for loc in range(18):                                             # same text for every locale
        h[0x411 + loc * 0x80:0x411 + (loc + 1) * 0x80] = _utf16(display_name, 0x80)
        h[0xD11 + loc * 0x80:0xD11 + (loc + 1) * 0x80] = _utf16(description or display_name, 0x80)
    h[0x1691:0x1711] = _utf16(title_name, 0x80)
    h[0x1711] = 0xC0
    if thumbnail_png:
        png = thumbnail_png[:0x4000]
        struct.pack_into(">II", h, 0x1712, len(png), len(png))
        h[0x171A:0x171A + len(png)] = png
        h[0x571A:0x571A + len(png)] = png
    h[0x32C:0x340] = hashlib.sha1(bytes(h[0x344:DATA_START_V1])).digest()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Stream the physical blocks: joining the body and then header+body made two
    # additional multi-GB copies at the peak of a console build.
    with out_path.open('wb', buffering=1024 * 1024) as f:
        f.write(h)
        f.writelines(phys)
    return out_path


# ---------------------------------------------------------------------------
# independent reader / verifier
# ---------------------------------------------------------------------------

class STFSReader:
    """Seeks on disk (packages can be >1 GB); files are read on demand."""

    def __init__(self, path):
        self.f = open(path, "rb")
        d = self.f.read(0xC000)
        if d[:4] not in (b"LIVE", b"PIRS", b"CON "):
            raise ValueError("not an STFS package")
        self.head = d
        self.hsize = int.from_bytes(d[0x340:0x344], "big")
        self.base = (self.hsize + 0xFFF) & ~0xFFF
        vd = d[0x379:0x379 + 0x24]
        if not vd[2] & 1:
            raise ValueError("only block-separation-1 (read-only) packages are supported")
        self.ft_blocks = int.from_bytes(vd[3:5], "little")
        self.ft_start = int.from_bytes(vd[5:8], "little")
        self.top_hash = vd[8:28]
        self.total = int.from_bytes(vd[28:32], "big")
        self.title = int.from_bytes(d[0x360:0x364], "big")
        self.ctype = int.from_bytes(d[0x344:0x348], "big")
        self.display = d[0x411:0x491].decode("utf-16-be", "replace").rstrip("\0")
        self._entries = None

    def close(self):
        self.f.close()

    def blk(self, i):
        self.f.seek(self.base + i * BLOCK)
        return self.f.read(BLOCK)

    def verify(self) -> list[str]:
        errs = []
        if hashlib.sha1(self.head[0x344:self.base]).digest() != self.head[0x32C:0x340]:
            errs.append("header hash mismatch")
        total = self.total
        nlev = 1 if total <= PER_TABLE else 2 if total <= PER_TABLE ** 2 else 3
        l0_pos = [phys_index(g) - 1 for g in range(0, total, PER_TABLE)]
        for gi, pos in enumerate(l0_pos):
            t = self.blk(pos)
            for j in range(min(PER_TABLE, total - gi * PER_TABLE)):
                b = gi * PER_TABLE + j
                if hashlib.sha1(self.blk(phys_index(b))).digest() != t[j * 24:j * 24 + 20]:
                    errs.append(f"data block {b} hash mismatch"); break
        tops = {1: [l0_pos[0]]}
        if nlev >= 2:
            l1_pos = [phys_index(max(k * PER_TABLE ** 2, PER_TABLE)) - 2 for k in range(-(-len(l0_pos) // PER_TABLE))]
            for k, pos in enumerate(l1_pos):
                t = self.blk(pos)
                for j, p0 in enumerate(l0_pos[k * PER_TABLE:(k + 1) * PER_TABLE]):
                    if hashlib.sha1(self.blk(p0)).digest() != t[j * 24:j * 24 + 20]:
                        errs.append(f"L1 entry {k}/{j} mismatch"); break
            tops[2] = l1_pos
        if nlev == 3:
            t = self.blk(phys_index(PER_TABLE ** 2) - 3)
            for j, p1 in enumerate(tops[2]):
                if hashlib.sha1(self.blk(p1)).digest() != t[j * 24:j * 24 + 20]:
                    errs.append(f"L2 entry {j} mismatch"); break
            top = t
        else:
            top = self.blk(tops[nlev][0])
        if hashlib.sha1(top).digest() != self.top_hash:
            errs.append("top hash mismatch")
        return errs

    def _next(self, b):
        t = self.blk(phys_index(b // PER_TABLE * PER_TABLE) - 1)
        return int.from_bytes(t[(b % PER_TABLE) * 24 + 21:(b % PER_TABLE) * 24 + 24], "big")

    def entries(self) -> dict[str, tuple[int, int, bool]]:
        """{path: (start block, size, contiguous)} for every file."""
        if self._entries is not None:
            return self._entries
        ents, b = [], self.ft_start
        for _ in range(self.ft_blocks):
            data = self.blk(phys_index(b))
            for i in range(64):
                e = data[i * 64:(i + 1) * 64]
                if not e[0]:
                    break
                ents.append(e)
            b = self._next(b)
        out, paths = {}, []
        for e in ents:
            name = e[:e[40] & 0x3F].decode("utf8", "replace")
            parent = int.from_bytes(e[50:52], "big")
            path = (paths[parent] + "/" if parent != 0xFFFF else "") + name
            paths.append(path)
            if not e[40] & 0x80:
                out[path] = (int.from_bytes(e[47:50], "little"), int.from_bytes(e[52:56], "big"), bool(e[40] & 0x40))
        self._entries = out
        return out

    def read(self, path: str) -> bytes:
        start, size, contig = self.entries()[path]
        chunks, blk, rem = [], start, size
        while rem > 0:
            chunks.append(self.blk(phys_index(blk))[:rem]); rem -= BLOCK
            blk = blk + 1 if contig else self._next(blk)
        return b"".join(chunks)

    def files(self) -> dict[str, bytes]:
        return {p: self.read(p) for p in self.entries()}
