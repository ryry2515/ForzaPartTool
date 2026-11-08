import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'toolkit'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import modelbin as reader
from test_desktop_settings import server
from test_part_transfer import car_blob          # noqa: F401  (fixture)
from test_project_save import session            # noqa: F401  (fixture)


def test_obj_and_mtl_cover_every_material(session):          # noqa: F811
    files = server.model_obj_files('mycar')
    assert set(files) == {'mycar.obj', 'mycar.mtl'}
    obj, mtl = files['mycar.obj'], files['mycar.mtl']
    assert obj.startswith('# Exported by ForzaPartTool\nmtllib mycar.mtl\n')
    used = {line[7:].strip() for line in obj.splitlines() if line.startswith('usemtl ')}
    defined = {line[7:].strip() for line in mtl.splitlines() if line.startswith('newmtl ')}
    assert used and used == defined
    assert sum(1 for line in obj.splitlines() if line.startswith('f ')) > 100


def test_modelbin_files_read_back_and_match_the_obj_geometry(session):          # noqa: F811
    files, failed = server.model_modelbin_files()
    assert files and not failed
    assert all(name.endswith('.modelbin') and '/' not in name and '\\' not in name for name in files)
    triangles = 0
    for data in files.values():
        for mesh in reader.load(data, 1):
            assert np.isfinite(mesh.positions).all()
            triangles += len(mesh.indices)
    assert triangles > 100
    # the model bins hold the same car as the open model: the body part is about as wide and long as the car
    body = [m for m in reader.load(files['body.modelbin'], 1)]
    pts = np.vstack([m.positions for m in body])
    span = pts.max(0) - pts.min(0)
    assert 1.4 < span[0] < 2.6 and 3.0 < span[2] < 6.5
