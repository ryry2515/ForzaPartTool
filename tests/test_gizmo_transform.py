import numpy as np
import pytest

from test_editor_api import editor, open_alfa, server
import carbin_geom as cg
import fh1_carbin as fc


def test_gizmo_matrix_updates_both_files_in_world_space_and_is_undoable(editor):
    open_alfa(editor)
    index = next(i for i, s in enumerate(server.S.carbin.sections) if s.name.lower() == 'body')
    original = fc.build(server.S.carbin)
    section = server.S.carbin.sections[index]
    positions = cg.decode_pool(section, cg.active_pool(section)).pos
    matrix = cg.trs_matrix([0.2, 0.3, -0.1], [20, 35, -15], [1.2, 0.8, 1.1], section.offset)
    _, relative, _ = server.origin_car()
    other = relative[:-7] + '_lod0.carbin'
    other_before = fc.parse(server.S.lib.read('game', server.S.origin['archive'], other))
    other_section = next(s for s in other_before.sections if s.name.lower() == 'body')
    other_positions = cg.decode_pool(other_section, cg.active_pool(other_section)).pos
    editor('/api/op', {'op': 'transform', 'sections': [index], 'matrix': matrix.T.ravel().tolist(),
                       'move_pivot': True, 'both_models': True})
    for model, before in [(server.S.carbin, positions), (fc.parse(server.S.staged[other]), other_positions)]:
        section = next(s for s in model.sections if s.name.lower() == 'body')
        expected = before @ matrix[:3, :3].T + matrix[:3, 3]
        actual = cg.decode_pool(section, cg.active_pool(section)).pos
        np.testing.assert_allclose(actual, expected, atol=0.001)
        assert not cg.validate(fc.parse(fc.build(model)))
    assert server.S.info()['undo'] == 1
    after = dict(server.S.staged)
    editor('/api/undo', {})
    assert fc.build(server.S.carbin) == original and not server.S.staged
    editor('/api/redo', {})
    assert server.S.staged == after


@pytest.mark.parametrize('bad', [[1] * 16, [float('nan')] * 16, [1, 2]])
def test_invalid_gizmo_matrix_cannot_change_the_model(editor, bad):
    open_alfa(editor)
    before = fc.build(server.S.carbin)
    with pytest.raises(ValueError, match='matrix'):
        server.op_transform({'sections': [0], 'matrix': bad, 'move_pivot': True})
    assert fc.build(server.S.carbin) == before
