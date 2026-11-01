"""xds.py - read and write the base level of Xbox 360 `.xds` textures (the textures of Forza Horizon 1 and Forza Motorsport 4).

An `.xds` file is a 52-byte header followed by the texture payload. The header ends with a 24-byte GPU texture fetch constant (six big-endian dwords at offset 28):
    dword 0: bit 31 = tiled, bits 22..30 = pitch in units of 32 texels
    dword 1: bits 0..5 = format, bits 6..7 = endianness
    dword 2: bits 0..12 = width - 1, bits 13..25 = height - 1
The first 28 bytes are kept verbatim (not decoded). Formats (names from the Xbox 360 GPU format list, see the Xenia emulator source [External]):
    6 / 50 = 8888 (BGRA order in memory), 18 / 51 = DXT1, 19 / 52 = DXT3, 20 / 53 = DXT5, 49 = DXN (two-channel), 58 / 59 = single-channel alpha blocks.
Tiling is the XGAddress2DTiledOffset layout (measured by round-trip against game textures); compressed formats are tiled in units of blocks, padded to 32 blocks.
Endianness 1 = byte swap inside each 16-bit word, 2 = inside each 32-bit word.

Public functions:
    info(data)                   -> dict(fmt, endian, w, h, tiled, pitch, payload_size, mips_hint)
    decode(data)                 -> RGBA uint8 array (h, w, 4) of the base level (formats 6, 50, 18, 51, 19, 52, 20, 53)
    encode_like(template, rgba)  -> bytes: same header and format as `template`, base level replaced by `rgba` (8888, DXT1, DXT3 not supported for encode, DXT5);
                                    any mip levels after the base level are left as the template had them [Unknown whether the game needs them updated]
    solid(template, kind)        -> bytes: a uniform texture ('white', 'black', 'normal', 'clear') reusing the template's header and size
Needs numpy. PNG helpers need Pillow (optional).
"""
from __future__ import annotations

import struct

import numpy as np

HEADER = 52
FMT_8888 = (6, 50)
FMT_DXT1 = (18, 51)
FMT_DXT3 = (19, 52)
FMT_DXT5 = (20, 53)


def info(data: bytes) -> dict:
    f = struct.unpack_from(">6I", data, 28)
    w, h = (f[2] & 0x1FFF) + 1, ((f[2] >> 13) & 0x1FFF) + 1
    return {"fmt": f[1] & 0x3F, "endian": (f[1] >> 6) & 3, "w": w, "h": h, "tiled": bool(f[0] >> 31),
            "pitch": max(((f[0] >> 22) & 0x1FF) * 32, w), "payload_size": len(data) - HEADER}


def tiled_offset(x: np.ndarray, y: np.ndarray, width: int, texel_pitch: int) -> np.ndarray:
    """XGAddress2DTiledOffset, vectorised. `texel_pitch` is bytes per texel (or per 4x4 block); returns element offsets."""
    aligned_w = (width + 31) & ~31
    log_bpp = (texel_pitch >> 2) + ((texel_pitch >> 1) >> (texel_pitch >> 2))
    macro = ((x >> 5) + (y >> 5) * (aligned_w >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return (((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63)
            + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> log_bpp


def _unswap(raw: np.ndarray, endian: int) -> np.ndarray:
    """raw: flat uint8 payload -> bytes in little-endian element order."""
    if endian == 1:
        return raw[: len(raw) // 2 * 2].reshape(-1, 2)[:, ::-1].reshape(-1)
    if endian == 2:
        return raw[: len(raw) // 4 * 4].reshape(-1, 4)[:, ::-1].reshape(-1)
    return raw


def _swap(raw: np.ndarray, endian: int) -> np.ndarray:
    return _unswap(raw, endian)        # byte reversal inside a word is its own inverse


def _blocks(data: bytes, inf: dict, block_bytes: int) -> np.ndarray:
    """Untiled block array (blocks_h, blocks_w, block_bytes) for the base level."""
    bw, bh = (inf["w"] + 3) // 4, (inf["h"] + 3) // 4
    raw = _unswap(np.frombuffer(data, np.uint8, len(data) - HEADER, HEADER), inf["endian"])
    pitch_blocks = max(inf["pitch"] // 4, bw)
    aw = (pitch_blocks + 31) & ~31
    ah = (bh + 31) & ~31
    need = aw * ah * block_bytes
    if len(raw) < need and inf["tiled"]:
        raw = np.concatenate([raw, np.zeros(need - len(raw), np.uint8)])
    grid = raw[: aw * ah * block_bytes].reshape(-1, block_bytes) if inf["tiled"] else raw.reshape(-1, block_bytes)
    yy, xx = np.mgrid[0:bh, 0:bw].astype(np.int64)
    off = tiled_offset(xx.ravel(), yy.ravel(), aw, block_bytes) if inf["tiled"] else (yy * pitch_blocks + xx).ravel()
    return grid[off].reshape(bh, bw, block_bytes)


def _expand565(v: np.ndarray) -> np.ndarray:
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float32)


def _color_block(cb: np.ndarray, allow_alpha: bool) -> tuple[np.ndarray, np.ndarray]:
    """cb: (N, 8) uint8 DXT colour blocks -> rgb (N,16,3) and alpha (N,16) (255 unless 1-bit alpha)."""
    c0 = cb[:, 0].astype(np.uint16) | (cb[:, 1].astype(np.uint16) << 8)
    c1 = cb[:, 2].astype(np.uint16) | (cb[:, 3].astype(np.uint16) << 8)
    bits = cb[:, 4:8].copy().view("<u4")[:, 0]
    p0, p1 = _expand565(c0), _expand565(c1)
    four = c0 > c1
    pal = np.zeros((len(cb), 4, 3), np.float32)
    pal[:, 0], pal[:, 1] = p0, p1
    pal[:, 2] = np.where(four[:, None], (2 * p0 + p1) / 3, (p0 + p1) / 2)
    pal[:, 3] = np.where(four[:, None], (p0 + 2 * p1) / 3, 0)
    idx = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).astype(np.int64)
    rgb = np.take_along_axis(pal, idx[:, :, None], 1)
    alpha = np.full((len(cb), 16), 255, np.uint8)
    if allow_alpha:
        alpha[(~four)[:, None] & (idx == 3)] = 0
    return rgb, alpha


def _alpha5(ab: np.ndarray) -> np.ndarray:
    a0, a1 = ab[:, 0].astype(np.float32), ab[:, 1].astype(np.float32)
    bits = np.zeros(len(ab), np.uint64)
    for i in range(6):
        bits |= ab[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    pal = np.zeros((len(ab), 8), np.float32)
    pal[:, 0], pal[:, 1] = a0, a1
    big = (a0 > a1)[:, None]
    for k in range(1, 7):
        pal[:, 1 + k] = np.where(big[:, 0], ((7 - k) * a0 + k * a1) / 7, np.where(k <= 4, ((5 - k) * a0 + k * a1) / 5, 0))
    pal[:, 7] = np.where(big[:, 0], pal[:, 7], 255)
    idx = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
    return np.take_along_axis(pal, idx, 1)


def _alpha4(ab: np.ndarray) -> np.ndarray:
    nib = np.stack([ab & 15, ab >> 4], -1).reshape(len(ab), 16).astype(np.float32)
    return nib * 17


def decode(data: bytes, level: int = 0) -> np.ndarray:
    if level:
        lay = mip_layout(info(data))
        if not lay or level >= len(lay):
            raise ValueError('no such mip level')
        data = _level_template(data, lay[level])
    inf = info(data)
    f, w, h = inf["fmt"], inf["w"], inf["h"]
    if f in FMT_8888:
        raw = _unswap(np.frombuffer(data, np.uint8, (len(data) - HEADER) // 4 * 4, HEADER), inf["endian"]).reshape(-1, 4)
        pitch = inf["pitch"]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.int64)
        off = tiled_offset(xx.ravel(), yy.ravel(), (pitch + 31) & ~31, 4) if inf["tiled"] else (yy * pitch + xx).ravel()
        px = raw[off].reshape(h, w, 4)
        return np.ascontiguousarray(px[..., [2, 1, 0, 3]])        # BGRA -> RGBA
    if f in FMT_DXT1 + FMT_DXT3 + FMT_DXT5:
        bb = 8 if f in FMT_DXT1 else 16
        blk = _blocks(data, inf, bb).reshape(-1, bb)
        if f in FMT_DXT1:
            rgb, a = _color_block(blk, True)
            alpha = a.astype(np.float32)
        elif f in FMT_DXT3:
            rgb, _ = _color_block(blk[:, 8:], False)
            alpha = _alpha4(blk[:, :8])
        else:
            rgb, _ = _color_block(blk[:, 8:], False)
            alpha = _alpha5(blk[:, :8])
        px = np.concatenate([rgb, alpha[:, :, None]], -1).reshape((h + 3) // 4, (w + 3) // 4, 4, 4, 4)
        px = px.transpose(0, 2, 1, 3, 4).reshape((h + 3) // 4 * 4, (w + 3) // 4 * 4, 4)[:h, :w]
        return np.clip(np.round(px), 0, 255).astype(np.uint8)
    raise ValueError(f"format {f} is not decoded by this module")


def _to_blocks(img: np.ndarray):
    h, w = img.shape[:2]
    ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (ph, pw) != (h, w):
        img = np.pad(img, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    return img.reshape(ph // 4, 4, pw // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)


def _rgb565(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.round(c), 0, 255).astype(np.uint16)
    return ((c[:, 0] >> 3) << 11) | ((c[:, 1] >> 2) << 5) | (c[:, 2] >> 3)


def _encode_color(blocks: np.ndarray) -> np.ndarray:
    rgb = blocks[..., :3].astype(np.float32)
    mn, mx = rgb.min(1), rgb.max(1)
    inset = (mx - mn) / 16.0
    c0, c1 = _rgb565(mx - inset), _rgb565(mn + inset)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _expand565(c0), _expand565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(-1).astype(np.uint32)
    idx[c0 == c1] = 0
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1, dtype=np.uint64).astype(np.uint32)
    out = np.zeros((len(blocks), 8), np.uint8)
    out[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def _encode_alpha(blocks: np.ndarray) -> np.ndarray:
    a = blocks[..., 3].astype(np.float32)
    a0, a1 = a.max(1), a.min(1)
    w = np.array([0, 7, 1, 2, 3, 4, 5, 6], np.float32)
    pal = ((7 - w)[None] * a0[:, None] + w[None] * a1[:, None]) / 7.0
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    idx[a0 == a1] = 0
    bits = (idx << (np.uint64(3) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    out = np.zeros((len(blocks), 8), np.uint8)
    out[:, 0] = a0.astype(np.uint8)
    out[:, 1] = a1.astype(np.uint8)
    out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out


def _encode_level(template: bytes, rgba: np.ndarray) -> bytes:
    inf = info(template)
    f, w, h = inf["fmt"], inf["w"], inf["h"]
    if rgba.shape[:2] != (h, w):
        raise ValueError(f"template is {w}x{h}, image is {rgba.shape[1]}x{rgba.shape[0]}")
    if f in FMT_8888:
        px = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])
        pitch = inf["pitch"]
        aw, ah = (pitch + 31) & ~31, (h + 31) & ~31
        out = np.zeros(((len(template) - HEADER) // 4, 4), np.uint8)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.int64)
        off = tiled_offset(xx.ravel(), yy.ravel(), aw, 4) if inf["tiled"] else (yy * pitch + xx).ravel()
        out[off] = px.reshape(-1, 4)
        payload = _swap(out.reshape(-1), inf["endian"]).tobytes()
        return template[:HEADER] + payload + template[HEADER + len(payload):]
    if f in FMT_DXT1 + FMT_DXT5:
        blocks = _to_blocks(np.asarray(rgba, np.uint8))
        lin = _encode_color(blocks) if f in FMT_DXT1 else np.hstack([_encode_alpha(blocks), _encode_color(blocks)])
        bb = lin.shape[1]
        bw, bh = (w + 3) // 4, (h + 3) // 4
        pitch_blocks = max(inf["pitch"] // 4, bw)
        aw, ah = (pitch_blocks + 31) & ~31, (bh + 31) & ~31
        if inf["tiled"]:
            dst = np.zeros((aw * ah, bb), np.uint8)
            yy, xx = np.mgrid[0:bh, 0:bw].astype(np.int64)
            dst[tiled_offset(xx.ravel(), yy.ravel(), aw, bb)] = lin
        else:
            dst = np.zeros((pitch_blocks * bh, bb), np.uint8)
            dst[: len(lin)] = lin
        payload = _swap(dst.reshape(-1), inf["endian"]).tobytes()
        if len(payload) > len(template) - HEADER:
            payload = payload[: len(template) - HEADER]
        return template[:HEADER] + payload + template[HEADER + len(payload):]
    raise ValueError(f"encoding format {f} is not supported (8888, DXT1 and DXT5 only)")


def _align32(n: int) -> int:
    return (n + 31) & ~31


def mip_layout(inf: dict) -> list[dict] | None:
    """Mip levels of a texture as stored (measured on the game's car atlases: levels follow each other, each tiled and padded to 32 x 32 blocks
    (32 x 32 texels for 8888); the last level ends exactly at the end of the payload). None when the layout does not add up."""
    f = inf["fmt"]
    if f in FMT_DXT1:
        unit, bpb = 4, 8
    elif f in FMT_DXT3 + FMT_DXT5:
        unit, bpb = 4, 16
    elif f in FMT_8888:
        unit, bpb = 1, 4
    else:
        return None
    w, h, off, out = inf["w"], inf["h"], 0, []
    while True:
        size = _align32(-(-w // unit)) * _align32(-(-h // unit)) * bpb
        out.append({"level": len(out), "w": w, "h": h, "offset": off, "size": size})
        off += size
        if off >= inf["payload_size"] or (w == 1 and h == 1):
            break
        w, h = max(w // 2, 1), max(h // 2, 1)
    return out if off == inf["payload_size"] else None


def _level_template(data: bytes, lv: dict) -> bytes:
    """A texture file holding only mip level `lv` as its base level (header patched to its size)."""
    hd = bytearray(data[:HEADER])
    f = list(struct.unpack_from(">6I", hd, 28))
    f[0] = (f[0] & ~(0x1FF << 22)) | ((max(-(-lv["w"] // 32), 1) & 0x1FF) << 22)
    f[2] = (f[2] & ~0x03FFFFFF) | ((lv["w"] - 1) & 0x1FFF) | (((lv["h"] - 1) & 0x1FFF) << 13)
    struct.pack_into(">6I", hd, 28, *f)
    seg = data[HEADER + lv["offset"]:HEADER + lv["offset"] + lv["size"]]
    return bytes(hd) + seg + bytes(lv["size"] - len(seg))


def downsample(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    """Box-filter an RGBA image to (h, w)."""
    a = rgba.astype(np.float32)
    while a.shape[1] > w or a.shape[0] > h:
        if a.shape[0] % 2:
            a = np.concatenate([a, a[-1:]], 0)
        if a.shape[1] % 2:
            a = np.concatenate([a, a[:, -1:]], 1)
        a = a.reshape(a.shape[0] // 2 if a.shape[0] > h else a.shape[0], 2 if a.shape[0] > h else 1,
                      a.shape[1] // 2 if a.shape[1] > w else a.shape[1], 2 if a.shape[1] > w else 1, 4).mean((1, 3))
    return np.clip(np.round(a), 0, 255).astype(np.uint8)


def encode_like(template: bytes, rgba: np.ndarray, mips: bool = True) -> bytes:
    """Same header and format as `template`, base level replaced by `rgba`. With mips=True every smaller level is regenerated by a box filter
    when the template's mip layout is understood (see mip_layout); otherwise only the base level is written and the rest stays as in the template."""
    out = _encode_level(template, rgba)
    lay = mip_layout(info(template)) if mips else None
    if not lay or len(lay) < 2:
        return out
    payload = bytearray(out[HEADER:])
    orig_base = decode(template).astype(np.float32)
    for lv in lay[1:]:
        # only touch a level whose original content decodes sensibly under this layout (the smallest tail levels of some files are packed differently)
        ref = downsample(orig_base.astype(np.uint8), lv["w"], lv["h"]).astype(np.float32)
        mse = float(((decode(template, lv["level"])[..., :3].astype(np.float32) - ref[..., :3]) ** 2).mean())
        if mse > 0 and 10 * np.log10(255 ** 2 / mse) < 15.0:
            continue
        small = downsample(rgba, lv["w"], lv["h"])
        enc = _encode_level(_level_template(template, lv), small)
        payload[lv["offset"]:lv["offset"] + lv["size"]] = enc[HEADER:HEADER + lv["size"]]
    return template[:HEADER] + bytes(payload)


def png_bytes(rgba: np.ndarray) -> bytes:
    """Encode an RGBA uint8 image as PNG without Pillow."""
    import zlib
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")

def solid(template: bytes, kind: str = "white") -> bytes:
    """Uniform texture of the template's size and format. kind: 'white', 'black', 'normal' (flat normal map), 'clear' (white, alpha 0)."""
    inf = info(template)
    f = inf["fmt"]
    c565 = {"white": 0xFFFF, "black": 0x0000, "normal": 0x841F, "clear": 0xFFFF}[kind]
    color = struct.pack("<HHI", c565, c565, 0)
    a = 0x80 if kind == "normal" else 0x00 if kind == "clear" else 0xFF
    alpha = bytes([a, a]) + b"\x00" * 6
    if f in (18, 51):
        block = color
    elif f in (20, 53):
        block = alpha + color
    elif f in (19, 52):
        block = b"\xFF" * 8 + color
    elif f == 49:
        v = 0x80 if kind == "normal" else 0xFF
        block = (bytes([v, v]) + b"\x00" * 6) * 2
    elif f in (58, 59):
        v = 0x00 if kind == "black" else 0xFF
        block = bytes([v, v]) + b"\x00" * 6
    elif f in FMT_8888:
        block = {"white": b"\xFF\xFF\xFF\xFF", "black": b"\xFF\x00\x00\x00", "normal": b"\xFF\x80\x80\xFF", "clear": b"\x00\xFF\xFF\xFF"}[kind]
    else:
        raise ValueError(f"unsupported texture format {f}")
    if inf["endian"] == 1:
        block = bytes(b for i in range(0, len(block), 2) for b in (block[i + 1], block[i]))
    elif inf["endian"] == 2:
        block = b"".join(block[i:i + 4][::-1] for i in range(0, len(block), 4))
    n = len(template) - HEADER
    return template[:HEADER] + (block * (n // len(block) + 1))[:n]


def to_png(data: bytes, path) -> None:
    from PIL import Image
    Image.fromarray(decode(data)).save(path)
