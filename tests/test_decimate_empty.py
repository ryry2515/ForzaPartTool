"""Regression: decimating a thin or flat part used to return a piece with no triangles, and the encoder then failed with
"zero-size array to reduction operation maximum which has no identity" (add_model failed). Synthetic shapes only; no game data."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'toolkit'))
import carbin_geom as cg


def thin_tube(rings=30, sides=40, radius=0.02, length=1.0):
    th = np.linspace(0, 2 * np.pi, sides + 1)[:-1]
    ring = np.stack([np.cos(th) * radius, np.zeros(sides), np.sin(th) * radius], 1)
    pos = np.vstack([ring + [0, y, 0] for y in np.linspace(0, length, rings)])
    tris = []
    for k in range(rings - 1):
        for i in range(sides):
            a, b = k * sides + i, k * sides + (i + 1) % sides
            tris += [[a, b, a + sides], [b, b + sides, a + sides]]
    nrm = np.tile([0.0, 0.0, 1.0], (len(pos), 1))
    return cg.Piece("m", 1, pos, nrm, pos[:, :2].copy(), pos[:, :2].copy(), np.array(tris), False)


def test_decimate_never_returns_an_empty_piece():
    p = thin_tube()
    for target in (12, 40, 120):
        q = cg.decimate(p, target)
        assert len(q.tris) > 0 and len(q.pos) > 0
        assert int(q.tris.max()) < len(q.pos)


def test_lod_pieces_has_no_empty_piece():
    out = cg.lod_pieces([thin_tube()], [1, 2, 3, 4, 5], {1: 1.0, 2: 0.5, 3: 0.3, 4: 0.18, 5: 0.08})
    assert len(out) == 5 and all(len(q.tris) for q in out)


def test_encoder_skips_an_empty_piece():
    empty = cg.Piece("m", 1, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 2)), np.zeros((0, 2)), np.zeros((0, 3), np.int64), False)
    vbytes, subs, count = cg._encode_groups([empty], np.zeros(3), np.ones(3), lambda n: None, 0, None)
    assert vbytes == [] and subs == [] and count == 0
