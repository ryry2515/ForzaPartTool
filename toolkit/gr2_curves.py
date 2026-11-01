"""Decoders for the Granny animation curve formats used by FH1's car animations."""
import math

# Granny's quantised quaternion tables (D4nK16uC15u / D4nK8uC7u)
_SCALE = [1.4142135, 0.70710677, 0.35355338, 0.35355338, 0.35355338, 0.17677669, 0.17677669, 0.17677669,
          -1.4142135, -0.70710677, -0.35355338, -0.35355338, -0.35355338, -0.17677669, -0.17677669, -0.17677669]
_OFFSET = [-0.70710677, -0.35355338, -0.53033006, -0.17677669, 0.17677669, -0.17677669, -0.088388346, 0.0,
           0.70710677, 0.35355338, 0.53033006, 0.17677669, -0.17677669, 0.17677669, 0.088388346, -0.0]


def _quat(a, b, c, scales, offsets, top):
    s1 = ((b & top) >> (top.bit_length() - 2)) | ((c & top) >> (top.bit_length() - 1))
    s2, s3, s4 = (s1 + 1) & 3, (s1 + 2) & 3, (s1 + 3) & 3
    m = top - 1
    A = (a & m) * scales[s2] + offsets[s2]
    B = (b & m) * scales[s3] + offsets[s3]
    C = (c & m) * scales[s4] + offsets[s4]
    D = math.sqrt(max(0.0, 1 - (A * A + B * B + C * C)))
    if a & top:
        D = -D
    f = [0.0] * 4
    f[s2], f[s3], f[s4], f[s1] = A, B, C, D
    return tuple(f)                                          # x, y, z, w


def decode(curve: dict):
    """curve: the walked CurveData dict -> (knots, controls)"""
    hdr_name = next(k for k in curve if k.startswith("CurveDataHeader_"))
    fmt = curve[hdr_name][1]["Format"][1]
    v = {k: x[1] for k, x in curve.items()}
    if fmt in (8, 9):
        kc = [list(e.values())[0][1] for e in v["KnotsControls"]]
        n = len(kc) // 4
        top, div = (0x8000, 32767.0) if fmt == 8 else (0x80, 127.0)
        ent = v["ScaleOffsetTableEntries"]
        scales = [_SCALE[(ent >> (i * 4)) & 15] / div for i in range(4)]
        offsets = [_OFFSET[(ent >> (i * 4)) & 15] for i in range(4)]
        knots = [k / v["OneOverKnotScale"] for k in kc[:n]]
        ctrl = [_quat(*kc[n + i * 3:n + i * 3 + 3], scales, offsets, top) for i in range(n)]
        return knots, ctrl
    if fmt in (16, 17, 18):                                    # D3I1K{32f,16u,8u}: one value per knot, 3 dims
        kc = [list(e.values())[0][1] for e in v["KnotsControls"]]
        n = len(kc) // 2
        import struct
        scale = struct.unpack("<f", struct.pack("<I", v["OneOverKnotScaleTrunc"] << 16))[0]
        knots = [k / scale for k in kc[:n]]
        ctrl = [tuple(v["ControlScales"][d] * c + v["ControlOffsets"][d] for d in range(3)) for c in kc[n:]]
        return knots, ctrl
    if fmt == 4:
        return [0.0], [tuple(v["Controls"])]
    if fmt == 5:
        return [0.0], [tuple(v["Controls"])]
    if fmt == 2:
        return [0.0], [None]
    raise NotImplementedError(f"curve format {fmt}")
