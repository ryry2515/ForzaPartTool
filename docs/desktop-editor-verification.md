# FH1 desktop editor 1.1.0 verification

Verified on this Windows PC, 2026-11-04.

- 12 pytest tests pass: transactional path settings, malformed settings, arbitrary DLC folders, independent part clipboard, effective secondary UV preservation, draw lists and resource keys, cross-LOD paste, donor isolation, undo/redo, Xenia/STFS export/read-back, invalid model rejection, overwrite refusal, and unique IDs across repeated exports.
- Existing toolkit selftest: 17/17 steps passed on the user's own FH1 GoD source, including compression, models, textures, physics, skeleton, clone database checks, request authorization, and DLC packaging.
- Native UI smoke: missing-path Settings onboarding, native dialog bridge restricted to choose_path, 697 base/DLC entries, context-menu import, transforms, donor copy without replacing edits, paste, undo/redo and custom OBJ placement. No captured JavaScript errors.
- Native lifecycle check: closing the app window stops the backend and releases its port.
- Final console-free packaged EXE smoke: Settings starts, embedded viewport loads, game/DLC indexing returns 697 entries, Alfa 8C imports with 33 parts and paste adds a 34th, no JavaScript errors. Screenshot: packaged-final.png in the local checkout (ignored by Git).
- Release SHA-256: 94225e601d7977df2abab78a5f741c95273f59cb7084a6c8fdc05679c706e3d8.

Independent review found a copied UV1 transform loss, which was reproduced with a failing regression and corrected. It also identified baseline export validation/overwrite/ID risks; these now have regression coverage and protections.

Limitations: these are offline and application tests, not a new Xenia/hardware gameplay test. Donor part copy transfers geometry with destination materials/colour swatches; it does not transfer donor atlases, collision, skeleton or animation bindings. Main, LOD0 and cockpit files remain separate; stage each edited file for export. FBX/GLB/glTF loaders remain available; the new native placement test exercised OBJ. Wheels supplied through separate game assets are not assembled into the body-model viewport.

Original release and original source checkout were left intact. Implementation is kept on feature/fh1-desktop-editor in this isolated checkout. Native worktree creation rejected the nested source repository; a Git worktree under Outputs was used instead. No publishing, merging, or installed game modification was performed.
