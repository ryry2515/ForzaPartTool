# Forza Horizon 1 (Xbox 360) modding guide for AI assistants

> **About this repository:** paths such as `research/` and `tools/` mentioned below belong to a larger private research archive and are not included here; the formats, rules and playbooks are self-contained. The editor and toolkit are in `toolkit/` (MIT license, see `LICENSE`).

One file. Give it to an AI, plus the user's own game files, and the AI should be able to build FH1 mods (cars, brands,
upgrades, sounds, lights, packaging) and avoid the known crashes. Everything here comes from inspecting real FH1 and
FM4 files, reading FH1's executable, and testing in Xenia and on hardware. It is a reconstruction, not source code.

**Start here:** read section 38 first. It says what a mod can and cannot change, how to begin (the toolkit in `toolkit/`), and gives step-by-step playbooks. The sections before it are the reference those playbooks point to.

## 0. Rules for the AI (read first)

1. **Evidence labels.** Every fact below carries one: **[Tested]** worked or failed in the game; **[Measured]** counted or
   parsed from real files or code; **[Inferred]** a reading of code or data that no test has proven; **[Unknown]**.
   Do not promote an [Inferred] to a rule. When you propose a change, say which test would falsify it.
2. **Never invent** addresses, offsets, table names, column names or SQL. Look in the user's own database and files.
3. **One change per test.** A game crash costs the user a full test cycle. Change one thing, keep a booting build
   installed as the revert point, read `xenia.log` before guessing.
4. **Do not distribute or ask for** game executables, archives, textures, audio banks, profiles or saves.
5. **Back up the save** before any test that adds or changes content. A bad DLC can leave a save that boots to a grey
   world (section 14).
6. Be honest about what is untested. "Installed" is not "works".

## 1. Environment and tools

- Platforms: Xenia canary (PC emulator), modded 360 (RGH/JTAG, BadUpdate softmod). Behaviour differs; always say which. [Tested]
- **`xenia.log`** is the main debugging tool. It lists every file the game opens (`HostPathDevice::ResolvePath(...)`) and, on
  a crash, a `==== CRASH DUMP ====` block with the guest PC and registers. The lines before the dump say what was loading. [Tested]
- Guest memory in Xenia: guest address `A` is host `0x100000000 + A`, values big-endian. A read fault at host
  `0x100000000` means a guest NULL dereference. [Measured]
- FH1 executable image base `0x82000000`. A mapped image (decrypted, sections laid out) can be disassembled with
  Capstone (`CS_ARCH_PPC`, `CS_MODE_BIG_ENDIAN | CS_MODE_64`); file offset = address - `0x82000000`. [Measured]
- Python with numpy, PIL, SQLite is enough for nearly everything. Readers and writers for these formats (Xbox LZX zips, STFS packages, LSB2 string tables, carbin, Granny, textures, GoD/ISO/folder game sources) are in `toolkit/` in this repository (`toolkit/README.md`; run `python toolkit/selftest.py GAME` first). Use them instead of rewriting format code.

## 2. System map

```
game archives (GoD/SVOD, zips, LZX)  ->  base files
      + every installed DLC package   ->  overlay: Media/DLCZips/<offer>_pri_99.zip (+ partN), zipmount.xml, stringtables.xml
      + every DLC *_merge.slt         ->  SQLite rows merged into gamedb at boot
car = DB rows (identity, engine, drivetrain, upgrades, wheels) + model files + textures + audio tuning + CarAttribs.xml
```

A car needs all of: database rows, model files, textures, audio tuning, and its string/name entries. A valid model does
not prove correct physics, lights, audio or driver pose; each needs its own check. [Tested]

## 3. DLC packaging

- Layout (Xenia content folder `content\0000000000000000\<TitleID>\00000002\<package>\`; FH1 title ID `4D5309C9`):
  ```
  Media/<offer>.puboffer
  Media/DLCZips/<offer>_pri_99.zip  (+ <offer>partN_pri_99.zip)   car files, paths media/cars/<Media>/...
  Media/DLCZips/<offer>_pri_99/Media/db/patch/<offer>00_merge.slt  rows merged into gamedb
  Media/DLCZips/stringtables.xml, zipmount.xml, StringTables_pri_99<offer>/<lang>/<Table>.str
  ```
  Offer IDs: `ContentOffers.ID = "4D5309C9" + offer(4 digits) + "FFFF"`, `OfferId` is that as an integer. [Tested]
- Xenia also needs `Headers\00000002\<package>.header` (332 bytes: u32 device 1, u32 content type 2, UTF-16BE name at 0x8,
  ASCII name at 0x108, title ID at 0x140). [Measured]
- **Zip limits.** A single 2.9 GB zip hung FH1 at boot; keep zips at about 900 MB each and keep one car's files together. [Tested]
  The 360's FAT32/FATX cannot hold a file of 4 GB or more: split console packages into parts under 3.8 GiB (offers 3060,
  3061, ...). Shared rows/files go in the first part; every part needs the full string tables. [Tested]
- **Compression.** Game zips use method 21 (Xbox LZX); stored and deflate also load. Copy already-compressed members
  verbatim when repacking (it is minutes faster). [Measured]
- **Archive member order.** When rewriting an existing archive, keep members in their ORIGINAL order. Appended members
  crashed the game once. [Tested]
- **Paths are case-insensitive.** Two members that differ only by case (e.g. `LOD05` vs `lod05`) crashed the launch. [Tested]
- **Resolution order.** The game asks the base game first, then every installed DLC in turn. **Base-game files win over DLC
  files with the same path**, so you cannot replace FH1's own `X_ET.xml`; rename yours instead. [Tested]
- **Never install two packs that contain the same car or the same database key.** FH1 merges all DLC databases at boot and a
  duplicate primary key usually shows as an endless load. [Tested]

## 4. Database (SQLite)

- `gamedb` (221 tables, 43,949 rows in the base game; **0 declared foreign keys**, but many logical ones) is merged at boot with
  each DLC's `*_merge.slt` (insert-or-replace per row, per existing table). A table your DLC adds rows to must exist with
  the same columns in your merge database; copy its `CREATE TABLE` from the base game. [Measured]
- **IDs.** Choose stable IDs: one workable scheme is car, engine, drivetrain = source-game id + 20000, wheels = source-game id + 60000.
  Saves store car IDs: reusing or changing IDs across builds crashed an existing save at boot. [Tested]
  **Engine IDs must stay below 32768** (signed 16-bit somewhere): 41313 crashed the boot. [Tested]
- **Key tables for a car:** `Data_Car`, `Data_Engine`, `Data_Drivetrain`, `Data_CarBody`, `List_Upgrade*` (Engine*, Drivetrain*,
  Motor, MotorParts, Tire..., Aero...), `List_Wheels`, `List_AeroPhysics`, `List_TorqueCurve`, `Combo_Colors`, `CarPartPositions`,
  `CameraOverrides`, `CarExceptions`, `ContentOffers`, `ContentOffersMapping`. [Measured]
- `Data_Car`: `IsUnicorn=1` hides a car from the Autoshow (set 0 to show it). `Thumbnail` is the exact game path of the thumbnail
  file; the game opens whatever that says (`.xds` = 768x288 format 6, 884,788 bytes, or RLE `.tga`). Pointing it at a file
  that does not exist or the wrong format hangs the purchase flow. [Tested]
  `FamilyModelID`: the garage resolves a car's family siblings; a car whose siblings are unbuyable traffic cars crashed the
  garage on reload (hybrids needed their own family, 40). [Tested]
- **Class and PI.** FM4 and FH1 rate the same car differently; fit FM4 PI to FH1 PI on cars both games have. Class = the first
  `CarClasses` row (IDs 1 to 9) whose `MaxPerformanceIndex` is at or above the PI. [Tested]
- **Makes (brands).** The Autoshow sorts brands by their `List_CarMake` string-table text, not by ID. A new make needs a row,
  a logo texture, and its text in a mounted `List_CarMake.str` (section 5). A make FH1 already has under another ID must be
  mapped to that ID. [Tested]
- **Names.** `Data_Car.DisplayName` / `ModelShort` are references `_&N`, `N = (52553 << 16) | key` into `Data_Car.str`; plain text
  also displays. [Tested]
- **Aero.** `List_AeroPhysics` rows can be shared between cars (IDs like 639400); missing ones crash Customize. [Tested]
- **Saves reference installed parts.** The garage stores each car's installed upgrade IDs. If a database build no longer has a
  saved part ID (for example a race engine part you removed), the game dereferences a NULL record at boot and crashes.
  Reset the car to stock parts, or sell it, before testing a build that removes parts. [Tested]
- Upgrade menu names: table `Upgrades` (`id, TypeId (text), IsException, Name, Level, Description, IconPath, ImagePath`) holds one
  row per (type, level); `Name` and `Description` are `_&N` references into `Upgrades.str`. Levels go up to 8 for some types.
  Motor and Battery is `TypeId '58'`, rows 259 (stock) to 262 (levels 0 to 3). A new tier = new `Upgrades` row + a motor/engine
  part row of the same level + two new strings. [Measured; a motor Level 4 [Tested] shown in the shop]

## 5. String tables (`.str`, LSB2)

- Chunks: UTF-16 text, an identifier-name pool in ordinal order, an order chunk. A reader/writer that round-trips all 22 FH1
  tables byte for byte exists (`lsb2`). [Tested]
- A DLC overrides a table by mounting `StringTables_pri_99<offer>/<lang>/<Table>.str` and listing `<table Name="..."/>` in
  `stringtables.xml`. **Only one mounted copy of a table wins.** Your file must therefore be a SUPERSET: FH1's own entries +
  other DLC's (UDLC, other packs of yours) + yours. A second pack shipping a thinner copy blanks the first pack's text (the
  a brand tile lost its name and sorted first). [Tested]
- **Key collisions.** Each pack numbers its new keys from the first free number. Two packs both using key 3444113409 made
  one car show another car's model name. When you build a second pack, pass the first pack in as a source so used keys are skipped. [Tested]
- Mount tables: `zipmount.xml` lines like `<zip Name="StringTables_pri_99<offer>" Mount="game:\Media\StringTables\"
  AltRootPath="game:\Media\StringTables\" ShouldCache="0"/>`. [Tested]

## 6. Models (`.carbin`)

Per car: `<Media>.carbin` (main, LOD1 and up, holds light records), `<Media>_lod0.carbin` (close-up / photo mode / menus),
`<Media>_cockpit.carbin`, caliper and rotor LOD0 files, and a `stripped_` copy of each (same model written with `stripped=True`).
Wheels are separate models under `media/wheels/<WheelMedia>/`. All header fields are big-endian. [Measured]

- Header (body files): `u32 5 | block A (0x004..0x154) | u32 light_count (0x154) | light records (32 B each) | u32 0x11 | draw-list block ...`.
  **Block A must be rebuilt from zeros**: FM4 leaves stray memory in it and FH1 reads it into the damage model (it exploded the
  damage model). The four per-wheel-corner records at 0x04/0x44/0x84/0xC4 stay zero (non-zero shrank wheels at LOD1+). [Tested]
- **Exhaust emitters** (block B, 0x160): do not change the count or the side flags (2 = left, 3 = right); a flag binds an
  emitter to an `exhaustL*` / `exhaustR*` part. Move positions only. [Tested]
- **Draw lists** (block D): `u32 nLOD` (written as array count / 2), then arrays of `u32 n, n x u32 (section << 16 | sub)`. There
  are `2 x nLOD` arrays: N transparent (one per LOD) then N opaque. Hiding a part = remove its entries; adding a part = append a
  section and entries. A part not in the draw list for a LOD is not drawn at that LOD. [Tested]
- Sections: marker, `offset[3]`, `bmin[3]`, `bmax[3]`, name, version (FM4 2, FH1 3), vertex pools for LOD1+ and LOD0, subs, tail.
  FH1 adds a storage block after every vertex/stream buffer: `u32 flag [+ u32 resource key]`. **Copies of a section whose vertices
  differ need their own random non-zero key** (`pool.bone[1]`); sharing a key between different vertex data risks aliasing in
  the GPU buffer cache. [Observed]
- Vertices: FM4 32 bytes, FH1 28. Bytes 0 to 23 are identical: `i16 pos[4]` (SNORM xyz*w), `u16 uv0[2]`, `u16 uv1[2]`, `i16 tangent
  quaternion[4]`. World position = `xyz * w * |bmax| + offset`. When you move vertices, re-encode against new bounds and drop any
  cached float block. [Measured]
- Coordinates: x left/right (negative left), y up, **mesh z negative = front**; light records use +z = front. [Measured]
- Subs: material name (`body`, `black`, `chrome`, `detail_glass_clear`, `oldhead`, `slorange`, ...), `uvxf` maps uv0 into the car's
  atlas (scale 0 = one swatch texel), `lod`, index type 4 (list) or 6 (strip, restart 0xFFFF or 0xFFFFFF). A material name must
  exist in the car's `ShaderSettings.xml` to behave as expected. [Measured]
- **Wheels.** FH1 draws each axle's tyre at the database size (`*WheelDiameterIN`, `*TireWidthMM`, `*TireAspect`) around the rim as
  modelled. Scaling a wheel radially to the tyre radius is how imports are fitted. FH1's staggered cars store a generic 15"
  LOD0 rim that the game rescales per axle; copying that trick to cars from another game made both rims tiny. **Shrinking rims to a
  database-derived size is NOT reliable** (a size fix for one car went too small twice); change only wheels a person names. [Tested]
- **Which file the camera draws.** Photo mode and menus use the `_lod0` file; driving uses main (LOD1+). People call the
  highest-detail model "LOD 1": confirm which file they mean before editing. [Tested]

## 7. Textures and shaders

- `.xds`: 52-byte header, Xbox 360 tiled data with 8-in-16 byte swaps, DXT formats (e.g. DXT5). Mips follow the base level one after another, each tiled and padded to 32 x 32 blocks (8888: 32 x 32 texels);
  the last level ends exactly at the end of the file. Measured on the car atlases: levels 1 to 5 of a 1024 texture decode as the box-filtered base level (35 to 23 dB), and rewriting them with new content works; the final 16-pixel level is packed
  differently and decodes to the wrong colour under this layout, so tools leave it as it was. [Measured]
- Per-car atlases: `nodamage`, `damage`, `lights`, `damagelights`, `interior`, `interior_emissive` (+ `_LOD0` variants), and
  `wheel.xds` for LOD1+ wheels (making it clear hid the wheels). [Tested]
- **A lights texture's alpha is both the glow mask and the glass opacity.** Writing a glow mask over glass blocks makes red lenses
  see-through. Keep the original alpha on blocks the glass UVs cover. [Tested]
- `ShaderSettings.xml` holds per-material colours/HDR values; light materials (`oldhead`, `hidhead`, `xenonhead`, `slorange`,
  `taillight2S`, `taillightst`, `reverse_light`, `drlwhite`) take their glow colour there. Cockpit overrides (`cockpit_reflector
  FresnelScalar 0.025`, `cockpit_gauge_emissive`, `cockpit_digital_gauge`) are per car. [Tested]

## 8. Lights

- **Light records** live only in the main carbin header (32 bytes each): position 3 x f32 (+z front); direction 3 x i16; bytes 18 to
  21 type / role (4 = beam) / side / flags; bytes 22 to 24 RGB; byte 25 glow size (clamp to 15; bigger gives huge halos). Role
  byte 19 and the glow size are what you edit most. [Measured]
- Put each record on the real bulb, inside the lens. Tail-lamp records red = tail/brake, grey `7c7c7c` = reverse. A glow point that
  sits in the gap between two lamps shows as a glow in the middle: move it onto a lamp. [Tested]
- Colours by era: halogen before 2008, HID 2008-2011, xenon 2012+ via material names. Amber indicators use `slorange`. [Tested]
- DRLs/angel eyes: transfer FH1's native DRL material (`drlwhite`) from a car of the same brand only where the surfaces match
  within millimetres. [Tested]
- The game clock for testing lights in Xenia: `world = [[[0x832DF024] + 4] + 4]`, `time of day = [world + 232]`, seconds since
  midnight = f32 at `tod + 10488`. Headlights are on from 18:50 to 08:02. (Title-update v4 pointer: `0x833CAAE4`.) [Measured]
- Light flares drawing through geometry on Xenia is an emulator limitation (fake occlusion queries); real hardware is fine. [Tested]

## 9. Pop-up headlights and animation

FH1 swings pop-up lids with a Granny skeleton animation; FM4 just swaps which pair of sections is drawn.

- Files per car: `caranimationevents.xml` (state machine: part `headlight`, states Closed/Open, triggers `lightsdown_to_lightsup` /
  `lightsup_to_lightsdown`, conditions on `InputType="Headlight"` >= 0.1 / <= 0.9), `car_skeleton_LOD05.gr2`,
  `car_animations_LOD05.gr2`, and in `CarAttribs.xml` right after `<General .../>`:
  `<animation Version="2" triggerName="lightsdown_to_lightsup" startTime="0" endTime="1.66" rootbone="headlight" inrace="1"/>` and
  the reverse (`startTime 1.66 endTime 3.33`), plus `<partDrop partName="headlightLdown" dropValue="0"/>` (and R). [Tested]
- **Per-file storage (verified against native Countach, M1, Miata).** `_lod0` file: only `headlight?up`, in the OPEN pose, static.
  Main file: `headlight?down` (the closed pose, hinge-animated) AND `headlight?up` (static open), both in every LOD draw list.
  `_cockpit` file: closed `headlight?down` lids. Skeleton bones: `headlight`, `hinge_headlight?_LOD0`, `headlight?down_LOD0`. [Measured]
- A car's real hinge can be recovered exactly by rigidly fitting its `down` pod to its `up` pod (rms near 0.0 mm): angle, axis,
  pivot. Rotate positions and tangent frames together and re-fit bounds. [Tested]
- **Open question and trap.** With the lights OFF, `_lod0` has only the open pods, so photo mode may show open pods in daytime, and
  restoring the closed pose in `_lod0` breaks the lights-on look. Setting `_lod0` pods to the open pose made closed pods in
  photo mode look like raised blisters on all 28 pop-up cars. Which state photo mode shows is not decided by the files alone;
  test both. [Tested]
- A broken `.gr2` shows as a crash at guest PC `0x829E1CF8` with a null write. [Tested]

## 10. Audio

- Chain: `Data_Engine.MediaName` -> `media/audio/cars/Engines/EngineTuning/<X>_ET.xml` -> `.../HarmonicTuning/<Y>_HT.xml` -> `.fsb` banks
  under `Media/Audio/Cars/Engines/Soundbanks/`; plus `CarModelTuning/<Media>_CMT.xml`. [Measured]
- **The game reads these tuning files only through mounted folders** declared in `zipmount.xml`, as official packs do:
  `<zip Name="EngineTuning_pri_<id>" Mount="game:\Media\audio\cars\Engines\EngineTuning\" AltRootPath="same" ShouldCache="0"/>`, and
  likewise `HarmonicTuning` (`...\Engines\HarmonicTuning\`) and `CarModelTuning` (`...\audio\cars\CarModelTuning\`). In the log the
  game asked for a DLC car's `_ET.xml` only in `enginetuning_pri_NNNN` mounts and never in the pack's own zip. A pack that only puts
  the xml inside its zip may therefore run on default audio. [Observed in the log; the effect on sound is [Unknown]]
- A DLC engine whose ET has the same file name as a base-game traffic car's ET gets the base (muted traffic) file: rename the
  engine (`<name>_FM4`). [Tested]
- Pops and backfire: an exhaust harmonic file (`*_Exh_HT.xml`, referenced by the ET `<Exhaust><Upgrade L0=..>`) has a `<Burble>`
  block (`Wavebank SequencePops_2.fsb burbles="2" backfires="5"`, a volume curve and a timer). All 1017 FH1 exhaust files have it;
  zero it (counts 0, flat curve, huge timer) rather than delete it. ET `VolumeAdjustments BackfireVolume` and `blowoffL1..3Level` are
  separate knobs. Whether zeroing removes the pops on an EV is not yet confirmed. [Inferred]

## 11. Engines, drivetrains, electric cars

- **Engine rows.** A car needs `List_UpgradeEngine` (Ordinal = car id, `IsStock=1`) pointing at `Data_Engine`; the engine-tuning file name
  comes from that engine's `MediaName`. With no engine record the lookup returns the error text and the game asks for a file named
  `Attempt to access invalid field or record_ET.xml` (endless load, then a grey world). A motor-only car must still have ONE stock
  engine row. [Tested]
- **Engine upgrade categories** (`List_UpgradeEngine*`: Camshaft, Displacement, Exhaust, Flywheel, FuelSystem, Ignition, Intake,
  Manifold, OilCooling, PistonsCompression, Valves, ...). A category whose only part is the stock part stores it at `Level 3` in FH1's
  own data (21 of 176 engines for the camshaft). Do not remove rows of a car that a save already has installed (see section 4). [Measured]
- **Engine configs** (`List_EngineConfig`): 1 V, 2 W, 3 I, 4 Rotary, 5 F, 6 E (electric), 7 SH, 8 PH, 9 SPH (hybrid). `Data_Car.EngineConfigID` and
  `Data_Motor.ConfigID` must agree; a mismatch crashed the garage on reload. With config 6 the game saves "no engine" for the car
  and a reload fails; the hybrid config 9 reloads fine. Cylinders `CylinderID 10` = 0 cylinders; aspiration `8` = None. [Tested]
- **Motors.** Tables `Data_Motor`, `List_UpgradeMotor`, `List_UpgradeMotorParts` exist but FH1 ships them empty. Torque curves are
  `List_TorqueCurve` rows (`TorqueScale`, `ZeroThrottleTorqueScale`, `NumTorqueValues`, `v0..vN`). With config 9 the game ADDS the engine
  row's power to the motor's: give the engine row ~2% torque. The stats screen's torque can read about 1.5x the motor curve peak; hp
  matched the spec. [Tested]
- **Rev limit and gearing.** The limiter was the engine row's redline (10,500 rpm), not the motor's 14,000: a 1-speed 8.27:1 car
  topped out at 99 mph and 6.55:1 gave 126 mph. FH1 caps redline at 11,000 and torque curves at 12,000 rpm; a 14,000 rpm engine row or a
  0-rpm peak torque crashed the boot or NaN'd the position. `NumGears` counts reverse (1 gear = 2). [Tested]
- **Drivetrain swaps.** A swap is an extra `List_UpgradeDrivetrain` row (Level 1, `IsStock=0`, new `DrivetrainID`, `PowertrainId`) plus a new
  `Data_Drivetrain` row (`DrivetypeID` 3 = AWD) and clutch / differential / driveline / transmission rows keyed to the new id
  (Id = drivetrain*1000 + level). `Powertrains`: 0 FWD front, 1 RWD front, 2 RWD mid, 3 RWD rear, 4 AWD front, 5 AWD mid, 6 AWD rear. [Measured; AWD swap shown in the shop [Tested]]
- The shop hides a one-gear car's gearbox upgrade; gearing tied to the gearbox part cannot follow a motor part. Put long gears on a
  drivetrain swap the shop does show. [Tested]
- Clutch capacity (`ClutchMaxTorque`) must exceed the motor's torque or the clutch caps it. [Inferred]
- Tyre/drag: FM4's Level 4 tyre is the drag tyre; FH1's categories end at 3 except some types, so it was removed. [Tested]

## 12. CarAttribs, driver, cockpit

- `CarAttribs.xml`: version matters (animation block needs the right version), `DriverHandsSteeringWheelOverride` = grip width
  (about 2.305 x steering wheel width - 0.347), `DriverHandsOffset` about the steering pivot + (0.002, -0.408, 0.437), `PartDrop*` and
  `DamageScrape` values (FH1 standard `PartDropGlobalScalar 0.15`). Hand offsets of 0,0,0 or on the wrong side put hands through
  the wheel. [Tested]
- Backfire flame direction: `OverrideBackireDirection`, `BackirePitch`, `BackfireYaw` (the misspelling is FH1's). [Measured]

## 13. Recipes

**Add a car from another Forza game.** (1) Convert models (header from zeros; vertices 32 -> 28 bytes; unique buffer keys). (2) Translate the
database rows with new stable IDs; keep every row of every car-keyed table; set `IsUnicorn 0`, class/PI, make. (3) Copy textures; write
`Thumbnail` path and the file. (4) Audio: ET/HT/CMT in mounted tuning folders. (5) Lights: records on bulbs, material names, ShaderSettings.
(6) Name strings as `_&N` into a superset `Data_Car.str`. (7) Pack: zips < 900 MB, merge db, zipmount/stringtables, headers. (8) Check keys
against the base game and every other pack (`db_check`). (9) Install on Xenia, boot, open Autoshow, buy, drive, switch cars and back,
relaunch and load the save. Read the log at each stage.

**Add a brand.** `List_CarMake` row + logo textures (`UI4/Logos/<folders>/L_MAN_x_Logo.xds`) + text in a superset `List_CarMake.str`.

**Add an upgrade tier.** New `Upgrades` row (TypeId text, Level N) + the part-table row at level N + two new entries in a superset
`Upgrades.str` + `<table Name="Upgrades"/>` in `stringtables.xml`. Test opening the shop first.

**Add a drivetrain swap.** section 11. **Add an electric car.** section 11 plus a motor spec table entry (hp, torque, redline, ratio).

**Hide a duplicate car.** `IsUnicorn = 1`, keep its rows and files (saves may reference it).

## 14. Crash and failure catalogue

| Symptom | Cause | Fix | Label |
|---|---|---|---|
| Endless load at boot | duplicate DB primary key (two packs with the same car/id) | unique ids; `db_check` against base and all packs | Tested |
| Boot crash, guest PC 0x82BEBF54, read at 0x100000000 | null `List_TorqueCurve` record in the engine-physics loader, from a missing upgrade part the save has installed | keep every part row a saved car uses; reset the car to stock first | Tested |
| Grey world on spawn / relaunch | motor-only car has no engine row (error-text file name) or config 6 saved as "no engine" | stock engine row + config 9 | Tested |
| Boot crash, engine id 41313 | engine id above 32767 | ids < 32768 | Tested |
| Boot crash at launch, pop-up car | two animation members differing by case | unique case-insensitive member names | Tested |
| Null write at 0x829E1CF8 | broken `.gr2` | rebuild the animation files | Tested |
| Wheels hidden | `wheel.xds` made clear | keep the swatch atlas | Tested |
| Damage model explodes | stray memory in carbin block A | rebuild block A from zeros | Tested |
| Customize crashes | missing shared `List_AeroPhysics` rows | extract and merge them | Tested |
| Purchase hangs | `Thumbnail` path points at a missing/wrong-format file | path = real file | Tested |
| Brand tile text blank / brand first in list | second pack's thin `List_CarMake.str` replaced the first | superset tables | Tested |
| Wrong model name (another car's name) | name key collision between packs | unique keys | Tested |
| Silent engine | ET name equals a muted traffic ET | rename engine | Tested |
| Shop crash with a new upgrade level | unknown (a motor Level 4 shows fine) | test the shop first, keep the revert | Inferred |

## 15. Native code leads (mapped image, base 0x82000000) [Measured unless noted]

- `0x82BEBF28`: torque-curve record loader. Reads fields `TorqueScale`, `ZeroThrottleTorqueScale`, `NumTorqueValues`, `v0..` from a
  `SELECT * FROM List_TorqueCurve WHERE TorqueCurveID=<id>` record; dereferences the record without a null check.
- `0x82BEEF98`: engine physics loader (`SELECT * FROM Data_Engine WHERE EngineID=`, `TorqueSteerLeftSpeedScale`, ...) -> `0x82BEECB0`: camshaft
  reader (`TorqueScale, RedlineRPM, StallRPM, StartRPM, RedlineWearSpeed, TorqueCurveMaxRPM, NumRPMEntriesArray, TorqueCurveFullThrottleID`).
- `0x82BEF798` / `0x82BEF450`: motor physics loader (`SELECT * FROM Data_Motor WHERE MotorID=`; fields `MomentInertia, RotationDirection,
  BatteryCapacity, ConfigID, RedlineRPM, NumRPMEntriesArray, TorqueCurveFullThrottleID`; also `SELECT * FROM List_DamageModDef WHERE
  DamageModName='Motor'`).
- `0x82BEFC50`: tyre physics record constructor; a saved Rally tyre with missing content leaves the record null (startup crash).
- Queries seen in the image: `SELECT MotorID FROM List_UpgradeMotor WHERE Id=`, `SELECT ConfigID From Data_Motor WHERE MotorID = %d`,
  `SELECT Data_Engine.MediaName, Data_Engine.EngineID FROM Data_Engine LEFT JOIN List_UpgradeEngine ON ... WHERE List_UpgradeEngine.Id=%d`.
- `0x82697DD0`: lead for the manufacturer (brand) screen per-frame work (not yet characterised). [Inferred]
- Related repo notes: `research/2026-10-26-native-ui-and-powertrain.md`, `.../manufacturer-item-queries.md`, `.../executable-mapping.md`.
  The mapped image hash used there: `5ce77d34952a8c65b432d84e5eb9b321f2cbb9f8c0377567decd77afbf93927c`.

## 16. Open questions

1. What the brand-select screen computes every frame (lag scales with car count on real hardware; 97 cars fine, +446 lag). [Unknown]
2. Which header field/flag tells the game to rescale a generic LOD0 rim per axle. [Unknown]
3. AI in FREE ROAM: the roster comes from the FreeRoamDrivers table joined to AIPlayers (section 21); the remaining question is what else filters it. For RACE opponents the picker is known (section 18.2): it selects installed, selectable cars by PI
   or at random, so DLC cars qualify on their own. A candidate database-only free-roam change exists (`FreeRoamDrivers`, `AIPlayers.IsFreeRoam`)
   but is untested. [Unknown]
4. Granny bone-to-section binding rules across LOD and cockpit files. [Unknown]
5. Why photo mode and driving show different pop-up states, and how a closed pod should be stored for `_lod0`. [Unknown]
6. Whether tuning/harmonic files shipped only inside a pack zip are ever read. [Unknown]
7. The exact row the engine-physics loader asks for when the saved parts and the database disagree (found by symptom, not by name). [Unknown]

## 17. Test protocol

1. Keep the last booting build installed; build into a new folder; install only with Xenia fully closed (check for leftover processes).
2. Boot -> Autoshow brand page -> buy -> drive -> switch car and back -> close and relaunch -> load the save.
3. After each step read `xenia.log` for `CRASH DUMP`, the last cars/files loaded, and unexpected `ResolvePath` patterns.
4. Say what is still untested. On real hardware, add the Autoshow brand page lag check.
5. Release discipline: clean rebuild, apply every post-build patch in order, verify every archive, update the README, never ship test cars.

## 18. Native map: what the executable does with the database [Measured: static analysis of the mapped image]

Tool chain (all in `research/native-map/`): `tools/native_map/native_map.py` (function table from `.pdata`, string scan, `lis`+`addi` string references,
`bl` call graph), `classify.py`, `build_docs.py`. Output: `table_readers.md` (per table: statements and functions), `sql_catalog.md` (every SQL
string and the identifiers near it), `assets_catalog.md` (path and file-type strings), `subsystems.md`. Numbers: 57,127 functions (matches the
`.pdata` record count), 41,392 strings, 18,982 of them referenced from code, 206,135 direct calls, **864 SQL strings over 155 table names** (816 referenced; 155 after handling the %s save prefix).
The image has no symbol table; only about 112 `Class::Method` strings exist. Roles of functions are inferred from their strings. [Inferred]

**18.1 Image layout** (PE, little-endian COFF headers, big-endian runtime data): base `0x82000000`; `.rdata` RVA 0x600 (0x3667FC), `.pdata` 0x366E00
(0x6F938 = 57,127 x 8-byte records: begin address, then flags: prologue length 8 bits, function length in words 22 bits), `.text` 0x3E0000 (0xE0E634),
`.data` 0x11F0000 (0x31E3C8), `.edata` 0x1520000, `.idata` 0x1530000. Machine 0x1F2 (PowerPC big-endian). Function-length field times 4 = bytes.

**18.2 What the code decides, by table**
- **AI race opponents**: function `0x8254AAD8` runs `SELECT id, PerformanceIndex, ClassID FROM Drivable_Data_Car WHERE id!=%d AND IsCarInstalled(id)
  AND IsUnicorn=0 AND IsSelectable=1 AND %s ORDER BY abs(%f-PerformanceIndex) LIMIT %d` (closest PI) and a `... ORDER BY RANDOM() LIMIT %d` variant, plus
  `WHERE ClassID < 6 ...`. `AIPlayers` supplies names and modifiers: `DriverTypeID` 1 and 3, `isUsed` (updated to 1/0 when a driver is taken/released),
  `SkillModifier, AggroModifier, RubberBandModifier`, `WristbandLevel`, `Rank`, `Nemesis`, `DriverModelStreet/Race`. So a DLC car is a possible
  race opponent as soon as `IsCarInstalled(id)`, `IsUnicorn=0` and `IsSelectable=1` hold; nothing else has to list it. Free roam is a different path (open).
- **`Drivable_Data_Car`** is not a table or view in the shipped `gamedb`; the game creates it at runtime over `Data_Car` (about 27 queries use it).
- **DLC and ownership gating**: `ContentOffers` columns `IsInstalled, IsPurchased, IsDLC, Hidden, LicenseMask, PubOfferingId, SeasonPassOffer, IsALaCarte,
  OfferId`. Function `0x825162D0` first runs `UPDATE ContentOffers SET IsInstalled=0, IsPurchased=0`, then per installed package
  `UPDATE ContentOffers SET IsInstalled=1, IsPurchased=(LicenseMask & %u)=LicenseMask WHERE PubOfferingId=%u`, and `IsPurchased=1 WHERE SeasonPassOffer <> 0`.
  Another statement sets new DLC offers to `IsDLC=1, Hidden=1, IsInstalled=0, IsPurchased=0, LicenseMask=1` (and rewrites image paths from
  `Textures\Marketplace` to `Textures\UI4\Marketplace`). `ContentOffersMapping(OfferId, ContentId, ContentType)` links an offer to cars and tracks;
  `Data_Car.IsInstalled`, `IsPurchased`, `ContentID` are also read (`SELECT DISTINCT ContentID FROM Data_Car WHERE IsInstalled=1 AND IsPurchased=1 AND ContentID >= 0 UNION ... Tracks`).
- **Native SQL functions** registered by `0x8250B9C0` (688 bytes): `IsCarInstalled, IsCarInstalledAndPurchased, IsCarOnDisc, IsCarDisc1, IsCarVisible(ModelId),
  IsDrivableCar, IsShowroomCar, IsShowroomCarInstalled, IsShowroomCarInstalledAndPurchased, IsShowroomCarOnDisc, IsShowroomCarDisc1, IsEnvironmentInstalled,
  IsEnvironmentInstalledAndPurchased, IsEnvironmentOnDisc, IsEnvironmentDisc1`. Another block (`0x827E1C18`) registers `IsUnlocked, IsSharedByMe, HasNetworkTexture,
  IsQualified`. A SQL query that uses one of these functions only works inside the game process (not in an external SQLite shell).
- **Damage model**: one `SELECT * FROM List_DamageModDef WHERE DamageModName='<X>'` per system: Steering (`0x82BE9D30`), Driveline and GearBox (`0x82BEA408`),
  RearDownforce / FrontDownforce / BodyDrag (`0x82BEA750`), Brakes (`0x82BEB148`), Engine (`0x82BECAB8`), Tires and Suspension (`0x82BEEA28`), Motor. All 11
  rows exist in the shipped database, so motor cars find theirs. Fields: `ProbabilityMin/Max`, `ProbabilityMin/MaxImpact`, `RandomImpact`, `DamageMin/Max`,
  `DamageMin/MaxImpact`, `ModFullDamage`, `ModZeroDamage`.
- **Events**: `Events` (68 read statements: `Name, ShortName, Sponsor, Description, Level, TimeOfDayStart, CareerEventStyle, PopularityPointsReq, EventLogo`) and
  `Races` (`EventId, TrackId, NumLaps, ...`) drive the career. `Tracks` (`EnvironmentId, RibbonConfiguration, RibbonIndex, Length, MediaName, DisplayName,
  IsHomespace, Active`) and `Environments`.
- **Tables the game WRITES (the save database)**: `Career_Garage` (11 update statements), `Career_PurchasedParts`, `Career_CarGroups`, `Career_EventResults`,
  `Clan_Garage`, `CareerEventUnlocks`, `CareerHubUnlocks`, `BadgeTitleProgress`, `BadgeTitleUnlocks`, `CarExperienceRevealsSeen`, `CarPointsOfInterestProgress`,
  `ContentSeen`, `LastUsedGarageId`, `MetaData`, `AIPlayers.isUsed`, `ContentOffers`. Garage rows reference car and part IDs: removing an ID a save uses
  crashes (section 4). Table names in queries often carry a `%s` prefix (the save database attached under a name).
- **Car record reads**: `Data_Car` is read by 123 functions (99 statements); the common one is `SELECT * FROM Data_Car WHERE Id=` (0x824C3F18, 0x824D11B0 (9,448 B),
  0x825708D8, 0x82BF4FB0 (6,616 B)). Columns seen with it: `MediaName, ModelShort, MakeID, ClassID, CarClassModifier, StockWheelID, EnvironmentId,
  Profile, Front/RearTireWidthMM, TireCompoundLevel, ColorStripe, ColorFinish, Metallic, CaliperAngleRear, DLC_Car`.

**18.2b Save database schema as the code writes it** (tables carry a `%s` prefix in queries: the save database attached under a name) [Measured: SQL strings; roles Inferred]
- `Career_Garage` (one row per owned car): `ID, CarId, GUID, SharedID, Flags, PartsValue, DisplacementId, EngineConfigId, CylinderId, DrivetypeID` and tuning columns
  `Tuning_*` (final drive, gears 1 to 6, front/rear downforce, centre torque, front/rear accel/decel diff, ...). A tuning value of `-1.0` means "use the car's default"
  (the game resets a setting by writing -1.0). Garage rows also back the "unicorn" check (`Data_Car.IsUnicorn=1` cars in the garage) and `LastUsedGarageId`.
- `Career_PurchasedParts`: **every installed upgrade is a row `(GarageId, PartEnum, PartId, ManufacturerId, PricePaid)`**; the game inserts, deletes and sums these
  (`PartsValue = SUM(PricePaid)`). `PartEnum` is the part category (`UpgradeTypes.PartEnum`), `PartId` the part-table row id. When a database no longer contains the
  part a row names, the load path dereferences a missing record (the boot crash in section 14). Selling a car deletes its rows.
- `Career_EventResults` (`EventId, Place, RivalsBeaten, ...`), `Career_CarGroups` (`Id, SortOrder, DisplayName`), `CareerEventUnlocks`, `CareerHubUnlocks`,
  `BadgeTitleProgress`, `BadgeTitleUnlocks`, `ContentSeen`, `CarExperienceRevealsSeen`, `CarPointsOfInterestProgress`, `Clan_Garage`, `MetaData`.
- The upgrade shop reads `UpgradeTypes (id, Name WHERE PartEnum=)` and `Upgrades (Name WHERE TypeId=)` (functions `0x82844900`, `0x82843760`); most shop SQL
  is assembled at run time from `%s` fragments, so the static string list under-reports it.
**18.3 Helper functions** (very many callers; roles inferred from call sites): `0x82DE7330` (6,066 callers; string release), `0x82CA7538` (3,744),
`0x824AFB20` (3,258; record/string accessor), `0x82411478` (2,976; string destructor, called as `(str, 1, 0)`), `0x823E19B0` (1,935), `0x82A7E6B0` (1,845),
`0x826E1B10` (1,749; field reader by name), `0x82450F48` (1,384; string constructor from a literal: `(dest, literal)`). The largest function is `0x8241A370`
(110,012 bytes), then `0x82BE1880` (30,196), `0x82D505C0` (track render settings serializer, 21,808), `0x82713750`, `0x8307EDA8`, `0x82521200`.

**18.4 Subsystem string counts** (strings / functions): career+events 2,875 / 1,490; UI and menus 2,626 / 1,338; graphics and materials 3,159 / 542; upgrades and
tuning 1,649 / 550; world and tracks 1,531 / 514; camera/photo/replay 1,090 / 436; network and Live 981 / 536; audio 965 / 532; physics 954 / 353; save/profile/
content 762 / 535; input 728 / 252; cars/garage/Autoshow 1,136 / 575; AI and traffic 457 / 335. See `subsystems.md`.

## 19. Emulation and recompilation facts (External: github.com/itsnotpaths/fh1-recomp-findings, rexglue static recompile of FH1)

These matter to people changing the emulator or recompile, and they explain a few behaviours seen when modding. [External; not re-tested here]
- `vaddsws` (signed saturating add) must blend per 32-bit lane; a per-byte blend caused 0.234 m camera snaps and rare 8,192 m teleports. The camera path:
  `CCar::TransformBodyPosToWorldPos` -> `Mtx33MultVec` -> `vcfpsxws` -> `vaddsws` -> `vcsxwfp` (fixed-point scale 2^18).
- `vpkd3d128` case 5 (FLOAT16_4 pack) with destination == source must snapshot the source lanes first; otherwise FMOD's `DSPConnectionI::setLevels` packs zeros and
  audio is silent.
- `vmsum3fp128` / `vmsum4fp128` return QNaN, not infinity, when the dot product overflows (xenia does this since PR 1565). `CGJK::Intersecting` seeds its direction to
  -FLT_MAX and relies on an unordered compare; with +inf no car or prop ever collides.
- `vmaddfp` / `vnmsubfp` are fused (single rounding). On the GPU side the HDR `exp_adjust` lives in fetch-constant word 3 (reading word 4 gave dim output).
- FH1's vertex shaders reference vertex-fetch slot 90, which the game never binds; with `gpu_allow_invalid_fetch_constants=false` those draws are dropped
  (black frame). FH1 issues many empty resolves (harmless). Concurrent writes to the function table race with dispatch from worker threads (needs a lock).
- Unloading the XMedia facade module with a DLL_PROCESS_DETACH that is not implemented leaves `CSystemEventParam` mutexes inconsistent (deadlock in
  `CTrackModel::SetFileIOFailState`); the workaround is to leak the DLL.
- Class names confirmed by those logs: `CCar`, `CGJK`, `CSimplex`, `CTrackModel`, `CSystemEventParam`, `FMOD::DSPConnectionI`.

## 20. Class map recovered from RTTI [Measured: type descriptors, object locators, vtables; constructor candidates Inferred]

The executable carries MSVC RTTI. `tools/native_map/rtti.py` found **6,744 type descriptors** (3,445 named classes, 3,299 template instances), 7,597 object locators and 6,357 classes
with a vtable. `research/native-map/classes.tsv` lists every class (bases, vtable address, number of virtual slots, constructor candidates), `function_labels.tsv` names 22,770 of the
57,127 functions (`Class::vfnK` = K-th slot of the class vtable, `Class::ctor?` = a function that stores the vtable address; slot numbers are positions, not recovered names), and
`class_index.md` groups them. Namespaces by size: `ForzaUI` 627 (screens, popups, data items), `States` 490 (game-flow states), `GameModes` 111 (activities, missions, race modes),
`Network` 92, `AIOpenWorld` 78, `Navigation` 74, `Camera` 28, `RaceFeats` 28, `HorizonGameState` 24, `RacingAI` 24, `SatNav` 18, plus the `...Deferred` render/audio command classes.
Largest vtables: `CCarDynamics` / `ICarDynamics` (339 virtual slots), `CAudioEngine` (242), `CCarPresentation` (151), `CTrackPresentation` (135).
States, activities and many UI items are created by NAME: `RTTI::CConstructor0/1/2` template instances are registered with a class-name string (for example `CLoadIntoFreeRoam`,
`CSetFreeRoamGarageCar`, `CResetTrafficCars`, `CWaitForTrafficCarsToBeRemoved`, `CDisableFestivalTrafficSpawning`), so game-flow data files refer to classes by those strings.
Source-path strings (`d:/dev/rad/granny/rt/*.cpp`, 225 distinct) identify the middleware (Granny) functions.

## 21. Free roam and traffic [Observed in code strings; not run]
- **Free-roam AI roster**: function `0x829AE1C0` (2,304 bytes) references the table name `FreeRoamDrivers`, `SELECT * FROM Events WHERE RTRIM(HorizonEventID)='FREE_ROAM'` and
  `SELECT AIPlayerID, CarId, LiveryLevel, TuningLevel, SkillModifier, AggroModifier, Nemesis, WristbandLevel FROM %s INNER JOIN AIPlayers ON %s.AIPlayerID = AIPlayers.Id`.
  So free-roam opponents are rows of `FreeRoamDrivers` joined to `AIPlayers`; a car appears there only if a row with its `CarId` exists. `FirstTimeCareerDrivers (AIPlayerId, CarId)`
  supplies the cars of the first-time experience (`States::CSpawnAIForInitialDrive`).
- **Traffic** is not database driven: the cars come from `GameTunableSettings/AIOpenWorld.xml` (section 33). `trafficcar%d`, `numtrafficcars`, `streamtrafficcars` are command-line parameters; `AI/Traffic/...` tunables come from the
  registry (section 22); `CVehicleLoader`, `AIOpenWorld::Traffic::CVehicleSelector` and `AIOpenWorld::Racing::CFreeRoamVehicleSelector` reach no SQL within three calls, which fits a vehicle list read from XML. [Measured for the file; the reader functions are Inferred]
- Race opponents: section 18.2 (picker by PI over installed cars).

## 22. Tunable settings [Measured]
The game reads `media/GameTunableSettings.zip` at start-up: `GlobalRegistry.xml` (683 `<Value id type value>` entries; 655 keys are created by `Registry::TValue*` constructors in
the executable, 610 are in both), `GameTunableSettings.ini` (268 `Group\Key value` lines: penalties, cheat detection, `CarValueScales\TradeValueScale / SellValueScale /
RestockingValueScale / RewardCarsValue`, HUD damage thresholds), `AIOpenWorld.xml`, `AIRacing.xml`, `Fame.xml`, `OnlineFame.xml`, `WristbandLevels.xml`, `HorizonFeats.xml`, `RaceFeats.xml`,
`CareerRaceModes.xml`, `FirstTimeCareer.xml`, `FreeRoamChallenges.xml`, `SponsorshipChallenges.xml`, `GameOptionPresets.xml`, `BadgesAndTitles/BadgeAndTitleRules.xml`, hopper options, drive
conductor settings and `PostRaceOptions/*.xml`. Registry groups: AI 257 keys, UI 124, Navigation 61, Activity 42, CarLabelColors 40, Audio 21, FirstTimeCareer 16, Notifications 13.
`research/native-map/tunables.md` documents every file (structure, numeric ranges, registry defaults) and `registry_keys.md/.tsv` lists the keys with value types. Base-game files win over
DLC files with the same path, so a DLC cannot override these by shipping the same name. [Measured; override behaviour Tested for other file types only]

## 23. Database data dictionary [Measured schema; Inferred relationships]
`research/native-map/data_dictionary.md` covers all 221 tables (43,949 rows): columns and types, key column, 209 inferred references (a column whose values are all present in another
table's key, with a name pointing at it), enum-like value sets, and the executable classes that read each table. `Data_Car` alone references `List_CarMake`, `CarClasses`, `List_CarType`,
`List_FamilyModel`, `List_FamilyBody`, `List_EnginePlacement` and others.

## 24. Screens, states and the data they read [Observed in code; virtual calls not followed]
`research/native-map/ui_screens.md` (624 `ForzaUI::` classes, 339 of which reach at least one table within three direct calls) and `states_and_modes.md` (640 classes of `States`, `GameModes`,
`HorizonGameState`, `StateSystem`; 49 reach tables) give for each class its vtable, base class and the tables whose SQL it reaches. Examples: the buy-car and career-event car pickers
(`CCareerEventBuyCarSelect`) reach `Data_Car, Combo_Colors, ContentOffers, ContentSeen, Events, EventRecommendedCars, EventRestrictions`; the manufacturer screens reach
`ContentOffers, ContentSeen, Data_Car, Drivable_Data_Car, Tracks` (the multiplayer one also `Clan_Garage`). A career event activation (`GameModes::CActivityCareerEventActivation`) reads
`Events` (`Level, Name, ShortName, Sponsor, CareerEventStyle`, lookup by `HorizonEventID`), `Tracks.RibbonConfiguration` and `Races.TrackId`.

## 25. The brand (manufacturer) screen and why it gets slow with many cars [Inferred from the queries; frequency not measured]
`ForzaUI::CManufacturerSelect` (and the `_BuyCar`, `_Marketplace`, `_Multiplayer` variants) run, from slot 0 / 9 / 54 (functions `0x8255A860`, `0x8255B808`, `0x826E5AE0`, `0x827017A8`, `0x826B40E0`):
`SELECT Id FROM Data_Car WHERE MakeID=%d AND IsCarOnDisc(Id)=0 AND IsCarVisible(Id) AND IsDrivableCar(Id)=1`,
`SELECT COUNT() FROM Data_Car WHERE MakeID=%d AND IsCarOnDisc(Id)=0 AND IsCarVisible(Id) AND IsDrivableCar(Id)=1 AND Id NOT IN (SELECT rowid & %u FROM %sContentSeen WHERE (rowid >> 32)=%d)`,
`SELECT MakeId, ClassId, DrivetypeId FROM Drivable_Data_Car AS Garage WHERE IsUnicorn=0 AND IsCarVisible(ModelId) AND IsSelectable=1 AND %s`, and the make list query that builds each tile's logo path
(`...\Manufacturer_Logos_I\L_MAN_<IconPathBase>_Logo.tga`, `CountryID`). `IsCarOnDisc`, `IsCarVisible` and `IsDrivableCar` are native SQL functions (section 18.2), so every row examined costs
native work, and the "new cars" badge adds a `NOT IN (SELECT ... ContentSeen ...)` subquery. The work therefore grows with the number of `Data_Car` rows (and with `ContentSeen`). This matches the
observation that a 97-car pack is fine and about 500 added cars lag on real hardware. **Test that would falsify it**: build the same pack with a quarter of the cars; if the lag does not scale down, the queries are
not the cause. Cheaper variants to try (untested): fewer `Data_Car` rows reachable per make (hide duplicates with `IsUnicorn=1`, which the picker still scans), or an empty `ContentSeen`.

## 26. The upgrade tree [Measured]
`research/native-map/upgrade_catalog.md` lists the whole tree with the game's English labels: 14 shop areas, 46 types (`UpgradeTypes.id`, `PartEnum`), and every `Upgrades` row (id, level, name). Levels
0 to 3 are Stock / Street / Sport / Race. **Level 4 is already used by FH1** for "Remove Front Bumper", "Remove Wing", "Remove Rear Bumper", "Remove Side Skirts" and "Drag Tire Compound" (`Upgrades.id`
264 to 268); tire-width types go up to level 6 to 8. A new tier therefore fits the existing structure. `PartEnum` is the category value stored in `Career_PurchasedParts`.

## 27. Configuration and archive vocabulary [Measured]
`research/native-map/xml_vocabulary.md` lists, over every file in the game archives, each element path and attribute of `CarAttribs.xml` (176 cars; versions 15, 17, 20, 21), `ShaderSettings.xml`
(171), `ColorShaderSettings*.xml` (114), `caranimationevents.xml` (33) and the first 400 audio tuning files per type, with numeric ranges and small value sets. `archive_map.md` lists the 524 zip
archives of the base game with member counts and extensions. `tunables.md` (section 22) and `data_dictionary.md` (section 23) complete the data layer.

## 28. Simulation inputs [Inferred from string co-location]
`research/native-map/physics_records.md`: the executable reads these physics-related tables with `SELECT * FROM <table> WHERE <key>=`: `Data_Engine` (function `0x82BEEF98`), `Data_Motor` (`0x82BEF450`),
`List_TorqueCurve` (`0x82BEECB0`, `0x82BEF450`), `Data_CarBody`, `List_Wheels`, `List_AeroPhysics`, `List_AntiSwayPhysics`, `List_SpringDamperPhysics`, `List_SuspensionPhysicsType`,
`List_TireCompound`, `List_TireFrictionCurve`, `List_TireFrictionMultiCurve`, `List_TireAffectCurve`, `List_UpgradeBrakes`, `List_DamageModDef`, `WheelLayouts`, `WheelExceptions`, `CarUpgradeExceptions`
(the drivetrain and upgrade-part tables are read through queries assembled at run time). The car-dynamics class itself (`CCarDynamics`, 339 virtual slots) reaches no SQL: it receives its parameters in record
structures filled by these loaders, so the database columns are the whole interface. Engine loader field names include `TorqueScale, RedlineRPMScale, ZeroRPMScale, TorqueDropOffRPM0/1,
TorqueDropOffScale0/1, PowerMinScale, PowerMaxScale, MaxScale, MinScale, RobScale, MomentInertia, TorqueSteerLeftSpeedScale, TorqueSteerRightSpeedScale`.

## 29. Granny files (`.gr2`, skeletons and animations) [Tested unless marked]
- Car animation files use Granny 2 format 7. Magic `e59b495e6f631f141e13eba990beedc4` = little-endian, 64-bit pointers (brake files use `29de6cc0...`, little-endian 32-bit); the console is big-endian with 32-bit
  pointers, so **Granny converts the file when it loads it**.
- Layout: header (magic, `sizeWithSectors`, format), file info at offset 32 (format 7, total size, CRC, file-info size 72, section count 8, type reference, root reference, tag), then 8 section headers of
  44 bytes: compression (0 none, 2 Oodle1), data offset, compressed length, decompressed length, alignment (4 or 32), **first16bit**, **first8bit**, fixup offset and count, marshalling offset and count.
  **The CRC is CRC32 of everything from file offset 104 to the end.**
- Oodle1 is Granny's own arithmetic-coded LZ (not modern Oodle). FH1's files are Oodle1 except an empty section. **Uncompressed sections load fine** if the next two rules are kept.
- `first16bit` / `first8bit` are not only decoder stop points: Granny sorts section data as 32-bit-or-larger, then 16-bit, then 8-bit, and the big-endian loader byte-swaps each region by those boundaries.
  Overwriting them crashed the game (guest PC `0x829E1CF8`, write to address 0). **Marshalling entries** (16 bytes: count, source offset, destination section, type-info offset) tell the loader how to swap
  mixed-size structures; dropping them gave the same crash. **Fixups** (12 bytes: source offset, destination section, destination offset) set the pointer slots, which are stored as 0.
- Type tree: 44-byte records ending with type 0. Member types: 1 inline struct, 2 reference, 3 reference to array (4+8), 4 array of references, 5 variant reference, 7 reference to variant array, 8 string, 9 transform
  (68 bytes: flags, t[3], q[4], scaleShear[9]), 10 real32, 11-14 int8/uint8, 15-18 int16/uint16, 19-20 int32/uint32, 21 real16, 22 empty reference.
- Car skeleton units: 20 per metre, 3ds Max axes (+z up, +y forward): `max = (game x * 20, -game z * 20, game y * 20)`. Bones carry a transform and a 4x4 `InverseWorldTransform` (row vectors, translation in the
  last row). A pop-up headlight skeleton has `headlight` (root), `hinge_headlight?_LOD0` and `headlight?down_LOD0`.
- Animation: a track group per animated part, transform tracks, duration 3.333 s, time step 1/60, oversampling 2. Quaternion curve formats 8 and 9 (`D4nK16uC15u`, `D4nK8uC7u`) are packed quaternions
  (the largest component is dropped; its index is in the top bits of two words); formats 16, 17, 18 (`D3I1K...`) hold one value per knot with `value = ControlScales * code + ControlOffsets`; 4 and 5 are
  constants, 2 is identity. Skinning = W_anim * W_bind^-1, so for a pure hinge only the hinge bone matters.

## 30. User interface (Anark Gameface) [Measured: headers and counts]
`media/UI.zip` holds 694 members: 230 `.bgf`, 205 `.bsg`, 205 `.fbf` (scenes in `Scenes/ui4/`), 22 `.jpg`, 21 `.xml`, 5 `.lua`, 3 `.xpr`. A `.bgf` starts `01 04 00 00 00 1A` then the text `AnarkBGF` (Anark Studio / Gameface); it holds
named scene objects (`parent.Layer.RotationHelper.MANUFACTURER_LIST...`), a `UIContract` object layer through which native code binds data to the scene (names such as `TEXT_TITLE`, `TEXT_DESCRIPTION`, `R_LIST`),
and text. The `.bsg` starts `01 04 00 00 00 00 00 18` and its payload is float data (scene graph); `.fbf` is the third file of the scene. About 70 of 230 scene names follow `NNN_<letter>_<NAME>` (C car menus 34,
D dialogs and popups 16, I in-game 13, P boot 2, F track menus 2). `research/native-map/ui_scenes.md` lists every scene with its three file sizes and object names. Which native class owns which scene is not recovered
here (scene names are mostly loaded through computed strings); `ui_screens.md` gives the classes and the data they reach. UI textures are `.xds`; make logos live under `UI4/Logos/<folder>/L_MAN_<name>_Logo`.

## 31. Navigating the executable (workflow for an AI) [Inferred tooling]
1. Know the function address -> look it up in `research/native-map/function_labels.tsv` (class and virtual slot) and `module_map.md` (which part of the address space it sits in).
2. Know a table -> `table_readers.md` (statements and functions), `data_dictionary.md` (columns, references), `physics_records.md` (simulation readers).
3. Know a class -> `classes.tsv` (bases, vtable, virtual count) and `python tools/native_map/deepdive.py DIR "ClassRegex" 3` (tables, strings and classes reached within three direct calls).
4. Know a setting -> `registry_keys.tsv`, `registry_defaults.tsv`, `tunables.md`.
5. Remember the limits: virtual calls are not followed; strings built at run time (`%s` fragments) are missed; helper functions with thousands of callers are skipped when tracing.
`module_map.md`: 649 address ranges with the dominant class namespace or string topic (graphics and fixed-function code near `0x823E0000`, game components and modes near `0x82496000`, UI classes in the `0x82600000`
region, physics loaders at `0x82BE0000`, and so on); 20,824 of the 57,127 functions have a topic. [Inferred]

## 32. Coverage: what is documented and what is not
Documented from evidence: containers and packaging; the database schema, relationships, readers, writers and save schema; car model, texture, light, animation, audio and configuration formats; the upgrade tree; DLC
ownership and AI roster selection; the tunable settings; 6,744 classes and 22,770 function labels; the executable's SQL surface; the UI file family; the audio containers (sound banks, event files, audio XML); the UI package (section 35); the networking, session, replay and challenge classes (section 36); the subsystem class maps for AI, camera, navigation, file I/O, rendering commands and presentation (section 37); the ambient traffic list; known crash signatures and fixes.
Not documented: the bodies of most of the 57,127 functions (only roles inferred from strings, classes and calls), the UI scene-to-class binding, whether a DLC can add traffic cars (the list itself is section 33), the exact brand-screen frequency of queries,
the FMOD event layout between its strings and the codec of the non-XMA samples (section 34), the `.bgf`/`.bsg`/`.fbf` payloads beyond their headers and name vocabulary (section 35), the wire format of network messages (the classes and services are in section 36), and the game's behaviour on hardware beyond what was tested. A static reading of 14 MB of PowerPC code is not a complete
decompilation; use section 31 to continue from the right place.

## 33. Ambient traffic [Measured from the shipped XML; override behaviour Unknown]
Ambient traffic cars are chosen from an XML list, not from the database. The file is `GameTunableSettings/AIOpenWorld.xml` (about 18 KB, inside `GameTunableSettings.zip`, section 22).
Structure, as shipped:
- Three `<Settings name="...">` blocks: `freeroam`, `race`, `firsttimecareer`. Each has a `<CarList>` and a `<Densities>` element.
- `<CarList numInitialTrafficCars numInitialFestivalCars maxLoadedTrafficModels>` holds `<Car model="N"/>`. `N` is a `Data_Car.Id`: all 17 distinct ids in the file resolve to `Data_Car` rows (check with
  `SELECT Id, MediaName FROM Data_Car WHERE Id IN (...)`). Free roam lists 17 cars, the first-time career lists the same 17, the race block lists 6.
- Counts per block: free roam `4 / 2 / 4` (initial traffic, initial festival, loaded models), race `0 / 0 / 4`, first-time career `0 / 5 / 2`.
- Two cars in the list carry `maxactive="1" streamonly="true" largevehicle="true"`: a bus and a truck. They are the cars of the `bus` group.
- `<Densities>` holds `<CarGroups>` (`bus`: the two large vehicles; `4x4`: four sport-utility or pickup models) and two `<DensityList>` elements, `cartype="traffic"` and `cartype="festival"`, 17 `<Density>` entries each
  (85 `Density` elements in the whole file across the three blocks).
- A `<Density id min max [speed spawnAwayChance spawnInDistance spawnOutDistance]>` is one road class (the meanings of `min`, `max`, `speed` and the spawn fields are Inferred from their names and values). The XML comments name the classes: empty, freeway, low-density freeway, A-road, B-road, dirt road, main town,
  festival, and area-specific variants. `min`/`max` are fractions (for example 0.04 to 0.4), `speed` is the traffic speed, `spawnAwayChance` is a probability. Child `<BannedVehicles><Group id="bus"/>` or
  `<AllowedVehicles><Group id="4x4"/>` limits which groups spawn in that class (the shipped files only ever ban `bus` and allow `4x4`).
Reading: a car can be ambient traffic only if its `Data_Car.Id` is in a `CarList`; the density entry for the road class then decides how many and how fast. `maxLoadedTrafficModels` caps how many different
models are loaded at once, so a longer list does not mean more variety on screen. [Inferred from names and values; not run]
Consequences for mods: adding a car to the database does not make it traffic, and a car in this list that is removed from the database is a dangling reference [Unknown how the game reacts]. The file is read from the
base game archive and base files win over DLC files of the same path (section 3), so a DLC copy of `AIOpenWorld.xml` is not expected to replace it. Whether a `zipmount.xml` mount can redirect the
`GameTunableSettings` folder the way audio tuning folders are redirected (section 10) was not tested. [Unknown]

## 34. Audio containers: banks, event files, audio XML [Measured headers; codecs and event layout partly Unknown]
Full tables: `research/native-map/audio_banks.md`; script: `tools/native_map/audio_banks.py`.
- **Sound banks.** All 990 `.fsb` files are FMOD 4 banks (`FSB4`, version word `0x00040000`), 1.7 GB with 21,932 samples. Where the bytes are: 7 radio banks 1.17 GB, 12 voice-over banks 258 MB, 937 engine banks
  197 MB (`media/audio/cars/Engines/Soundbanks/LOD1`), then small banks for burbles and pops (6), turbo, supercharger, transmission, tires, horns, wind, suspension, damage, active aero, the electric motor (8 samples), collisions,
  UI sounds and track ambience. Engine banks come in sets per engine named `<Engine>_EngAmb`, `<Engine>_Exh`, `<Engine>_Int` (291, 328 and 314 files).
- **Header.** Little-endian: `id[4]`, `numsamples`, `shdrsize`, `datasize`, `version`, `mode`, then 24 more bytes (an 8-byte field and a 16-byte hash in the public FSB4 notes; the 8-byte field is non-zero in 988 of 990 files),
  `0x30` bytes in all; then the sample headers; then the sample data. `0x30 + shdrsize + datasize == file size` holds for all 990 files, so `shdrsize` and `datasize` are reliable when you need to size a bank.
  The last sample header ends 0 to 28 bytes before the data starts.
- **Sample header.** An 80-byte FSB4 record (`size` u16, 30-byte name, length in samples, compressed length in bytes, loop start and end, mode word, default frequency, volume, pan, priority, channels, distances), followed by
  platform data up to `size`. 14,325 samples carry mode `0x1000000` (the XMA flag in the public notes [External]); 5,494 carry `0x10000200` and have the bare 80-byte header, so they are not XMA; 2,110 carry `0x90000200`.
  The codec of the last two groups is not decoded. [Unknown] 18,677 samples are mono and 3,255 stereo; default frequency runs from 2,000 to 48,000 Hz (median 36,000); 18,002 samples loop over their whole length.
- **Electric motor bank.** `cars/ElectricMotor/ElectricEngine.fsb` has 8 mono XMA samples: two layers of four, named with the rpm bands 3,500, 5,500, 9,500 and 12,500, and its event file has an RPM parameter and a
  throttle parameter. The game ships a ready-made electric-motor sound; whether an engine tuning file can select it for a DLC car was not tested here. [Unknown]
- **Event files.** 38 `.fev` files, 3,502 wave entries. Magic `FEV1`, a version word (`0x400000` in 36 files, `0x370000` in 2 older ones), then length-prefixed NUL-terminated strings: project name, categories (`master`, `music`),
  event parameters, and for each wave a bank-relative name (`/<name>`) followed by its original source path (`wav/<name>.wav`). The binary structure between the strings is not decoded. The engine events use an RPM parameter, a throttle
  parameter and a `3dpandist` parameter. Navigation voice files (`SatNav_<lang>`), the voice-over banks (`VO_<lang>`) and the in-game UI bank each have an event file.
- **Audio XML** (38 files directly under `media/audio`, all UTF-8): `321VolumeConfig.xml` (one `<Shot>` of per-category volumes: ambient, car wind, collision, engine ambient, intake, exhaust, opponent, supercharger,
  suspension, tire, transmission, turbo), `AudioEngineConfig.xml`, `AudioMixerSnapshots.xml` (mixer snapshots, 104 KB), `BackfireTimerConfig.xml` and `BackfireVolumeConfig.xml`, `GlobalDSPConfig.xml`, `CockpitDSPConfig.xml`,
  `GlobalRolloffs.xml` and `GlobalRolloffsTraffic.xml`, `cars/Tires/Tires.xml` and `SurfaceChanges.xml`, `collisions/CollisionData.xml`, `tracks/colorado/Ribbon_00/*.xml`.
  `UI/ui4audio.xml` maps 762 UI actions to sounds: `<event play="<action>" group="<FMOD group>" cue="<cue>" [duration isLooping ggroup]/>` with scene overrides (`<scene name override>`).
  Radio: `radio/RadioSystem.xml` defines 9 `<RadioStation>` elements (attributes `name isOffStation reverbWetLevel volume dPanLevel musicLoops`), about 125 `<MusicTrack>` elements with time-of-day likelihoods, idents and DJ dialogue;
  its own comment says the `RadioSoundbankInfo_*.xml` files are produced by running the game with the command-line option `generateradiosoundbankinfo`. There is one `RadioSoundbankInfo_VO_<lang>.xml` per language (15) and one
  `RadioSoundbankInfo_Music.xml`. `vo/DialogueScript_<lang>.xml` maps 435 triggers to 1,028 `<Filename id soundbankIndex>` entries.
- **For mods.** A new engine sound is a bank under `Soundbanks/` plus tuning files that name it and the `zipmount.xml` mounts (section 10); an existing bank can be reused by name without building one. Building a new Xbox 360 bank
  means producing FSB4 with XMA samples (most engine samples here are) and is not covered by this guide. [Unknown: not attempted]

## 35. The UI package in detail [Measured headers, vocabularies and layout; payload decoding Unknown]
Extends section 30. Full tables: `research/native-map/ui_package.md`; script: `tools/native_map/ui_package.py`.
- **Layout.** `media/UI.zip` is 694 members, 136 MB uncompressed: `Scenes/ui4/` (641 members, 46 MB: the scenes and `EventNames.txt`), `New Map/` (3 files, 80 MB, plus 20 reference tiles under `Map Ref/`),
  three `.xpr` cube-map files (`TXCM` resources in an `XPR2` container), `Behaviors/UI4/` (5 Lua scripts), `ResPack.xml`, `cameras.xml`, `colorpicker.xml`, `credits.xml`, `SHLightSettings.xml` and
  16 `Intro_Subtitles/<lang>/Subtitles.xml`. Of the 230 scene names, 205 have all three files; 25 have a `.bgf` only.
- **BGF.** All 230 files begin with the same 14 bytes (`01 04 00 00 00 1A` then `AnarkBGF`) and carry big-endian fields; the canvas size at byte 22 is 1280 by 720 in 224 files and 800 by 450 in 6.
- **BSG.** All 205 files begin `01 04 00 00 00 00 00 18` followed by the magic written as `FGBkranA`, which is `AnarkBGF` with its bytes swapped inside the eight-byte unit: this file kind is stored byte-swapped relative to the BGF. [Inferred reason]
- **FBF.** Big-endian words: word 0 is 1008 (118 files), 1007 (81) or 1006 (6); word 1 varies (an object count in the files checked [Inferred]); word 2 is 3 and word 3 is 2 in all 205. Then the scene tree: records that
  start with a length-prefixed ASCII name (u32 length, text) followed by float transform data (`3F 80 00 00` = 1.0 is everywhere). The first object is always `Layer`.
- **Scene-tree vocabulary.** The most frequent identifiers are `Group`, `Stacker`, `Pill`, `Text_BUTTON`, `BUTTON_LOGO`, `LeftMarker`, `Rectangle`, `Text`, `Camera`, `Light`, `Scene`, many `Material<N>` names, font names
  (for example a Swiss 721 face in medium, bold-condensed and roman-condensed variants) and texture names ending `.TGA`. Of 633 distinct `.TGA` names, 491 match the file stem of an `.xds` member in the UI texture archives
  (below). The other 142 names were not found by stem. [Measured; rule Inferred]
- **UI events.** `Scenes/ui4/EventNames.txt` lists 950 event names (the older `Scenes/EventNames.txt` 578), almost all upper case: 216 begin `SHOW`, 152 `HIDE`, 90 `SET`, then `SB`, `STATS`, `ARROW`, `GROUP`, `INTRO`. Scene objects fire and
  receive these names; the native class that handles an event is not recovered. [Unknown]
- **Lua.** `Behaviors/UI4/` has 5 scripts: three plain source (hooks `onInitialize`, `onActivate`; one uses `dofile` on a `GAME:` path) and two compiled Lua 5.1 big-endian bytecode files (`onInitialize`, `onUpdate`,
  `onMoveOn`, `registerForEvent`, `fireEvent`). So scene behaviour can be scripted per object. [Measured]
- **Resource packages.** `ResPack.xml` is, in its own words, the definition file loaded by `CUI4ResourceManager`: 130 `<package name useHeap>` elements list `<scene path>` (96), `<font path size>` (11) and
  `<texture path>` (7) entries to preload; a nested `<package name="<scene path>">` names the dependency package of a scene. The `Global` package keeps the empty scene, the fonts, the popup menus, the loading scene and the
  controller-button textures resident. Whether a new scene must be added here to load is not tested. [Unknown]
- **Related archives.** `media/ui/Textures.zip` (79 MB, 3,312 `.xds`: `UI4/Logos` 1,565, `UI4/Upgrades` 479, `UI4/Common` 217, `P_Map/P_STA` 148, `UI4/DynamicLoading` 134, `UI4/BuyCar` 84),
  `media/ui/textures/Horizon.zip` (68 MB, 1,061 `.xds`: event tickets, map icons, activation-zone icons, carousel icons, kit-part tape), `media/ui/Fonts.zip` (39 members: `.dt` vector fonts and settings),
  loose CJK fonts under `media/ui/fonts/`, 76 files of intro and menu video (528 MB: 63 `.xml`, 9 `.wmv`, 4 `.def`), `media/ui/LoadingDefs.xml` (23 `<LoadingGroup>` and 174 `<Tip>` elements),
  `MapProfileFullscreen.xml` and `MapProfileMinimap.xml` (map symbol, texture, atlas and animation definitions).
- **Per-car gauges.** In the base-game car archives, 94 of 179 contain `DigitalGauge/Dash_<MediaName>.bgf/.bsg/.fbf` (medians 7 KB, 2 KB and 12 KB). A car with a digital dash is built from the same three-file family as the menus.

## 36. Networking, online services, replay and challenges [Inferred from class names, strings and the data files; not run online]
Full tables: `research/native-map/network_map.md` (96 classes); script: `tools/native_map/network_map.py`.
- **Transport.** The network layer is built on DirectPlay 8 (`DP8Queue`, `Network::DirectPlayPeer`, `CDirectPlayReceiveHandler`). Three connection classes share one base: `CClientConnection`, `CServerConnection`,
  `CPeerConnection` (`CConnBase`, 33 virtual slots each) beside `CPeerNet`. Messages are objects (`INetMessage`, `CNetMessage`, `CNetMessageManager`, `CNetMessageHandler`; `CUserMessages` and `CTimeSyncMsgs` are message groups).
  Shared game objects are replicated through an object manager (`CMultiscreen`, `CMultiscreenClient`, `CMultiscreenServer`, `CObjectManagerBase`, local and remote object info). `CRaceSynchronizationManager` is a message handler.
  `CNetThroughputAnalyzer` measures traffic. The binary layout of individual messages is not decoded.
- **Services.** Online features are asynchronous "title server" calls (`ITitleServerCallHandler`, `CTitleServerCallTask`). The tasks are named after their owners: garage-car sale at the autoshow, the auction house car picker,
  the clan invite board and clan member query, marketplace featured and popular content, the user's own cars, public lobby search settings, storefront files and livery upload. Other services: `CLspConnection` (a singleton with an
  upload-friends task), `UserAuthorizer`, `CSignedFileSecurity` and `Xls::IFileSecurity`, `Xls::ISStream` and `Xls::IUploaderCompletionHandler`, quality-of-service reporting, a TrueSkill reader (skill-based matchmaking),
  Xbox session operations (`XSessionOperation`, `IXSessionOperationCompletion`), and `Transfer::CTransferManager` with read and write work items for resource transfer. Without a title server these calls cannot succeed; the
  game then falls back to local defaults (below).
- **Party and lobby.** `PartyLobby` (a singleton with 44 virtual slots) is a state machine: states `Offline`, `Connected`, `ConnectingToGameLobby`, `InGameLobby`, and ten transition classes (host or client finding a public hopper,
  creating the game lobby, connecting to a private lobby or a public hosted match, leaving the party, returning to the party lobby, cleaning up). `InviteHandler` and `IInviteObserver` handle invites.
- **Hoppers (matchmaking playlists).** `CHopperGameOptions` loads game options from the title server. The offline fallback is `GameTunableSettings/DefaultRacesHopperOptions.xml` and `DefaultPlaygroundHopperOptions.xml`;
  for races the default list also comes from the database: `SELECT * FROM HopperRaces ORDER BY Id`, joined with `Tracks`. Option names seen as strings: `BucketId`, `Enabled`, `AmountOfChallenges`, `Challenge1` to `Challenge4`,
  `TagEnabled`, `NoTagBacksTime`, `GetAwayTime`, `NumHeats`, `NumSeriesRaces`, `ReverseSafeDistance`, `ForwardSafeDistance`, `TargetDistance`, `CarsPerUniverse`, `FirstCarHeadStartMeters`. The mapping of each option to a game mode is not decoded.
- **Replay.** `Replay::CReplayManager` and `CReplayStream` (32 virtual slots) record a stream of packets: `CReplayPacketAttach`, `CReplayPacketUpdate` and `CReplayPacketKeyframe`, for objects that implement `IReplayObject`.
  A replay is therefore keyframes plus per-object updates. [Inferred from class names]
- **Voice commands.** `VoiceCommand::CVoiceCommandComponent` (a game component) with a handler registry and `IVoiceCommandHandler`.
- **Free-roam challenges.** Classes `CDistanceDrivenFreeRoamChallenge`, `CDriveFromAToBFreeRoamChallenge`, `CSkillsFreeRoamChallenge`, `CSpeedCameraFreeRoamChallenge`, a coordinator singleton and four restriction classes
  (`Car`, `OneOf`, `Speed`, `TimeOfDay`). They read `GameTunableSettings/FreeRoamChallenges.xml`: 24 `<Challenge type id sponsorName sponsorLogo challengeName challengeDesc>` elements (14 drive-from-A-to-B,
  6 speed-camera, 3 skills, 1 distance), each with `<Reward credits>`, `<Options ...>` and `<Restrictions>`. A-to-B options are `start_location.x/y/z`, `start_radius`, `end_location.x/y/z`, `end_radius`, `min_players`, `bidirectional`, `time_limit`;
  speed-camera options are `num_cameras_to_flash` and `cameras_must_be_unique` with `<Camera camera_id>`; skills use `feat_id` and `target_feat_count`. Restrictions in the shipped file: time of day (11; `start_hour start_minute end_hour end_minute`),
  car (11; `MakeId` x8, `BaseCostMin`, `BaseCostMax`, `YearMax`) and speed (9; `min_speed`). The car restriction class reaches `Data_Car`, `Data_Drivetrain`, `Data_Engine` and `Data_Motor`, so a car's make, price and year
  decide whether it qualifies; a car added with a new make id or price is judged by the same fields. [Inferred]
  Sponsor skill challenges are a separate file, `SponsorshipChallenges.xml`: 15 `<Challenge>` elements with 150 `<Rank skill grade number credits>` entries.
- **For mods.** Offline play is expected not to depend on the transport or service classes [Unknown: not tested], but anything that adds a game mode, a playlist or a challenge must fit these data files and the hopper table. Whether a DLC can supply its own copy of a
  tunable file is untested (section 33).

## 37. Engine subsystems at a glance [Inferred from class names, bases and strings; data-file links are inferred]
Tables: `research/native-map/ai_navigation_io_map.md` (348 classes), `render_presentation_map.md` (186), `deferred_commands_map.md` (548 command classes); script: `tools/native_map/namespace_map.py`
(`python namespace_map.py DIR OUT.md NS1,NS2,...`). Use these to find which class family a function address or a data file belongs to.
- **Open-world AI** (`AIOpenWorld`, 76 classes). Traffic is a set of behaviours (`Traffic::CNormalBehavior`, `CLaneChangeBehavior`, `CStopBehavior`, `CJunctionBehavior`, `CCollisionReactionBehavior`, `CDeadBehavior`) managed by
  `CBehaviorManager`, with junction rules, lane changers, path and collision prediction, a vehicle selector and a spawn manager (`CRandomSpawnManager`, density calculator and density values; settings come from
  `AIOpenWorld.xml`, section 33). `Racing::` classes drive free-roam opponents and race behaviours (`CFreeRoamBehavior`, `CRaceBehavior`, `CFreeRoamVehicleSelector`, route planner, route simulator, recovery tracker).
  `View::` classes decide which cars are visible. The track is cut into segments (`CTrackSegmentation`).
- **Racing AI** (24 classes). Driver types: `CHumanDriver`, `CAIDriver`, `CAIDrivatar` with `CDrivatarDriverBase`, `CCrashRecoveryDriver`, `CPassingDriver`, `CMetaAIDriver`, `CLearningAI`. Racing lines are compressed
  piecewise curves (`CCompressedRacingLine`, `CCompressedSegment`, `CPinnedCubicSegment`). A car is an `IAICar` or `INonAICar`; `CAICar` has 31 virtual slots.
- **Feats and challenges** (`RaceFeats`, 28 classes). One class per skill: 180, air, burnout, clean speed, combo, destruction, donut, draft, drift, J-turn, multiplier, near miss, pass, tag holder, threading the needle,
  two wheels; plus `CDestructionChallenge`, `CFameRankChallenge`, `CSkillChallenge`, `CSponsorshipChallenge` with a manager. Data: `HorizonFeats.xml`, `RaceFeats.xml`, `SponsorshipChallenges.xml`, `Fame.xml` (section 22). [Inferred pairing]
- **Navigation and maps** (65 classes). A map is built from entities (`CMapEntityBoundary`, `Route`, `Tiles`, `Track`, `Way`) and drawn through render-profile layers (background, career icon, ellipse, icon, anti-aliased
  line, screen fill, sorted group, text, tiles, vehicle icon) and symbolizer layers, with a deferred renderer (`CMapRendererDeferred`). These layer kinds match the element names of `media/ui/MapProfileFullscreen.xml` and
  `MapProfileMinimap.xml` (`Symbolizer`, `Texture`, `Atlas`, `Group`, `Filter`; section 35). Route planning uses `CRoutePlanner` with pluggable heuristics (simple distance, road, and speeds read from the global registry,
  `CGlobalRegistryRoutingHeuristicSpeeds`). **Satellite navigation** (`SatNav`, 18) is a command state machine: set destination, to nearest event, to next event, show command, toggle race line, with a voice-command handler.
- **Cameras** (28 classes). Modes are classes: bumper (high), cockpit, driver, follow (normal, high, low), hood, rear view, side, wheel, free (plus free-on-target-car and free-on-track), photo mode, fixed-free, orthographic,
  no-car, a countdown camera (`C321Camera`), a pre-race track camera, a camera-animation player and a blend camera. The photo-mode camera has the same 37 virtual slots as the free cameras, which suggests a common base. [Inferred] The UI cameras are in `cameras.xml` (section 35).
- **File system** (`IOSys`, 50 classes). Storage is a stack of volumes (`IVolume`: `CFileVolume`, `CCompressedVolume` for archives, `CMultiVolume` which presents several volumes as one, `CSaveGameVolume`,
  `CScratchVolume`) and stream wrappers (buffered, compressed, windowed, circular, versioned, text, XML, and signing and security streams). An asynchronous file manager and devices (`CAsyncFileDevice`, `...HD`) read data;
  `CFileEvent*` classes (open, read, write, seek, close, size, delete, directories) describe file operations. Strings show checks for dirty-disc errors and for operations on secure files. The multi-volume layer is the likely
  home of the "base files win over mounted packs" behaviour seen in packaging (section 3). [Inferred]
- **Database** (`Database`, 8 classes). `CDatabase` and `CRecordSet` wrap SQLite; `CSqliteMemoryFile`, `CSqliteMemoryStream` and `CSqliteStreamMultiInstance` indicate the database is opened from memory streams, and
  `IDatabaseFunc` is the interface of the native SQL functions (section 18.3). **Scripting** (`X_Script`, 11): typed script values (bool, colour, float, int, u8, string), commands, a scope and a manager.
  **Core**: scheduler, overlapped-I/O manager, system-event manager. **Registry**: `CRegistry`, `CGlobalRegistry`, `CFileSystemRegistry`, `CValue` (section 22). **Memory**: heaps, dual heap, small-block allocator.
  **Track positioning**: drive spline, track matrix and its builder, segmentation, trigger zones (`CTriggerZone`, `CMobileTriggerZone`).
- **Radio** (`RadioSystem`, 12 classes): station, playlist, music track, ident, simple and special dialogue, festival update, and sound-bank info; these are the objects of `RadioSystem.xml` and the
  `RadioSoundbankInfo_*.xml` files (section 34).
- **Online data sources and downloaders** (`Forza`, 84 classes; `Forza2`, 28). Auction house (search, my auctions, my bids; create, bid, resolve and restart transactions), clans and invites, gifting of cars and credits,
  scoreboards, lobby and party player lists, storefront files and pages, DLC metadata and digest downloaders, a database patch downloader (`CDBPatchDownloader`, `CDBPatchInfoDownloader`, `CDBPatchSaveTask`),
  replay, photo and livery file transfer, and a consumable-asset manager. `Forza2` holds the profile and player layer: `CForzaProfile`, `CGamerProfile`, `CPlayer` (76 slots), `CLoginManager`, `CLivePresenceUser`,
  `CProfileSaveGameVolume`, replay record and watch components, a tag component. A game-database patch download exists in code; how a downloaded patch is applied to `gamedb` was not traced. [Unknown]
- **Rendering and presentation.** Game code never draws directly: it queues commands. `CRenderThreadLink` (61 command classes) carries frame control and global switches (begin and end render, depth of field, motion blur,
  shadow system, forced cascaded-shadow culling, photo mode, replay mode, large-shot tiles for big screenshots, post effects from camera or time of day); `CRenderAdapterLink` (104) mirrors the fixed-function renderer
  (lights, streams, textures, predication, text); `CGraphicsStreamDeferred` (33) carries raw draw calls, shader and state sets. `CPresentationDeferred`, `CCarPresentationDeferred`, `CTrackPresentationDeferred`,
  `CPresentationCameraDeferred` and `CUI4PresentationRendererDeferred` carry what to show. Presentation has three implementations: `Presentation_Unified` (the real one), `Default_Presentation` and `Null_Presentation`.
  Procedural geometry (`proceduralGeometry`) covers billboards, light glows, light maps, models, points, vegetation, characters and animated scenes.
- **Car presentation commands** (useful when a car looks wrong). The queue includes `Load` and `LoadLOD0` (a separate high-detail load), `ReattachPart`, `SetBodyLengthOffset`, `SetBrakeLightAmount`,
  `SetDRLightAmount`, `SetDigitalGaugeTexture`, `SetAnalogGaugeInformation`, `SetDLCGeneric`, `ResetDirt`, ambient and blurred cubemap textures, drop shadow texture, driver visibility and head-position variable, tire smoke
  and collision effects. [Observed as command names]
- **Car audio initialisation phases.** `CCarAudioDeferred` shows the order the game loads a car's sound: phase 1 preload XMLs, phase 2 serialize the car-model tuning (`_CMT`) and engine tuning (`_ET`) files,
  phase 3 serialize the harmonic tuning (`_HT`), phase 4 add and load the `.fsb` banks (batched), phase 5 finalize. This matches the file chain in section 10: a missing or wrong file at an earlier phase stops the later ones.
  [Inferred from the command names]

## 38. Quick start, what a mod can change, and playbooks [Toolkit tested offline on real game files; packages not yet installed in the game]
**Start.** (1) Ask the user for the path of their own game folder (`4D5309C9`, the Games on Demand folder or an extracted copy), which platform they test on (Xenia or a modded 360) and the Xenia content folder if they use Xenia.
Never ask for the game files themselves. (2) Run `python toolkit/selftest.py GAME`; all twelve steps must pass. (3) Explore with `python toolkit/fh1tool.py GAME ...` (commands in `toolkit/README.md`); check names and columns there
instead of guessing (rule 2). (4) Pick a playbook below. (5) Build into a new output folder, run `verify_folder` and `fh1tool.py check`, then tell the user exactly which folder to copy and wait: never install while Xenia is running, and
back up the save first. (6) Follow the test protocol (section 17): one change per test, read `xenia.log` after a crash.

**What a mod can change.** Everything works by adding a DLC package (section 3) that the game overlays on its own data.

| goal | possible? | how, evidence |
|---|---|---|
| add a car (rows, model, textures, lights, audio tuning, names) | yes | section 13; the toolkit reads and writes each file; converting models, textures and lights is up to you (sections 6 to 8). [Tested in game] |
| add a brand | yes | `List_CarMake` row, logo `.xds`, superset string table (playbook B). [Tested in game] |
| add an upgrade tier or a drivetrain or motor swap to a car | yes | sections 11, 13, 26 (playbook C). [Tested in game] |
| add an engine or an electric motor | yes | section 11. [Tested in game] |
| add or change engine sound | partly | tuning files must be reachable through a mounted folder (section 10); whether a bank sounds right needs a listening test. [Partly tested] |
| rename a car or a make, add text | yes | superset `.str` files (section 5). [Tested] |
| hide a car | only your own | `IsUnicorn = 1` on a row your package adds; base-game rows cannot be edited (section 13). [Tested for added cars] |
| change a base-game car, a base-game database row, or any base-game file | **no** | base-game files win over DLC files with the same path, and a primary key the game already has breaks the boot-time merge (sections 3 and 14). [Tested] |
| change a tunable file (traffic, challenges, feats, fame, hoppers) | **unknown** | those files are read from the base game archive (sections 22 and 33); a mounted folder like the audio ones may work. [Unknown: untested] |
| change game logic or physics constants in the executable | out of scope here | needs patching the executable (sections 15, 18, 19); this guide documents no patch workflow. [Unknown] |

**Playbook A: inspect.** `tables`, `tables <Table>`, `db "select ..."`, `car <MediaName>`, `str en <Table>`, `zipls <archive>`, `carbin`, `xds`. Use the data dictionary (section 23) for what a column means and section 4 for how tables relate.

**Playbook B: add a brand.** (1) `db "select max(ID) from List_CarMake"` and pick a new ID that no installed pack uses (`fh1tool.py packages` lists the other packs; `check` finds collisions). (2) Read the columns of `List_CarMake` (`tables List_CarMake`).
(3) Make the logo: use an existing logo texture of the same kind as a template (`zipls media/ui/Textures.zip "Logos"`), then `xds.encode_like(template, rgba)` with your artwork at the template's size. (4) Add the row with `dlc_builder.new_merge_db(...)` and `insert(...)`.
(5) Strings: read the base `List_CarMake.str` of **every** language (`str <lang> List_CarMake`), append your entry with a key that neither the game nor any other pack uses, write each with `lsb2.write`, and pass them as `string_tables={"List_CarMake": {...}}`
(one mounted table wins, so it must contain every existing key). (6) `build_files`, `write_xenia_folder`, `verify_folder`, `fh1tool.py check`. (7) Test: the Autoshow brand list, then a car of that brand. Sections 4, 5, 7, 13.
[Recipe tested in game; toolkit steps tested offline]

**Playbook C: add an upgrade tier or swap to your own car.** Follow section 11 (engines, drivetrains, electric cars) and section 26 (the upgrade tree): a new `Upgrades` row, the matching part-table row at the same level, two entries in a superset `Upgrades.str`,
and `<table Name="Upgrades"/>` in `stringtables.xml` (pass `string_tables={"Upgrades": ...}`). A missing saved part id crashes the boot, so keep the stock part available at the level you replace (section 14). [Tested in game; toolkit steps tested offline]

**Playbook D: add a car.** The largest job. Per car you need: database rows in every car-keyed table (sections 4 and 23), model files (section 6; `fh1_carbin` reads and writes them, `fm4_carbin` reads FM4 models), textures (section 7; `xds`), lights (section 8),
pop-up headlights if any (section 9), audio tuning (section 10), `CarAttribs.xml` and driver files (section 12), a thumbnail (`Data_Car.Thumbnail`) and name strings (section 5). The carbin editor (section 39) edits and extends the model files. A converter that automates FM4-to-FH1 conversion is not part of this repository:
build the steps from the sections above and the recipe in section 13, one stage at a time, testing each (open the Autoshow, buy, drive, switch cars, relaunch, load the save). Keep a car's files in one zip, zips about 900 MB, console packages under 3.8 GiB (section 3).

**Playbook E: package, install, test.** `dlc_builder.build_files`, then `write_xenia_folder(files, out, offer, name)` (adds the `.header`) or `write_stfs` for a console, then `verify_folder`, then `fh1tool.py check GAME merge.slt --package <each other pack>`.
Pick an offer number that no other pack uses. Then ask the user to close Xenia, copy `xenia/<package>` to `<xenia>/content/0000000000000000/4D5309C9/00000002/` and the `.header` file to `.../4D5309C9/Headers/00000002/`, back up the save, boot, and report.
Read `xenia.log` before changing anything.

**If something fails.** Boot crash or endless load: section 14 first (duplicate keys, case-duplicate zip members, wrong archive order, missing stock part). A car that loads but looks, sounds or drives wrong: change one stage at a time (model, textures, lights,
audio, physics) and compare against a base-game car of the same kind with `fh1tool.py car <MediaName>`.

## 39. The carbin editor (`toolkit/carbin_editor/`) [Round trips tested offline; added models, texture, car copy, name, wheelbase and collision spheres tested in the game]
A local web app for `.carbin` models: `python toolkit/carbin_editor/server.py --game <game> --dlc <4D5309C9\00000002>`. See `toolkit/carbin_editor/README.md`.
- **What it edits.** Parts (sections), their sub-meshes (material, LOD) and the draw lists that decide what is drawn at each LOD; light records and exhaust emitters in the header; it exports parts to OBJ and imports an OBJ over a part.
  **Add a model:** drop an FBX, GLB/glTF or OBJ, place it with a gizmo, choose the part, LODs and materials; the geometry is added onto the part (existing vertices untouched) or replaces it.
- **Where it reads and writes.** Two locations: the game (`media/cars/<Car>.zip`; writable only for an extracted game folder) and DLC package folders (`Media/DLCZips/*.zip`, members `media/cars/<Media>/*.carbin`; console STFS packages are read only).
  Saving replaces one member of the zip and copies every other member byte for byte (order, compressed data and the Xbox LZX extra field `0x1123` are kept), after a `.bak` copy; nothing is written while Xenia runs.
- **Rule that follows from section 3.** The game prefers its own files to a DLC's files with the same path, so an edit to a base-game car only takes effect when saved into the game; a DLC can change only cars it adds.
- **Verified encodings** (section 6): position = `xyz * w * |bmax| + offset`; uv through the per-sub transform; frame quaternion; 4 baked-lighting bytes fitted from the car's own vertices; strips with restart `0x00FFFFFF`.
  Offline checks on the base cars: re-encode error under 0.07 mm; OBJ round trip of 73 parts keeps triangle counts per LOD and the draw lists; adding a model keeps the old vertex bytes.
- **Still untested in the game:** automatic LODs of a heavy model, a copy made from a car inside a DLC, the `_lod0` and `_cockpit` copies of an edit, edited lights, a changed sphere count, skeleton and animation edits. Change one thing per test (section 17).
- **Textures, files, strings (added after the first release of the editor).** The viewer can show the car's own atlases through each sub-mesh's uv0 (`nodamage`, `lights`, `interior`, `damage`; `_LOD0` variants for `_lod0` files); an Archive tab lists every file of the car's archive and can replace a texture from an image (mips regenerated, section 7), a text file or any file in place; a Strings tab browses `.str` tables. Offline checks: replace-texture, replace-text and replace-raw on a throwaway DLC package keep all other members; wrong-size images are refused.
- **Copy a car into a new DLC car** (`toolkit/clone_car.py`, also the Save as a new car button): measured on the base game, a car's ids live in several spaces: car (`Data_Car.Id`; columns `Ordinal`, `CarId`, `ModelId`), engine (`EngineID`), drivetrain (`DrivetrainID`), body (`CarBodyID`) and torque curves (`TorqueCurveID` = engine x 1000 + n); a DLC car owns its own body, engine and drivetrain rows (linked through `List_UpgradeCarBody`, `List_UpgradeEngine`, `List_UpgradeDrivetrain`), plus rows in about 45 per-car tables, a `ContentOffers` row and a `ContentOffersMapping` row (`Id` = offer x 1000, `ContentId` = car id, `ContentType` 1). New ids stay below 32768. A pack that ships no strings must not declare string tables. **[Tested in the game: a copy of the Alfa 8C booted, showed in the Autoshow, was bought and drove.]**
- **Added geometry in the game:** a real model (the Alfa race wing added onto another car's body part at LOD 1 to 5) and cubes added at known positions showed in the right place and orientation; the main carbin is what the driving view draws. A texture painted into an atlas rectangle showed on the added model and on any part that already read that rectangle. [Tested in the game]
- **Physics, body numbers, name, skeleton, animation** (section 40): a Physics tab edits collision spheres and hull and the new car's `Data_CarBody` numbers; the new-car button takes a plain-text name; a Skeleton tab moves and turns bones and stretches animations. FBX (binary, centimetres) and GLB files were loaded in the browser and added onto a part with clean checks.
- **Not in the editor** (compared with the FH6 tool): converting models between games (the FM4-to-FH1 converter is separate), the materials (shaders) themselves, adding bones, editing animation curves.
- **For an AI:** use `toolkit/carbin_geom.py` directly (`parse` a carbin with `fh1_carbin`, then `transform_section`, `rebuild_section`, `append_pieces`, `export_obj`, `parse_obj`, `set_light`, `validate`) when a task is scripted rather than interactive.

## 40. Collision physics, body numbers, skeletons, and which texture a material reads [Measured on all 176 base cars; effects marked Tested were seen in the game]
- **`physicsdefinition.bin`** (in every car archive; big-endian; `toolkit/physdef.py` reads and writes it and rebuilds all 176 base cars byte for byte): `u32 1, u32 13, u32 1`; 30 floats (`[0]` = 0.01, then two 3x3 blocks that look like inertia tensors, then eleven floats; their meaning is **Unknown**); `u32 n` and `u32 0`;
  `n` records of 22 bytes, the first `n - 1` of which are collision spheres (`u16 flags` 2 = body sphere, 1 = small corner sphere, `f32 x, y, z, radius`, `u32 0`); the last record starts an offset table. Offsets in that table are relative to 18 bytes before the end of the `n` records, so they stay valid when spheres are added or removed.
  After the table: the convex hull (24 points of `f32 x, y, z, 0` in 143 cars, 20 in 8, 28 in 1, 8 in 3, none found in 21), then face and index tables. Adding or removing spheres rebuilds and reads back [Tested offline]; the game was not run with a changed sphere count.
- **`Physics/MAXData.xml`** repeats some of it as text (`Misc` wheelbase, track widths, ride heights; `Collision/BoundingBox`; `CollSpheres`) but is not authoritative: some cars list 4 spheres where the `.bin` has 35. The editor writes both.
- **Orientation.** The spheres and the hull use the model's orientation (front = -z); their extent against the mesh extent is 1.05 (x), 1.08 (y), 0.99 (z) in the median car, and drawn over the car they follow the body [Measured]. `Data_CarBody.PristineBoundingBox*` is the mesh bounding box with **z negated** (x identical; 100 of 115 front/rear-asymmetric cars), and its floor (MinY) is lower than the body because it includes the wheels [Measured].
- **Effect in the game** [Tested in the game]: with every sphere radius multiplied by 2.5 the car stopped well short of a barrier, so this file is what the game uses for crashes; spawning and driving were normal (the spheres do not collide with the ground). `Data_CarBody.ModelWheelbase` + 0.15 moved the wheels apart by that amount, so wheel positions come from the body row (`ModelWheelbase`, `ModelFrontTrackOuter`, `ModelRearTrackOuter`, ride heights) [Tested]. Plain text in `Data_Car.DisplayName` / `ModelShort` shows as the car's name [Tested].
- **Which texture a material reads.** A sub-mesh stores no texture reference (the two unknown blocks of its record are the same for every material). Materials are shaders: `media/shaders/Cars/*.fxobj` (153 files) and `CustomCarShaders.xml` (145 `Value name=... technique=...` rows). Strings in each `.fxobj` list its samplers:
  every shader has `baseSampler` (the car's body atlas); 14 materials (`detail_glass_*`, `head_light`, `lights_*`, `reflector`, `reverse_light`, `tail_light`, `textured_reflector`) also read `LightsSampler`; `cockpit_*` shaders read `CockpitInteriorHiSampler` and `CockpitAOSampler`; `engine_*`, `brake_*` and `rim_*` shaders read engine, brake and wheel diffuse textures; carbon fibre, grille, cloth, leather and tread materials read their own textures with tiling uv [Measured].
  Body atlas (`nodamage`, `damage`, with `_LOD0` sizes) coverage by uv: 95 to 98% of the atlas in the cars measured; the main and `_lod0` models share one layout (overlap 0.96 to 0.99); the cockpit model fills the interior atlas, which the main model also reads [Measured]. A texture for an added part therefore costs area that other parts read; the editor offers the largest free rectangle after you choose parts to give up. A painted checker showed on an added cube in the game [Tested].
- **Skeletons and animations** (`car_skeleton_LOD05.gr2`, `car_animations_LOD05.gr2`, only cars with moving parts; `toolkit/gr2_skeleton.py`): a bone's world matrix is `local * parent world` (row vectors, local = scale-shear * rotation, translation in the last row); the stored `InverseWorldTransform` equals the inverse of that to 1e-5 on every bone tested [Measured]. Units are 3ds Max units, 20 per metre, `game = (x/20, z/20, -y/20)`; on the Reventon the hinge bones land within 0.2 m of the vent parts' pivots [Measured]. Editing a bone's translation or rotation and rewriting its and its children's inverse matrices, and stretching an animation (`Duration` and each curve's `OneOverKnotScale` or `OneOverKnotScaleTrunc`), keep the file size and read back [Tested offline]. Not run in the game.
