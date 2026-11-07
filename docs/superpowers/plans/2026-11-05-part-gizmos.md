# Direct viewport editing for car parts

The user wants the same visible drag/rotation handles used for custom-model placement on existing or already-merged car parts. Prior permission covers implementation without another planning approval.

Design: one shared TransformControls with explicit ownership (selected car part or pending custom model). A separate part anchor sits at the selected part's pivot. Its matrix delta previews geometry movement without changing vertex buffers. Releasing commits that exact affine matrix through the existing undoable API; cancellation restores the preview. The backend can apply that same world matrix to matching parts in main and LOD0 transactionally.

1. Add meaningful matrix/paired-transform API regressions and controller lifecycle tests.
2. Implement a focused part_gizmo.mjs controller, affine transform validation, and paired section-list mapping.
3. Connect selection, visible Move/Rotate/Scale controls, W/E/R, Escape, asynchronous reloads and custom-model ownership.
4. Verify actual pointer dragging and rotating, undo/redo and export/reopen in the native app.
5. Review, package test build 5, update the local shortcut while preserving the user's current window.

Do not alter game archives during verification. Keep numeric inputs available for precision.
