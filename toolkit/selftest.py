"""selftest.py - check that the toolkit works on the user's own Forza Horizon 1 files before building anything.

    python selftest.py GAME_FOLDER

GAME_FOLDER is the Games on Demand folder (4D5309C9), an extracted folder or an .iso. Read-only on the game; the package test writes to a temporary folder.
Every step prints PASS or FAIL. Fix a FAIL before trusting results from the tool that failed.
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

results = []


def step(name):
    def deco(fn):
        try:
            msg = fn()
            results.append((name, True, msg or ""))
            print(f"PASS  {name}  {msg or ''}")
        except Exception as e:  # noqa: BLE001
            results.append((name, False, str(e)))
            print(f"FAIL  {name}  {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
        return fn
    return deco


if len(sys.argv) < 2:
    raise SystemExit(__doc__)
GAME = Path(sys.argv[1])
state = {}


@step("open the game folder and list files")
def _():
    import fh1_sources as src
    game = src.open_fh1_game(GAME)
    state["game"] = game
    n = len(game.sv.entries)
    assert n > 1000, f"only {n} entries"
    return f"{n} top-level files"


@step("read and open the game database")
def _():
    import fh1_sources as src
    game = state["game"]
    blob = game.gamedb() if callable(game.gamedb) else game.gamedb
    state["gamedb"] = blob
    con = src.open_db(blob)
    t = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    cars = con.execute("select count(*) from Data_Car").fetchone()[0]
    assert t > 200 and cars > 100
    return f"{t} tables, {cars} cars"


@step("decompress a member of an Xbox LZX archive")
def _():
    import xlzx
    z = state["game"].zip("media/UI.zip")
    d = xlzx.read_member(z, "cameras.xml")
    assert b"<Cameras>" in d
    return f"cameras.xml {len(d)} bytes"


@step("read a string table")
def _():
    import lsb2
    import xlzx
    z = state["game"].zip("media/stringtables/en.zip")
    names = {n.lower(): n for n in z.namelist()}
    raw = xlzx.read_member(z, names["data_car.str"])
    ents = lsb2.read(raw)
    assert lsb2.write(ents) == raw, "string table does not round-trip"
    return f"Data_Car.str {len(ents)} entries, rewrite identical"


@step("parse and rebuild a car model (.carbin)")
def _():
    import fh1_carbin as cb
    import xlzx
    z = state["game"].zip("media/cars/ALF_8C_08.zip")
    d = xlzx.read_member(z, "ALF_8C_08_lod0.carbin")
    c = cb.parse(d)
    assert cb.build(c) == d, "carbin does not round-trip"
    return f"{len(c.sections)} sections, rebuild identical"


@step("edit a car model: re-encode, OBJ round trip, draw lists, checks")
def _():
    import numpy as np
    import carbin_geom as cg
    import fh1_carbin as cb
    import xlzx
    z = state["game"].zip("media/cars/ALF_8C_08.zip")
    d = xlzx.read_member(z, "ALF_8C_08.carbin")
    c = cb.parse(d)
    si = [s.name for s in c.sections].index("bumperRa")
    sec = c.sections[si]
    before = cg.decode_pool(sec, cg.active_pool(sec))
    cg.transform_section(c, si, np.eye(4))
    err = float(np.abs(before.pos - cg.decode_pool(c.sections[si], cg.active_pool(c.sections[si])).pos).max())
    assert err < 0.001, f"identity re-encode moved vertices by {err} m"
    c2 = cb.parse(d)
    obj = cg.export_obj(c2, [si])
    parsed = cg.parse_obj(obj)
    _, a0, _ = cg.draw_lists(c2.header)
    cg.rebuild_section(c2, si, cg.pieces_from_obj(parsed["bumperRa"], 1, cg.donor_swatches(c2)), cg.SHModel(c2), seed=3)
    _, a1, _ = cg.draw_lists(c2.header)
    assert [len(a) for a in a0] == [len(a) for a in a1], "draw lists changed size"
    assert not cg.validate(c2), cg.validate(c2)
    t0 = sum(len(cg.sub_triangles(s, cg.active_pool(sec).count)) for s in sec.subs)
    t1 = sum(len(cg.sub_triangles(s, cg.active_pool(c2.sections[si]).count)) for s in c2.sections[si].subs)
    assert t0 == t1, f"triangle count {t0} -> {t1}"
    return f"bumperRa: re-encode error {err * 1000:.3f} mm, OBJ round trip keeps {t1} triangles and the draw lists"

@step("save one member back into a game zip (everything else stays byte-identical)")
def _():
    import io
    import zipfile
    import carbin_library as cl
    game = state["game"]
    raw = game.sv.read("media/cars/ALF_8C_08.zip")
    with tempfile.TemporaryDirectory() as tmp:
        zp = Path(tmp) / "ALF_8C_08.zip"
        zp.write_bytes(raw)
        member = "ALF_8C_08_lod0.carbin"
        import xlzx
        new = b"edited" + xlzx.read_member(zipfile.ZipFile(io.BytesIO(raw)), member)[6:]
        cl.replace_zip_member(zp, member, new)
        a, b = zipfile.ZipFile(io.BytesIO(raw)), zipfile.ZipFile(zp)
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()], "member order changed"
        for x, y in zip(a.infolist(), b.infolist()):
            if x.filename != member:
                assert (x.CRC, x.compress_size, x.compress_type, x.extra) == (y.CRC, y.compress_size, y.compress_type, y.extra), x.filename
        assert b.read(member) == new
        b.close()
        assert (Path(str(zp) + ".bak")).read_bytes() == raw
    return f"{len(a.infolist())} members, only {member} changed; Xbox LZX members and their extra fields untouched; backup made"

@step("decode and re-encode a texture (.xds)")
def _():
    import numpy as np
    import xds
    import xlzx
    z = state["game"].zip("media/ui/Textures.zip")
    d = xlzx.read_member(z, "UI4/Common/Reticle_Burst.xds")
    px = xds.decode(d)
    d2 = xds.encode_like(d, px)
    assert len(d2) == len(d) and np.abs(xds.decode(d2).astype(int) - px.astype(int)).max() <= 1
    return f"{px.shape[1]}x{px.shape[0]}, round trip within 1 level"


@step("replace a texture with all its mip levels (size kept, levels follow the new image)")
def _():
    import numpy as np
    import xds
    import xlzx
    z = state["game"].zip("media/cars/ALF_8C_08.zip")
    d = xlzx.read_member(z, "lights.xds")
    lay = xds.mip_layout(xds.info(d))
    assert lay and len(lay) >= 5, "mip layout not understood"
    img = xds.decode(d)
    img[..., :3] = 255 - img[..., :3]
    new = xds.encode_like(d, img)
    assert len(new) == len(d)
    worst = 99.0
    for lv in lay[:-1]:
        want = xds.downsample(img, lv["w"], lv["h"]).astype(float)
        got = xds.decode(new, lv["level"]).astype(float)
        mse = ((want[..., :3] - got[..., :3]) ** 2).mean()
        worst = min(worst, 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse))
    assert worst > 20, f"a mip level does not follow the new image ({worst:.1f} dB)"
    return f"{len(lay)} levels, size kept, worst regenerated level {worst:.0f} dB against the new image"

@step("simplify a model for the lower LODs")
def _():
    import numpy as np
    import carbin_geom as cg
    n, m = 120, 90
    u, v = np.meshgrid(np.linspace(0, 2 * np.pi, n, endpoint=False), np.linspace(0.05, np.pi - 0.05, m))
    P = np.stack([np.cos(u) * np.sin(v), np.cos(v), np.sin(u) * np.sin(v)], -1).reshape(-1, 3)
    idx = np.arange(n * m).reshape(m, n)
    T = np.vstack([np.vstack([np.stack([idx[j], idx[j + 1], np.roll(idx[j], -1)], 1), np.stack([np.roll(idx[j], -1), idx[j + 1], np.roll(idx[j + 1], -1)], 1)]) for j in range(m - 1)])
    UV = np.stack([u.reshape(-1) / (2 * np.pi), v.reshape(-1) / np.pi], 1)
    piece = cg.Piece("chrome", 1, P, P.copy(), UV, UV, T)
    out = cg.lod_pieces([piece], [1, 2, 3], {1: 1.0, 2: 0.5, 3: 0.2})
    counts = {q.lod: len(q.tris) for q in out}
    assert counts[1] == len(T) and 0.35 * len(T) < counts[2] < 0.6 * len(T) and counts[3] < 0.3 * len(T), counts
    return f"{len(T)} triangles -> {counts}"


@step("copy a car into a new DLC car (ids, rows, files, merge check)")
def _():
    import clone_car as cc
    import fh1_sources as src
    res = cc.clone_car(state["game"], "ALF_8C_08", "ALF_8C_Z1", 9990, [], log=lambda *_: None)
    rep = res["report"]
    assert not rep["problems"], rep["problems"]
    con = src.open_db(res["merge_blob"])
    car = con.execute("select Id, MediaName from Data_Car").fetchall()
    assert len(car) == 1 and car[0][1] == "ALF_8C_Z1" and car[0][0] < 32768, car
    for t, col in (("List_UpgradeEngine", "EngineID"), ("List_UpgradeDrivetrain", "DrivetrainID"), ("List_UpgradeCarBody", "CarBodyID")):
        assert con.execute(f'select count(*) from {t} where Ordinal=?', (car[0][0],)).fetchone()[0] >= 1
    return f"car id {car[0][0]}, {sum(rep['tables'].values())} rows in {len(rep['tables'])} tables, {rep['files']} files, no key collisions"

@step("name a copied car and change its body numbers (plain-text name, wheelbase)")
def _():
    import clone_car as cc
    import fh1_sources as src
    res = cc.clone_car(state["game"], "ALF_8C_08", "ALF_8C_Z2", 9991, [], log=lambda *_: None, display_name="Self Test 8C", body_overrides={"ModelWheelbase": 2.8})
    assert not res["report"]["problems"], res["report"]["problems"]
    con = src.open_db(res["merge_blob"])
    assert con.execute("select DisplayName, ModelShort from Data_Car").fetchone() == ("Self Test 8C", "Self Test 8C")
    assert con.execute("select ModelWheelbase from Data_CarBody").fetchone()[0] == 2.8
    return "name and wheelbase written into the new rows"


@step("read, edit and rebuild a car's physics (collision spheres, hull) on every base car")
def _():
    import fh1_sources as src
    import physdef
    game = state["game"]
    n = hulls = 0
    for path in game.car_media_names():
        z = game.zip(path if path.startswith("media/") else f"media/cars/{path}.zip")
        low = {m.lower(): m for m in z.namelist()}
        if "physicsdefinition.bin" not in low:
            continue
        raw = src.zread(z, low["physicsdefinition.bin"])
        pd = physdef.parse(raw)
        assert pd.build() == raw, path                      # an untouched car rebuilds byte for byte
        n += 1
        hulls += bool(pd.hull)
    z = game.zip("media/cars/ALF_8C_08.zip")
    raw = src.zread(z, "physicsdefinition.bin")
    pd = physdef.parse(raw)
    r0, c0 = pd.spheres[0].r, len(pd.spheres)
    pd.spheres[0].r = r0 * 2
    pd.spheres.append(physdef.Sphere(2, 0.0, 0.5, 0.0, 0.3))
    new = physdef.parse(pd.build())
    assert len(new.spheres) == c0 + 1 and abs(new.spheres[0].r - 2 * r0) < 1e-6 and len(new.hull) == len(pd.hull)
    return f"{n} cars rebuild identically ({hulls} with a hull); an edit and an added sphere read back"


@step("find free room in a car's body atlas")
def _():
    import fh1_carbin as fc
    import fh1_sources as src
    import atlas_paint as ap
    import numpy as np
    z = state["game"].zip("media/cars/ALF_8C_08.zip")
    masks = ap.part_masks([fc.parse(src.zread(z, "ALF_8C_08.carbin"))], 128)
    assert len(masks) > 10
    used = ap.used_mask(masks, frozenset(), 128)
    free_all = ap.largest_free_rect(np.zeros((128, 128), bool), 3, 1.0)
    assert free_all and free_all[2] - free_all[0] > 0.9                 # an empty atlas gives a (nearly) full square
    freed = ap.used_mask(masks, set(masks), 128)
    assert freed.sum() == 0 and used.sum() > 0
    return f"{len(masks)} parts; {used.mean() * 100:.0f}% of the atlas is read by parts"


@step("edit a skeleton bone and an animation's length (Granny)")
def _():
    import fh1_sources as src
    import gr2
    import gr2_skeleton as sk
    import xlzx
    import numpy as np
    z = state["game"].zip("media/cars/LAM_ReventonRoadster_09.zip")
    raw = xlzx.read_member(z, "car_skeleton_LOD05.gr2")
    f = gr2.read(raw)
    sk.set_bone(f, 1, rotate_deg=[0, 0, 30])
    out = gr2.read(f.build())
    bs = sk.skeleton_of(out)["Bones"][1]
    W = sk._worlds(bs)
    err = max(float(np.abs(np.linalg.inv(W[i]) - np.array(bs[i]["InverseWorldTransform"][1]).reshape(4, 4)).max()) for i in range(len(bs)))
    assert err < 1e-3, err
    fa = gr2.read(xlzx.read_member(z, "car_animations_LOD05.gr2"))
    d0 = sk.animations(fa)[0]["duration"]
    sk.scale_time(fa, 2.0)
    assert abs(sk.animations(gr2.read(fa.build()))[0]["duration"] - 2 * d0) < 1e-3
    return f"bone turned, inverse matrices consistent ({err:.1e}); animation {d0:.2f} s -> {2 * d0:.2f} s"


@step("the editor server refuses requests without its secret, from other sites and for other host names")
def _():
    import http.client
    import os
    import socket
    import subprocess
    import time
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    token = "selftest-token-0123456789"
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "carbin_editor" / "server.py"), "--port", str(port), "--no-browser"],
                            env=dict(os.environ, FH1_EDITOR_TOKEN=token), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def req(method, path, headers=None, body=None, host=None, declared=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{port}")
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if body is not None or declared is not None:
            c.putheader("Content-Length", str(declared if declared is not None else len(body)))
        c.endheaders(body if declared is None else None)
        r = c.getresponse()
        r.read()
        return r.status, r.getheader("Set-Cookie") or ""
    try:
        for _ in range(80):
            try:
                req("GET", "/")
                break
            except OSError:
                time.sleep(0.25)
        ck = {"Cookie": f"fh1ed={token}"}
        assert req("GET", "/")[0] == 401 and req("GET", "/api/status")[0] == 401, "no secret must be refused"
        assert req("POST", "/api/save_path", body=b'{"path": "x.carbin"}')[0] == 401, "writes need the secret"
        assert req("GET", "/?token=wrong")[0] == 401
        code, cookie = req("GET", f"/?token={token}")
        assert code == 302 and "SameSite=Strict" in cookie and "HttpOnly" in cookie, (code, cookie)
        assert req("GET", "/api/status", ck)[0] == 200
        assert req("GET", "/api/status", ck, host="evil.example:%d" % port)[0] == 403, "foreign Host header (DNS rebinding)"
        assert req("POST", "/api/library/config", {**ck, "Origin": "http://evil.example"}, b"{}")[0] == 403, "foreign Origin"
        assert req("POST", "/api/open", ck, declared=10 ** 10)[0] == 413, "oversized body"
        assert req("GET", "/vendor/../server.py", ck)[0] == 404 and req("GET", "/vendor/%2e%2e/server.py", ck)[0] == 404, "path escape"
        assert req("GET", "/server.py", ck)[0] == 404, "source files are not served"
        assert req("GET", "/vendor/three/build/three.module.js", ck)[0] == 200
    finally:
        proc.terminate()
    return "no secret, wrong secret, foreign Host, foreign Origin, oversized body and path escapes are all refused"


@step("build a throwaway DLC package, verify it as a folder and as an STFS file")
def _():
    import sqlite3
    import dlc_builder as b
    import fh1_sources as src
    import lsb2
    offer = 9999
    db = b.new_merge_db(state["gamedb"], ["ContentOffers"])
    b.insert(db, "ContentOffers", b.content_offer_row(offer, "_&1"))
    blob = b.finish_merge_db(db)
    files = b.build_files(offer, merge_db=blob, car_zips={"a": {"media/cars/TEST_Car/readme.txt": b"test"}},
                          string_tables={"Data_Car": {"EN": lsb2.write([lsb2.Entry(1, "Test", "IDS_Test", 1)])}})
    with tempfile.TemporaryDirectory() as tmp:
        folder = b.write_xenia_folder(files, tmp, offer, "Self test")
        problems = b.verify_folder(folder)
        assert not problems, problems
        p = b.write_stfs(files, Path(tmp) / "console" / b.package_name(offer), "Self test")
        assert p.stat().st_size > 0
        probs = src.DLCPackage(p).merges()
        assert len(probs) == 1
        import db_check
        con = sqlite3.connect(":memory:")
        existing = {"base": src.open_db(state["gamedb"])}
        new = {"throwaway": src.open_db(blob)}
        assert not db_check.collisions(new, existing), "unexpected key collision"
    return f"package {b.package_name(offer)}: folder and STFS verified, no key collisions"


bad = [r for r in results if not r[1]]
print(f"\n{len(results) - len(bad)} of {len(results)} steps passed")
sys.exit(1 if bad else 0)
