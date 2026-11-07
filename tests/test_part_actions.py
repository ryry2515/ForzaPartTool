import numpy as np
import pytest

from test_part_transfer import car_blob
import fh1_carbin as fc
import carbin_geom as cg


def test_paste_coordinates_moves_geometry_and_pivot(car_blob):
    import part_actions as actions
    car = fc.parse(car_blob)
    sec = car.sections[0]
    before = cg.decode_pool(sec, cg.active_pool(sec)).pos.copy()
    target = np.asarray(sec.offset) + [0.3, -0.2, 0.5]
    actions.set_coordinates(car, 0, target)
    np.testing.assert_allclose(sec.offset, target, atol=1e-6)
    actual = cg.decode_pool(sec, cg.active_pool(sec)).pos
    np.testing.assert_allclose(actual, before + [0.3, -0.2, 0.5], atol=0.001)
    assert not cg.validate(fc.parse(fc.build(car)))


def test_merge_into_empty_slot_keeps_identity_and_position(car_blob):
    import part_actions as actions
    car = fc.parse(car_blob)
    source, target = 0, 1
    names = [s.name for s in car.sections]
    expected = cg.decode_pool(car.sections[source], cg.active_pool(car.sections[source])).pos.copy()
    cg.remove_subs(car, target, range(len(car.sections[target].subs)))
    actions.merge_sections(car, source, target)
    rebuilt = fc.parse(fc.build(car))
    assert [s.name for s in rebuilt.sections] == names
    assert not rebuilt.sections[source].subs
    assert rebuilt.sections[target].subs
    triangles = [cg.sub_triangles(s, cg.active_pool(rebuilt.sections[target]).count) for s in rebuilt.sections[target].subs]
    used = np.unique(np.concatenate(triangles))
    actual = cg.decode_pool(rebuilt.sections[target], cg.active_pool(rebuilt.sections[target])).pos[used]
    np.testing.assert_allclose(actual.min(0), expected.min(0), atol=0.002)
    np.testing.assert_allclose(actual.max(0), expected.max(0), atol=0.002)
    assert not cg.validate(rebuilt)
    assert any(s == target for s, _ in cg.drawn_at(rebuilt))


def test_invalid_actions_leave_model_unchanged(car_blob):
    import part_actions as actions
    car = fc.parse(car_blob)
    before = fc.build(car)
    for point in ([1, 2], [0, float('nan'), 0]):
        with pytest.raises(ValueError):
            actions.set_coordinates(car, 0, point)
    with pytest.raises(ValueError):
        actions.merge_sections(car, 0, 0)
    with pytest.raises(ValueError):
        actions.merge_sections(car, 0, -1)
    assert fc.build(car) == before


def test_merge_preserves_small_textured_uv_regions(car_blob):
    import part_actions as actions
    car = fc.parse(car_blob)
    sub = car.sections[0].subs[0]
    sub.uvxf[:4] = [0.2, 0.005, 0.3, 0.005]
    sub.uvxf[4:8] = [0.1, 0.25, 0.2, 0.3]
    pool = cg.active_pool(car.sections[0])
    decoded = cg.decode_pool(car.sections[0], pool)
    used = np.unique(cg.sub_triangles(sub, pool.count))
    expected = [cg.final_uv(uv, sub.uvxf[start:start + 4])[used] for uv, start in [(decoded.uv0, 0), (decoded.uv1, 4)]]
    cg.remove_subs(car, 1, range(len(car.sections[1].subs)))
    actions.merge_sections(car, 0, 1)
    target = car.sections[1]
    decoded = cg.decode_pool(target, cg.active_pool(target))
    result = next(s for s in target.subs if s.name == sub.name and s.lod == sub.lod)
    used = np.unique(cg.sub_triangles(result, len(decoded.pos)))
    for uv, start, original in [(decoded.uv0, 0, expected[0]), (decoded.uv1, 4, expected[1])]:
        actual = cg.final_uv(uv, result.uvxf[start:start + 4])[used]
        np.testing.assert_allclose(actual.min(0), original.min(0), atol=0.0001)
        np.testing.assert_allclose(actual.max(0), original.max(0), atol=0.0001)
