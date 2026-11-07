"""Extract a Forza Horizon 1 Games-on-Demand copy into a normal, writable folder.

    python extract_game.py GAME_FOLDER OUT_FOLDER

GAME_FOLDER is the folder that holds the GoD container (the `4D5309C9` folder with `00007000` inside, or the container file itself).
OUT_FOLDER receives default.xex and the media tree (about 7 GB). ForzaPartTool can then use it as a game folder and its
"Apply to existing car" button can change the real cars in it, which a read-only disc image does not allow.
Existing files are skipped when their size already matches, so an interrupted run can be repeated.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fh1_sources as fs  # noqa: E402


def extract(game_folder, out_folder, log=print):
    game = fs.open_fh1_game(Path(game_folder))
    out = Path(out_folder)
    entries = {p: size for p, _a, size in game.sv.entries.values()}
    total = sum(entries.values())
    done = skipped = 0
    started = time.time()
    for i, (path, size) in enumerate(sorted(entries.items()), 1):
        target = out / path
        if target.is_file() and target.stat().st_size == size:
            skipped += size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(game.sv.read(path))
        tmp.replace(target)
        done += size
        if i % 500 == 0:
            log(f"{i}/{len(entries)} files, {(done + skipped) / 1e9:.2f} of {total / 1e9:.2f} GB, {time.time() - started:.0f} s")
    log(f"done: {len(entries)} files, {total / 1e9:.2f} GB, new {done / 1e9:.2f} GB, already present {skipped / 1e9:.2f} GB")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    extract(sys.argv[1], sys.argv[2])
