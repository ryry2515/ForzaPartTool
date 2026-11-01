# FH1 Carbin Editor

A `.carbin` file is a Forza Horizon 1 car model: the body, doors, bumpers, glass, interior and so on, as named parts made of triangles, plus the car's light points and exhaust flame points.
This editor lets you look at a car in 3D, move and reshape its parts, **drop in your own model (FBX, GLB / glTF or OBJ), drag it into place and add it onto the car**, and save the result either into a **DLC** package or into an **extracted copy of the game**.

It is a local web app: a Python backend (standard library plus `numpy`) and a three.js viewer in your browser. Everything stays on your computer; the server only listens on `127.0.0.1`.

```
python toolkit/carbin_editor/server.py                                                    # then open the printed address
python toolkit/carbin_editor/server.py --game "<game folder>" --dlc "<...\4D5309C9\00000002>"
python toolkit/carbin_editor/server.py Car.carbin                                         # open one file at start
```

three.js and its model loaders are bundled in `vendor/three/` (MIT, unmodified files from npm), so the editor needs no internet and makes no request to any other site. Your model data never leaves your computer.

**The address matters.** Each run the program prints an address ending in `?token=...`; open exactly that one (the browser opens it for you unless you pass `--no-browser`). The secret keeps other web pages from using the editor; it is described, with the rest of the protections, in `SECURITY.md` at the root of the repository. To run scripts against the API, set the environment variable `FH1_EDITOR_TOKEN` before starting the server and send it as the cookie `fh1ed`.

## The two places cars live

| location | what it is | read | save back |
|---|---|---|---|
| **Game** | the base game's cars (`media/cars/<Car>.zip`) | Games on Demand folder, `.iso` or extracted folder | **only an extracted game folder**: the editor replaces the car's file inside that zip (backup `.bak` made once) |
| **DLC** | cars inside installed or built packages (`Media/DLCZips/*.zip`, files `media/cars/<Media>/...`) | package folders and console (STFS) packages | package **folders** (the Xenia content folder `4D5309C9\00000002`, or a built pack) |

Use the **Open / Save** tab: set the game folder and the DLC folder, pick a location, pick a car, pick a file (`<Car>.carbin` is the main model used while driving, `_lod0` the close-up used in photo mode and menus, `_cockpit` the cockpit view).
The **Save into game / Save into DLC** button writes the edited file back where it came from. Only that one file inside the zip changes: every other file is copied byte for byte, in the same order, with its Xbox compression marker intact.
Nothing is written while Xenia is running (the editor checks). "Download .carbin" and "Save to path" save a copy anywhere else.

**Important:** the game always uses its own files before a DLC's files of the same path. A change to a **base-game car** therefore only takes effect when saved **into the game**. A DLC can carry changes only for cars that the DLC itself adds. (To put a changed copy of a game car into a DLC, use **Save as new car**: it gives the copy its own database rows and ids.)

## Adding your own model

1. Open a car file, click the part you want to add to (for example `body`).
2. Drop an FBX / GLB / OBJ file on the page (or **Add model** tab, **Choose a model file**). It appears in orange with a move / rotate / scale gizmo (buttons, or keys W / E / R).
3. Drag it into place. Number fields give exact position, rotation and scale; **Set size** scales the longest side to metres; **Turn around** and **Z-up to Y-up** fix the orientation. The car is about 4.5 m long and its front points to -z (yellow arrow). Most FBX files are in centimetres: the editor guesses and scales by 0.01.
4. Pick the **part** it belongs to, **add onto the part** (keeps everything the part has) or **replace** its geometry, which **LODs** get it, and a car **material** for each material in your model (`body`, `chrome`, `black`, glass names...).
5. **Add to car.** It is added to the part and to the game's draw lists for the LODs you chose. Do the same in the `_lod0` and `_cockpit` files if the model should show in photo mode and the cockpit view.

By default an added model has no texture of its own: it takes the flat colour of the material you pick (the car's atlas textures hold the colours; section 7 of the guide). To give it a picture, use the texture picker (see "Textures, files and strings" below).

## Save as a new car (and what has been seen in the game)

The Open / Save tab can copy the open car into a **new car inside a DLC package** (`toolkit/clone_car.py`): new car id, new body, engine and drivetrain ids, copies of every per-car database row (47 tables for a base car), the car's files under a new name (same length as the original), the Autoshow thumbnail and the car-model audio file. Your edited files replace the originals: edit a file, press **Stage this file**, open the next file, and so on; the file that is open is included automatically.
- **Export as DLC:** the button writes the package as a **Xenia folder** (package folder plus its `.header`, checked offline) and/or a **console package** (STFS, for a modded 360, read back and verified after writing); tick the formats you want.
- **Name in game:** type a name (and a short name); it is written as plain text into `Data_Car.DisplayName` / `ModelShort`, so no string table is needed and nothing clashes with other packs. Empty = the donor's name.
- **Body numbers:** the Physics tab lets you set wheelbase, track widths, ride heights and the bounding box for the new car (`Data_CarBody`); the game places the wheels from these numbers.

**Seen in the game (Xenia):** a copy of the Alfa 8C booted, appeared in the Autoshow, was bought and drove; the **plain-text name showed**; a real model (the Alfa race wing) added onto another car showed in the correct place and orientation; cubes added at the roof, rear and side showed there; a **texture painted into the atlas showed on an added cube**; **+0.15 m of wheelbase moved the wheels** accordingly; and with every collision sphere made 2.5 times bigger **the car stopped too early against a barrier**, so the edited `physicsdefinition.bin` is what the game uses. Built and checked offline only: a copy made from a car inside a DLC, the `_lod0` and `_cockpit` copies of an edit, automatic LODs of a heavy model, copies with edited lights, adding or removing collision spheres, skeleton and animation edits.

## Textures, files and strings

- **Textured view:** the texture selector in the viewer paints the car with its own baked atlases (`nodamage`, `lights`, `interior`, `damage`), chosen automatically per part or by hand. Body paint is shown as a red tint because the game applies paint at run time.
- **Texture for an added model:** in the Add model tab choose an image. Every exterior material of a car reads one body atlas (`nodamage`, with `damage` and the `_LOD0` sizes; the game's shaders, guide section 7), and that atlas is about 95% in use: parts' own areas, plus lamp, glass and logo pictures. The picker therefore shows the atlas with what is still read in red, lists the parts with the area **only** they read, and you tick the parts whose texture you will give up (they look wrong afterwards). **Auto place** puts the picture into the largest free rectangle of the image's shape (all mip levels, both atlas sizes) and the model's UVs point there. You can also drag a rectangle yourself. Expect small areas: with nothing given up a base car has about 1 to 3% free, in slivers; giving up a few parts frees a few percent more (a 40 by 40 texel patch in the test). For a car in a DLC the atlas files are rewritten in place (backup made); for a game car they go into the new car you build.
- **Automatic LODs:** the lowest ticked LOD gets the full model, higher LODs get simplified copies sized like the part's own LODs.
- **Archive tab:** every file in the open car's archive. Textures show a preview, their size and mip count, and can be downloaded as PNG or **replaced from any image** (resized to the texture, every mip level regenerated except a tiny tail level the game packs differently). Text files (`CarAttribs.xml`, `ShaderSettings.xml`, ...) open in an editor and save back. Any other file can be downloaded or replaced.
- **Strings tab:** browse the game's string tables by language, table and search (read only).
- Overlays: normals of the selected part, all part bounding boxes, Hide / Isolate / Show all (Ctrl+H, Ctrl+I, Ctrl+Shift+H).

## Compared with ForzaTech Studio (the FH6 tool)

| ForzaTech Studio | this editor |
|---|---|
| carbin editor: parts, models, LOD flags | parts, sub-meshes, LODs, draw lists |
| 3D viewer: textures, LODs, wireframe, normals, boxes, hide/isolate, OBJ export, undo | all of these |
| move / rotate / scale parts with undo | yes |
| import models, modelbin creator | drop FBX / GLB / OBJ and add or replace |
| lights editor | light records and exhaust points |
| swatchbin / texture viewer, import, export | Archive tab (xds preview, PNG export, replace with mips) |
| string tables | Strings tab (read only) |
| car scene XML editor | Archive tab text editor (`CarAttribs.xml`, ...) |
| zip viewer, create zip | Archive tab (browse, replace); packages via `dlc_builder.py` |
| conversion tool between games | **not included** (the FM4-to-FH1 converter is separate and private) |
| physics definition | **Physics tab**: collision spheres and hull drawn on the car, edit / add / remove spheres, scale and move, fit to your edits, body numbers; saves `physicsdefinition.bin` and `MAXData.xml` |
| skeleton and animation | **Skeleton tab**: bones drawn on the car, move and turn a bone, make an animation longer or shorter; saves the `.gr2` files |
| fxb, bxml, materials | **not included** (materials live in the game's shader files; a material name picks a shader, guide section 7) |

## Everything else it does

| area | what you can do |
|---|---|
| view | orbit/pan/zoom, click a part, LOD checkboxes, wireframe, selection box, light and exhaust markers, filter, hide parts, see which sub-meshes the game does not draw |
| part | rename, move / rotate / scale (about the part pivot or the origin, optionally moving the pivot), mirror across an axis, one part or all shown parts |
| sub-meshes | change a material name or LOD, delete a sub-mesh; draw lists are kept consistent |
| draw lists | hide a part from the game or draw it again |
| OBJ | export one part, the shown LODs or everything; **replace a part's geometry from an OBJ** that you edited in Blender (group names `<part>_<sub>_L<lod>` bring each LOD back) |
| lights | edit, add, copy and remove the 32-byte light records: position, direction, role bytes, colour, glow size |
| exhausts | move emitter positions (count and side flags are read-only: changing them crashed the game) |
| file | undo and redo (20 steps), checks before every save |

## How geometry is written

(Encodings come from the game's own cars; see guide section 6.) Positions are re-encoded against new bounds around the part pivot; normals and tangent frames from your normals and UVs; uv0 (the atlas) uses a flat swatch texel for materials the car already draws flat, and your UVs otherwise; uv1 uses your UVs; the 4 baked-lighting bytes per vertex are fitted from the car's own vertices; triangles become strips. **Add** keeps the part's existing vertices untouched (only their position words are re-encoded when the bounds grow) and clears the part's per-vertex damage data, because its length must match the vertex count.

## What is tested and what is not

Tested offline on the base game's cars: vertex decode and re-encode within 0.07 mm; OBJ export and import round trip for every part of three carbins, with identical triangle counts per LOD, identical draw lists and clean checks; adding a model onto a part keeps the old vertices byte for byte; saving into an extracted game copy and into a DLC package folder keeps every other archive member identical (Xbox LZX members included); the editor's API, undo, error handling, the drop-in flow in a browser.
**Tested in the game (Xenia):** see "Save as a new car" above. **Not tested in the game:** everything listed there as built offline only. Test every change (guide section 17): one change per test, keep a booting build as the revert point, read `xenia.log`. The FBX and glTF loaders (three.js's) were driven in the browser with a binary FBX (centimetres) and a GLB; both loaded at the right size and added onto a part with clean checks.

## Limits

- A textured part costs texture area of other parts (see the texture bullet); which materials read the body atlas is decided by their shaders, not stored in the car files, so the part list is a good estimate, not a proof.
- Collision hull points can be moved and scaled, not added; the face tables of the hull are not edited. The 30 header floats of `physicsdefinition.bin` (two 3x3 tensors and eleven more) are shown in the guide but their meaning is not decoded, so mass-related changes are not made.
- Skeleton edits move or turn a bone; they do not add bones. Animation edits change the length only, not the curves.
- You cannot add or remove whole parts: the header refers to parts by position and the game finds some by name. Add geometry onto an existing part instead.
- Imported geometry is not welded across sub-meshes, so vertex counts grow (triangle counts stay the same).
- No skinning or damage data is created for new geometry: it is rigid (it still scratches and detaches with its part).
- Wheels, calipers and rotors are separate carbin files: open them like any other.
- Saving into a DLC changes the package folder on disk. If Xenia has an older copy installed, copy the changed package over again with Xenia closed.
