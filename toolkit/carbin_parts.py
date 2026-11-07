"""Copy geometry between FH1 models without changing the active or donor car."""
from __future__ import annotations

import copy
import secrets

import numpy as np

import carbin_geom as cg
import fh1_carbin as fc


class PartClipboard:
    def __init__(self):
        self._section = None
        self._source = ''

    def copy(self, car: fc.Carbin, section: int, source: str = '') -> dict:
        if not isinstance(section, int) or not 0 <= section < len(car.sections):
            raise ValueError('Select a valid part to copy')
        sec = car.sections[section]
        pool = cg.active_pool(sec)
        if not pool.count or not any(len(cg.sub_triangles(s, pool.count)) for s in sec.subs):
            raise ValueError('This part has no geometry to copy')
        self._section = copy.deepcopy(sec)
        self._source = source
        return self.info()

    def info(self) -> dict | None:
        if self._section is None:
            return None
        return {'name': self._section.name, 'source': self._source,
                'materials': sorted({s.name for s in self._section.subs}),
                'lods': sorted({s.lod for s in self._section.subs}),
                'vertices': cg.active_pool(self._section).count}

    def paste(self, car: fc.Carbin, material_map: dict | None = None, name: str = '',
              translate=(0, 0, 0)) -> int:
        if self._section is None:
            raise ValueError('Copy a part first')
        if not car.sections or not any(s.subs for s in car.sections):
            raise ValueError('The destination needs an existing model part')
        if len(car.sections) >= 65535:
            raise ValueError('The model has too many parts')
        src = self._section
        available = {s.name for sec in car.sections for s in sec.subs}
        mapping = {s.name: (material_map or {}).get(s.name, s.name) for s in src.subs}
        missing = sorted(set(mapping.values()) - available)
        if missing:
            raise ValueError('Map these materials to a destination material: ' + ', '.join(missing))
        base = (name.strip() or src.name + '_copy')[:240]
        delta = np.asarray(translate, dtype=np.float64)
        if delta.shape != (3,) or not np.isfinite(delta).all():
            raise ValueError('Part position must contain three finite numbers')
        pool = cg.active_pool(src)
        decoded = cg.decode_pool(src, pool)
        swatches = cg.donor_swatches(car)
        target_lods = sorted(set(cg.summary(car)['array_lods'])) if cg.has_body_header(car) else sorted({s.lod for sec in car.sections for s in sec.subs})
        by_lod = {}
        for sub in src.subs:
            tris = cg.sub_triangles(sub, pool.count)
            if len(tris):
                by_lod.setdefault(sub.lod, []).append((sub, tris))
        pieces = []
        for lod in target_lods:
            nearest = min(by_lod, key=lambda value: (abs(value - lod), value))
            for sub, tris in by_lod[nearest]:
                material = mapping[sub.name]
                # Destination swatches avoid accidentally reading unrelated donor atlas regions.
                uv = np.tile(swatches.get(material, (0.5, 0.5)), (pool.count, 1))
                pieces.append(cg.Piece(material, lod, decoded.pos + delta, decoded.nrm,
                                       uv, cg.final_uv(decoded.uv1, sub.uvxf[4:8]), tris, swatch=True))
        return add_section(car, pieces, base, np.asarray(src.offset) + delta)


def valid_part_name(name: str) -> bool:
    """What the carbin reader accepts as a part name: 1-240 printable ASCII characters and no space. Anything else makes the saved file unreadable."""
    return 1 <= len(name) <= 240 and all(32 < ord(c) < 127 for c in name)


def clean_part_name(wanted: str) -> str:
    """`wanted` with every character the file cannot hold (spaces, accents, symbols) replaced by an underscore."""
    cleaned = ''.join(c if 32 < ord(c) < 127 else '_' for c in str(wanted or '').strip())[:200].strip('_')
    return cleaned or 'part'


def unique_name(car: fc.Carbin, wanted: str, taken=()) -> str:
    """A part name nobody uses yet in `car` (or in `taken`, the names of a paired model): `wanted`, else wanted_2, wanted_3 ..."""
    used = {s.name.lower() for s in car.sections} | {str(n).lower() for n in taken}
    base = clean_part_name(wanted)
    unique, suffix = base, 2
    while unique.lower() in used:
        unique = f'{base}_{suffix}'
        suffix += 1
    return unique


def add_section(car: fc.Carbin, pieces, name: str, offset, template_index: int | None = None, taken=()) -> int:
    """Append a NEW part (its own section, own name, own pivot) to `car` made of `pieces` and put it in the game's draw lists. -> the new part's index.
    A part of its own stays selectable, movable and removable later, which merging geometry into an existing part is not."""
    if not car.sections or not any(s.subs for s in car.sections):
        raise ValueError('The destination needs an existing model part')
    if len(car.sections) >= 65535:
        raise ValueError('The model has too many parts')
    if not pieces:
        raise ValueError('There is no geometry to add')
    pivot = np.asarray(offset, dtype=np.float64)
    if pivot.shape != (3,) or not np.isfinite(pivot).all():
        raise ValueError('Part position must contain three finite numbers')
    unique = unique_name(car, name, taken)
    sh = cg.SHModel(car)
    ref = car.sections[template_index] if template_index is not None and 0 <= template_index < len(car.sections) and car.sections[template_index].subs else next(sec for sec in car.sections if sec.subs)
    template = copy.deepcopy(ref)
    template.name = unique
    template.offset = list(pivot)
    candidate = fc.Carbin(car.header, list(car.sections) + [template], car.footer, car.type_id)
    index = len(car.sections)
    cg.rebuild_section(candidate, index, pieces, sh, seed=secrets.randbits(64), draw=True)
    keys = {p.bone[1] for sec in car.sections for p in (sec.lod, sec.lod0) if p.count and len(p.bone) > 1}
    for p in (template.lod, template.lod0):
        if p.count:
            key = secrets.randbelow(0xFFFFFFFE) + 1
            while key in keys:
                key = secrets.randbelow(0xFFFFFFFE) + 1
            p.bone = [1, key]
            keys.add(key)
    car.sections.append(template)
    car.header = candidate.header
    return index
