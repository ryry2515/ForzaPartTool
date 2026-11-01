"""
db_check.py - FH1 merges every DLC's patch database into the game database at boot; a primary key that is
already used (by the base game, another installed DLC or another car in the same batch) can make that
merge fail and the car(s) never appear. This lists every such collision.

  python db_check.py --fh1-game ..\\4D5309C9 [--package STFS_OR_FOLDER ...] MERGE.slt [MERGE.slt ...]
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

import fh1_sources as srcmod

SKIP = {"ContentOffers"}            # offers are keyed by offer id, checked separately by the offer numbers


def keys_of(con: sqlite3.Connection) -> dict[str, set]:
    out = {}
    for (t,) in con.execute("select name from sqlite_master where type='table'"):
        pk = [r[1] for r in con.execute(f'pragma table_info("{t}")') if r[5]]
        if pk and t not in SKIP:
            out[t] = {r for r in con.execute(f'select {",".join(chr(34) + c + chr(34) for c in pk)} from "{t}"')}
    return out


def collisions(new: dict[str, sqlite3.Connection], existing: dict[str, sqlite3.Connection]) -> list[str]:
    """new/existing: label -> connection. Checks new against existing and against each other."""
    owner = defaultdict(dict)                                  # table -> key -> label
    for label, con in existing.items():
        for t, ks in keys_of(con).items():
            for k in ks:
                owner[t].setdefault(k, label)
    problems = []
    for label, con in new.items():
        for t, ks in keys_of(con).items():
            for k in ks:
                if k in owner[t]:
                    problems.append(f"{label}: {t} key {k} already used by {owner[t][k]}")
                else:
                    owner[t][k] = label
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("merges", nargs="+")
    ap.add_argument("--fh1-game", required=True)
    ap.add_argument("--package", action="append", default=[], help="installed DLC to check against")
    a = ap.parse_args()
    existing = {"FH1 base game": srcmod.open_db(srcmod.open_fh1_game(Path(a.fh1_game)).gamedb)}
    for p in a.package:
        for i, blob in enumerate(srcmod.DLCPackage(Path(p)).merges()):
            existing[f"{Path(p).name}#{i}"] = srcmod.open_db(blob)
    new = {Path(m).name: sqlite3.connect(m) for m in a.merges}
    probs = collisions(new, existing)
    print("\n".join(probs) if probs else f"OK: no key collisions in {len(new)} database(s)")


if __name__ == "__main__":
    main()
