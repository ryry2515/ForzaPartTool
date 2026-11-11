"""part_look.py - edits to how a part looks and how it is split up: rename, move out, move into another part, recolour, and a car's ShaderSettings.xml.

A part (section) of a car model is made of sub-meshes; a sub-mesh is named after its material and exists once per level of detail (LOD). Everything here works on
a *material group*: all sub-meshes of one part that have the same material name, every LOD together (the "glass" of the body, the "chrome" of a bumper, ...).

    split_material(car, part, material, name)    # the group becomes a part of its own (selectable, movable, renamable)
    move_material(car, part, material, target)   # the group joins another part
    rename_material(car, part, material, new)    # every LOD of the group gets another material name
    point_at(sub, u, v) / find_free_cell(...)    # recolour: a flat colour is one texel ("swatch") of the car's baked atlas; give the group a texel of its own

ShaderSettings.xml (one per car) holds the values the game's shaders read per material: the tint of window glass (GlassColor), the colour of lamp lenses and
light bulbs (BrakeColor, TailLightColor, XenonHeadlightColor, ...), the opacity of lamp glass (GlassMaxOpacity). The functions below read and edit it as TEXT,
changing only the one element asked for, because about 3 % of the stock files are not well-formed XML and must not be rewritten wholesale.
[Atlas recolouring and the tint values are checked in the viewer; whether the game draws them exactly so is not tested unless the README says so.]
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import numpy as np

import atlas_paint as ap
import carbin_geom as cg
import carbin_parts
import part_actions


# ------------------------------------------------------------------------------------------------------------------------ material groups

def group_subs(car, si: int, material: str) -> list[int]:
    """Indexes of the sub-meshes of part `si` named `material` (all LODs)."""
    sec = part_actions.section_at(car, si)
    ks = [k for k, sb in enumerate(sec.subs) if sb.name == material]
    if not ks:
        raise ValueError(f'The part {sec.name} has no sub-part named {material}')
    return ks


def pieces_of(car, si: int, ks) -> list:
    """The geometry of sub-meshes `ks` of part `si` as cg.Piece objects in world coordinates (what the encoder takes), uv kept as the game reads them."""
    sec = car.sections[si]
    pool = cg.active_pool(sec)
    if not pool.count:
        raise ValueError(f'The part {sec.name} has no geometry')
    dec = cg.decode_pool(sec, pool)
    pieces = []
    for k in ks:
        sub = sec.subs[k]
        tris = cg.sub_triangles(sub, pool.count)
        if not len(tris):
            continue
        uv0 = np.nan_to_num(cg.final_uv(dec.uv0, sub.uvxf[:4]), nan=0.0, posinf=0.0, neginf=0.0)       # unused LOD placeholders carry a "no transform" marker
        uv1 = np.nan_to_num(cg.final_uv(dec.uv1, sub.uvxf[4:8]), nan=0.0, posinf=0.0, neginf=0.0)
        pieces.append(cg.Piece(sub.name, sub.lod, dec.pos, dec.nrm, uv0, uv1, tris, swatch=cg.is_swatch(sub)))
    if not pieces:
        raise ValueError('That sub-part has no triangles')
    return pieces


def split_material(car, si: int, material: str, name: str = '', taken=()) -> int:
    """Move a material group out of part `si` into a NEW part of its own (all its LODs; pivot in the middle of the group so it rotates and scales sensibly).
    The new part is drawn in the same draw lists as the group was. -> the new part's index."""
    ks = group_subs(car, si, material)
    pieces = pieces_of(car, si, ks)
    sec = car.sections[si]
    placement = cg.capture_placement(car, si)
    every = np.vstack([p.pos[np.unique(p.tris)] for p in pieces])
    centre = (every.min(0) + every.max(0)) / 2.0
    new = carbin_parts.add_section(car, pieces, (name or '').strip() or f'{sec.name}_{material}', centre, template_index=si, taken=taken)
    cg.refresh_draw_lists(car, new, placement, bool(placement) or not cg.has_body_header(car))      # a group the game did not draw stays undrawn
    cg.remove_subs(car, si, ks)
    return new


def move_material(car, si: int, material: str, target: int) -> None:
    """Move a material group of part `si` into part `target` (all its LODs). Geometry keeps its world position."""
    part_actions.section_at(car, target)
    if si == target:
        raise ValueError('Choose a different part to move it into')
    ks = group_subs(car, si, material)
    pieces = pieces_of(car, si, ks)
    cg.append_pieces(car, target, pieces, cg.SHModel(car))
    cg.remove_subs(car, si, ks)


def rename_material(car, si: int, material: str, new: str) -> int:
    """Give every sub-mesh of the group another material name. Returns how many sub-meshes changed. The name must be one the file can hold (printable ASCII, no spaces)."""
    new = (new or '').strip()
    if not carbin_parts.valid_part_name(new):
        raise ValueError('A name must be 1 to 240 plain characters (letters, digits, underscore) without spaces')
    ks = group_subs(car, si, material)
    for k in ks:
        cg.set_sub(car, si, k, material=new)
    return len(ks)


# ------------------------------------------------------------------------------------------------------------------------ recolour (atlas swatches)

RES = 256               # the usage grid: one cell is 8 pixels of the 2048 atlas, 4 of the 1024 atlas
CELL = 4                # a swatch cell is 4 x 4 grid cells: 32 pixels on the big atlas, 16 on the small one (a mip level or two before it blurs into its neighbours)
GUTTER = 1              # free grid cells kept clear around a cell

# Materials that are not coloured through the body atlas: glass and lamps read the lamp atlas / shader values, gauges and mirrors are the interior's own.
NOT_FLAT = re.compile(r'glass|window|gls|light|lamp|lens|reflector|indicator|^fog|hidhead|xenonhead|oldhead|slorange|^drl|gauge|mirror|emissive', re.I)


def flat_problem(sub) -> str | None:
    """Why this sub-mesh cannot be given one flat colour, or None."""
    if NOT_FLAT.search(sub.name):
        return 'glass and lamps are coloured in the Glass & lights tab'
    if not cg.is_swatch(sub) and ap.OWN_TEXTURE.search(sub.name):
        return 'its material reads a texture of its own (a flat colour would not look like it)'
    return None


def _mark(mask: np.ndarray, uv: np.ndarray, tris: np.ndarray) -> None:
    """Mark the atlas cells a set of triangles covers. Triangles that fit in about a 2 x 2 block of cells (nearly all of them) are marked from their corners, middle and edge middles in one
    vectorised step; the few bigger ones are rasterised properly. The gutter around a cell (GUTTER) absorbs the difference."""
    res = mask.shape[0]
    p = uv[tris] * res                                              # (T, 3, 2)
    small = ((np.ceil(p.max(1)) - np.floor(p.min(1))) <= 2).all(1)
    if small.any():
        q = p[small]
        for pt in (q[:, 0], q[:, 1], q[:, 2], q.mean(1), (q[:, 0] + q[:, 1]) / 2, (q[:, 1] + q[:, 2]) / 2, (q[:, 0] + q[:, 2]) / 2):
            xy = np.clip(pt.astype(int), 0, res - 1)
            mask[xy[:, 1], xy[:, 0]] = True
    if not small.all():
        ap._raster(mask, uv, tris[~small])


_usage_cache: dict = {}


def _section_usage(sec, res: int) -> np.ndarray:
    """What one part reads of the body atlas (see uv_usage), cached on what the answer depends on: its vertex pool and its sub-meshes' uv transforms."""
    pool = cg.active_pool(sec)
    if not pool.count:
        return np.zeros((res, res), bool)
    finite = lambda xs: tuple(0.0 if x != x else float(x) for x in xs)          # unused LOD placeholders carry NaN, which never equals itself and would defeat the cache
    key = (res, sec.name, pool.count, tuple(pool.bone), tuple((sb.name, finite(sb.uvxf[:4]), len(sb.indices)) for sb in sec.subs))
    hit = _usage_cache.get(key)
    if hit is not None:
        return hit
    mask = np.zeros((res, res), bool)
    dec = cg.decode_pool(sec, pool)
    for sb in sec.subs:
        tris = cg.sub_triangles(sb, pool.count)
        if not len(tris):
            continue
        swatch = cg.is_swatch(sb)
        if ap.OWN_TEXTURE.search(sb.name) and not swatch:           # its uv are for a texture of its own, not for the body atlas
            continue
        uv = cg.final_uv(dec.uv0, sb.uvxf[0:4])
        if not np.isfinite(uv).all() or uv.min() < -0.01 or uv.max() > 1.01:
            continue
        if swatch:                                                  # a flat colour is one texel, whatever the material is called (glass swatches count too)
            xy = np.clip((uv.mean(0) * res).astype(int), 0, res - 1)
            mask[xy[1], xy[0]] = True
        else:
            _mark(mask, uv, tris)
    if len(_usage_cache) > 600:
        _usage_cache.clear()
    _usage_cache[key] = mask
    return mask


def uv_usage(cars, res: int = RES) -> np.ndarray:
    """True where some sub-mesh of `cars` reads the body atlas: textured parts, minus the materials that sample a texture of their own (their uv cover the whole map and mean nothing for
    the body atlas), plus the one texel of EVERY flat-colour sub-mesh. The picture content of the atlas is protected separately (anything that is not white), so what is left is white and unread."""
    used = np.zeros((res, res), bool)
    for car in cars:
        for sec in car.sections:
            used |= _section_usage(sec, res)
    return used


def find_free_cell(used: np.ndarray, size: int = CELL, gutter: int = GUTTER):
    """Top-left grid cell (x, y) of a free size x size block with `gutter` free cells around it, or None. Takes the bottom-most, right-most one, so cells pack along an edge."""
    R = used.shape[0]
    side = size + 2 * gutter
    if side > R:
        return None
    S = np.zeros((R + 1, R + 1), np.int64)
    S[1:, 1:] = np.cumsum(np.cumsum(used.astype(np.int64), axis=0), axis=1)
    tot = S[side:, side:] - S[:-side, side:] - S[side:, :-side] + S[:-side, :-side]
    ys, xs = np.nonzero(tot == 0)
    if not len(ys):
        return None
    return int(xs[-1]) + gutter, int(ys[-1]) + gutter


def cell_rect(cell, size: int = CELL, res: int = RES):
    """(u0, v0, u1, v1) of a cell in atlas uv (v counted from the top, as the files store it)."""
    x, y = cell
    return x / res, y / res, (x + size) / res, (y + size) / res


def cell_centre(cell, size: int = CELL, res: int = RES):
    x, y = cell
    return (x + size / 2) / res, (y + size / 2) / res


def point_at(sub, u: float, v: float) -> None:
    """Make a sub-mesh read the single atlas texel (u, v) for every vertex: its uv transform gets scale 0 (a "swatch")."""
    xf = list(sub.uvxf)
    xf[0], xf[1], xf[2], xf[3] = float(u), 0.0, float(v), 0.0
    sub.uvxf = xf


def swatch_point(sub):
    """The (u, v) a swatch sub-mesh reads, or None."""
    if not cg.is_swatch(sub):
        return None
    return sub.uvxf[0] + sub.uvxf[1] / 2, sub.uvxf[2] + sub.uvxf[3] / 2


def count_pointing(car, u: float, v: float, tol: float = 2e-4) -> int:
    """How many sub-meshes of `car` read the swatch texel (u, v)."""
    n = 0
    for sec in car.sections:
        for sb in sec.subs:
            p = swatch_point(sb)
            if p and abs(p[0] - u) < tol and abs(p[1] - v) < tol:
                n += 1
    return n


def recolour_targets(car, si: int, material: str, scope: str = 'part') -> list[tuple[int, int]]:
    """(part, sub) pairs to recolour: the material group of part `si` ('part') or every part's sub-meshes of that material ('car'). Sub-meshes that cannot be a flat colour are left out."""
    out = []
    for i, sec in enumerate(car.sections):
        if scope != 'car' and i != si:
            continue
        for k, sb in enumerate(sec.subs):
            if sb.name == material and flat_problem(sb) is None:
                out.append((i, k))
    return out


# ------------------------------------------------------------------------------------------------------------------------ ShaderSettings.xml (as text)

COLOUR_PARAMS = ('GlassColor', 'BrakeColor', 'TailLightColor', 'HidHeadlightColor', 'XenonHeadlightColor', 'OldHeadlightColor', 'ReverseLightColor',
                 'ReverselightColor', 'DRLWhiteColor', 'DRLOrangeColor', 'DRLRedColor', 'SideLightsOrangeColor', 'IndicatorLightColor', 'FogWhiteColor',
                 'CockpitHeadlinerColor', 'StitchColor', 'GrilleColor', 'NumberPlateLightColor')
VALUE_PARAMS = ('GlassMaxOpacity', 'DarkValues', 'LightValues')
# Glass materials a car may use without having an entry yet, with the starting tint offered. The values come from stock cars that set them (Jeep Grand Cherokee SRT8: window 0.02 / 0.8,
# Camaro SS '69: detail_glass_red 0.8 0 0 / 0.7).
GLASS_START = {'window': (0.02, 0.02, 0.02, 0.8), 'detail_glass_red': (0.8, 0.0, 0.0, 0.7), 'detail_glass_amber': (0.45, 0.2, 0.0, 0.8),
               'detail_glass_clear': (0.75, 0.75, 0.75, 0.6), 'glass': (0.1, 0.1, 0.1, 0.3), 'lights_glass': (0.5, 0.5, 0.5, 1.0)}
LABELS = {
    'window': 'Windows', 'detail_glass_clear': 'Clear lamp glass (headlight and tail light covers)', 'detail_glass_red': 'Red lamp glass (brake and tail lights)',
    'detail_glass_amber': 'Amber lamp glass (indicators)', 'glass': 'Glass', 'lights_glass': 'Lamp glass',
    'tail_light': 'Tail light', 'taillight2S': 'Tail light (type 2)', 'taillightst': 'Tail light (type ST)', 'oldhead': 'Headlight bulb (old style)',
    'xenonhead': 'Headlight bulb (xenon)', 'hidhead': 'Headlight bulb (HID)', 'reverse_light': 'Reverse light', 'drlwhite': 'Daytime running light (white)',
    'slorange': 'Side light (orange)', 'cockpit_headliner': 'Roof lining', 'cockpit_bump_stitching': 'Seat stitching',
}
WINDOW_PRESETS = [('Light tint', (0.06, 0.06, 0.06, 0.55)), ('Dark tint', (0.02, 0.02, 0.02, 0.8)), ('Limo', (0.0, 0.0, 0.0, 0.92)), ('Blue tint', (0.0, 0.05, 0.12, 0.7))]
LAMP_GLASS_PRESETS = [('Red', (0.8, 0.0, 0.0, 0.7)), ('Smoked', (0.1, 0.03, 0.03, 0.85)), ('Clear', (0.9, 0.9, 0.9, 0.35))]

_BLOCK = re.compile(r'<(?P<n>[A-Za-z_][\w.\-]*)>(?P<b>.*?)</(?P=n)>', re.S)
_PARAM = re.compile(r'<(?P<p>[A-Za-z_]\w*)(?P<a>(?:\s+[\w:.\-]+\s*=\s*"[^"]*")*)\s*/>')
_ATTR = re.compile(r'([\w:.\-]+)\s*=\s*"([^"]*)"')


def _root(text: str):
    """(start, end) of the text between <ShaderSettings> and </ShaderSettings>."""
    head = re.search(r'<ShaderSettings\b[^>]*>', text)
    end = text.rfind('</ShaderSettings')
    if not head or end < head.end():
        raise ValueError('This ShaderSettings.xml has no <ShaderSettings> element')
    return head.end(), end


def _attrs(raw: str) -> dict:
    out = {}
    for k, v in _ATTR.findall(raw):
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def parse_settings(text: str) -> dict:
    """{material: {parameter: {attribute: value}}} of a ShaderSettings.xml, read leniently (a malformed file still gives what can be read)."""
    try:
        start, end = _root(text)
    except ValueError:
        return {}
    out: dict = {}
    for m in _BLOCK.finditer(text, start, end):
        mats = out.setdefault(m.group('n'), {})
        for p in _PARAM.finditer(m.group('b')):
            mats[p.group('p')] = _attrs(p.group('a'))
    return out


def _fmt(attrs: dict) -> str:
    return ' '.join(f'{k}="{float(v):.6f}"' for k, v in attrs.items())


def _find(text: str, material: str, param: str | None = None):
    """(block match, param match or None) for a material (and parameter), searching only inside the root element."""
    start, end = _root(text)
    for m in _BLOCK.finditer(text, start, end):
        if m.group('n') != material:
            continue
        if param is None:
            return m, None
        for p in _PARAM.finditer(m.group('b')):
            if p.group('p') == param:
                return m, p
        return m, None
    return None, None


def clean_values(param: str, values: dict, existing: dict | None = None) -> dict:
    """The attributes to write for `param`: `values` merged onto what the element has now, numbers checked and limited."""
    if param in COLOUR_PARAMS:
        keys = ('r', 'g', 'b', 'a') if (param == 'GlassColor' or 'a' in (existing or {}) or 'a' in values) else ('r', 'g', 'b')
        out = {k: float((existing or {}).get(k, 1.0 if k == 'a' else 0.0)) for k in keys}
        for k in keys:
            if k in values:
                out[k] = float(values[k])
        if not all(np.isfinite(v) for v in out.values()):
            raise ValueError('Colour values must be numbers')
        for k in ('r', 'g', 'b'):
            out[k] = min(max(out[k], 0.0), 16.0)
        if 'a' in out:
            out['a'] = min(max(out['a'], 0.0), 1.0 if param == 'GlassColor' else 16.0)
        return out
    if param in VALUE_PARAMS:
        v = float(values.get('value', (existing or {}).get('value', 1.0)))
        if not np.isfinite(v):
            raise ValueError('The value must be a number')
        return {'value': min(max(v, 0.0), 16.0)}
    raise ValueError(f'{param} is not a setting this tool edits')


def set_param(text: str, material: str, param: str, attrs: dict) -> str:
    """The text with `<material><param .../>` set to `attrs`: the element is replaced in place, added to the material, or the material is added. Nothing else changes."""
    if not re.fullmatch(r'[A-Za-z_][\w.\-]*', material or ''):
        raise ValueError('That is not a valid material name')
    elem = f'<{param} {_fmt(attrs)}/>'
    block, found = _find(text, material, param)
    if block is not None and found is not None:
        a = block.start('b') + found.start()
        return text[:a] + elem + text[block.start('b') + found.end():]
    if block is not None:
        a = block.start('b')
        return text[:a] + '\n    ' + elem + text[a:]
    _, end = _root(text)
    lead = '' if text[:end].endswith('\n') else '\n'
    return text[:end] + f'{lead}  <{material}>\n    {elem}\n  </{material}>\n' + text[end:]


def remove_param(text: str, material: str, param: str) -> str:
    """The text without `<material><param/>` (and without the material when nothing else is left in it)."""
    block, found = _find(text, material, param)
    if block is None or found is None:
        return text
    a, b = block.start('b') + found.start(), block.start('b') + found.end()
    while a > 0 and text[a - 1] in ' \t':
        a -= 1
    if a > 0 and text[a - 1] == '\n':
        a -= 1
    text = text[:a] + text[b:]
    block2, _ = _find(text, material)
    if block2 is not None and not _PARAM.search(block2.group('b')):
        s, e = block2.start(), block2.end()
        while s > 0 and text[s - 1] in ' \t':
            s -= 1
        if s > 0 and text[s - 1] == '\n':
            s -= 1
        text = text[:s] + text[e:]
    return text


def wellformed(text: str) -> bool:
    try:
        ET.fromstring(text.strip())
        return True
    except ET.ParseError:
        return False


def edit_checked(original: str, edited: str) -> str:
    """An edit may not turn a well-formed file into a malformed one."""
    if wellformed(original) and not wellformed(edited):
        raise ValueError('That edit would make ShaderSettings.xml malformed, so it was not applied')
    return edited


def reset_param(current: str, original: str, material: str, param: str) -> str:
    """Put `<material><param/>` back as the car's original file has it (or remove it where the original has none)."""
    was = parse_settings(original).get(material, {}).get(param)
    if was is None:
        return edit_checked(current, remove_param(current, material, param))
    return edit_checked(current, set_param(current, material, param, was))


def changed_params(original: str, current: str) -> list[list[str]]:
    """[[material, parameter], ...] whose values differ from the original file (added, changed or removed)."""
    a, b = parse_settings(original), parse_settings(current)
    out = []
    for mat in sorted(set(a) | set(b)):
        for prm in sorted(set(a.get(mat, {})) | set(b.get(mat, {}))):
            if a.get(mat, {}).get(prm) != b.get(mat, {}).get(prm):
                out.append([mat, prm])
    return out


def tints(original: str, current: str) -> dict:
    """What the viewer needs to draw edited values: {material: {'GlassColor': [r, g, b, a], 'GlassMaxOpacity': v, 'colour': [r, g, b]}} for changed parameters only."""
    now = parse_settings(current)
    out: dict = {}
    for mat, prm in changed_params(original, current):
        v = now.get(mat, {}).get(prm)
        if not v:
            continue
        if prm == 'GlassColor':
            out.setdefault(mat, {})['GlassColor'] = [v.get('r', 0.0), v.get('g', 0.0), v.get('b', 0.0), v.get('a', 1.0)]
        elif prm == 'GlassMaxOpacity':
            out.setdefault(mat, {})['GlassMaxOpacity'] = v.get('value', 1.0)
        elif prm in COLOUR_PARAMS:
            out.setdefault(mat, {})['colour'] = [v.get('r', 0.0), v.get('g', 0.0), v.get('b', 0.0)]
    return out


def look_rows(original: str, current: str, used_materials) -> list[dict]:
    """The rows of the Glass & lights tab: every colour or opacity setting the car's file has, plus the glass materials the car's model uses that have no GlassColor yet (those start at the game's own value)."""
    now, was = parse_settings(current), parse_settings(original)
    changed = {tuple(c) for c in changed_params(original, current)}
    rows = []
    for mat, params in now.items():
        for prm, vals in params.items():
            if prm in COLOUR_PARAMS or prm in VALUE_PARAMS:
                rows.append({'material': mat, 'param': prm, 'values': vals, 'changed': (mat, prm) in changed, 'present': True})
    have = {(r['material'], r['param']) for r in rows}
    for mat in GLASS_START:
        if mat in used_materials and (mat, 'GlassColor') not in have:
            rows.append({'material': mat, 'param': 'GlassColor', 'values': None, 'start': list(GLASS_START[mat]), 'changed': False, 'present': False})
    for r in rows:
        r['label'] = LABELS.get(r['material'], r['material'])
        r['original'] = was.get(r['material'], {}).get(r['param'])

    def order(r):
        glass = 0 if r['material'] == 'window' else 1 if r['param'] in ('GlassColor', 'GlassMaxOpacity', 'DarkValues', 'LightValues') else 2
        return glass, r['material'], r['param']
    rows.sort(key=order)
    return rows
