# FH1 modding toolkit and carbin editor

Open-source tools for modding **Forza Horizon 1** (Xbox 360, running on Xenia or a modded console), and a guide written so an AI assistant can use them.

- **Carbin editor** (`toolkit/carbin_editor/`): a local web app to look at a car model in 3D, move and reshape its parts, drop in your own model (FBX, GLB or OBJ) and place it with a gizmo, edit lights and exhausts, collision physics, skeleton and animations, and export the result as a DLC package or save it into an extracted copy of the game.
- **Toolkit** (`toolkit/`): Python libraries and command-line tools for the game's formats (containers, LZX archives, string tables, carbin models, `.xds` textures, Granny `.gr2`, STFS packages, the database) and for building DLC packages. Standard library plus `numpy`.
- **Guide** (`FH1_AI_MODDING_GUIDE.md`): the formats, rules and playbooks, with every fact labelled by how it was established (tested in the game, measured, inferred, unknown). Start at section 38.

## Quick start

You need your own copy of the game (a Games on Demand folder, an `.iso` or an extracted folder). Nothing from the game is included here.

```
pip install numpy
python toolkit/selftest.py "<your 4D5309C9 game folder>"            # checks that everything works on your files first
python toolkit/carbin_editor/server.py --game "<game folder>" --dlc "<...\4D5309C9\00000002>"
```

Open the address the editor prints (it carries a secret, see `SECURITY.md`). Details: `toolkit/carbin_editor/README.md` and `toolkit/README.md`.

## What has been seen working in the game

Adding a real model and cubes onto a car; painting a texture into a car atlas; copying a base-game car into a new DLC car that was bought and driven; a plain-text car name; changing the wheelbase; changing collision spheres (the car stops earlier at barriers). The editor's README lists what is only tested offline. Always test one change at a time and keep a backup of your save.

## Notes

- The guide mentions files under `research/` and `tools/`; that larger research archive is not part of this repository.
- Not affiliated with Microsoft, Turn 10 or Playground Games. See `THIRD_PARTY_NOTICES.md` for licenses of bundled code (three.js is bundled, so the editor needs no internet).
- License: MIT (`LICENSE`), except `toolkit/gr2.py` (MPL-2.0 header) and the bundled three.js files (MIT).