"""Build the Windows program: a folder with "ForzaPartTool.exe" that runs without Python installed.

    python -m venv env && env\Scripts\pip install pyinstaller numpy
    env\Scripts\python packaging\build_windows.py [--version 1.0.0] [--out build]

Result: <out>/ForzaPartTool-<version>-windows.zip (extract it anywhere, double-click "ForzaPartTool.exe").
The program is the same server.py as `python toolkit/carbin_editor/server.py`, with its files bundled (including three.js, so it needs no internet).
"""
import argparse
import hashlib
import importlib.metadata
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TK = ROOT / "toolkit"
ED = TK / "carbin_editor"
MODULES = ["carbin_geom", "carbin_library", "atlas_paint", "clone_car", "dlc_builder", "fh1_sources", "lsb2", "physdef", "gr2", "gr2_curves",
           "gr2_skeleton", "xds", "xlzx", "fh1_carbin", "stfs", "fm4_carbin", "carbin_parts", "modelbin", "modelbin_writer", "part_actions", "part_look", "editor_desktop", "webview"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="2.3.1")
    ap.add_argument("--out", default=str(ROOT / "build"))
    a = ap.parse_args()
    import PyInstaller.__main__ as pi
    out = Path(a.out)
    work, dist = out / "work", out / "dist"
    if dist.exists():
        raise SystemExit(f"Output already exists: {dist}. Choose a new --out folder.")
    sep = ";" if sys.platform == "win32" else ":"
    args = [str(ED / "server.py"), "--name", "ForzaPartTool", "--onedir", "--windowed", "--noconfirm", "--clean",
            "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work), "--paths", str(TK),
            "--exclude-module", "matplotlib", "--exclude-module", "scipy", "--exclude-module", "pytest", "--exclude-module", "PIL"]
    icon = ROOT / "packaging" / "assets" / "icon.ico"                       # optional: put your own icon here and the exe uses it
    if icon.is_file():
        args += ["--icon", str(icon)]
    for m in MODULES:
        args += ["--hidden-import", m]
    for f in ("index.html", "app.js", "library_actions.mjs", "viewer_materials.mjs", "part_tools.mjs", "part_gizmo.mjs", "gizmo_snap.mjs", "editor_workflow.js", "startup_diagnostics.js", "style.css"):
        args += ["--add-data", f"{ED / f}{sep}."]
    args += ["--add-data", f"{ED / 'vendor'}{sep}vendor"]
    if (ED / "logo.png").is_file():                                          # optional logo shown in the header
        args += ["--add-data", f"{ED / 'logo.png'}{sep}."]
    pi.run(args)
    app = dist / "ForzaPartTool"
    (app / "README.txt").write_text(README.replace("{version}", a.version), encoding="utf-8")
    for filename in ('START HERE - TESTING.txt', 'BUG REPORT TEMPLATE.txt'):
        source = ROOT / 'packaging' / filename
        if source.exists():
            shutil.copyfile(source, app / filename)
    for f in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"):
        shutil.copy(ROOT / f, app / f)
    licenses = app / 'Third-party licenses'
    licenses.mkdir()
    for name in ('numpy', 'pywebview', 'pythonnet', 'clr_loader', 'cffi', 'pycparser', 'proxy_tools', 'bottle', 'typing_extensions'):
        distribution = importlib.metadata.distribution(name)
        for member in distribution.files or []:
            if member.name.lower().startswith(('license', 'copying')) and distribution.locate_file(member).is_file():
                target = licenses / name / str(member).replace('../', '').replace('..\\', '')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(member), target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, licenses / 'Python-LICENSE.txt')
    zp = out / f"ForzaPartTool-{a.version}-windows.zip"
    zp.unlink(missing_ok=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(app.rglob("*")):
            if p.is_file():
                z.write(p, Path("ForzaPartTool") / p.relative_to(app))
    h = hashlib.sha256(zp.read_bytes()).hexdigest()
    (out / (zp.name + ".sha256")).write_text(f"{h}  {zp.name}\n")
    print(f"\n{zp}  {zp.stat().st_size / 2**20:.1f} MB\nSHA-256 {h}")


README = """ForzaPartTool {version}
===========================

Double-click ForzaPartTool.exe. The editor opens in its own desktop window.
No Python installation or external browser is needed. Windows WebView2 Runtime is required.

1. If no valid game path is saved, Settings opens automatically.
   Browse to your own FH1 game (extracted folder, GoD container/folder, or ISO).
   Add your DLC content folder, usually content\\0000000000000000\\4D5309C9\\00000002.
   Save paths & load cars. Paths are remembered on this computer.
2. Open Cars. Search the base game and installed DLC together.
   Right-click a car and choose Import, or double-click it.
   Select a specific model for main/driving, LOD0, cockpit, etc.
3. Select a part in the list or viewport. Use Move, rotate, scale and Apply.
   Material names, visibility, lights and other tools remain available. Undo/redo edits.
4. Copy a selected part (Ctrl+C), import a destination car, and open Copy parts (Ctrl+V).
   Or right-click a donor car in Cars and choose Copy parts from this car.
   This donor browser does not replace your current car or its edits.
   Choose a donor part, Copy, map its materials, and Paste into current car.
   A pasted part is independently selectable and transformable.
   Geometry uses destination materials/colour swatches. Donor textures, skeleton/animations
   and collision are not transferred by Copy parts.
5. Add your own FBX, GLB/glTF or OBJ using Custom model. Position with the gizmo,
   map materials, select LODs and Apply. External glTF resources must be embedded;
   prefer GLB for a self-contained import.
   Right-click a part/imported model for Copy coordinates / Paste coordinates.
   XYZ position is copied; rotation and scale stay as they are. Undo/redo is supported.
   Merge into... combines a part into an existing named part/upgrade slot, including
   an empty slot. The source slot is kept empty so header references remain intact.
   Driving + photo model stages main and LOD0 together by default. Both files
   need matching named parts. Double-sided adds real reverse-facing triangles
   to every imported LOD, with inverted normals, so FH1 can draw both sides.
   New shop upgrades and automatic Assetto Corsa conversion are not included.
   This is a preview: validate exported edits in Xenia or on hardware before sharing.
6. Apply to existing car updates the existing ZIP, preserving the car ID.
   All staged files and the current model are written in one atomic swap, with
   matching stripped companions regenerated. The original ZIP is backed up once.
   This needs an extracted game or DLC folder. GoD, ISO and STFS are read only.
   Export car ZIP writes a replacement ZIP to a chosen folder without changing
   your install. Use that ZIP in an extracted game/package copy.
   Download .carbin saves only the open model.
7. Export as new DLC car creates a NEW car with its own IDs and files.
   Stage additional edited model files before changing models if needed; the open file
   is included automatically. Main and LOD0 are separate files: edit/stage both to cover
   driving and menus/photo mode. Choose Xenia folder or console STFS output.
   Output is separate from your installed game. Follow the displayed installation paths.

Close the app window to stop its backend.
Settings: %APPDATA%\\FH1CarbinEditor\\settings.json
Startup errors: %APPDATA%\\FH1CarbinEditor\\startup-error.log

Use your own game and DLC files. Nothing from the game is included.
Back up your save before testing a newly exported car. Do not remove a DLC while
its cars remain in your save. Close Xenia before replacing installed content.
A base game's original file wins over the same path in DLC, which is why DLC export
creates a separately named car. In-game compatibility must be checked in Xenia/hardware.

Source: https://github.com/ryry2515/ForzaPartTool
Not affiliated with Microsoft, Turn 10 or Playground Games. MIT license.
"""

if __name__ == "__main__":
    main()
