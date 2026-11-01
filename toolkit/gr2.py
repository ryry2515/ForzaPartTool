"""Granny 2 (.gr2) reader/writer for FH1's car animation files (little-endian, 64-bit, file format 7).

read(data) -> Gr2 with the decompressed sections, pointer fixups and a walkable type tree; Gr2.build() writes
it back with uncompressed sections (Granny loads type 0 sections as they are), so values patched in place in
Gr2.data survive. Only fixed-size edits: nothing is moved.

SPDX-License-Identifier: MPL-2.0
This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

The Oodle1 decoder is a port of libopengrn's oodle1.c (https://github.com/arves100/opengr2, MPL-2.0), itself
derived from nwn2mdk's gr2_decompress.cpp.
"""
from __future__ import annotations

import struct
import zlib

MAGIC64 = bytes.fromhex("e59b495e6f631f141e13eba990beedc4")   # little-endian, 64-bit pointers, format 7


# ---------------------------------------------------------------- Oodle1 (Granny's arithmetic-coded LZ)
class _Decoder:
    __slots__ = ("numer", "denom", "next_denom", "s", "p")

    def __init__(self, s: bytes, p: int):
        self.s, self.p = s, p
        self.numer = s[p] >> 1
        self.denom = 0x80
        self.next_denom = 0

    def decode(self, mx: int) -> int:
        s = self.s
        while self.denom <= 0x800000:
            self.numer = ((self.numer << 8) | ((s[self.p] << 7) & 0x80) | ((s[self.p + 1] >> 1) & 0x7F)) & 0xFFFFFFFF
            self.p += 1
            self.denom = (self.denom << 8) & 0xFFFFFFFF
        self.next_denom = self.denom // mx
        return min(self.numer // self.next_denom, mx - 1)

    def commit(self, mx: int, val: int, err: int) -> int:
        self.numer = (self.numer - self.next_denom * val) & 0xFFFFFFFF
        if val + err < mx:
            self.denom = (self.next_denom * err) & 0xFFFFFFFF
        else:
            self.denom = (self.denom - self.next_denom * val) & 0xFFFFFFFF
        return val

    def decode_commit(self, mx: int) -> int:
        return self.commit(mx, self.decode(mx), 1)


class _Window:
    __slots__ = ("count_cap", "ranges", "values", "weights", "weight_total", "thresh_increase",
                 "thresh_increase_cap", "thresh_range_rebuild", "thresh_weight_rebuild")

    def __init__(self, max_value: int, count_cap: int):
        self.weight_total = 4
        self.count_cap = (count_cap + 1) & 0xFFFF
        self.ranges = [0, 0x4000]
        self.weights = [4]
        self.values = [0]
        self.thresh_increase = 4
        self.thresh_range_rebuild = 8
        self.thresh_weight_rebuild = max(256, min(32 * max_value, 15160)) & 0xFFFF
        if max_value > 64:
            self.thresh_increase_cap = min(2 * max_value, self.thresh_weight_rebuild // 2 - 32) & 0xFFFF
        else:
            self.thresh_increase_cap = 128

    def rebuild_ranges(self):
        n = len(self.weights)
        self.ranges = [0] * (n + 1)
        range_weight = (8 * 0x4000 // self.weight_total) & 0xFFFF
        start = 0
        for i in range(n):
            self.ranges[i] = start
            start = (start + (self.weights[i] * range_weight) // 8) & 0xFFFF
        self.ranges[n] = 0x4000
        if self.thresh_increase > self.thresh_increase_cap // 2:
            self.thresh_range_rebuild = (self.weight_total + self.thresh_increase_cap) & 0xFFFF
        else:
            self.thresh_increase = (self.thresh_increase * 2) & 0xFFFF
            self.thresh_range_rebuild = (self.weight_total + self.thresh_increase) & 0xFFFF

    def rebuild_weights(self):
        w, v = self.weights, self.values
        for i in range(len(w)):
            w[i] //= 2
        self.weight_total = sum(w) & 0xFFFF
        i = 1
        while i < len(w):
            while i < len(w) and w[i] == 0:
                w[i] = w[-1]; v[i] = v[-1]
                w.pop(); v.pop()
            i += 1
        best, idx = 0, 1
        for j in range(1, len(w)):
            if w[j] > best:
                best, idx = w[j], j
        if idx < len(w):
            w[idx], w[-1] = w[-1], w[idx]
            v[idx], v[-1] = v[-1], v[idx]
        if len(w) < self.count_cap and w[0] == 0:
            w[0] = 1
            self.weight_total = (self.weight_total + 1) & 0xFFFF

    def try_decode(self, d: _Decoder):
        """-> (new_index or None, value)"""
        if self.weight_total >= self.thresh_range_rebuild:
            if self.thresh_range_rebuild >= self.thresh_weight_rebuild:
                self.rebuild_weights()
            self.rebuild_ranges()
        value = d.decode(0x4000)
        r = self.ranges
        it = len(r) - 1
        for i in range(len(r)):
            if r[i] > value:
                it = i
                break
        it -= 1
        d.commit(0x4000, r[it], r[it + 1] - r[it])
        self.weights[it] = (self.weights[it] + 1) & 0xFFFF
        self.weight_total = (self.weight_total + 1) & 0xFFFF
        if it > 0:
            return None, self.values[it]
        if len(self.weights) >= len(r) and d.decode_commit(2) == 1:
            idx = len(r) + d.decode_commit(len(self.weights) - len(r) + 1) - 1
            self.weights[idx] = (self.weights[idx] + 2) & 0xFFFF
            self.weight_total = (self.weight_total + 2) & 0xFFFF
            return None, self.values[idx]
        self.values.append(0)
        self.weights.append(2)
        self.weight_total = (self.weight_total + 2) & 0xFFFF
        if len(self.weights) == self.count_cap:
            self.weight_total = (self.weight_total - self.weights[0]) & 0xFFFF
            self.weights[0] = 0
        return len(self.values) - 1, 0


class _Dictionary:
    def __init__(self, decoded_value_max, backref_value_max, decoded_count, highbit_count, sizes_count):
        self.decoded_size = 0
        self.backref_size = 0
        self.decoded_value_max = decoded_value_max
        self.backref_value_max = backref_value_max
        self.lowbit_value_max = min(backref_value_max + 1, 4)
        self.midbit_value_max = min(backref_value_max // 4 + 1, 256)
        self.highbit_value_max = backref_value_max // 1024 + 1
        self.lowbit = _Window(self.lowbit_value_max - 1, self.lowbit_value_max)
        self.highbit = _Window(self.highbit_value_max - 1, highbit_count + 1)
        self.midbit = [_Window(self.midbit_value_max - 1, self.midbit_value_max) for _ in range(self.highbit_value_max)]
        self.decoded = [_Window(decoded_value_max - 1, decoded_count) for _ in range(4)]
        self.sizes = [_Window(64, sizes_count[3 - i]) for i in range(4) for _ in range(16)]
        self.sizes.append(_Window(64, sizes_count[0]))

    @staticmethod
    def _get(win: _Window, d: _Decoder, mx: int) -> int:
        idx, val = win.try_decode(d)
        if idx is not None:
            val = win.values[idx] = d.decode_commit(mx)
        return val

    def block(self, d: _Decoder, out: bytearray, pos: int) -> int:
        self.backref_size = self._get(self.sizes[self.backref_size], d, 65)
        if self.backref_size > 0:
            size = self.backref_size + 1 if self.backref_size < 61 else (128, 192, 256, 512)[self.backref_size - 61]
            rng = min(self.backref_value_max, self.decoded_size)
            d3 = self._get(self.lowbit, d, self.lowbit_value_max)
            d4 = self._get(self.highbit, d, rng // 1024 + 1)
            d5 = self._get(self.midbit[d4], d, min(rng // 4 + 1, 256))
            off = (d4 << 10) + (d5 << 2) + d3 + 1
            self.decoded_size += size
            src = out[pos - off:pos]
            rep, rem = divmod(size, off)
            out[pos:pos + rep * off + rem] = src * rep + src[:rem]
            return size
        out[pos] = self._get(self.decoded[pos % 4], d, self.decoded_value_max) & 0xFF
        self.decoded_size += 1
        return 1


def unoodle1(comp: bytes, out_len: int, stop0: int, stop1: int) -> bytes:
    if not comp:
        return b""
    comp = comp + b"\0" * 8
    params = []
    for i in range(3):
        a, b = struct.unpack_from("<II", comp, i * 12)
        params.append((a & 0x1FF, a >> 9, b & 0x1FF, b >> 19, comp[i * 12 + 8:i * 12 + 12]))
    d = _Decoder(comp, 36)
    out = bytearray(out_len + 1024)
    pos = 0
    for (dvm, bvm, dc, hc, sc), stop in zip(params, (stop0, stop1, out_len)):
        dic = _Dictionary(dvm, bvm, dc, hc, sc)
        while pos < stop:
            pos += dic.block(d, out, pos)
    return bytes(out[:out_len])


# ---------------------------------------------------------------- file structure
SECTION = struct.Struct("<11I")
FIXUP = struct.Struct("<3I")
# type id -> packed size (64-bit pointers)
SIZES = {2: 8, 3: 12, 4: 12, 5: 16, 7: 20, 8: 8, 9: 68, 10: 4, 11: 1, 12: 1, 13: 1, 14: 1, 15: 2, 16: 2, 17: 2,
         18: 2, 19: 4, 20: 4, 21: 2, 22: 8}
PRIM = {10: "f", 11: "b", 12: "B", 13: "b", 14: "B", 15: "h", 16: "H", 17: "h", 18: "H", 19: "i", 20: "I", 21: "e"}


class Gr2:
    def __init__(self, raw: bytes):
        if raw[:16] != MAGIC64:
            raise ValueError("not a little-endian 64-bit Granny 2 file")
        self.raw = raw
        self.header_size, self.format = struct.unpack_from("<II", raw, 16)
        info = 32
        (self.version, self.total, self.crc, self.secoff, self.nsec) = struct.unpack_from("<5I", raw, info)
        self.type_ref = struct.unpack_from("<II", raw, info + 20)
        self.root_ref = struct.unpack_from("<II", raw, info + 28)
        self.sections = [list(SECTION.unpack_from(raw, info + self.secoff + i * 44)) for i in range(self.nsec)]
        chunks, self.base = [], []
        at = 0
        for s in self.sections:
            comp, off, clen, dlen, align, st0, st1 = s[:7]
            if comp == 0:
                body = raw[off:off + dlen]
            elif comp == 2:
                body = unoodle1(raw[off:off + clen], dlen, st0, st1)
            else:
                raise ValueError(f"unsupported Granny compression {comp}")
            self.base.append(at)
            chunks.append(body)
            at += len(body)
        self.data = bytearray(b"".join(chunks))
        self.fixups = []        # (section, src offset, dst section, dst offset)
        for si, s in enumerate(self.sections):
            for k in range(s[8]):
                src, dsec, dofs = FIXUP.unpack_from(raw, s[7] + k * 12)
                self.fixups.append((si, src, dsec, dofs))
        # pointer value -> global offset; stored pointers are 0 in the file, the fixups give their targets
        self.ptr = {self.base[si] + src: self.base[dsec] + dofs for si, src, dsec, dofs in self.fixups}

    # -- navigation
    def deref(self, at: int):
        return self.ptr.get(at)

    def cstr(self, at):
        if at is None:
            return None
        end = self.data.index(0, at)
        return self.data[at:end].decode("latin-1")

    def members(self, type_at: int):
        out, p = [], type_at
        while True:
            t = struct.unpack_from("<I", self.data, p)[0]
            if t == 0:
                return out
            name = self.cstr(self.deref(p + 4))
            sub = self.deref(p + 12)
            width = struct.unpack_from("<i", self.data, p + 20)[0]
            out.append((t, name, sub, width))
            p += 44

    def size_of(self, type_at: int) -> int:
        n = 0
        for t, _, sub, width in self.members(type_at):
            one = self.size_of(sub) if t == 1 else SIZES.get(t, 0)
            n += one * max(width, 1)
        return n

    def walk(self, type_at: int, at: int, depth: int = 0, seen=None):
        """-> dict name -> value; each value is (offset, python value). Arrays of structs become lists of dicts."""
        seen = set() if seen is None else seen
        obj, p = {}, at
        for t, name, sub, width in self.members(type_at):
            n = max(width, 1)
            if t == 1:                      # inline struct
                sz = self.size_of(sub)
                vals = [self.walk(sub, p + i * sz, depth, seen) for i in range(n)]
                obj[name] = (p, vals if width > 0 else vals[0]); p += sz * n
                continue
            if t in PRIM:
                fmt = "<" + PRIM[t] * n
                v = struct.unpack_from(fmt, self.data, p)
                obj[name] = (p, list(v) if width > 0 else v[0]); p += SIZES[t] * n
                continue
            if t == 9:                      # transform: flags, translation[3], rotation[4], scale/shear 3x3
                v = struct.unpack_from("<I3f4f9f", self.data, p)
                obj[name] = (p, {"flags": v[0], "t": v[1:4], "q": v[4:8], "ss": v[8:17]}); p += 68
                continue
            if t == 8:
                obj[name] = (p, self.cstr(self.deref(p))); p += 8
                continue
            if t in (2, 22):                # reference to one struct
                tgt = self.deref(p)
                obj[name] = (p, self.walk(sub, tgt, depth + 1, seen) if tgt is not None and sub and depth < 12 else tgt)
                p += 8
                continue
            if t == 3:                      # count + pointer to an array of structs
                cnt = struct.unpack_from("<I", self.data, p)[0]
                tgt = self.deref(p + 4)
                items = []
                if tgt is not None and sub and depth < 12:
                    sz = self.size_of(sub)
                    items = [self.walk(sub, tgt + i * sz, depth + 1, seen) for i in range(cnt)]
                obj[name] = (p, items); p += 12
                continue
            if t == 4:                      # count + pointer to an array of pointers
                cnt = struct.unpack_from("<I", self.data, p)[0]
                arr = self.deref(p + 4)
                items = []
                if arr is not None and depth < 12:
                    for i in range(cnt):
                        tgt = self.deref(arr + i * 8)
                        items.append(self.walk(sub, tgt, depth + 1, seen) if tgt is not None and sub else tgt)
                obj[name] = (p, items); p += 12
                continue
            if t == 5:                      # variant reference: type pointer + object pointer
                ty, tgt = self.deref(p), self.deref(p + 8)
                obj[name] = (p, self.walk(ty, tgt, depth + 1, seen) if ty is not None and tgt is not None and depth < 12 else None)
                p += 16
                continue
            if t == 7:                      # variant array: type pointer, count, pointer
                ty = self.deref(p)
                cnt = struct.unpack_from("<I", self.data, p + 8)[0]
                tgt = self.deref(p + 12)
                items = []
                if ty is not None and tgt is not None and depth < 12:
                    sz = self.size_of(ty)
                    items = [self.walk(ty, tgt + i * sz, depth + 1, seen) for i in range(cnt)]
                obj[name] = (p, items); p += 20
                continue
            p += SIZES.get(t, 0) * n
        return obj

    def root(self):
        t = self.base[self.type_ref[0]] + self.type_ref[1]
        r = self.base[self.root_ref[0]] + self.root_ref[1]
        return self.walk(t, r)

    # -- writing
    def build(self) -> bytes:
        """The file with every section stored uncompressed (Granny compression type 0)."""
        info = 32
        head = bytearray(self.raw[:info + self.secoff + self.nsec * 44])
        body, fix = bytearray(), bytearray()
        offs = []
        for si, s in enumerate(self.sections):
            start, end = self.base[si], self.base[si] + s[3]
            align = max(s[4], 4)
            while (len(head) + len(body)) % align:
                body += b"\0"
            offs.append(len(head) + len(body))
            body += self.data[start:end]
        fix_at = []
        for si, s in enumerate(self.sections):
            fix_at.append(len(head) + len(body) + len(fix))
            for k in range(s[8]):
                fix += self.raw[s[7] + k * 12:s[7] + k * 12 + 12]
        # marshalling entries (16 bytes each) tell the big-endian 360 how to byte-swap mixed-size structs (the
        # animation curves); dropping them crashed FH1
        marsh = bytearray()
        marsh_at = []
        for si, s in enumerate(self.sections):
            marsh_at.append(len(head) + len(body) + len(fix) + len(marsh))
            marsh += self.raw[s[9]:s[9] + s[10] * 16]
        for si, s in enumerate(self.sections):
            n = list(s)
            n[0], n[1], n[2] = 0, offs[si], s[3]
            # n[5], n[6] stay: besides the Oodle stops they mark where the section's 16-bit and 8-bit data start,
            # which the big-endian 360 needs to byte-swap the file (overwriting them crashed FH1)
            n[7] = fix_at[si]
            n[9] = marsh_at[si]
            SECTION.pack_into(head, info + self.secoff + si * 44, *n)
        out = head + body + fix + marsh
        struct.pack_into("<I", out, info + 4, len(out))
        crc = zlib.crc32(bytes(out[info + self.secoff:])) & 0xFFFFFFFF      # everything after the file info
        struct.pack_into("<I", out, info + 8, crc)
        return bytes(out)


def read(data: bytes) -> Gr2:
    return Gr2(data)
