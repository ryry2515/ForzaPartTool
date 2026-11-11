"""Random sweep over the stock cars for the sub-part edits (split out, move into another part, rename, recolour, glass/lamp tint).

    set FH1_GAME=<extracted FH1 folder>
    python tests/sweep_part_look.py [cars] [seed] [recolour]     (the last word: mostly colours and tints)

For every car it opens the main model, applies a few random edits (in the open model only, or in both the main and the _lod0 model), checks after EACH edit that the model still
validates as the stock file did, that it survives build + parse, and finally undoes everything and checks the model and the staged files are exactly as they were.
A ValueError from an edit is a refusal (counted, not a failure); any other exception or a changed file is a bug and is printed.
"""
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "toolkit"), str(HERE.parent / "toolkit" / "carbin_editor")]
import carbin_geom as cg  # noqa: E402
import carbin_library as cl  # noqa: E402
import fh1_carbin as fc  # noqa: E402
import part_look as pl  # noqa: E402
import server  # noqa: E402


def open_car(name):
    members = server.S.lib.members("game", name)
    main = next(m for m in members if m["member"].lower() == name.lower() + ".carbin")
    server.S.load(server.S.lib.read("game", main["archive"], main["member"]), main["member"], None,
                  {"source": "game", "archive": main["archive"], "member": main["member"]})


def groups(car):
    return [(i, sb.name) for i, s in enumerate(car.sections) for sb in s.subs if sb.lod == min(x.lod for x in s.subs)]


BIAS = None          # "recolour": mostly colours and tints (slower: every colour rewrites the atlases)


def random_edit(rng, allow_recolour):
    car = server.S.carbin
    pool = [(i, m) for i, m in dict.fromkeys(groups(car))]
    if not pool:
        return None
    si, material = rng.choice(pool)
    both = rng.random() < 0.6
    kind = rng.choice(["recolour", "recolour", "shader"] if BIAS == "recolour" else ["split", "move", "rename", "shader", "recolour" if allow_recolour else "split"])
    base = {"both_models": both}
    if kind == "split":
        return "split_material", dict(base, section=si, material=material, name=f"{car.sections[si].name}_{material}")
    if kind == "move":
        others = [i for i, s in enumerate(car.sections) if i != si and s.subs]
        return ("move_material", dict(base, section=si, material=material, target=rng.choice(others))) if others else None
    if kind == "rename":
        return "rename_material", dict(base, section=si, material=material, to=material + "_x")
    if kind == "shader":
        rows = server.look_rows()["rows"]
        if not rows:
            return None
        r = rng.choice(rows)
        values = {"value": rng.uniform(0, 2)} if r["param"] in pl.VALUE_PARAMS else {"r": rng.random(), "g": rng.random(), "b": rng.random(), "a": rng.random()}
        return "shader_set", {"material": r["material"], "param": r["param"], "values": values}
    return "recolour", dict(base, section=si, material=material, rgb=[rng.randrange(256) for _ in range(3)], scope=rng.choice(["part", "car"]))


def check(label, bugs):
    problems = server.new_problems(server.S.carbin, server.S.baseline)
    if problems:
        bugs.append(f"{label}: open model has new problems {problems[:3]}")
    try:
        fc.parse(fc.build(server.S.carbin))
    except Exception as e:  # noqa: BLE001
        bugs.append(f"{label}: build/parse failed {e!r}")
    for rel, raw in server.S.staged.items():
        if rel.lower().endswith(".carbin"):
            bad = server.new_problems(fc.parse(raw), server.original_problem_keys(rel))
            if bad:
                bugs.append(f"{label}: staged {rel} has new problems {bad[:3]}")


def main():
    global BIAS
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    BIAS = sys.argv[3] if len(sys.argv) > 3 else None
    game = os.environ.get("FH1_GAME")
    if not game:
        sys.exit("set FH1_GAME")
    server.S = server.Session()
    server.S.lib = cl.Library(game, [])
    server.S.cfg = {"game": game, "dlc": []}
    rng = random.Random(seed)
    cars = sorted(c for c in server.S.lib.cars("game") if not c.lower().startswith("nul_car"))
    done = refused = edits = 0
    bugs = []
    t0 = time.time()
    for name in rng.sample(cars, min(count, len(cars))):
        try:
            open_car(name)
        except Exception as e:  # noqa: BLE001 - a car that does not open at all is not this feature's business
            print(f"{name}: skipped ({e!r})")
            continue
        original = fc.build(server.S.carbin)
        recoloured = 0
        server.S.undo.clear()
        for _ in range(rng.randrange(2, 6)):
            edit = random_edit(rng, allow_recolour=recoloured < (3 if BIAS == "recolour" else 1))
            if edit is None:
                continue
            op, args = edit
            before = server.S.capture()
            try:
                server.OPS[op](args)
                fc.build(server.S.carbin)
            except ValueError:
                server.S.restore(before)
                refused += 1
                continue
            except Exception as e:  # noqa: BLE001
                server.S.restore(before)
                bugs.append(f"{name}: {op} {args} raised {e!r}")
                continue
            server.S.undo.append(before)
            edits += 1
            recoloured += op == "recolour"
            check(f"{name} after {op}", bugs)
        while server.S.undo:
            server.S.restore(server.S.undo.pop())
        if fc.build(server.S.carbin) != original or server.S.staged or server.S.looks:
            bugs.append(f"{name}: undo did not bring everything back (staged {sorted(server.S.staged)}, looks {len(server.S.looks)})")
        done += 1
        print(f"{name}: ok ({time.time() - t0:.0f}s)", flush=True)
    print(f"\n{done} cars, {edits} edits applied, {refused} refused; {len(bugs)} problems")
    for b in bugs:
        print("  BUG", b)
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
