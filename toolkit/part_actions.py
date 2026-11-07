"""Position and merge edits that keep FH1's existing part/upgrade identities."""
import numpy as np

import carbin_geom as cg


def section_at(car, index):
    if type(index) is not int or not 0 <= index < len(car.sections):
        raise ValueError('Select a valid part')
    return car.sections[index]


def set_coordinates(car, index, coordinates):
    sec = section_at(car, index)
    point = np.asarray(coordinates, dtype=np.float64)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ValueError('Coordinates must contain three finite numbers')
    delta = point - np.asarray(sec.offset)
    cg.transform_section(car, index, cg.trs_matrix(translate=delta), move_pivot=True)
    # Empty slots still have a meaningful pivot, although transform_section skips them.
    sec.offset = point.tolist()
    if hasattr(sec, 'fraw'):
        del sec.fraw


def merge_sections(car, source, target):
    src = section_at(car, source)
    section_at(car, target)
    if source == target:
        raise ValueError('Choose a different target part')
    pool = cg.active_pool(src)
    if not pool.count or not src.subs:
        raise ValueError('The source part has no geometry to merge')
    decoded = cg.decode_pool(src, pool)
    pieces = []
    for sub in src.subs:
        tris = cg.sub_triangles(sub, pool.count)
        if len(tris):
            pieces.append(cg.Piece(sub.name, sub.lod, decoded.pos, decoded.nrm,
                                   cg.final_uv(decoded.uv0, sub.uvxf[:4]),
                                   cg.final_uv(decoded.uv1, sub.uvxf[4:8]), tris,
                                   swatch=False))
    if not pieces:
        raise ValueError('The source part has no triangles to merge')
    cg.append_pieces(car, target, pieces, cg.SHModel(car))
    # Keep the now-empty source slot: deleting it would renumber header references.
    cg.remove_subs(car, source, range(len(src.subs)))
