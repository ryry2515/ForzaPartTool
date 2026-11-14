# Priority files: replacing base-game files with a DLC

A DLC can carry files that the game reads **instead of** the base game's file of the same path. The mechanism is the number in the folder name:

```
Media/DLCZips/<offer>_pri_<N>/<path as the game sees it>
```

Higher numbers are searched first. `toolkit/priority_pack.py` builds such a package.

## What was measured

An emulator log of a normal run (33,419 file lookups, one `_pri_100001` folder and seven `_pri_99` car packs installed) shows this order for a file that exists nowhere in the base game:

1. the `_pri_100001` folder,
2. the base game,
3. the `_pri_99` folders, one after another.

For a file the base game **does** have, the log stops after step 1 or 2: the `_pri_99` folders are never asked. So:

- `_pri_99` (what car packs use) can only **add** files the base game lacks. It cannot replace a base file.
- `_pri_100001` is asked **before** the base game. It is the kind of folder that can replace a base file.

The same holds for files inside the base game's own zips (effects, camera, sky, spectators, game modes, tunable settings, physics, UI textures): the lookups for them are asked of the `_pri_100001` folder first. The executable's own side of it (the volume list is sorted by descending priority; the base game's zips are listed with priorities of 80 and 95 in `zipmanifest.xml`) was read from the disassembly.

## What is not known

- The log shows the game **asks** the high-priority folder first. It does not show that a replacement file is then used for every kind of file. Use the test below, and test each kind of file you rely on.
- The lowest number that still beats the base game. `100001` is known to be asked first, `99` is known to be asked later; use `100001` or more.
- Files the game reads before the DLC folders are scanned (for example the splash videos), streamed world data, and anything it looks up by an id instead of a path cannot be replaced this way.
- Car zips (`media/cars/<name>.zip`): the base game mounts its zips at start-up with the directory position stored in `zipmanifest.xml`. A replacement zip with another directory could be opened wrongly. Replacing the loose files of a car is the safer experiment, and even that was not tried.

## Make a package

```
python priority_pack.py --offer 9500 --name "My override" --out OUT --dir FILES
python priority_pack.py --offer 9500 --name "My override" --out OUT media/effects/Smoke.xml=my_smoke.xml
```

`FILES` must look like the game folder: `FILES/media/effects/Smoke.xml` replaces `media/effects/Smoke.xml`. Add `--game <extracted game folder>` and the tool warns about every path the game does not have (a typo makes a file nothing reads). `--stfs` also writes a console package. Pick an `--offer` number no other package uses.

## The one-file test

```
python priority_pack.py --offer 935710 --name "Priority A-button test" --out OUT --test-pack <extracted game folder>
```

This builds a pack with one visible change: the controller **A button** icon of the menus (`media/ui/textures/horizon/controller/A_Button.xds`) with its colours inverted. To try it:

1. Close Xenia.
2. Copy `OUT/xenia/4D5309C900935710` to `<xenia>/content/0000000000000000/4D5309C9/00000002/` and `OUT/xenia/Headers/00000002/4D5309C900935710.header` to `<xenia>/content/0000000000000000/4D5309C9/Headers/00000002/` (`toolkit/xenia_install.py` does this).
3. Start the game and open a menu that shows the A button prompt.
4. The icon in inverted colours means priority files are read for UI textures. The normal icon means the game did not take the replacement.
5. Remove both copies again (with Xenia closed) when you are done.

## In the tool

Not in the window yet; the command line is the only way for now. The window's existing exports are unchanged (they still use `_pri_99`).

The Windows program has the same tool built in. It is a windowed program, so start it from a command prompt in its folder:

```
ForzaPartTool.exe --priority-pack --help
ForzaPartTool.exe --priority-pack --offer 9500 --name "My override" --out OUT --dir FILES
```

From a source checkout the same thing is `python toolkit/carbin_editor/server.py --priority-pack ...` or `python toolkit/priority_pack.py ...`.
