"""
xlzx.py - pure-Python decoder for Xbox 360 XMemCompress LZX, as used by Forza zip
entries with compression method 21 (extra field 0x1123).

Stream framing (verified on FH1 uDLC zips):
    FF hh hh cc cc  -> frame with uncompressed size hhhh, compressed size cccc (big-endian)
    cc cc           -> frame with compressed size cccc, uncompressed size 0x8000
    00 00           -> end
The concatenated frames form one standard LZX stream (16-bit LE words, MSB-first bits,
24-bit block lengths), with the bitstream re-aligned at every frame start.
"""

from __future__ import annotations

import struct
import zipfile
import hashlib
import os
import tempfile
import zlib
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def _decoder_version():
    return hashlib.sha256(Path(__file__).read_bytes()).digest()

EXTRA_BITS = [0, 0, 0, 0] + [min(i // 2 - 1, 17) for i in range(4, 52)]
POSITION_BASE = [0]
for _eb in EXTRA_BITS[:-1]:
    POSITION_BASE.append(POSITION_BASE[-1] + (1 << _eb))
POSITION_SLOTS = {15: 30, 16: 32, 17: 34, 18: 36, 19: 38, 20: 42, 21: 50}


class LZXError(Exception):
    pass


class _Huff:
    __slots__ = ("table", "maxlen", "flat")

    def __init__(self, lens: list[int]):
        maxlen = max(lens) if lens else 0
        self.maxlen = maxlen
        self.flat = None
        if maxlen == 0:
            self.table = None
            return
        size = 1 << maxlen
        table = [None] * size
        code = 0
        for L in range(1, maxlen + 1):
            for sym, l in enumerate(lens):
                if l == L:
                    start = code << (maxlen - L)
                    end = (code + 1) << (maxlen - L)
                    if end > size:
                        raise LZXError("over-subscribed Huffman table")
                    entry = (sym, L)
                    for k in range(start, end):
                        table[k] = entry
                    code += 1
            code <<= 1
        self.table = table
        self.flat = [((e[0] << 5) | e[1]) if e else 0 for e in table]   # one int per code: the decode loop's table


class _Bits:
    __slots__ = ("data", "pos", "buf", "n")

    def __init__(self, data: bytes):
        self.data, self.pos, self.buf, self.n = data, 0, 0, 0

    def _fill(self, need: int) -> None:
        d = self.data
        while self.n < need:
            if self.pos + 1 < len(d):
                w = d[self.pos] | (d[self.pos + 1] << 8)
            else:
                w = 0
            self.pos += 2
            self.buf = (self.buf << 16) | w
            self.n += 16

    def read(self, k: int) -> int:
        if k == 0:
            return 0
        if self.n < k:
            self._fill(k)
        self.n -= k
        v = self.buf >> self.n
        self.buf &= (1 << self.n) - 1
        return v

    def sym(self, h: _Huff) -> int:
        if h.table is None:
            raise LZXError("decode from empty tree")
        m = h.maxlen
        if self.n < m:
            self._fill(m)
        e = h.table[self.buf >> (self.n - m)]
        if e is None:
            raise LZXError("invalid Huffman code")
        self.n -= e[1]
        self.buf &= (1 << self.n) - 1
        return e[0]

    def align16(self) -> None:
        """Drop to the next 16-bit boundary (the LZX uncompressed-block rule: 1..16 bits)."""
        self._fill(16)
        self.n -= self.n % 16 or 16
        self.buf &= (1 << self.n) - 1

    def raw_offset(self) -> int:
        """Byte position of the next unread byte, assuming we're 16-bit aligned."""
        return self.pos - self.n // 8


def split_frames(blob: bytes) -> list[tuple[bytes, int]]:
    frames, p = [], 0
    while p + 2 <= len(blob):
        if blob[p] == 0xFF:
            usize, csize = struct.unpack_from(">HH", blob, p + 1)
            p += 5
        else:
            csize, = struct.unpack_from(">H", blob, p)
            usize = 0x8000
            p += 2
        if csize == 0:
            break
        frames.append((blob[p:p + csize], usize))
        p += csize
    return frames


# ---------------------------------------------------------------------------------------------------------
# Windows' own LZX decoder (cabinet.dll, the CAB format's LZX): ~100x faster than the pure-Python loop below.
# An XMemCompress stream is a standard LZX stream cut into frames, i.e. exactly a CAB folder's CFDATA blocks, so
# the frames are wrapped in a one-file in-memory CAB and handed to FDICopy. Any failure (not Windows, odd frames,
# a CRC mismatch the caller detects) falls back to the Python decoder.
# ---------------------------------------------------------------------------------------------------------
_FAST = {"lib": None, "state": None}


def _fast_setup():
    import ctypes
    from ctypes import (CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_long, c_size_t, c_ssize_t, c_uint,
                        c_ulong, c_ushort, c_void_p, cast, memmove, string_at)
    lib = ctypes.cdll.LoadLibrary("Cabinet.dll")
    crt = ctypes.cdll.msvcrt
    crt.malloc.restype = c_void_p
    crt.malloc.argtypes = [c_size_t]
    crt.free.argtypes = [c_void_p]
    state = {"cab": b"", "pos": 0, "out": bytearray(), "err": False}

    class ERF(Structure):
        _fields_ = [("erfOper", c_int), ("erfType", c_int), ("fError", c_int)]

    class NOTIFY(Structure):
        _pack_ = 4
        _fields_ = [("cb", c_long), ("psz1", c_void_p), ("psz2", c_void_p), ("psz3", c_void_p), ("pv", c_void_p),
                    ("hf", c_ssize_t), ("date", c_ushort), ("time", c_ushort), ("attribs", c_ushort),
                    ("setID", c_ushort), ("iCabinet", c_ushort), ("iFolder", c_ushort), ("fdie", c_int)]

    ALLOC = CFUNCTYPE(c_void_p, c_ulong)
    FREE = CFUNCTYPE(None, c_void_p)
    OPEN = CFUNCTYPE(c_ssize_t, c_char_p, c_int, c_int)
    READ = CFUNCTYPE(c_uint, c_ssize_t, c_void_p, c_uint)
    WRITE = CFUNCTYPE(c_uint, c_ssize_t, c_void_p, c_uint)
    CLOSE = CFUNCTYPE(c_int, c_ssize_t)
    SEEK = CFUNCTYPE(c_long, c_ssize_t, c_long, c_int)
    NOTE = CFUNCTYPE(c_ssize_t, c_int, POINTER(NOTIFY))

    def f_open(name, flag, mode):
        state["pos"] = 0
        return 1

    def f_read(hf, pv, cb):
        cab, pos = state["cab"], state["pos"]
        chunk = cab[pos:pos + cb]
        memmove(pv, chunk, len(chunk))
        state["pos"] = pos + len(chunk)
        return len(chunk)

    def f_write(hf, pv, cb):
        state["out"] += string_at(pv, cb)
        return cb

    def f_seek(hf, dist, kind):
        n = len(state["cab"])
        state["pos"] = dist if kind == 0 else state["pos"] + dist if kind == 1 else n + dist
        return state["pos"]

    def f_note(kind, nfy):
        if kind == 2:                      # fdintCOPY_FILE: take it, writing into our buffer
            return 2
        if kind == 3:                      # fdintCLOSE_FILE_INFO
            return 1
        return 0

    keep = (ALLOC(lambda n: crt.malloc(n)), FREE(lambda p: crt.free(p)), OPEN(f_open), READ(f_read),
            WRITE(f_write), CLOSE(lambda hf: 0), SEEK(f_seek), NOTE(f_note))
    lib.FDICreate.restype = c_void_p
    lib.FDICreate.argtypes = [ALLOC, FREE, OPEN, READ, WRITE, CLOSE, SEEK, c_int, POINTER(ERF)]
    lib.FDICopy.argtypes = [c_void_p, c_char_p, c_char_p, c_int, NOTE, c_void_p, c_void_p]
    lib.FDICopy.restype = c_int
    erf = ERF()
    hfdi = lib.FDICreate(*keep[:7], -1, ctypes.byref(erf))
    if not hfdi:
        raise OSError("FDICreate failed")
    _FAST["lib"], _FAST["state"] = (lib, hfdi, keep, erf), state
    return state


def _fast_decompress(blob: bytes, out_size: int, window_bits: int) -> bytes | None:
    import sys
    if sys.platform != "win32" or os.environ.get("FH_NO_FAST_LZX"):
        return None
    try:
        state = _FAST["state"] or _fast_setup()
        frames = split_frames(blob)
        if not frames or any(u > 0x8000 for _, u in frames[:-1]) or any(u != 0x8000 for _, u in frames[:-1]):
            return None                                       # CAB LZX wants full 32 KB frames except the last
        data = b"".join(struct.pack("<IHH", 0, len(c), u) + c for c, u in frames)
        name = b"a\0"
        coff_files = 36 + 8
        coff_data = coff_files + 16 + len(name)
        cab = (b"MSCF" + struct.pack("<IIIIIBBHHHHH", 0, coff_data + len(data), 0, coff_files, 0, 3, 1, 1, 1, 0, 1, 0)
               + struct.pack("<IHH", coff_data, len(frames), 0x0003 | (window_bits << 8))
               + struct.pack("<IIHHHH", out_size, 0, 0, 0, 0, 0x20) + name + data)
        state["cab"], state["pos"], state["out"] = cab, 0, bytearray()
        lib, hfdi = _FAST["lib"][0], _FAST["lib"][1]
        ok = lib.FDICopy(hfdi, b"a.cab", b"", 0, _FAST["lib"][2][7], None, None)
        out = bytes(state["out"])
        state["cab"] = b""
        return out if ok and len(out) == out_size else None
    except Exception:                                         # noqa: BLE001 - any trouble: the Python decoder
        return None


def decompress(blob: bytes, out_size: int, window_bits: int = 17, crc: int | None = None) -> bytes:
    quick = _fast_decompress(blob, out_size, window_bits)
    if quick is not None and (crc is None or zlib.crc32(quick) == crc):
        return quick
    nslots = POSITION_SLOTS[window_bits]
    main_n = 256 + nslots * 8
    main_lens = [0] * main_n
    len_lens = [0] * 249
    out = bytearray()
    r0 = r1 = r2 = 1
    header_read = False
    block_type = 0
    block_remaining = 0
    block_len = 0
    main_t = len_t = align_t = None
    target = 0

    def read_lengths(bits: _Bits, lens: list[int], first: int, last: int) -> None:
        pre = _Huff([bits.read(4) for _ in range(20)])
        x = first
        while x < last:
            z = bits.sym(pre)
            if z == 17:
                run = bits.read(4) + 4
                lens[x:x + run] = [0] * run
                x += run
            elif z == 18:
                run = bits.read(5) + 20
                lens[x:x + run] = [0] * run
                x += run
            elif z == 19:
                run = bits.read(1) + 4
                z = bits.sym(pre)
                v = (lens[x] - z) % 17
                lens[x:x + run] = [v] * run
                x += run
            else:
                lens[x] = (lens[x] - z) % 17
                x += 1

    for chunk, usize in split_frames(blob):
        bits = _Bits(chunk)
        target += usize
        if not header_read:
            if bits.read(1):
                raise LZXError("E8 translation streams not supported")
            header_read = True
        while len(out) < target:
            if block_remaining == 0:
                if block_type == 3 and block_len & 1:
                    bits.pos += 1  # pad byte after odd-length uncompressed block
                block_type = bits.read(3)
                block_remaining = block_len = (bits.read(16) << 8) | bits.read(8)
                if block_type == 2:
                    align_t = _Huff([bits.read(3) for _ in range(8)])
                if block_type in (1, 2):
                    read_lengths(bits, main_lens, 0, 256)
                    read_lengths(bits, main_lens, 256, main_n)
                    main_t = _Huff(main_lens)
                    read_lengths(bits, len_lens, 0, 249)
                    len_t = _Huff(len_lens)
                elif block_type == 3:
                    bits.align16()
                    p = bits.raw_offset()
                    r0, r1, r2 = struct.unpack_from("<III", chunk, p)
                    bits.pos, bits.n, bits.buf = p + 12, 0, 0
                else:
                    raise LZXError(f"bad block type {block_type}")

            if block_type == 3:
                n = min(block_remaining, target - len(out))
                p = bits.pos
                seg = chunk[p:p + n]
                if len(seg) != n:
                    raise LZXError("uncompressed block crosses frame boundary")
                out += seg
                bits.pos += n
                block_remaining -= n
                continue

            run_end = len(out) + min(block_remaining, target - len(out))
            start = len(out)
            if main_t.flat is None:
                raise LZXError("decode from empty tree")
            # the hot loop: the bit buffer lives in local variables (a method call per symbol cost ~40% of a build).
            # buf holds n valid low bits (higher bits are stale); refilled 16 bits at a time to >= 32 bits
            data = chunk
            dlen = len(data)
            buf, n, pos = bits.buf, bits.n, bits.pos
            mt, mmax = main_t.flat, main_t.maxlen
            mmask = (1 << mmax) - 1
            lt, lmax = len_t.flat, len_t.maxlen
            lmask = (1 << lmax) - 1 if lmax else 0
            aligned = block_type == 2
            if aligned:
                at, amax = align_t.flat, align_t.maxlen
                amask = (1 << amax) - 1 if amax else 0
            olen = len(out)
            append = out.append
            while olen < run_end:
                if n < 32:
                    while n < 32:
                        w = (data[pos] | (data[pos + 1] << 8)) if pos + 1 < dlen else 0
                        pos += 2
                        buf = ((buf & ((1 << n) - 1)) << 16) | w
                        n += 16
                e = mt[(buf >> (n - mmax)) & mmask]
                if not e:
                    raise LZXError("invalid Huffman code")
                n -= e & 31
                m = e >> 5
                if m < 256:
                    append(m)
                    olen += 1
                    continue
                m -= 256
                mlen = m & 7
                if mlen == 7:
                    if not lmax:
                        raise LZXError("decode from empty tree")
                    e = lt[(buf >> (n - lmax)) & lmask]
                    if not e:
                        raise LZXError("invalid Huffman code")
                    n -= e & 31
                    mlen += e >> 5
                mlen += 2
                slot = m >> 3
                if slot == 0:
                    off = r0
                elif slot == 1:
                    off = r1
                    r1 = r0
                    r0 = off
                elif slot == 2:
                    off = r2
                    r2 = r0
                    r0 = off
                else:
                    eb = EXTRA_BITS[slot]
                    off = POSITION_BASE[slot] - 2
                    if n < 32:
                        while n < 32:
                            w = (data[pos] | (data[pos + 1] << 8)) if pos + 1 < dlen else 0
                            pos += 2
                            buf = ((buf & ((1 << n) - 1)) << 16) | w
                            n += 16
                    if aligned and eb >= 3:
                        if eb > 3:
                            k = eb - 3
                            n -= k
                            off += ((buf >> n) & ((1 << k) - 1)) << 3
                        if not amax:
                            raise LZXError("decode from empty tree")
                        e = at[(buf >> (n - amax)) & amask]
                        if not e:
                            raise LZXError("invalid Huffman code")
                        n -= e & 31
                        off += e >> 5
                    elif eb:
                        n -= eb
                        off += (buf >> n) & ((1 << eb) - 1)
                    r2, r1, r0 = r1, r0, off
                src = olen - off
                if src < 0:
                    raise LZXError("match before start of output")
                if off >= mlen:
                    out += out[src:src + mlen]
                else:
                    for k in range(mlen):
                        append(out[src + k])
                olen += mlen
            bits.buf, bits.n, bits.pos = buf & ((1 << n) - 1), n, pos
            block_remaining -= len(out) - start
            if block_remaining < 0:
                raise LZXError("match overran block")
    if len(out) < out_size:
        raise LZXError(f"short output {len(out)} < {out_size}")
    return bytes(out[:out_size])


def read_member(zf: zipfile.ZipFile, name: str, window_bits: int = 17) -> bytes:
    """Read a zip member, handling method 21 (XMem LZX) as well as stored/deflate."""
    info = zf.getinfo(name)
    if info.compress_type != 21:
        return zf.read(name)
    fp = zf.fp
    fp.seek(info.header_offset)
    h = fp.read(30)
    nlen, xlen = struct.unpack_from("<HH", h, 26)
    fp.seek(info.header_offset + 30 + nlen + xlen)
    raw = fp.read(info.compress_size)
    quick = _fast_decompress(raw, info.file_size, window_bits)
    if quick is not None and zlib.crc32(quick) == info.CRC:
        return quick                  # Windows' decoder is faster than reading the decoded-file cache back
    cache_root = os.environ.get('FH_PKG_CACHE')
    if not cache_root:
        return decompress(raw, info.file_size, window_bits, info.CRC)
    key = hashlib.sha256(_decoder_version() + struct.pack('<QII', info.file_size, window_bits, info.CRC) + raw).hexdigest()
    path = Path(cache_root) / 'decoded_lzx' / key[:2] / (key + '.zlib')
    try:
        data = zlib.decompress(path.read_bytes())
        if len(data) == info.file_size and zlib.crc32(data) == info.CRC:
            return data
    except (OSError, zlib.error):
        pass
    data = decompress(raw, info.file_size, window_bits, info.CRC)
    # Cache only verified output; failures to write this optional cache must not
    # break conversion. Unique temp files allow workers to share the cache safely.
    if zlib.crc32(data) == info.CRC:
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(zlib.compress(data, 1))
            os.replace(temp, path)
        except OSError:
            pass
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
    return data
