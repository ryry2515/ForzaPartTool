"""Forza Horizon 1 carbin editor: a native desktop window with an embedded three.js viewer.

    python toolkit/carbin_editor/server.py [FILE.carbin] [--game GAME_FOLDER] [--port 8765] [--no-browser]

Normal mode opens a dedicated desktop window. --headless runs only the local API. Everything stays on your computer: the server listens on 127.0.0.1 only.
Back up a file before you overwrite it; "Save as" never overwrites without making a .bak copy first.
See toolkit/carbin_editor/README.md for what each tool does and what is untested in the game.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.cookies
import json
import os
import re
import secrets
import shutil
import struct
import sys
import threading
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

FROZEN = bool(getattr(sys, "frozen", False))                                   # running as the packaged program (PyInstaller)
HERE = Path(getattr(sys, "_MEIPASS", "") or Path(__file__).resolve().parent)       # where index.html, app.js and vendor/ are
sys.path.insert(0, str(HERE.parent))
import carbin_geom as cg  # noqa: E402
import carbin_library as cl  # noqa: E402
import atlas_paint as ap  # noqa: E402
import clone_car as cc  # noqa: E402
import dlc_builder as dlcb  # noqa: E402
import fh1_sources as fsrc  # noqa: E402
import lsb2  # noqa: E402
import physdef  # noqa: E402
import gr2  # noqa: E402
import gr2_skeleton as gr2s  # noqa: E402
import xds  # noqa: E402
import xlzx  # noqa: E402
import fh1_carbin as fc  # noqa: E402
import carbin_parts
from carbin_parts import PartClipboard
import part_actions

MAX_UNDO = 20
MAX_BODY = 512 * 1024 * 1024                 # largest request body accepted (a model with textures is a few MB)
TOKEN = os.environ.get("FH1_EDITOR_TOKEN") or secrets.token_urlsafe(24)       # secret of this run: only the browser that opened the printed address (or a script that sets the variable) can use the API
COOKIE = "fh1ed"


def safe_name(s: str) -> str:
    """A file name that is safe inside a Content-Disposition header."""
    return re.sub(r"[^A-Za-z0-9._ -]", "_", s)[:120] or "file"


class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.carbin: fc.Carbin | None = None
        self.name = ""
        self.path: Path | None = None
        self.undo: list[bytes] = []
        self.redo: list[bytes] = []
        self.original = b""
        self.staged: dict[str, bytes] = {}       # edited files waiting to go into a new car: file name relative to the car folder -> bytes
        self.body_over: dict = {}                # Data_CarBody values (wheelbase, track, ride height, bounding box) for the next new car
        self.origin: dict | None = None          # where the open file came from: {source, archive, member}
        self.lib: cl.Library | None = None
        self.cfg = {"game": "", "dlc": []}
        self.setup_error = ''
        self.path_notice = ''
        self.clipboard = PartClipboard()
        self.last_pasted = None
        self.baseline = set()
        self.export_dirs = []
        self.output_dir = ''                   # the last folder an export went to (ZIP or DLC package): offered again next time

    def load(self, data: bytes, name: str, path: Path | None = None, origin: dict | None = None):
        c = fc.parse(data)
        key = None if not origin else (origin['source'], origin['archive'] if origin['source'] == 'game' else re.sub(r'(?i)^(media/cars/[^/]+)/.*', r'\1', origin['member']))
        if key != getattr(self, '_stage_key', None):
            self.staged.clear()
            self.body_over.clear()
        self._stage_key = key
        self.carbin, self.name, self.path, self.origin = c, name, path, origin
        self.undo, self.redo, self.original = [], [], data
        self.baseline = problem_keys(data)

    def capture(self):
        return (fc.build(self.carbin), dict(self.staged), self.last_pasted)

    def unload(self):
        self.carbin, self.name, self.path, self.origin = None, '', None, None
        self.original, self.baseline, self._stage_key = b'', set(), None
        self.undo.clear()
        self.redo.clear()
        self.staged.clear()
        self.body_over.clear()
        self.last_pasted = None

    def restore(self, state):
        raw, staged, last_pasted = state
        self.carbin, self.staged, self.last_pasted = fc.parse(raw), dict(staged), last_pasted

    def snapshot(self):
        self.undo.append(self.capture())
        del self.undo[:-MAX_UNDO]
        self.redo.clear()

    def info(self) -> dict:
        s = cg.summary(self.carbin)
        s.update({"name": self.name, "path": str(self.path) if self.path else "", "undo": len(self.undo), "redo": len(self.redo),
                  "problems": new_problems(self.carbin, self.baseline), "size": len(fc.build(self.carbin)), "origin": self.origin,
                  "clipboard": self.clipboard.info(), "last_pasted": self.last_pasted})
        return s


S = Session()


def need_loaded():
    if S.carbin is None:
        raise ValueError("no carbin is open")


def problem_keys(raw: bytes) -> set:
    """The consistency problems a file ALREADY has (numbers removed so an edit that moves a value does not make it look new).
    About a seventh of the stock files have some (a flat part with a zero extent, two buffers sharing a key); those must not block saving."""
    try:
        return {re.sub(r"-?[0-9][0-9.e+\-]*", "#", p) for p in cg.validate(fc.parse(raw))}
    except Exception:  # noqa: BLE001
        return set()


def new_problems(model, baseline: set) -> list:
    """Problems of an edited model that the original file did not already have."""
    return [p for p in cg.validate(model) if re.sub(r"-?[0-9][0-9.e+\-]*", "#", p) not in baseline]


def original_problem_keys(relative: str) -> set:
    """Baseline for a file of the open car: the member as it is in the car's archive, before any edit or staging."""
    try:
        archive = origin_zip()
        media, _, _ = origin_car()
        member = find_member(archive, f"media/cars/{media}/{relative}" if S.origin["source"] != "game" else relative)
        return problem_keys(xlzx.read_member(archive, member))
    except Exception:  # noqa: BLE001
        return set()


COCKPIT_PARTS = re.compile(r"^(interior|seat[lr]?|steering_wheel|cagerace|tach_|speed_|fuel_|boost_)", re.I)
_cockpit_cache: dict = {}


def cockpit_carbin():
    """The sharp-interior model of the open car (<car>_cockpit.carbin), or None. Used only to draw the preview of the driving model."""
    o = S.origin
    if not (o and S.lib) or o["member"].lower().endswith("_cockpit.carbin"):
        return None
    if o["source"] == "game":
        car = Path(o["archive"]).stem
    else:
        found = re.match(r"(?i)media/cars/([^/]+)/", o["member"])
        if not found:
            return None
        car = found.group(1)
    want = f"{car}_cockpit.carbin".lower()
    member = next((m for m in S.lib.members(o["source"], car) if m["member"].split("/")[-1].lower() == want), None)
    if member is None:
        return None
    relative = re.sub(rf"(?i)^media/cars/{re.escape(car)}/", "", member["member"])
    staged = next((raw for k, raw in S.staged.items() if k.lower() == relative.lower()), None)
    if staged is not None:                       # an imported or edited cockpit model that is not saved yet
        return fc.parse(staged)
    key = (o["source"], member["archive"], member["member"])
    if key not in _cockpit_cache:
        _cockpit_cache.clear()
        _cockpit_cache[key] = fc.parse(S.lib.read(o["source"], member["archive"], member["member"]))
    return _cockpit_cache[key]


def cockpit_info() -> dict:
    need_loaded()
    try:
        car = cockpit_carbin()
    except Exception:  # noqa: BLE001
        car = None
    if car is None:
        return {"has": False}
    mine = {s.name for s in S.carbin.sections}
    parts = [{"index": i, "name": s.name} for i, s in enumerate(car.sections) if COCKPIT_PARTS.match(s.name) and s.subs]
    return {"has": bool(parts), "sections": parts, "replaces": sorted(p["name"] for p in parts if p["name"] in mine)}


def cockpit_geometry(si: int) -> bytes:
    need_loaded()
    car = cockpit_carbin()
    if car is None or not COCKPIT_PARTS.match(car.sections[si].name):
        raise ValueError("no such cockpit part")
    return geometry_blob(si, car)


def geometry_blob(si: int, carbin=None) -> bytes:
    sec = (carbin or S.carbin).sections[si]
    pool = cg.active_pool(sec)
    d = cg.decode_pool(sec, pool)
    pos = d.pos.astype("<f4")
    nrm = d.nrm.astype("<f4")
    parts = [pos.tobytes(), nrm.tobytes()]
    subs, off = [], len(parts[0]) + len(parts[1])
    for k, sb in enumerate(sec.subs):
        tris = cg.sub_triangles(sb, pool.count).astype("<u4") if pool.count else np.zeros((0, 3), "<u4")
        b = tris.tobytes()
        # unused LOD placeholders carry a "no transform" marker (FLT_MAX, -inf) that turns every UV into NaN; WebGL must never get NaN
        uvb = np.nan_to_num(cg.final_uv(d.uv0, sb.uvxf[0:4]), nan=0.0, posinf=0.0, neginf=0.0).astype("<f4").tobytes() if pool.count else b""
        subs.append({"k": k, "material": sb.name, "lod": sb.lod, "tris": int(len(tris)), "offset": off, "swatch": cg.is_swatch(sb), "uvOffset": off + len(b)})
        parts.append(b)
        parts.append(uvb)
        off += len(b) + len(uvb)
    head = json.dumps({"index": si, "name": sec.name, "verts": int(pool.count), "subs": subs, "offset": list(sec.offset)}).encode()
    head += b" " * (-(4 + len(head)) % 4)       # the data after the head starts on a 4-byte boundary, so the browser can use it without copying
    return struct.pack("<I", len(head)) + head + b"".join(parts)


def transform_geometry(a):
    secs = a["sections"]
    if 'matrix' in a:
        values = np.asarray(a['matrix'], dtype=np.float64)
        if values.shape != (16,) or not np.isfinite(values).all():
            raise ValueError('Transform matrix must contain 16 finite numbers')
        matrix = values.reshape(4, 4).T  # Three.js column-major elements
        if not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8) or abs(np.linalg.det(matrix[:3, :3])) < 1e-10:
            raise ValueError('Transform matrix must be affine and have nonzero scale')
        for si in secs:
            part_actions.section_at(S.carbin, si)
            cg.transform_section(S.carbin, si, matrix, move_pivot=bool(a.get('move_pivot', True)))
        return
    M = cg.trs_matrix(a.get("translate", (0, 0, 0)), a.get("rotate", (0, 0, 0)), a.get("scale", (1, 1, 1)), (0, 0, 0))
    mirror = a.get("mirror")
    for si in secs:
        sec = S.carbin.sections[si]
        pivot = np.zeros(3) if a.get("pivot") == "origin" else np.asarray(sec.offset, np.float64)
        Mi = cg.trs_matrix(a.get("translate", (0, 0, 0)), a.get("rotate", (0, 0, 0)), a.get("scale", (1, 1, 1)), pivot)
        if mirror:
            ax = "xyz".index(mirror)
            F = np.eye(4)
            F[ax, ax] = -1.0
            F[ax, 3] = 2.0 * pivot[ax]
            Mi = Mi @ F
        cg.transform_section(S.carbin, si, Mi, move_pivot=bool(a.get("move_pivot", False)))


def rename_section(index, name):
    """The game finds parts by name, and the file stores it as Latin-1: an empty, duplicate or non-Latin-1 name would break saving or the paired model."""
    section = part_actions.section_at(S.carbin, index)
    name = str(name).strip()
    if not name:
        raise ValueError("A part needs a name")
    if not carbin_parts.valid_part_name(name):
        raise ValueError("A part name can use only letters, digits and symbols (no spaces or accented letters), up to 240 characters: "
                         "anything else makes the saved file unreadable. Try " + carbin_parts.clean_part_name(name))
    if any(i != index and s.name.lower() == name.lower() for i, s in enumerate(S.carbin.sections)):
        raise ValueError(f'Another part is already called "{name}". Part names must be unique.')
    section.name = name


def op_transform(a):
    return model_operation(a, transform_geometry, ['sections'], skip_missing=True)


def op_import_obj(a):
    si = a["section"]
    parsed = cg.parse_obj(a["text"], flip_v=a.get("flip_v", True))
    obj = a.get("object")
    if obj is None or obj not in parsed:
        obj = next(iter(parsed))
    by_lod = parsed[obj]
    sw = cg.donor_swatches(S.carbin)
    pieces = cg.pieces_from_obj(by_lod, int(a.get("default_lod", 1)), sw, a.get("material_map"), a.get("uv0_mode", "swatch"),
                                {int(k): int(v) for k, v in (a.get("lod_map") or {}).items()})
    if not pieces:
        raise ValueError("the OBJ has no triangles")
    cg.rebuild_section(S.carbin, si, pieces, cg.SHModel(S.carbin), seed=int(a.get("seed") or secrets.randbits(62)), draw=True if a.get("force_draw") else None)


def _arr(b64, dtype, cols):
    a = np.frombuffer(base64.b64decode(b64), dtype)
    return a.reshape(-1, cols) if len(a) else a.reshape(0, cols)


def paint_atlases(tex: dict):
    """Paste the model's image into the chosen rectangle of the car's body atlases (nodamage and damage, both sizes) and regenerate their mips.
    In place when the open file's location is writable, otherwise staged for the next new car."""
    z = origin_zip()
    o = S.origin
    src = S.lib.sources() if S.lib else []
    writable = next((x["writable"] for x in src if x["id"] == o["source"]), False)
    rgba = np.frombuffer(base64.b64decode(tex["rgba"]), np.uint8).reshape(int(tex["h"]), int(tex["w"]), 4)
    rect = tuple(float(x) for x in tex["rect"])
    media, _, _ = origin_car()
    done = []
    for name in tex.get("atlases", ["nodamage", "damage"]):
        for suffix in ("", "_LOD0"):
            low = {n.lower(): n for n in z.namelist()}
            hit = [n for n in low.values() if re.search(rf"(^|/){name}{suffix}\.xds$", n, re.I)]
            for member in hit:
                rel = re.sub(rf"(?i)^media/cars/{re.escape(media)}/", "", member)
                template = S.staged[rel] if rel in S.staged else xlzx.read_member(z, member)
                new = ap.paint(template, rgba, rect)
                if writable and not tex.get("stage_only"):
                    S.lib.write(o["source"], o["archive"], member, new)
                    z = origin_zip()
                else:
                    S.staged[rel] = new
                done.append(member)
    if not done:
        raise ValueError("this car has no nodamage / damage atlas to paint into")

def put_member(member: str, data: bytes, stage_only: bool = False) -> str:
    """Write a file of the open car's archive: in place when its location is writable (backup made), otherwise staged for the next new car. -> 'written' or 'staged'."""
    o = S.origin
    media, _, _ = origin_car()
    writable = next((x["writable"] for x in (S.lib.sources() if S.lib else []) if x["id"] == o["source"]), False)
    if writable and not stage_only:
        S.lib.write(o["source"], o["archive"], member, data)
        return "written"
    S.staged[re.sub(rf"(?i)^media/cars/{re.escape(media)}/", "", member)] = data
    return "staged"


# ---------------------------------------------------------------------------------------------- physics (physicsdefinition.bin + Physics/MAXData.xml)
def phys_member(kind: str):
    """(archive member name, bytes) of 'bin' or 'xml' for the open car; staged edits win. (None, None) if the car has none."""
    z = origin_zip()
    media, _, _ = origin_car()
    want = "physicsdefinition.bin" if kind == "bin" else "physics/maxdata.xml"
    member = next((n for n in z.namelist() if n.lower() == want or n.lower().endswith("/" + want)), None)
    if member is None:
        return None, None
    rel = re.sub(rf"(?i)^media/cars/{re.escape(media)}/", "", member)
    return member, S.staged.get(rel) or xlzx.read_member(z, member)


def mesh_bounds(c) -> tuple[list, list]:
    """Bounds of what the car shows at its lowest LOD, without wheels, calipers and rotors (they are separate files and sit in other places)."""
    pts = []
    low = min((sb.lod for s in c.sections for sb in s.subs), default=1)
    for sec in c.sections:
        if sec.name.lower().startswith(("wheel", "caliper", "rotor")):
            continue
        pool = cg.active_pool(sec)
        if not pool.count:
            continue
        d = cg.decode_pool(sec, pool)
        used = np.zeros(pool.count, bool)
        for sb in sec.subs:
            if sb.lod == low:
                t = cg.sub_triangles(sb, pool.count)
                if len(t):
                    used[np.unique(t)] = True
        if used.any():
            pts.append(d.pos[used])
    if not pts:
        return [0, 0, 0], [0, 0, 0]
    P = np.vstack(pts)
    return P.min(0).tolist(), P.max(0).tolist()


def physics_info() -> dict:
    need_loaded()
    bm, bdata = phys_member("bin")
    if bm is None:
        return {"has": False}
    pd = physdef.parse(bdata)
    xm, xdata = phys_member("xml")
    mx = physdef.read_maxdata(xdata.decode("latin1")) if xdata else None
    lo, hi = mesh_bounds(S.carbin)
    olo, ohi = mesh_bounds(fc.parse(S.original))
    body = None
    try:
        media, _, pkg = origin_car()
        donor = fsrc.Donor(media, base=S.lib.game, package=pkg) if pkg else fsrc.Donor(media, base=S.lib.game)
        sdb = fsrc.open_db(donor.db_blob)
        cur = sdb.execute("select * from Data_CarBody where Id=(select min(CarBodyID) from List_UpgradeCarBody where Ordinal=?)", (donor.row["Id"],))
        row = cur.fetchone()
        if row:
            body = dict(zip([d[0] for d in cur.description], row))
            body.update(S.body_over)
    except Exception:  # noqa: BLE001
        body = None
    return {"has": True, "spheres": [{"flags": s.flags, "x": s.x, "y": s.y, "z": s.z, "r": s.r} for s in pd.spheres],
            "hull": [v[:3] for v in pd.hull], "header": pd.header, "maxdata": mx, "bounds": physdef.bounds(pd),
            "mesh": {"lo": lo, "hi": hi, "original_lo": olo, "original_hi": ohi}, "body": body, "staged": "physicsdefinition.bin" in S.staged}


def wheel_layout() -> dict:
    """Where the game puts the four wheels. The car file holds ONE wheel model at the centre of the car; the game places and mirrors it using the
    car's database row (wheelbase, front/rear track, offset of the wheelbase centre). Used only to draw the preview. Front is -z, +x is the car's right.
    Checked against 45 base-game cars: this z offset leaves the wheel arches clear far more often than the opposite sign."""
    need_loaded()
    wheel = next((i for i, s in enumerate(S.carbin.sections) if s.name.lower() == "wheel"), None)
    if wheel is None:
        return {"has": False}
    try:
        media, _, pkg = origin_car()
        donor = fsrc.Donor(media, base=S.lib.game, package=pkg) if pkg else fsrc.Donor(media, base=S.lib.game)
        cur = fsrc.open_db(donor.db_blob).execute("select * from Data_CarBody where Id=(select min(CarBodyID) from List_UpgradeCarBody where Ordinal=?)", (donor.row["Id"],))
        row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        sec = S.carbin.sections[wheel]
        pos = cg.decode_pool(sec, cg.active_pool(sec)).pos
        lo, hi = pos.min(0), pos.max(0)
        mid = (lo + hi) / 2.0
        radius = float((hi[1] - lo[1]) / 2.0)
        tyre = -float(row["PristineBoundingBoxMinY"] or 0.0)       # the car's own bounding box starts one tyre radius below the axles; the LOD0 file's wheel part is smaller than the tyre
        if 0.2 < tyre < 0.8:
            radius = max(radius, tyre)
        wb, shift = float(row["ModelWheelbase"]), float(row["BottomCenterWheelbasePosZ"])
        lift = float(row["BottomCenterWheelbasePosy"] or 0.0)       # 0 on most cars; a few (Diablo, F40, E-Type...) have the axles above or below the model's origin
        tracks = {-1: float(row["ModelFrontTrackOuter"]), 1: float(row["ModelRearTrackOuter"])}
    except Exception:  # noqa: BLE001  (a file from disk, or a car without these rows: no preview wheels)
        return {"has": False}
    if not (radius > 0.05 and wb > 1.0 and all(t > 0.8 for t in tracks.values())):
        return {"has": False}
    wheels = []
    for axle, track in tracks.items():                      # -1 = front axle (-z), +1 = rear
        z = axle * wb / 2.0 + shift - float(mid[2])
        y = lift - float(mid[1])                            # the car's own frame has the wheel centres at height 0 (the pristine bounding box starts one radius below)
        outer = track / 2.0 + float(lo[0])                  # the model's outer face is at -x, so the left wheel uses it as it is and the right wheel is turned round
        wheels.append({"x": -outer, "y": y, "z": z, "flip": False})
        wheels.append({"x": outer, "y": y, "z": z, "flip": True})
    return {"has": True, "section": wheel, "radius": radius, "ground": lift - radius, "wheels": wheels}


def physics_save(a: dict) -> dict:
    need_loaded()
    bm, bdata = phys_member("bin")
    if bm is None:
        raise ValueError("this car has no physicsdefinition.bin")
    pd = physdef.parse(bdata)
    if len(a["hull"]) != len(pd.hull):
        raise ValueError(f"the hull has {len(pd.hull)} points; it cannot change size")
    new = []
    for i, s in enumerate(a["spheres"]):
        old = pd.spheres[i] if i < len(pd.spheres) else None
        new.append(physdef.Sphere(int(s.get("flags", old.flags if old else 2)), float(s["x"]), float(s["y"]), float(s["z"]), float(s["r"]), old.tail if old else 0))
    if not new or any(not np.isfinite([s.x, s.y, s.z, s.r]).all() or s.r <= 0 for s in new):
        raise ValueError("every sphere needs finite coordinates and a radius above 0")
    pd.spheres = new
    for v, h in zip(pd.hull, a["hull"]):
        v[0], v[1], v[2] = float(h[0]), float(h[1]), float(h[2])
    out = pd.build()
    physdef.parse(out)                                            # must read back
    res = {"bin": put_member(bm, out, bool(a.get("stage_only")))}
    xm, xdata = phys_member("xml")
    if xm is not None and a.get("write_xml", True):
        text = xdata.decode("latin1")
        mx = physdef.read_maxdata(text)
        sp = a["spheres"] if len(mx["spheres"]) == len(a["spheres"]) else None      # MAXData.xml lists all spheres in some cars only
        text = physdef.patch_maxdata(text, sp and [{k: float(s[k]) for k in "xyzr"} for s in sp], a.get("bbox"), a.get("misc"))
        res["xml"] = put_member(xm, text.encode("latin1"), bool(a.get("stage_only")))
    return res


# ---------------------------------------------------------------------------------------------- skeleton and animation (.gr2)
def gr2_files() -> dict[str, bytes]:
    z = origin_zip()
    media, _, _ = origin_car()
    out = {}
    for n in z.namelist():
        if n.lower().endswith(".gr2"):
            rel = re.sub(rf"(?i)^media/cars/{re.escape(media)}/", "", n)
            out[n] = S.staged.get(rel) or xlzx.read_member(z, n)
    return out


def skeleton_info() -> dict:
    need_loaded()
    files = []
    for name, raw in gr2_files().items():
        f = gr2.read(raw)
        root = f.root()
        e = {"name": name, "bones": [], "animations": []}
        if root["Skeletons"][1]:
            e["bones"] = gr2s.bones(f)
        if root["Animations"][1]:
            e["animations"] = gr2s.animations(f)
        files.append(e)
    return {"files": files}


def skeleton_edit(a: dict) -> dict:
    need_loaded()
    name = a["file"]
    raw = gr2_files().get(name)
    if raw is None:
        raise KeyError("no such .gr2 file in this car")
    f = gr2.read(raw)
    if a.get("op") == "bone":
        gr2s.set_bone(f, int(a["bone"]), t=a.get("t"), rotate_deg=a.get("rotate_deg"))
    elif a.get("op") == "speed":
        gr2s.scale_time(f, float(a["factor"]))
    else:
        raise ValueError("unknown op")
    out = f.build()
    gr2.read(out)                                                 # must read back
    return {"saved": put_member(name, out, bool(a.get("stage_only")))}


_free_cache: dict = {}


def atlas_free(aspect: float | None, overwrite: list[str]) -> dict:
    """The car's body atlas: which pixels a part still reads (res x res, 1 = used) if the parts in `overwrite` are given up, the largest free rectangle (shaped to `aspect`), and the list of parts
    with the area each reads and the area only it reads (what overwriting it frees). Looks at the main and _lod0 models of the open car (they share one uv layout)."""
    z = origin_zip()
    media, _, _ = origin_car()
    members = [n for n in z.namelist() if re.fullmatch(rf"(?i)(media/cars/{re.escape(media)}/)?{re.escape(media)}(_lod0)?\.carbin", n)]
    key = (S.origin["source"], S.origin["archive"], tuple(sorted((m, z.getinfo(m).file_size, z.getinfo(m).CRC) for m in members)))
    if key not in _free_cache:
        _free_cache.clear()
        low = {n.lower(): n for n in z.namelist()}
        atl = next((low[c] for c in ("nodamage.xds", f"media/cars/{media.lower()}/nodamage.xds") if c in low), None)
        white = ap.white_mask(xds.decode(xlzx.read_member(z, atl)), 256) if atl else None
        _free_cache[key] = (ap.part_masks([fc.parse(xlzx.read_member(z, m)) for m in members], 256), white)
    masks, white = _free_cache[key]
    ow = {n for n in overwrite if n in masks}
    used = ap.used_mask(masks, ow, 256, white)
    best = ap.largest_free_rect(used, 3, aspect)
    parts = []
    for n, m in masks.items():
        if not m.any():
            continue
        others = ap.used_mask(masks, {n}, 256, white)
        parts.append({"name": n, "pct": round(float(m.mean()) * 100, 2), "only_pct": round(float((m & ~others).mean()) * 100, 2)})
    parts.sort(key=lambda p: (-p["only_pct"], p["name"]))
    return {"res": 256, "used": base64.b64encode(np.packbits(used).tobytes()).decode(), "used_pct": round(float(used.mean()) * 100, 1),
            "best": list(best[:4]) if best else None, "free_pct": round(best[4] * 100, 1) if best else 0.0, "parts": parts, "overwrite": sorted(ow)}


def add_model_geometry(a):
    """meshes: [{material, positions, normals, uvs, indices}] as base64 little-endian float32 / uint32, already in FH1 coordinates.
    mode 'add' keeps the part's own sub-meshes and appends; 'replace' rebuilds the part from the new geometry only."""
    si = a["section"]
    sw = cg.donor_swatches(S.carbin)
    lods = [int(x) for x in a["lods"]]
    if not lods:
        raise ValueError("choose at least one LOD")
    pieces = []
    for m in a["meshes"]:
        pos = _arr(m["positions"], "<f4", 3).astype(np.float64)
        tris = _arr(m["indices"], "<u4", 3).astype(np.int64)
        if not len(pos) or not len(tris):
            continue
        uv = _arr(m["uvs"], "<f4", 2).astype(np.float64) if m.get("uvs") else np.zeros((len(pos), 2))
        if len(uv) != len(pos):
            uv = np.zeros((len(pos), 2))
        if a.get("flip_v"):
            uv = uv * [1, -1] + [0, 1]
        nrm = _arr(m["normals"], "<f4", 3).astype(np.float64) if m.get("normals") else np.zeros((0, 3))
        if len(nrm) != len(pos) or not np.isfinite(nrm).all() or np.abs(nrm).sum() == 0:
            nrm = cg.smooth_normals(pos, tris)
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        mat = str(m["material"])
        tex = a.get("texture")
        pt = None if tex else (sw.get(mat) if a.get("uv0_mode", "swatch") == "swatch" else None)
        uv0 = ap.uv_in_rect(uv, tex["rect"]) if tex else (np.tile(np.array(pt), (len(pos), 1)) if pt else uv)
        pieces.append(cg.Piece(mat, min(lods), pos, nrm, uv0, uv, tris, swatch=bool(pt)))
    if not pieces:
        raise ValueError("the model has no triangles")
    if a.get("auto_lods", True) and len(lods) > 1:
        pieces = cg.lod_pieces(pieces, lods, cg.lod_ratios(S.carbin, si, lods))      # the lowest chosen LOD keeps the full model, the others get simplified copies
    else:
        pieces = [cg.Piece(p.material, l, p.pos, p.nrm, p.uv0, p.uv1, p.tris, p.swatch) for l in lods for p in pieces]
    if a.get('double_sided'):
        # Separate vertices with inverted normals are essential: the encoder orients
        # triangles against their normals, so reversing only indices gets undone.
        pieces += [cg.Piece(p.material, p.lod, p.pos.copy(), -p.nrm, p.uv0.copy(), p.uv1.copy(),
                            p.tris[:, [0, 2, 1]].copy(), p.swatch) for p in pieces]
    sh = cg.SHModel(S.carbin)
    if a.get("mode") == "new":
        # A part of its own: it shows up in the part list, can be selected, moved, rotated, scaled, renamed or merged later, and survives saving and reopening.
        every = np.vstack([p.pos for p in pieces])
        centre = (every.min(0) + every.max(0)) / 2.0                 # the pivot sits in the middle of the model, so rotating and scaling it behave
        S.last_pasted = carbin_parts.add_section(S.carbin, pieces, str(a.get("name") or "custom_model"), centre, template_index=si, taken=a.get("_taken", ()))
    elif a.get("mode") == "replace":
        # the seed makes the new vertex buffer's resource key: a fixed seed gave EVERY replaced part the same key, which fails validation
        # (and means two buffers with one identity in the game). A paired edit passes one seed so both models get the same key.
        cg.rebuild_section(S.carbin, si, pieces, sh, seed=int(a.get("seed") or secrets.randbits(62)), draw=True)
    else:
        cg.append_pieces(S.carbin, si, pieces, sh, seed=int(a.get("seed") or secrets.randbits(62)))
    if a.get("texture") and not a.get('_skip_paint'):
        paint_atlases(dict(a["texture"], stage_only=True))


def model_operation(a, operation, section_keys, skip_missing=False):
    """Build paired edits in memory; only commit after both files validate. With skip_missing, a part that the other model does not have
    (the close-up model leaves out some parts, e.g. the Alfa 8C's hood) is simply not moved there instead of cancelling the whole edit."""
    if not a.get('both_models'):
        return operation(a)
    media, relative, _ = origin_car()
    expected = {f'{media}.carbin'.lower(), f'{media}_lod0.carbin'.lower()}
    if relative.lower() not in expected:
        raise ValueError('Open the main or LOD0 car model to edit both models')
    archive = origin_zip()
    other_relative = f'{media}.carbin' if relative.lower().endswith('_lod0.carbin') else f'{media}_lod0.carbin'
    try:
        other_member = find_member(archive, str(Path(S.origin['member']).with_name(other_relative)).replace('\\', '/'))
    except KeyError:
        raise ValueError(f'This car has no {other_relative} file, so there is no second model to change. Untick "Driving + photo models" to change only the open model.') from None
    other_relative = re.sub(rf'(?i)^media/cars/{re.escape(media)}/', '', other_member)
    other_raw = next((v for k, v in S.staged.items() if k.lower() == other_relative.lower()), None)
    other = fc.parse(other_raw if other_raw is not None else xlzx.read_member(archive, other_member))
    other_baseline = original_problem_keys(other_relative)
    a = dict(a, seed=a.get('seed') or secrets.randbits(62))
    args = dict(a, both_models=False)
    # Both models share these atlases. Paint once after both geometries validate.
    texture = a.get('texture')
    a = dict(a, _skip_paint=True)
    args['_skip_paint'] = True
    for key in section_keys:
        indices = a[key] if isinstance(a[key], list) else [a[key]]
        mapped = []
        for index in indices:
            name = part_actions.section_at(S.carbin, index).name
            matches = [i for i, s in enumerate(other.sections) if s.name.lower() == name.lower()]
            if not matches and skip_missing:
                continue
            if len(matches) != 1:
                raise ValueError(f'The other model of this car has {"no part" if not matches else "several parts"} named {name}, so this cannot be done in both models. '
                                 'Untick the both-models option (Driving + photo / Main + LOD0) to change only the open model. Nothing was changed.')
            mapped.append(matches[0])
        args[key] = mapped if isinstance(a[key], list) else mapped[0]
    if 'lods' in a:
        args['lods'] = sorted(set(cg.summary(other)['array_lods'])) or sorted({s.lod for sec in other.sections for s in sec.subs})
    original = S.carbin
    staged_before = dict(S.staged)
    pasted_before = S.last_pasted
    current = fc.parse(fc.build(original))
    try:
        # a new part gets one name in BOTH models: it must be free in each
        a = dict(a, _taken=[s.name for s in other.sections])
        args['_taken'] = [s.name for s in current.sections]
        S.carbin = current
        operation(a)
        current = S.carbin
        current_pasted = S.last_pasted
        S.carbin = other
        operation(args)
        other = S.carbin
        S.last_pasted = current_pasted
        for model, known in ((current, S.baseline), (other, other_baseline)):
            problems = new_problems(model, known)
            if problems:
                raise ValueError('Model validation failed: ' + '; '.join(problems[:5]))
        pending = {relative: fc.build(current), other_relative: fc.build(other)}
        if texture:
            S.carbin = current
            paint_atlases(dict(texture, stage_only=True))
    except Exception:
        S.carbin = original
        S.staged = staged_before
        S.last_pasted = pasted_before
        raise
    S.carbin = current
    for key, raw in pending.items():
        for old in list(S.staged):
            if old.lower() == key.lower():
                del S.staged[old]
        S.staged[key] = raw


def op_add_model(a):
    return model_operation(a, add_model_geometry, ['section'])


def car_replacements():
    """All pending files of this car, including matching stripped model metadata."""
    need_loaded()
    if not S.origin:
        raise ValueError('Import a car from Cars first to save its ZIP')
    media, relative, _ = origin_car()
    archive = origin_zip()
    pending = dict(S.staged)
    for key in list(pending):
        if key.lower() == relative.lower():
            del pending[key]
    pending[relative] = fc.build(S.carbin)
    result = {}
    prefix = f'media/cars/{media}/'
    for name, raw in pending.items():
        member = find_member(archive, prefix + name if S.origin['source'] != 'game' else name)
        if member.lower().endswith('.carbin'):
            model = fc.parse(raw)
            problems = new_problems(model, original_problem_keys(name))
            if problems:
                raise ValueError(f'{name}: ' + '; '.join(problems[:5]))
            stripped = str(Path(member).with_name('stripped_' + Path(member).name)).replace('\\', '/')
            hits = [n for n in archive.namelist() if n.lower() == stripped.lower()]
            if hits:
                result[hits[0]] = fc.build(model, stripped=True)
        result[member] = raw
    return result


def save_existing_car():
    replacements = car_replacements()
    origin = S.origin
    result = S.lib.write_many(origin['source'], origin['archive'], replacements)
    S.staged.clear()
    # Old undo snapshots contain pre-save staged data; start a new edit history.
    S.undo.clear()
    S.redo.clear()
    S.original = fc.build(S.carbin)
    return result


def export_replacement_zip(out_dir):
    replacements = car_replacements()
    archive = origin_zip()
    folder = Path(out_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / Path(S.origin['archive']).name
    try:
        source_path = S.lib.zip_path(S.origin['source'], S.origin['archive']).resolve()
    except PermissionError:
        source_path = None
    if destination.resolve() == source_path:
        raise ValueError('Use Apply to existing car to update the original archive')
    with tempfile.TemporaryDirectory(dir=folder, prefix='.fh1-export-') as temporary:
        copy = Path(temporary) / destination.name
        archive.fp.seek(0)
        with copy.open('wb') as stream:
            shutil.copyfileobj(archive.fp, stream, 1024 * 1024)
        result = cl.replace_zip_members(copy, replacements, backup=False)
        if destination.exists() and not Path(str(destination) + '.bak').exists():
            shutil.copy2(destination, str(destination) + '.bak')
        os.replace(copy, destination)
    return dict(result, zip=str(destination), backup=str(destination) + '.bak' if Path(str(destination) + '.bak').exists() else '')


def op_merge_parts(a):
    return model_operation(a, lambda args: part_actions.merge_sections(S.carbin, args['source_section'], args['target_section']),
                           ['source_section', 'target_section'])

def op_obj_info(a):
    parsed = cg.parse_obj(a["text"], flip_v=True)
    return {"objects": {o: {str(lod): {m: int(len(g["tris"])) for m, g in mats.items()} for lod, mats in d.items()} for o, d in parsed.items()}}


OPS = {
    "transform": op_transform,
    "rename_section": lambda a: rename_section(a["section"], a["name"]),
    "set_sub": lambda a: cg.set_sub(S.carbin, a["section"], a["sub"], a.get("material"), a.get("lod")),
    "delete_sub": lambda a: cg.remove_subs(S.carbin, a["section"], a["subs"]),
    "hide_section": lambda a: cg.hide_section(S.carbin, a["section"]),
    "show_section": lambda a: cg.show_section(S.carbin, a["section"]),
    "light_set": lambda a: cg.set_light(S.carbin, a["index"], **a["fields"]),
    "light_add": lambda a: cg.add_light(S.carbin, a.get("like")),
    "light_remove": lambda a: cg.remove_light(S.carbin, a["index"]),
    "exhaust_set": lambda a: cg.set_exhaust_pos(S.carbin, a["index"], a["pos"]),
    "import_obj": op_import_obj,
    "add_model": op_add_model,
}


SETTINGS = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "FH1CarbinEditor" / "settings.json"


TIMING_LOG = SETTINGS.parent / "load-times.log"


def log_timing(entry) -> dict:
    """Append one line of load timings (this computer only) so a slow step can be found from numbers."""
    if not isinstance(entry, dict):
        raise ValueError("timing entry must be an object")
    fields = {str(k)[:40]: v for k, v in list(entry.items())[:12] if isinstance(v, (int, float, str, bool))}
    fields = {k: (v[:80] if isinstance(v, str) else v) for k, v in fields.items()}
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + json.dumps(fields, sort_keys=True)
    try:
        TIMING_LOG.parent.mkdir(parents=True, exist_ok=True)
        if TIMING_LOG.exists() and TIMING_LOG.stat().st_size > 256 * 1024:
            TIMING_LOG.write_text("", encoding="utf-8")
        with TIMING_LOG.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        pass
    return {"ok": True}


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or not isinstance(saved.get('game', ''), str) or not isinstance(saved.get('dlc', []), list):
            return {}
        if not all(isinstance(p, str) for p in saved.get('dlc', [])):
            return {}
        return saved
    except (OSError, ValueError):
        return {}


def save_settings(config=None) -> None:
    config = S.cfg if config is None else config
    temporary = None
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=SETTINGS.parent,
                                         prefix='settings-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"game": config["game"], "dlc": config["dlc"], "export_dirs": S.export_dirs, "output_dir": S.output_dir}, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, SETTINGS)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def default_out_dir() -> Path:
    """Where exported packages go by default: next to where you ran the script, or (packaged program) in Documents."""
    return Path.home() / "Documents" / "FH1 Carbin Editor" / "new_cars" if FROZEN else Path.cwd() / "new_cars"


def set_library(game: str, dlc: list):
    game = (game or "").strip().strip('"')
    dlc = [d.strip().strip('"') for d in dlc if d and d.strip()]
    for folder in dlc:
        if not Path(folder).is_dir():
            raise ValueError(f'DLC folder does not exist: {folder}')
    notice = ''
    try:
        library = cl.Library(game or None, dlc) if (game or dlc) else None
    except ValueError:
        selected_dlc = fsrc.fh1_dlc_directory(game) if game else None
        previous_game = S.cfg.get('game', '')
        if not (selected_dlc and previous_game and S.lib and S.lib.game):
            raise
        # Revalidate the saved base game before accepting a correction. Never
        # persist a DLC-only folder as a game or fall back to unrelated guesses.
        game = previous_game
        if str(selected_dlc).casefold() not in {str(Path(p)).casefold() for p in dlc}:
            dlc.append(str(selected_dlc))
        library = cl.Library(game, dlc)
        notice = 'That was your Xenia DLC folder. Kept your saved FH1 base game and added the DLC path below.'
    config = {"game": game, "dlc": dlc}
    save_settings(config)
    S.cfg = config
    S.lib = library
    S.setup_error = ''
    S.path_notice = notice


def library_info():
    return {"sources": S.lib.sources() if S.lib else [], "config": S.cfg,
            "needs_setup": not bool(S.lib and S.lib.game), "setup_error": S.setup_error,
            "path_notice": S.path_notice, "output_dir": S.output_dir}


def inspect_library_model(a):
    if not S.lib:
        raise ValueError('Set your game path in Settings first')
    # Reading a donor must never call Session.load: it would replace active edits/staged files.
    return fc.parse(S.lib.read(a['source'], a['archive'], a['member']))


def paste_part(a):
    S.last_pasted = S.clipboard.paste(S.carbin, a.get('material_map'), a.get('name', ''), a.get('translate', (0, 0, 0)))


OPS['paste_part'] = paste_part
OPS['set_coordinates'] = lambda a: part_actions.set_coordinates(S.carbin, a['section'], a['coordinates'])
OPS['merge_parts'] = op_merge_parts

def origin_zip():
    need_loaded()
    if not (S.origin and S.lib):
        raise ValueError("this file was not opened from the game or a DLC, so its archive is not known")
    return S.lib.archive_zip(S.origin["source"], S.origin["archive"])


def find_member(z, name: str) -> str:
    low = {n.lower(): n for n in z.namelist()}
    if name.lower() not in low:
        raise KeyError(f"{name} is not in the archive")
    return low[name.lower()]


def atlas_member(z, name: str) -> str:
    """'nodamage' -> nodamage_LOD0.xds for _lod0/_cockpit style files, nodamage.xds for the main model."""
    low = {n.lower(): n for n in z.namelist()}
    prefix = ''
    if S.origin and S.origin['source'] != 'game':
        media, _, _ = origin_car()
        prefix = f'media/cars/{media}/'
    order = [f"{name}_lod0.xds", f"{name}.xds"] if cg.pool_name_of(S.carbin) == "lod0" else [f"{name}.xds", f"{name}_lod0.xds"]
    for c in order:
        if (prefix + c).lower() in low:
            return low[(prefix + c).lower()]
    raise KeyError(f"no {name} texture in this car's archive")


def preview_texture_blob(name: str) -> bytes:
    if name.startswith('shared:'):
        allowed = {'carbonFiber.xds', 'grille1.xds', 'grille2.xds'}
        member = name.split(':', 1)[1]
        if member not in allowed or not (S.lib and S.lib.game):
            raise ValueError('Unknown or unavailable shared preview texture')
        archive = S.lib.game_zip('media/cars/shared.zip')
        return xlzx.read_member(archive, find_member(archive, member))
    if name not in {'nodamage', 'damage', 'lights', 'damagelights', 'interior', 'interior_emissive'}:
        raise ValueError('Unknown preview atlas')
    archive = origin_zip()
    member = atlas_member(archive, name)
    media, _, _ = origin_car()
    relative = re.sub(rf'(?i)^media/cars/{re.escape(media)}/', '', member)
    staged = next((raw for key, raw in S.staged.items() if key.lower() == relative.lower()), None)
    return staged if staged is not None else xlzx.read_member(archive, member)


def member_kind(n: str) -> str:
    e = n.lower().rsplit(".", 1)[-1] if "." in n else ""
    return "texture" if e == "xds" else "text" if e in ("xml", "txt", "html", "ini", "csv") else "model" if e == "carbin" else "image" if e in ("tga", "png", "jpg") else "other"

def origin_car():
    """(donor media name, file name relative to the car folder, donor DLC package or None) of the open file."""
    o = S.origin
    if not (o and S.lib):
        raise ValueError("open a car from the game or a DLC first")
    if o["source"] == "game":
        media = Path(o["archive"]).stem
        return media, o["member"], None
    m = re.match(r"(?i)media/cars/([^/]+)/(.+)$", o["member"])
    if not m:
        raise ValueError("this file is not inside a car folder")
    return m.group(1), m.group(2), S.lib._pk[o["source"].split(":", 1)[1]]


def export_packages(out_dir=None):
    """Reserve IDs from installed DLC and previous exports, including after restart."""
    packs = list(S.lib._pk.values())
    seen = {str(p.path.resolve()).lower() for p in packs}
    roots = [default_out_dir(), *map(Path, S.export_dirs)]
    if out_dir:
        roots.append(Path(out_dir))
    for root in roots:
        for folder in (root / 'xenia', root / 'console'):
            for package in fsrc.list_packages(folder):
                key = str(package.path.resolve()).lower()
                if key not in seen:
                    packs.append(package)
                    seen.add(key)
    return packs


def clone_defaults():
    media, _, pkg = origin_car()
    packs = export_packages()
    used = fsrc.scan_used_ids(S.lib.game, packs, log=lambda *_: None)
    offer = 9000
    while offer in used["offer"]:
        offer += 1
    return {"donor": media, "new_media": cc.same_length_name(media, used["media"]), "offer": offer, "staged": sorted(S.staged), "out_dir": S.output_dir or str(default_out_dir())}


def do_clone(a):
    media, rel, pkg = origin_car()
    files = dict(S.staged)
    if a.get("include_open", True):
        files[rel] = fc.build(S.carbin)
    relative_names = {re.sub(rf'(?i)^media/cars/{re.escape(media)}/', '', name).lower():
                      re.sub(rf'(?i)^media/cars/{re.escape(media)}/', '', name) for name in origin_zip().namelist()}
    files = {relative_names.get(name.lower(), name): raw for name, raw in files.items()}
    for filename, blob in list(files.items()):
        if filename.lower().endswith('.carbin') and not Path(filename).name.lower().startswith('stripped_'):
            try:
                model = fc.parse(blob)
                problems = new_problems(model, original_problem_keys(filename))
            except Exception as exc:
                raise ValueError(f'Model validation failed for {filename}: {exc}') from exc
            if problems:
                raise ValueError(f'Model validation failed for {filename}: ' + '; '.join(problems))
            stripped = str(Path(filename).with_name('stripped_' + Path(filename).name)).replace('\\', '/')
            if stripped.lower() in relative_names:
                files[relative_names[stripped.lower()]] = fc.build(model, stripped=True)
    formats = a.get('formats', ['xenia'])
    if not isinstance(formats, list) or not formats or any(f not in ('xenia', 'stfs') for f in formats):
        raise ValueError("Choose Xenia folder and/or console STFS as an export format")
    root = Path(a['out_dir']).resolve()
    package_name = dlcb.package_name(int(a['offer']))
    targets = ([root / 'xenia' / package_name, root / 'xenia' / 'Headers' / '00000002' / (package_name + '.header')] if 'xenia' in formats else [])
    if 'stfs' in formats:
        targets.append(root / 'console' / package_name)
    for target in targets:
        if target.exists():
            raise FileExistsError(f'Export already exists: {target}. Choose a new offer number or output folder.')
    packs = export_packages(root)
    res = cc.clone_car(S.lib.game, media, a["new_media"], int(a["offer"]), packs, donor_package=pkg, replace_files=files, log=lambda *_: None, body_overrides=dict(S.body_over) or None,
                       display_name=(a.get("display_name") or "").strip() or None, short_name=(a.get("short_name") or "").strip() or None)
    if res["report"]["problems"]:
        return {"error": "the new car's database rows collide with existing rows", "problems": res["report"]["problems"]}
    display = (a.get("display_name") or "").strip() or f"{a['new_media']} (copy of {media})"
    out = {}
    problems = []
    if "xenia" in formats:
        folder = dlcb.write_xenia_folder(res["files"], a["out_dir"], int(a["offer"]), display)
        problems = dlcb.verify_folder(folder)
        out.update({"folder": str(folder), "header": str(Path(a["out_dir"]) / "xenia" / "Headers" / "00000002" / (folder.name + ".header"))})
    if "stfs" in formats:
        stfs_path = Path(a["out_dir"]) / "console" / dlcb.package_name(int(a["offer"]))
        out["stfs"] = str(dlcb.write_stfs(res["files"], stfs_path, display, f"{a['new_media']}: edited copy of {media}"))
    rep = res["report"]
    out.update({"problems": problems, "car_id": rep["ids"]["car"], "rows": sum(rep["tables"].values()), "tables": len(rep["tables"]), "files": rep["files"], "edited": sorted(files)})
    remembered = str(root)
    S.export_dirs = [folder for folder in S.export_dirs if folder != remembered] + [remembered]
    S.output_dir = remembered
    save_settings()
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "FH1CarbinEditor/1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def _err(self, e: Exception, code=400):
        message = f"{type(e).__name__}: {e}"
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            (SETTINGS.parent / 'last-error.txt').write_text(message, encoding='utf-8')
        except OSError:
            pass  # Reporting the original error must still work when its log cannot be saved.
        self._json({"error": message}, code)

    def _guard(self, u, q) -> bool:
        """Refuse everything that does not come from our own page: wrong Host header (DNS rebinding), another site as Origin (cross-site requests), or no secret.
        The secret travels in the address the program prints (?token=...); the first visit swaps it for a SameSite=Strict cookie, which other sites' requests do not carry."""
        port = self.server.server_address[1]
        # Cookies are shared across ports. Each editor instance needs its own name,
        # otherwise opening another window invalidates the first window's API access.
        cookie_name = f'{COOKIE}_{port}'
        if (self.headers.get("Host") or "").lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._send(403, b"wrong host", "text/plain")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            self._send(403, b"cross-site request refused", "text/plain")
            return False
        if u.path == "/" and q.get("token") and hmac.compare_digest(q["token"], TOKEN):
            self.send_response(302)
            self.send_header("Set-Cookie", f"{cookie_name}={TOKEN}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        jar = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
        # Keep the documented cookie name available for authenticated API scripts.
        if not any(name in jar and hmac.compare_digest(jar[name].value, TOKEN) for name in (cookie_name, COOKIE)):
            self._send(401, b"Open the address printed in the terminal where you started the editor (it ends with ?token=...).", "text/plain")
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._guard(u, q):
            return
        try:
            with S.lock:
                if u.path in ("/", "/index.html"):
                    html = (HERE / "index.html").read_bytes()
                    m = re.search(rb'<script type="importmap">(.*?)</script>', html, re.S)
                    # HTML parsing normalizes CRLF before CSP checks inline script text.
                    script = m.group(1).replace(b'\r\n', b'\n').replace(b'\r', b'\n') if m else b''
                    pin = "'sha256-" + base64.b64encode(hashlib.sha256(script).digest()).decode() + "'" if m else ""
                    csp = ("default-src 'none'; script-src 'self' " + pin + "; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; "
                           "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                    return self._send(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
                if u.path.startswith("/vendor/"):                       # the bundled three.js: files below vendor/ only
                    p = (HERE / u.path[1:]).resolve()
                    if (HERE / "vendor").resolve() not in p.parents or not p.is_file() or p.suffix not in (".js", ".txt", ""):
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, p.read_bytes(), "text/javascript; charset=utf-8" if p.suffix == ".js" else "text/plain; charset=utf-8")
                if u.path in ("/app.js", "/editor_workflow.js", "/startup_diagnostics.js", "/library_actions.mjs", "/viewer_materials.mjs", "/part_tools.mjs", "/part_gizmo.mjs", "/style.css"):
                    return self._send(200, (HERE / u.path[1:]).read_bytes(), "text/javascript; charset=utf-8" if u.path.endswith((".js", ".mjs")) else "text/css")
                if u.path == "/api/summary":
                    need_loaded()
                    return self._json(S.info())
                if u.path == "/api/status":
                    return self._json({"open": S.carbin is not None, "library": S.lib is not None, "name": S.name, "clipboard": S.clipboard.info()})
                if u.path == "/api/geometry":
                    need_loaded()
                    return self._send(200, geometry_blob(int(q["sec"])), "application/octet-stream")
                if u.path == "/api/export_obj":
                    need_loaded()
                    secs = [int(x) for x in q["sections"].split(",")] if q.get("sections") else None
                    lods = {int(x) for x in q["lods"].split(",")} if q.get("lods") else None
                    text = cg.export_obj(S.carbin, secs, lods, q.get("uv", "auto"), q.get("flip_v", "1") == "1")
                    return self._send(200, text.encode(), "text/plain; charset=utf-8", {"Content-Disposition": f'attachment; filename="{safe_name(Path(S.name).stem)}.obj"'})
                if u.path == "/api/save":
                    need_loaded()
                    probs = new_problems(S.carbin, S.baseline)
                    if probs and q.get("force") != "1":
                        return self._json({"error": "validation failed", "problems": probs}, 409)
                    return self._send(200, fc.build(S.carbin), "application/octet-stream", {"Content-Disposition": f'attachment; filename="{safe_name(Path(S.name).name)}"'})
                if u.path == "/api/archive":
                    z = origin_zip()
                    return self._json({"members": [{"name": i.filename, "size": i.file_size, "kind": member_kind(i.filename)} for i in z.infolist()], "origin": S.origin})
                if u.path == "/api/atlas":
                    data = preview_texture_blob(q['name'])
                    return self._send(200, xds.png_bytes(xds.decode(data)), "image/png")
                if u.path == "/api/cockpit_info":
                    return self._json(cockpit_info())
                if u.path == "/api/cockpit_geometry":
                    return self._send(200, cockpit_geometry(int(q["sec"])), "application/octet-stream")
                if u.path == "/api/wheel_layout":
                    return self._json(wheel_layout())
                if u.path == "/api/physics":
                    return self._json(physics_info())
                if u.path == "/api/skeleton":
                    return self._json(skeleton_info())
                if u.path == "/api/atlas_free":
                    return self._json(atlas_free(float(q["aspect"]) if q.get("aspect") else None, [x for x in (q.get("overwrite") or "").split("|") if x]))
                if u.path == "/api/member":
                    z = origin_zip()
                    name = find_member(z, q["name"])
                    data = xlzx.read_member(z, name)
                    if q.get("png"):
                        inf = xds.info(data)
                        return self._send(200, xds.png_bytes(xds.decode(data)), "image/png")
                    if q.get("info"):
                        inf = xds.info(data)
                        return self._json({**inf, "mip_levels": [(l["w"], l["h"]) for l in (xds.mip_layout(inf) or [])]})
                    if q.get("text"):
                        return self._json({"text": data.decode("utf-8-sig", "replace"), "bytes": len(data)})
                    return self._send(200, data, "application/octet-stream", {"Content-Disposition": f'attachment; filename="{safe_name(name.rsplit("/", 1)[-1])}"'})
                if u.path == "/api/strings":
                    if not S.lib:
                        raise ValueError("set the game folder first")
                    z = S.lib.game_zip(f"media/stringtables/{q.get('lang', 'en')}.zip")
                    names = {n.lower(): n for n in z.namelist() if n.lower().endswith(".str")}
                    if not q.get("table"):
                        return self._json({"tables": sorted(n[:-4] for n in names.values())})
                    key = (q["table"] + ".str").lower()
                    if key not in names:
                        raise KeyError("no such table")
                    needle = (q.get("q") or "").lower()
                    rows = [{"key": e.key, "name": e.name, "text": e.text} for e in lsb2.read(xlzx.read_member(z, names[key]))
                            if not needle or needle in e.text.lower() or needle in e.name.lower() or needle == str(e.key)]
                    return self._json({"rows": rows[:500], "total": len(rows)})
                if u.path == "/api/clone/defaults":
                    need_loaded()
                    return self._json(clone_defaults())
                if u.path == "/api/library":
                    return self._json(library_info())
                if u.path == "/api/library/cars":
                    return self._json({"cars": S.lib.cars(q["source"])})
                if u.path == "/api/library/members":
                    return self._json({"members": S.lib.members(q["source"], q["car"])})
            self._send(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            self._err(e)

    def do_POST(self):
        u = urlparse(self.path)
        if not self._guard(u, {}):
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._send(413, b"request too large", "text/plain")
            return
        body = self.rfile.read(n) if n else b""
        try:
            with S.lock:
                if u.path == '/api/unload':
                    args = json.loads(body or b'{}')
                    changed = bool(S.staged or S.body_over or (S.carbin is not None and fc.build(S.carbin) != S.original))
                    if changed and not args.get('discard'):
                        return self._json({'error': 'Unload this car and discard its unsaved edits?', 'requires_discard': True}, 409)
                    S.unload()
                    _cockpit_cache.clear()
                    _free_cache.clear()
                    return self._json({'open': False})
                if u.path == '/api/parts/inspect':
                    car = inspect_library_model(json.loads(body))
                    return self._json(cg.summary(car))
                if u.path == '/api/parts/copy':
                    a = json.loads(body)
                    if a.get('source'):
                        car = inspect_library_model(a)
                        label = Path(a['member']).name
                    else:
                        need_loaded()
                        car, label = S.carbin, S.name
                    return self._json({'clipboard': S.clipboard.copy(car, a['section'], label)})
                if u.path == "/api/open":
                    S.load(body, self.headers.get("X-Name", "model.carbin"))
                    return self._json(S.info())
                if u.path == "/api/open_path":
                    p = Path(json.loads(body)["path"])
                    S.load(p.read_bytes(), p.name, p)
                    return self._json(S.info())
                if u.path == "/api/member/replace":
                    a = json.loads(body)
                    z = origin_zip()
                    name = find_member(z, a["name"])
                    if a["kind"] == "text":
                        data = a["text"].encode("utf-8")
                    elif a["kind"] == "raw":
                        data = base64.b64decode(a["data"])
                    elif a["kind"] == "texture":
                        tpl = xlzx.read_member(z, name)
                        inf = xds.info(tpl)
                        rgba = np.frombuffer(base64.b64decode(a["rgba"]), np.uint8).reshape(int(a["h"]), int(a["w"]), 4)
                        if (int(a["w"]), int(a["h"])) != (inf["w"], inf["h"]):
                            raise ValueError(f"image is {a['w']}x{a['h']} but the texture is {inf['w']}x{inf['h']}")
                        data = xds.encode_like(tpl, rgba)
                    else:
                        raise ValueError("unknown kind")
                    o = S.origin
                    res = S.lib.write(o["source"], o["archive"], name, data)
                    return self._json({"saved": res})
                if u.path == "/api/skeleton/edit":
                    return self._json(skeleton_edit(json.loads(body)))
                if u.path == "/api/physics/save":
                    return self._json(physics_save(json.loads(body)))
                if u.path == "/api/physics/body":
                    allowed = {"ModelWheelbase", "ModelFrontTrackOuter", "ModelRearTrackOuter", "ModelFrontStockRideHeight", "ModelRearStockRideHeight", "BottomCenterWheelbasePosx", "BottomCenterWheelbasePosy",
                               "BottomCenterWheelbasePosZ", "PristineBoundingBoxMinX", "PristineBoundingBoxMinY", "PristineBoundingBoxMinZ", "PristineBoundingBoxMaxX", "PristineBoundingBoxMaxY", "PristineBoundingBoxMaxZ"}
                    vals = json.loads(body)
                    S.body_over = {k: float(v) for k, v in vals.items() if k in allowed}
                    return self._json({"body_overrides": S.body_over})
                if u.path == "/api/diag":
                    return self._json(log_timing(json.loads(body or b"{}")))
                if u.path == "/api/stage":
                    need_loaded()
                    _, rel, _ = origin_car()
                    if json.loads(body or b"{}").get("clear"):
                        S.staged.clear()
                    else:
                        S.staged[rel] = fc.build(S.carbin)
                    return self._json({"staged": sorted(S.staged)})
                if u.path == "/api/clone":
                    need_loaded()
                    res = do_clone(json.loads(body))
                    return self._json(res, 409 if "error" in res else 200)
                if u.path == "/api/library/config":
                    a = json.loads(body)
                    set_library(a.get("game", ""), a.get("dlc", []))
                    return self._json(library_info())
                if u.path == "/api/library/open":
                    a = json.loads(body)
                    data = S.lib.read(a["source"], a["archive"], a["member"])
                    if S.origin and a['source'] == S.origin['source'] and a['archive'] == S.origin['archive']:
                        media, _, _ = origin_car()
                        relative = re.sub(rf'(?i)^media/cars/{re.escape(media)}/', '', a['member'])
                        data = next((raw for key, raw in S.staged.items() if key.lower() == relative.lower()), data)
                    S.load(data, Path(a["member"]).name, None, {"source": a["source"], "archive": a["archive"], "member": a["member"]})
                    return self._json(S.info())
                if u.path == "/api/library/save":
                    need_loaded()
                    if not S.origin:
                        raise ValueError("this file was not opened from the library; use Save to path or Download")
                    res = save_existing_car()
                    return self._json({"saved": res})
                if u.path == '/api/library/export_zip':
                    folder = json.loads(body)['out_dir']
                    result = export_replacement_zip(folder)
                    S.output_dir = str(Path(folder).expanduser().resolve())
                    save_settings()
                    return self._json({'saved': result, 'output_dir': S.output_dir})
                if u.path == "/api/save_path":
                    need_loaded()
                    a = json.loads(body)
                    probs = new_problems(S.carbin, S.baseline)
                    if probs and not a.get("force"):
                        return self._json({"error": "validation failed", "problems": probs}, 409)
                    p = Path(a["path"])
                    if p.exists() and not Path(str(p) + ".bak").exists():
                        Path(str(p) + ".bak").write_bytes(p.read_bytes())
                    p.write_bytes(fc.build(S.carbin))
                    S.path = p
                    return self._json({"saved": str(p), "bytes": p.stat().st_size, "backup": str(p) + ".bak" if Path(str(p) + ".bak").exists() else ""})
                if u.path == "/api/undo":
                    need_loaded()
                    if S.undo:
                        S.redo.append(S.capture())
                        S.restore(S.undo.pop())
                    return self._json(S.info())
                if u.path == "/api/redo":
                    need_loaded()
                    if S.redo:
                        S.undo.append(S.capture())
                        S.restore(S.redo.pop())
                    return self._json(S.info())
                if u.path == "/api/obj_info":
                    return self._json(op_obj_info(json.loads(body)))
                if u.path == "/api/op":
                    need_loaded()
                    a = json.loads(body)
                    fn = OPS.get(a["op"])
                    if fn is None:
                        raise ValueError(f"unknown operation {a['op']}")
                    before = S.capture()
                    try:
                        fn(a)
                        fc.build(S.carbin)          # an edit that cannot be written back into the file must not stay applied
                    except Exception:
                        S.restore(before)
                        raise
                    S.undo.append(before)
                    del S.undo[:-MAX_UNDO]
                    S.redo.clear()
                    return self._json(S.info())
            self._send(404, b"not found", "text/plain")
        except Exception as e:  # noqa: BLE001
            self._err(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("--game", help="FH1 GoD folder, ISO or extracted game folder")
    ap.add_argument("--dlc", action="append", default=[])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", "--headless", dest="headless", action="store_true", help="run only the local API for testing")
    a = ap.parse_args()
    saved = {"game": a.game or "", "dlc": a.dlc} if a.game or a.dlc else load_settings()
    S.export_dirs = [p for p in saved.get('export_dirs', []) if isinstance(p, str)] if isinstance(saved.get('export_dirs', []), list) else []
    S.output_dir = saved.get('output_dir', '') if isinstance(saved.get('output_dir', ''), str) else ''
    if saved:
        try:
            set_library(saved.get("game", ""), saved.get("dlc", []))
        except Exception as exc:
            S.cfg = {"game": saved.get("game", ""), "dlc": saved.get("dlc", [])}
            S.setup_error = str(exc)
    if a.file:
        p = Path(a.file)
        S.load(p.read_bytes(), p.name, p)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={TOKEN}"
    if a.headless:
        print(url, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        from editor_desktop import run_desktop
        run_desktop(server, url, SETTINGS.parent / "desktop-profile")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        if FROZEN:
            import ctypes
            message = traceback.format_exc()
            log = SETTINGS.parent / "startup-error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(message, encoding="utf-8")
            ctypes.windll.user32.MessageBoxW(None, f"The editor could not start.\n\n{message.splitlines()[-1]}\n\nDetails: {log}", "FH1 Carbin Editor", 0x10)
        else:
            traceback.print_exc()
        raise
