import os
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolkit'))
import modelbin  # noqa: E402

SAMPLE = Path(os.environ.get('FH1_MODELBIN_SAMPLE', Path.home() / 'Downloads' / 'chute.modelbin'))


def test_rejects_files_without_the_grub_header():
    with pytest.raises(modelbin.ModelbinError):
        modelbin.load(b'not a model file at all, just text')


def test_truncated_file_is_a_clear_error_not_a_crash():
    header = struct.pack('<4sBBHIII', b'burG', 1, 1, 0, 0x14 + 0x18 * 3, 0x200, 3)    # claims 3 tags, holds none
    with pytest.raises(modelbin.ModelbinError):
        modelbin.load(header)


def test_lod_must_be_known():
    with pytest.raises(modelbin.ModelbinError):
        modelbin.load(b'burG' + bytes(64), lod=9)


def test_fh1_conversion_mirrors_z_and_keeps_triangles_facing_out():
    mesh = modelbin.Mesh('p', 'n', 'steel', 2, 1,
                         np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
                         np.array([[0, 0, 1]] * 3, np.float32), np.zeros((3, 2), np.float32),
                         np.array([[0, 1, 2]], np.uint32))
    out = modelbin.to_fh1(mesh)
    assert out.positions[:, 2].tolist() == [0, 0, 0] and out.normals[:, 2].tolist() == [-1, -1, -1]
    a, b, c = out.positions[out.indices[0]].astype(float)
    geometric = np.cross(b - a, c - a)
    assert geometric @ out.normals[0] > 0          # the winding was flipped together with the mirror, so the face still points along its normal


@pytest.mark.skipif(not SAMPLE.is_file(), reason='sample .modelbin not available (set FH1_MODELBIN_SAMPLE)')
def test_sample_modelbin_converts_to_obj_with_consistent_counts():
    text = modelbin.to_obj(SAMPLE.read_bytes(), 0)
    meshes = modelbin.load(SAMPLE.read_bytes(), 0)
    assert text.count('\no ') == len(meshes)
    assert text.count('\nf ') == sum(len(m.indices) for m in meshes)
    for m in meshes:
        assert np.isfinite(m.positions).all() and len(m.indices) and int(m.indices.max()) < len(m.positions)
