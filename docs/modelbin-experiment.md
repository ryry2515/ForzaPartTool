# Experiment: importing a modern Forza `.modelbin`

Status: **experimental**. Tried on one standalone part (a drag chute) and, earlier, on FH6 car parts. Nothing here has been run in the game.

## What it does

`toolkit/modelbin.py` reads the `.modelbin` models of modern Forza games (a "Grub" bundle, magic `burG`), picks the sharpest level of detail and returns the meshes in FH1 coordinates. The editor turns them into OBJ text and feeds that to the normal **Custom models** importer, so placing, material mapping, automatic LODs, merge/replace and saving all work as they do for an FBX, GLB or OBJ.

Drop a `.modelbin` onto the page, or choose it in **Custom models**.

## What was tried

`chute.modelbin` (682,304 bytes): 77 tags (60 `Mesh` records of version 1.12, 6 vertex buffers, 1 index buffer, 2 vertex layouts, 5 materials, a 47-bone skeleton). Its sharpest LOD has 5 meshes (the canister, a loop, a wire, a bracket and a small tab), 9,882 vertices and 15,124 triangles; the five LODs hold the same counts.

1. It decodes: the five meshes come out 0.241 x 0.756 x 0.68 m, and each mesh's size matches the bounding box stored in the file to three decimals.
2. Added to a Viper (`VIP_Viper_13`) as a new part with the editor's own add-model code, it validated with no new problems, rebuilt, and re-parsed.
3. In the viewer the chute sits on the rear deck, the right way round, with its cable hanging down.

## How it is converted

- Modern Forza has the car's front at +z and FH1 at -z (x stays), a mirror. Positions and normals get z negated and the triangle winding flips, so faces still point outwards.
- Positions use the mesh's scale and translate; normals and UVs are decoded from the vertex layout.
- Material names are kept as `usemtl` names; the importer's material mapping then picks the car's own materials.

## Limits (be honest about these)

- **No textures.** The file holds no images, so the part takes the car's material swatches.
- **Sharpest LOD only**; the editor makes the lower LODs itself.
- **Position:** the geometry is shifted by a constant (0, 0.1625, 0.2576) m from the bounding box stored in the file, on all five meshes of this file. Size and shape match, and the editor re-centres a model before you place it, so it does not matter for importing; where that offset comes from is not known.
- **Not tried:** other games' files, files with record versions other than 1.9 and 1.12, compressed tags (refused with a message), skinned or morph-target meshes (the skeleton is only used to place rigid parts).
- **Not tested in the game.** Treat a car that has such a part as untested until you have driven it.
- A damaged or unsupported file gives a clear error (400 from the server) instead of a crash; a corrupted-copy test (400 variants of the chute file) produced no crash and no hang.

## Reproduce

```
python -m pytest tests/test_modelbin.py          # set FH1_MODELBIN_SAMPLE to a .modelbin to run the file test
```

The sample file is not in this repository: it is a game asset.
