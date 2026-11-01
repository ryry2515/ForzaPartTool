"""atlas_paint.py - give an added model its own texture by painting into an UNUSED region of the car's baked atlas.

A car's body textures are one atlas (`nodamage`, `damage`, with `_LOD0` variants at twice the size). Every sub-mesh reads it through its uv0; most materials read a single
flat-colour texel ("swatch"), a few read real regions. You choose the rectangle (the editor shows the atlas and you drag over a plain area): an image painted there and a model whose uv0 points there get that texture.
Work out what is free by looking at the atlas; guessing it from the models' uvs was tried and does not work (tiling materials and swatch texels make everything look used).

    img  = resize(rgba, w, h)                           # bilinear
    out  = paint(atlas_xds_bytes, rgba, rect)           # new .xds bytes: image pasted into the rect, every mip level regenerated

Which atlas a given material samples is NOT known from the files; the editor paints `nodamage` and `damage` (the atlases the body parts use) and the user chooses the material.
[Untested in the game unless the README says so]
"""
from __future__ import annotations

import re

import numpy as np

import xds


def resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Bilinear resize of an RGBA uint8 image (box filter when shrinking a lot)."""
    sh, sw = img.shape[:2]
    if (sw, sh) == (w, h):
        return img
    a = img.astype(np.float32)
    if sw >= 2 * w or sh >= 2 * h:
        a = xds.downsample(img, max(w, 1), max(h, 1)).astype(np.float32)
        sh, sw = a.shape[:2]
    ys = (np.arange(h) + 0.5) * sh / h - 0.5
    xs = (np.arange(w) + 0.5) * sw / w - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, sh - 1); y1 = np.clip(y0 + 1, 0, sh - 1); fy = (ys - np.floor(ys))[:, None, None]
    x0 = np.clip(np.floor(xs).astype(int), 0, sw - 1); x1 = np.clip(x0 + 1, 0, sw - 1); fx = (xs - np.floor(xs))[None, :, None]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return np.clip(np.round(top * (1 - fy) + bot * fy), 0, 255).astype(np.uint8)


def paint(atlas: bytes, rgba: np.ndarray, rect: tuple[float, float, float, float]) -> bytes:
    """Paste `rgba` into `rect` (uv) of an atlas .xds and regenerate its mip levels. The image is resized to the rectangle's pixel size in THIS atlas."""
    inf = xds.info(atlas)
    base = xds.decode(atlas)
    u0, v0, u1, v1 = rect
    x0, y0 = int(round(u0 * inf["w"])), int(round(v0 * inf["h"]))
    x1, y1 = int(round(u1 * inf["w"])), int(round(v1 * inf["h"]))
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(max(x1, x0 + 1), inf["w"]), min(max(y1, y0 + 1), inf["h"])
    base[y0:y1, x0:x1] = resize(rgba, x1 - x0, y1 - y0)
    return xds.encode_like(atlas, base)


def _raster(mask: np.ndarray, uv: np.ndarray, tris: np.ndarray) -> None:
    R = mask.shape[0]
    for t in tris:
        p = uv[t] * R
        x0, y0 = np.floor(p.min(0)).astype(int)
        x1, y1 = np.ceil(p.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, R), min(y1, R)
        if x1 <= x0 or y1 <= y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1) + .5, np.arange(y0, y1) + .5)
        a, b, c = p
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-9:
            mask[y0:y1, x0:x1] = True
            continue
        l1 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / den
        l2 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / den
        mask[y0:y1, x0:x1] |= (l1 >= -.02) & (l2 >= -.02) & (1 - l1 - l2 >= -.02)


# Materials left out of the "read by a part" test: their shader reads a texture of its own (a WheelDiffuse / BrakeDiffuse / EngineDiffuse / BumperFrame / carbon fibre / grille / cloth sampler),
# or they are lamp, glass and badge materials whose atlas area is picture content that the white test (white_mask) already protects.
OWN_TEXTURE = re.compile(r"rim|tire|tyre|rotor|caliper|brake|carbon|grille|tread|cloth|leather|mottled|headliner|gauge|stitch|wood|frame|under|bottom|window|glass|light|reflector|indicator|fog|head|reverse|emblem|badge", re.I)


def part_masks(carbins, res: int = 256) -> dict[str, np.ndarray]:
    """{part name: True where that part reads the car's body atlas}. A sub-mesh reads the body atlas when its material is not one that has a texture of its own (OWN_TEXTURE) and its uv stay inside
    0..1 (tiling uv leave it). Flat-colour sub-meshes read one texel and are marked as one texel. Pass the main and _lod0 models (they share one uv layout); the cockpit model reads the interior atlas."""
    import carbin_geom as cg
    out: dict[str, np.ndarray] = {}
    for c in carbins:
        for sec in c.sections:
            pool = cg.active_pool(sec)
            if not pool.count:
                continue
            d = cg.decode_pool(sec, pool)
            mask = out.setdefault(sec.name, np.zeros((res, res), bool))
            for sb in sec.subs:
                if OWN_TEXTURE.search(sb.name):
                    continue
                tris = cg.sub_triangles(sb, pool.count)
                if not len(tris):
                    continue
                uv = cg.final_uv(d.uv0, sb.uvxf[0:4])
                if not np.isfinite(uv).all() or uv.min() < -0.01 or uv.max() > 1.01:
                    continue
                if cg.is_swatch(sb):
                    xy = np.clip((uv.mean(0) * res).astype(int), 0, res - 1)
                    mask[xy[1], xy[0]] = True
                else:
                    _raster(mask, uv, tris)
    return out


def white_mask(atlas_rgba: np.ndarray, res: int = 256, threshold: int = 248) -> np.ndarray:
    """True for atlas blocks (res x res) that are entirely near-white. The body atlas is white where the car's paint goes and in its padding; anything not white is texture content in use."""
    h, w = atlas_rgba.shape[:2]
    rgb = atlas_rgba[:, :, :3].min(axis=2)
    by, bx = max(h // res, 1), max(w // res, 1)
    blocks = rgb[:by * res, :bx * res].reshape(res, by, res, bx).min(axis=(1, 3))
    return blocks >= threshold


def used_mask(masks: dict[str, np.ndarray], overwrite: set[str] | frozenset = frozenset(), res: int = 256, white: np.ndarray | None = None) -> np.ndarray:
    """Pixels still in use: read by a part that is NOT in `overwrite` (pixels shared with a kept part stay protected, so only area used by the chosen parts alone is freed), or, when the atlas picture
    `white` is given, not white (picture content: lamp and glass textures, logos, interior)."""
    keep = np.zeros((res, res), bool)
    for name, m in masks.items():
        if name not in overwrite:
            keep |= m
    if white is not None:
        keep |= ~white
    return keep


def largest_free_rect(used: np.ndarray, margin: int = 3, aspect: float | None = None):
    """Biggest axis-aligned rectangle with no used pixel within `margin` pixels (room for texture filtering and mip levels). With `aspect` (width / height) the rectangle is shrunk to that shape.
    -> (u0, v0, u1, v1, free fraction of the whole atlas) or None."""
    blocked = used.copy()
    for _ in range(margin):                      # grow the used area by one pixel per pass (4-neighbourhood)
        g = blocked.copy()
        g[1:] |= blocked[:-1]
        g[:-1] |= blocked[1:]
        g[:, 1:] |= blocked[:, :-1]
        g[:, :-1] |= blocked[:, 1:]
        blocked = g
    free = ~blocked
    if aspect:
        # biggest rectangle of exactly this shape: try heights from the top down, an integral image tells whether any position is completely free
        R = used.shape[0]
        S = np.zeros((R + 1, R + 1), np.int64)
        S[1:, 1:] = np.cumsum(np.cumsum(blocked.astype(np.int64), axis=0), axis=1)
        for hh in range(R, 3, -1):
            ww = int(round(hh * aspect))
            if ww < 4 or ww > R:
                continue
            tot = S[hh:, ww:] - S[:-hh, ww:] - S[hh:, :-ww] + S[:-hh, :-ww]
            ys, xs = np.nonzero(tot == 0)
            if len(ys):
                y0, x0 = int(ys[0]), int(xs[0])
                return (x0 / R, y0 / R, (x0 + ww) / R, (y0 + hh) / R, float(free.mean()))
        return None
    h = np.zeros(used.shape[1], int)
    best, rect = 0, None
    for y in range(used.shape[0]):
        h = np.where(free[y], h + 1, 0)
        st = []
        for x in range(len(h) + 1):
            cur = h[x] if x < len(h) else 0
            start = x
            while st and st[-1][1] >= cur:
                s, hh = st.pop()
                if hh * (x - s) > best:
                    best, rect = hh * (x - s), (s, y - hh + 1, x, y + 1)
                start = s
            st.append((start, cur))
    if not rect:
        return None
    R = used.shape[0]
    x0, y0, x1, y1 = rect
    w, hh = x1 - x0, y1 - y0
    if aspect:
        if w / hh > aspect:
            nw = int(hh * aspect)
            x0 += (w - nw) // 2
            x1 = x0 + nw
        else:
            nh = int(w / aspect)
            y0 += (hh - nh) // 2
            y1 = y0 + nh
    return (x0 / R, y0 / R, x1 / R, y1 / R, float(free.mean()))


def uv_in_rect(uv: np.ndarray, rect) -> np.ndarray:
    """Map model uv (0..1, v down) into the atlas rectangle."""
    u0, v0, u1, v1 = rect
    return np.column_stack([u0 + np.clip(uv[:, 0], 0, 1) * (u1 - u0), v0 + np.clip(uv[:, 1], 0, 1) * (v1 - v0)])
