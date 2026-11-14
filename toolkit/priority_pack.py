"""priority_pack.py - package files that REPLACE base-game files (a "priority" pack) for Forza Horizon 1.

A priority pack holds files under  Media/DLCZips/<offer>_pri_<N>/<path as the game sees it>.  The number after _pri_ is the search order:
higher numbers are searched first. In an emulator log, a folder named _pri_100001 was asked for a file before the base game, and the
_pri_99 folders of ordinary car packs were asked after the base game (and only when the base game does not have the file). So a file
placed in a _pri_100001 folder is the one the game can pick up instead of the base game's file of the same path.
Whether the game really takes it for a given kind of file has to be seen in the game; docs/priority-files.md lists what was and was not checked.

Usage (from the toolkit folder):
    python priority_pack.py --offer 9500 --name "My override" --out OUT --dir FILES        every file under FILES, by its path relative to FILES
    python priority_pack.py --offer 9500 --name "My override" --out OUT media/effects/Smoke.xml=my_smoke.xml
    python priority_pack.py --offer 9500 --name "A button test" --out OUT --test-pack GAME  one-file in-game test (see below)

Options:
    --priority N   folder priority, default 100001 (anything below 100000 is probably searched after the base game and cannot replace its files)
    --game GAME    an extracted game folder: warns about every path the game does not have (a typo makes a file that nothing reads)
    --stfs         also write a console package (STFS)
    --test-pack GAME   writes a pack with ONE visible change: the controller A-button icon of the menus (media/ui/textures/horizon/controller/A_Button.xds)
                   with its colours inverted. If the icon looks inverted in the menus, priority files are read for UI textures.

Nothing here installs anything. Close Xenia, then copy OUT/xenia/<package> to <xenia>/content/0000000000000000/4D5309C9/00000002/ and
OUT/xenia/Headers/00000002/<package>.header to <xenia>/content/0000000000000000/4D5309C9/Headers/00000002/ (xenia_install.py does this).
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import dlc_builder as b

TEST_PATH = "media/ui/textures/horizon/controller/A_Button.xds"
TEST_ZIP_MEMBER = ("media/ui/textures/Horizon.zip", "Controller/A_Button.xds")


def collect_dir(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by its path relative to `root`."""
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"not a folder: {root}")
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def collect_pairs(pairs: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(f"expected GAMEPATH=FILE, got {item!r}")
        game_path, local = item.split("=", 1)
        p = Path(local)
        if not p.is_file():
            raise SystemExit(f"no such file: {local}")
        out[game_path] = p.read_bytes()
    return out


def make_test_files(game_dir: Path) -> dict[str, bytes]:
    """The A-button icon with its colours inverted (alpha kept), taken from the game's own Horizon.zip."""
    import numpy as np
    import xds
    import xlzx
    zpath = Path(game_dir) / TEST_ZIP_MEMBER[0]
    if not zpath.is_file():
        raise SystemExit(f"{zpath} not found: --test-pack needs an extracted game folder")
    with zipfile.ZipFile(zpath) as zf:
        original = xlzx.read_member(zf, TEST_ZIP_MEMBER[1])
    rgba = xds.decode(original).copy()
    rgba[..., :3] = 255 - rgba[..., :3]
    return {TEST_PATH: xds.encode_like(original, rgba)}


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=prog, description="Package files that replace base-game files (priority files).")
    ap.add_argument("--offer", type=int, required=True, help="offer number of the package (pick one no other pack uses)")
    ap.add_argument("--name", required=True, help="display name of the package")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--priority", type=int, default=b.OVERRIDE_PRIORITY)
    ap.add_argument("--dir", help="folder whose files are the replacements, by path relative to it")
    ap.add_argument("--game", help="extracted game folder, used to check that each path exists in the game")
    ap.add_argument("--stfs", action="store_true", help="also write a console package")
    ap.add_argument("--test-pack", metavar="GAME", help="build the one-file A-button test from this extracted game folder")
    ap.add_argument("pairs", nargs="*", help="GAMEPATH=FILE")
    a = ap.parse_args(argv)

    files: dict[str, bytes] = {}
    if a.test_pack:
        files.update(make_test_files(Path(a.test_pack)))
    if a.dir:
        files.update(collect_dir(Path(a.dir)))
    files.update(collect_pairs(a.pairs))
    if not files:
        ap.error("no files: give --dir, GAMEPATH=FILE pairs or --test-pack")

    try:
        pkg = b.build_priority_files(a.offer, files, priority=a.priority)
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    warnings: list[str] = []
    if a.priority < 100000:
        warnings.append(f"priority {a.priority} is below 100000: such a folder was searched AFTER the base game in the log, so it cannot replace base-game files")
    check_dir = a.game or a.test_pack
    if check_dir:
        miss = b.missing_in_game(check_dir, [b.clean_game_path(p) for p in files])
        for m in miss:
            warnings.append(f"the game has no file {m}: this adds a file, it does not replace one")

    folder = b.write_xenia_folder(pkg, a.out, a.offer, a.name)
    problems = b.verify_folder(folder, allow_no_database=True)
    if a.stfs:
        b.write_stfs(pkg, Path(a.out) / "console" / b.package_name(a.offer), a.name)

    print(f"{len(files)} file(s) in {b.priority_folder(a.offer, a.priority)}")
    print(f"package folder: {folder}")
    for w in warnings:
        print("warning:", w)
    for p in problems:
        print("problem:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
