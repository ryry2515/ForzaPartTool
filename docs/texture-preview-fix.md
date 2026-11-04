# Texture preview fix — 1.1.1

User report: Focus RS500 showed unrelated atlas graphics over the windshield, grille, trim and interior when textures were enabled.

Root cause: the viewer chose one texture using the part name, then applied it to every material in that part. A bumper has multiple shader families; windows/chrome/procedural materials must not receive the body picture atlas, and low-detail interior geometry uses the body atlas rather than the cockpit atlas.

Changes:
- Per-material preview profiles with lamp/body/interior/shared-carbon/shared-grille choices and appropriate glass, paint, chrome and dark trim properties.
- Auto material textures enabled by default; explicit raw-atlas inspection modes remain labelled as such.
- Staged texture images are used in the preview. Adding another image preserves previously staged images. Clearing staging invalidates the viewport and Add model atlas image caches.
- In-flight image responses cannot repopulate caches after changing cars/textures.
- Corrected partial geometry reload callback passing its array index as a generation number. Failed custom-model commits retain the placement preview.

Verification:
- Reproduced the original Focus appearance in 1.1.0, then compared the same model/camera with the corrected source. Unrelated window/grille/trim graphics are removed.
- 14 pytest tests pass, including new staged-preview and multiple-staged-paint tests.
- Node per-material atlas/glass/paint/procedural material tests pass; existing library model-choice tests pass.
- Full native smoke passed: 697 game/DLC entries, donor copy/paste, undo/redo and OBJ placement.
- Final 1.1.1 packaged EXE smoke passed on the Focus: 36 original parts, 37 after paste, no JavaScript errors, clearing staged textures reloads its displayed atlas.
- Independent review identified the Clear staged cache issue; the native regression failed before the correction and passed in the final packaged build.

This fixes the editor preview and staged texture handling. Preview shading is a Three.js approximation, not an exact reproduction of the FH1 renderer. No installed game assets were rewritten during testing. The original app and user's open editing session were preserved.

Release SHA-256: f3734bfb142abc5f6fb5421a36483716301feb21c312e874800edab2555713fe
Local visual comparison (Git-ignored): packaged-focus-before.png and packaged-focus-after.png.
