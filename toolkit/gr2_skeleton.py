"""gr2_skeleton.py - read and edit the Granny skeleton and animation files of a Forza Horizon 1 car (`car_skeleton_LOD05.gr2`, `car_animations_LOD05.gr2`).

Only cars with moving parts have them (pop-up headlights, vents, flaps). Units are 3ds Max units, 20 per metre; axes: max = (game x * 20, -game z * 20, game y * 20).
All edits are in place and keep the file size (gr2.Gr2.build() writes the sections back uncompressed, which the game loads).

    f = gr2.read(data)
    bones(f)                                  # [{index, name, parent, t, q, world: [x, y, z]}]
    set_bone(f, 1, t=[-10, 10, -14], rotate_deg=[0, 0, 15])   # moves the bone, recomputes its and its children's InverseWorldTransform
    animations(f)                             # [{name, duration, tracks: [{name, position, orientation, scale}]}]
    scale_time(f, 1.5)                        # makes every animation 1.5 times as long (slower)
    new_bytes = f.build()
"""
from __future__ import annotations

import math
import struct

import numpy as np

import gr2
import gr2_curves


def _q2m(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
                     [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
                     [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]])


def _m2q(m: np.ndarray):
    """Quaternion (x, y, z, w) whose _q2m is m (row-vector convention)."""
    r = np.asarray(m).T
    t = r[0, 0] + r[1, 1] + r[2, 2]
    if t > 0:
        s = math.sqrt(t + 1) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    n = math.sqrt(sum(v * v for v in q))
    return [v / n for v in q]


def _euler_m(deg) -> np.ndarray:
    """Rotation matrix (row-vector convention) for rotations about x, then y, then z by `deg` degrees."""
    rx, ry, rz = (math.radians(v) for v in deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    X = np.array([[1, 0, 0], [0, cx, sx], [0, -sx, cx]])
    Y = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]])
    Z = np.array([[cz, sz, 0], [-sz, cz, 0], [0, 0, 1]])
    return X @ Y @ Z


def _local(t) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = np.array(t["ss"]).reshape(3, 3) @ _q2m(t["q"])
    M[3, :3] = t["t"]
    return M


def skeleton_of(f: gr2.Gr2, index: int = 0):
    root = f.root()
    sks = root["Skeletons"][1]
    if not sks:
        raise ValueError("this Granny file has no skeleton")
    return sks[index]


def _worlds(bones) -> list[np.ndarray]:
    out = []
    for b in bones:
        par = b["ParentIndex"][1]
        M = _local(b["Transform"][1])
        out.append(M @ out[par] if par >= 0 else M)
    return out


def bones(f: gr2.Gr2) -> list[dict]:
    """The skeleton's bones: name, parent index, local translation / rotation (x, y, z, w) and world position (max units)."""
    bs = skeleton_of(f)["Bones"][1]
    W = _worlds(bs)
    out = []
    for i, b in enumerate(bs):
        t = b["Transform"][1]
        out.append({"index": i, "name": b["Name"][1], "parent": b["ParentIndex"][1], "t": [float(v) for v in t["t"]], "q": [float(v) for v in t["q"]],
                    "world": [float(v) for v in W[i][3, :3]]})
    return out


def set_bone(f: gr2.Gr2, index: int, t=None, rotate_deg=None) -> None:
    """Set a bone's local translation `t` (max units) and/or turn it by `rotate_deg` (x, y, z degrees, in the bone's own frame), then rewrite the InverseWorldTransform of the bone and all its descendants."""
    bs = skeleton_of(f)["Bones"][1]
    if not 0 <= index < len(bs):
        raise IndexError("no such bone")
    if t is None and not (rotate_deg is not None and any(rotate_deg)):
        return
    at, tr = bs[index]["Transform"]
    if t is not None:
        struct.pack_into("<3f", f.data, at + 4, *[float(v) for v in t])
    if rotate_deg is not None and any(rotate_deg):
        R = _euler_m(rotate_deg) @ _q2m(tr["q"])
        struct.pack_into("<4f", f.data, at + 16, *_m2q(R))
    bs = skeleton_of(f)["Bones"][1]                          # re-read the edited values
    W = _worlds(bs)
    kids = {index}
    for i, b in enumerate(bs):                               # parents come before their children
        if b["ParentIndex"][1] in kids:
            kids.add(i)
    for i in sorted(kids):
        inv = np.linalg.inv(W[i])
        struct.pack_into("<16f", f.data, bs[i]["InverseWorldTransform"][0], *inv.reshape(-1).astype(np.float32))


def animations(f: gr2.Gr2) -> list[dict]:
    """Every animation: name, duration (s), time step and its transform tracks with the first and last position / orientation value."""
    out = []
    for an in f.root()["Animations"][1]:
        tracks = []
        for tg in an["TrackGroups"][1]:
            for tr in tg["TransformTracks"][1]:
                row = {"group": tg["Name"][1], "name": tr["Name"][1]}
                for key, label in (("PositionCurve", "position"), ("OrientationCurve", "orientation"), ("ScaleShearCurve", "scale")):
                    try:
                        knots, ctrl = gr2_curves.decode(tr[key][1]["CurveData"][1])
                        row[label] = {"keys": len(knots), "t0": knots[0], "t1": knots[-1],
                                      "first": [round(float(v), 4) for v in ctrl[0]] if ctrl and ctrl[0] is not None else None,
                                      "last": [round(float(v), 4) for v in ctrl[-1]] if ctrl and ctrl[-1] is not None else None}
                    except Exception as e:  # noqa: BLE001
                        row[label] = {"error": str(e)[:60]}
                tracks.append(row)
        out.append({"name": str(an["Name"][1])[-60:], "duration": float(an["Duration"][1]), "time_step": float(an["TimeStep"][1]), "tracks": tracks})
    return out


def scale_time(f: gr2.Gr2, factor: float) -> int:
    """Stretch every animation in time by `factor` (> 1 = slower): the durations and every curve's knot scale. Returns how many values were changed."""
    if factor <= 0:
        raise ValueError("factor must be above 0")
    done: set[int] = set()
    n = 0
    for an in f.root()["Animations"][1]:
        at, _ = an["Duration"]
        if at not in done:
            struct.pack_into("<f", f.data, at, struct.unpack_from("<f", f.data, at)[0] * factor); done.add(at); n += 1
        for tg in an["TrackGroups"][1]:
            for tr in tg["TransformTracks"][1]:
                for key in ("PositionCurve", "OrientationCurve", "ScaleShearCurve"):
                    cd = tr[key][1]["CurveData"][1]
                    if "OneOverKnotScale" in cd:                           # formats 8 and 9: knot time = stored / OneOverKnotScale
                        o = cd["OneOverKnotScale"][0]
                        if o not in done:
                            struct.pack_into("<f", f.data, o, struct.unpack_from("<f", f.data, o)[0] / factor); done.add(o); n += 1
                    if "OneOverKnotScaleTrunc" in cd:                      # formats 16-18: the high 16 bits of that float
                        o = cd["OneOverKnotScaleTrunc"][0]
                        if o not in done:
                            old = struct.unpack("<f", struct.pack("<I", struct.unpack_from("<H", f.data, o)[0] << 16))[0]
                            new = struct.unpack("<I", struct.pack("<f", old / factor))[0] >> 16
                            struct.pack_into("<H", f.data, o, new); done.add(o); n += 1
    return n
