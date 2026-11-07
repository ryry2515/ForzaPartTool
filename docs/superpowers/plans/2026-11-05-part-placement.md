# Part placement and merging implementation plan

**Goal:** Copy/paste XYZ position and merge imported or existing meshes into existing named parts, optionally in main and LOD0 together.
**Architecture:** Keep the existing desktop/Python/Three.js stack. Geometry operations retain section identities. Paired edits are staged transactionally and participate in undo/redo. Existing upgrade names remain the targets; new upgrade database rows are outside this update.
**User direction:** “get going while i have a shower”; implement existing slots using sensible defaults.

## Tasks
- [x] Add geometry regressions for pivot alignment, merge preserving world position/material UVs, empty destinations and invalid targets. Observe failures, implement helpers and rerun.
- [x] Add API regressions for paired main/LOD0 staging, missing counterparts, and undo/redo restoring staged files. Implement transactional operations.
- [x] Add part/model context menus, XYZ clipboard and merge chooser. Offer current model or main + LOD0; keep empty sections selectable. Add pure browser-helper tests.
- [x] Run Python/API and JavaScript checks, inspect desktop UI, package a separate 1.2.0 candidate and document limitations.

## Review focus
- Pivots represent position; paste translates vertices and pivot without copying rotation/scale.
- Empty target slots keep their names and header references.
- Missing paired section/file fails without changing either model.
- Main and LOD0 have different internal LOD numbers; paired merge uses the receiving model's LODs.
- Undo/redo must restore staged counterpart files as well as the active model.

## Execution ledger
Existing isolated worktree: feature/fh1-desktop-editor. Preserve pre-existing dirty files. No commit/push/release requested. Candidate packaging only.

Review resolved: preserve small nonzero UV ranges; stage paired files with actual archive member case; support placement-coordinate undo/redo without undoing unrelated geometry.

Verification: 28 Python tests passed, 5 Node tests passed. Packaged desktop smoke passed with 178 cars, coordinate paste, paired wing-to-body merge, both staged files, and no JavaScript errors. ZIP CRC check passed and SHA256 matches sidecar. Sandbox WebView2 debugging was unavailable; identical desktop test passed in the normal Windows session. Temporary headless servers stopped. No installed game archives were written by feature tests. In-game testing remains outstanding.

Artifact: Outputs/carbin_editor_120_preview1/FH1-Carbin-Editor-1.2.0-preview1-windows.zip (25 MiB).
