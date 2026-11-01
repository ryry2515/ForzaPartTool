"""Build the Windows program: a folder with "FH1 Carbin Editor.exe" that runs without Python installed.

    python -m venv env && env\Scripts\pip install pyinstaller numpy
    env\Scripts\python packaging\build_windows.py [--version 1.0.0] [--out build]

Result: <out>/FH1-Carbin-Editor-<version>-windows.zip (extract it anywhere, double-click "FH1 Carbin Editor.exe").
The program is the same server.py as `python toolkit/carbin_editor/server.py`, with its files bundled (including three.js, so it needs no internet).
"""
import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TK = ROOT / "toolkit"
ED = TK / "carbin_editor"
MODULES = ["carbin_geom", "carbin_library", "atlas_paint", "clone_car", "dlc_builder", "fh1_sources", "lsb2", "physdef", "gr2", "gr2_curves",
           "gr2_skeleton", "xds", "xlzx", "fh1_carbin", "stfs", "fm4_carbin"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--out", default=str(ROOT / "build"))
    a = ap.parse_args()
    import PyInstaller.__main__ as pi
    out = Path(a.out)
    work, dist = out / "work", out / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    sep = ";" if sys.platform == "win32" else ":"
    args = [str(ED / "server.py"), "--name", "FH1 Carbin Editor", "--onedir", "--console", "--noconfirm", "--clean",
            "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work), "--paths", str(TK),
            "--exclude-module", "tkinter", "--exclude-module", "matplotlib", "--exclude-module", "scipy", "--exclude-module", "pytest", "--exclude-module", "PIL"]
    for m in MODULES:
        args += ["--hidden-import", m]
    for f in ("index.html", "app.js", "style.css"):
        args += ["--add-data", f"{ED / f}{sep}."]
    args += ["--add-data", f"{ED / 'vendor'}{sep}vendor"]
    pi.run(args)
    app = dist / "FH1 Carbin Editor"
    (app / "README.txt").write_text(README.replace("{version}", a.version), encoding="utf-8")
    for f in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"):
        shutil.copy(ROOT / f, app / f)
    zp = out / f"FH1-Carbin-Editor-{a.version}-windows.zip"
    zp.unlink(missing_ok=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(app.rglob("*")):
            if p.is_file():
                z.write(p, Path("FH1 Carbin Editor") / p.relative_to(app))
    h = hashlib.sha256(zp.read_bytes()).hexdigest()
    (out / (zp.name + ".sha256")).write_text(f"{h}  {zp.name}\n")
    print(f"\n{zp}  {zp.stat().st_size / 2**20:.1f} MB\nSHA-256 {h}")


README = """FH1 Carbin Editor {version}
===========================

A model editor for Forza Horizon 1 (Xbox 360 / Xenia) cars. Look at a car in 3D, move and reshape parts, add your own model
(FBX, GLB or OBJ), edit lights, collision physics, skeleton and animations, and export the result as a DLC package or
save it into an extracted copy of the game.

HOW TO USE
1. Extract this folder anywhere (Desktop is fine). Do not run it from inside the zip.
2. Double-click "FH1 Carbin Editor.exe". A black window opens (leave it open: it is the editor's engine) and your web browser
   opens the editor. If the browser does not open, copy the address shown in the black window (it ends with ?token=...).
3. In the editor open the "Open / Save" tab and fill in:
     game  = your Forza Horizon 1 game folder (the Games on Demand folder 4D5309C9, an .iso, or an extracted game folder)
     DLC   = your Xenia content folder for the game, ...\\content\\0000000000000000\\4D5309C9\\00000002 (optional)
   The editor remembers these folders. Pick a car and a file, edit, and use "Export as DLC" (or Save).
4. Close the black window to stop the editor.

GOOD TO KNOW
- You need your own copy of the game. Nothing from the game is included.
- Saving INTO the game needs an EXTRACTED game folder. A Games on Demand image is read only: use "Export as DLC" (a new car
  in its own package) instead. Close Xenia before the editor writes into an installed package.
- Back up your save before testing new content. Removing a DLC that holds a car you bought breaks the save.
- Windows SmartScreen or an antivirus may warn about a program it does not know ("unrecognised app"). The source code is public
  at https://github.com/ryry2515/fh1-modding-toolkit and this program is built from it with PyInstaller (packaging/build_windows.py);
  compare the SHA-256 on the release page with the zip you downloaded.
- Nothing leaves your computer: the editor only listens on 127.0.0.1 and makes no internet requests. See SECURITY.md.

Not affiliated with Microsoft, Turn 10 or Playground Games. MIT license (LICENSE); third-party notices in THIRD_PARTY_NOTICES.md.
"""

if __name__ == "__main__":
    main()