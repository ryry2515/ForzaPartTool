# FH1 desktop editor design
Approved scope: user authorized the recommended embedded desktop approach and all necessary local work on 2026-11-04.

Build a dedicated Windows application using the existing Python backend and Three.js viewport, embedded through pywebview/WebView2. No external browser and no console window in the release. Keep explicit headless mode for tests. Close the backend with the window. Choose an available loopback port.

Startup opens Settings if the game is absent or invalid. Settings retain validated game and DLC paths, offer native folder/file selection, and explain errors. Cars provides one searchable list across game and installed DLC, with right-click Import. Preserve model selection for main, LOD0 and cockpit files.

Parts remain selectable and transformable with undo/redo. A server-side part clipboard survives opening another car. Donor browsing inspects a separate model without replacing current edits. Copy/paste adds a new independently selectable section, repairs draw lists and buffer keys, and maps donor material names to existing destination materials. Report that destination textures/shaders are used; do not pretend donor texture atlases were transferred.

Keep FBX/GLB/glTF/OBJ placement and material mapping. Export creates a new DLC car with independent IDs using existing clone/packaging routines; it does not overwrite installed game files. Verify emitted package contents and edited geometry. Runtime gameplay remains a separate test.

Approaches: embedded existing UI (selected, preserves format tooling); complete native UI rewrite (more work and regression surface); external browser app mode (fails requested desktop integration).
