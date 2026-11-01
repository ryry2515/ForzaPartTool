# Forza Horizon 1 toolkit

Small, dependency-light Python modules for reading and writing the file formats of Forza Horizon 1 (Xbox 360), plus a command-line tool, a package builder and a self-test.
They exist so a modder or a script can inspect their own game and build mods without rewriting the format code.

Requirements: Python 3.10+, `numpy`, `Pillow` (textures only). On Windows the Xbox LZX decoder uses the system `Cabinet.dll` (about 100 times faster); set `FH_NO_FAST_LZX=1` to force the pure-Python decoder. Elsewhere the pure-Python decoder is used and is slow on big archives.

The toolkit never contains game data. You supply your own game files. Do not upload them anywhere.

## Start here

```
python toolkit/selftest.py "<path to the 4D5309C9 folder>"
```

All sixteen steps must say PASS (open game, read database, decompress an archive member, read a string table, round-trip a car model, edit a car model, save a member back into a game zip, round-trip a texture, replace a texture with its mips, simplify a model, copy a car, name a copy and change its body numbers, read and edit physics, find free atlas room, edit a skeleton and an animation, build and verify a throwaway package). If a step fails, fix that before building anything.

## The command-line tool

```
python toolkit/fh1tool.py GAME ls "audio/cars/Engines"          list top-level files matching a regex
python toolkit/fh1tool.py GAME zipls media/UI.zip "\.lua$"      list a zip's members
python toolkit/fh1tool.py GAME cat "media/UI.zip::ResPack.xml"  print a file (archive.zip::member for files inside a zip)
python toolkit/fh1tool.py GAME cat PATH --out file.bin          save a binary file
python toolkit/fh1tool.py GAME tables                           every database table with its row count
python toolkit/fh1tool.py GAME tables Data_Car                  columns of one table
python toolkit/fh1tool.py GAME db "select Id, MediaName from Data_Car where MakeID=12"
python toolkit/fh1tool.py GAME str en Data_Car 1420             string table lookup (language, table, optional key)
python toolkit/fh1tool.py GAME car ALF_8C_08                    one car: its archive members and its database rows
python toolkit/fh1tool.py carbin "media/cars/ALF_8C_08.zip::ALF_8C_08_lod0.carbin" --game GAME
python toolkit/fh1tool.py xds texture.xds --png out.png         describe and decode a texture
python toolkit/fh1tool.py packages "<xenia>/content/0000000000000000/4D5309C9/00000002"
python toolkit/fh1tool.py check GAME my_merge.slt --package <other pack>   primary-key collisions (a collision shows up as an endless load)
```

`GAME` is the Games on Demand folder, an extracted folder or an `.iso`. Everything in `fh1tool.py` is read-only.

## Modules

| module | what it does |
|---|---|
| `fh1_sources.py` | `open_fh1_game(path)` returns a game object: `.sv.entries` (files), `.sv.read(path)`, `.zip(path)` (a zip archive in the game), `.gamedb()` (the database bytes). `open_db(bytes)` opens a database. `DLCPackage(path)` reads an installed package folder or an STFS file. `list_packages(content_dir)`. |
| `xlzx.py` | Xbox 360 LZX (zip method 21). `read_member(zipfile, name)` returns the decompressed bytes of a member of a game zip. Decode only; stored and deflate zips also load in the game, so you never need to compress. |
| `lsb2.py` | `.str` string tables: `read(bytes)` returns entries (`key`, `text`, `name`, `ordinal`), `write(entries)` returns bytes. Round-trips byte for byte. |
| `fh1_carbin.py` | `.carbin` car models (Forza Horizon 1): `parse(bytes)`, `build(carbin)`, vertex pools, sub-meshes, index buffers. Lossless: unknown bytes are kept. |
| `fm4_carbin.py` | the same for Forza Motorsport 4 models (32-byte vertices). |
| `gr2.py`, `gr2_curves.py` | Granny 2 (`.gr2`) animation and skeleton files: `read(bytes)`, walk the type tree, edit values in place, `build()`. Includes an Oodle1 decoder (MPL-2.0 headers kept in the file). |
| `xds.py` | `.xds` textures: `info`, `decode` to RGBA, `encode_like(template, rgba)` for the same format and size, `solid`, `to_png`. Formats 8888, DXT1, DXT3 and DXT5 decode; 8888, DXT1 and DXT5 encode. Mip levels are read (`decode(data, level)`) and regenerated on encode where the layout is understood (`mip_layout`). `png_bytes` writes PNG without Pillow. |
| `stfs.py` | Xbox 360 console packages: `build_stfs(files, path, display_name=...)`, `STFSReader(path)` with `verify()`, `entries()`, `read()`. |
| `dlc_builder.py` | Assemble a package: `new_merge_db`, `insert`, `finish_merge_db`, `content_offer_row`, `build_files`, `write_xenia_folder`, `write_stfs`, `verify_folder`, `package_name`, `offer_id_text`. See the docstring for a worked example. |
| `carbin_geom.py` | Geometry-level editing of `.carbin` models: decode and re-encode vertices, transform or replace a part, OBJ export and import, draw lists, light records, exhaust emitters, `validate()`. Used by the editor below. |
| `carbin_library.py` | The two places carbins live (game and DLC packages): list cars, read a carbin, and save one back by replacing a single zip member (everything else byte-identical, backup made, refused while Xenia runs). |
| `clone_car.py` | Copy a car (from the game or a DLC) into a new car in a DLC package: new ids in every id space, cloned rows in every per-car table, renamed files, thumbnail, audio file; merge-checked offline. `python clone_car.py GAME DONOR NEWNAME OFFER OUT`. Seen working in the game for a base-game donor. |
| `physdef.py` | Read and edit a car's `physicsdefinition.bin` (collision spheres, hull, header) and `Physics/MAXData.xml`; rebuilds every base car byte for byte. Collision sphere size seen to change crashes in the game. |
| `gr2_skeleton.py` | List and edit the bones and animations of a car's Granny files (move or turn a bone and keep its inverse matrices right; stretch an animation). Offline tested. |
| `atlas_paint.py` | Paint an image into a chosen rectangle of a car atlas (mips regenerated) and map model UVs into it. |
| `carbin_editor/` | The carbin editor: a local web app (`python toolkit/carbin_editor/server.py`). See `carbin_editor/README.md`. |
| `db_check.py` | `collisions(new, existing)` for primary-key clashes between a merge database, the base game and other packages. |
| `xenia_install.py` | `install(package_folder, xenia_content_root, display)` copies a package into Xenia and writes the header. **Close Xenia first.** It refuses when files are in use. |

## What the toolkit does not do

- It does not convert a car from another game or from a 3D modelling program. The building blocks here read and write each file of a car.
- It does not run the game. Always test in Xenia or on hardware and read `xenia.log`.
- Texture mip levels after the base level are not regenerated by `xds.encode_like`.
- Bank files (`.fsb`) are not written.

## Safety

- Back up the save before testing a pack that adds content.
- Never install two packs that share a car or a database key.
- Do not install into Xenia while it is running.
