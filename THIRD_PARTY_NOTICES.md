# Third-party notices

| what | where | license |
|---|---|---|
| three.js 0.160.0 (rendering, `OrbitControls`, `TransformControls`, `FBXLoader`, `GLTFLoader`, `OBJLoader`, `BufferGeometryUtils`, `NURBS*`) | `toolkit/carbin_editor/vendor/three/` (unmodified files from the npm package, license text in `vendor/three/LICENSE`) | MIT |
| fflate (used by three.js's `FBXLoader`) | `toolkit/carbin_editor/vendor/three/examples/jsm/libs/fflate.module.js` | MIT |
| pywebview, pythonnet, clr_loader, cffi, pycparser, proxy_tools, bottle, typing_extensions | Embedded Windows desktop host and dependencies; bundled license texts in `Third-party licenses/` | See bundled license texts |
| NumPy | Model geometry processing; bundled license texts in `Third-party licenses/` | BSD and bundled component licenses |
| Microsoft WebView2 SDK assemblies | Bundled by pywebview; uses the system-installed WebView2 Runtime | Microsoft WebView2 redistribution terms |
| Oodle1 decoder in `toolkit/gr2.py`, a port of libopengrn's `oodle1.c` ([opengr2](https://github.com/arves100/opengr2)), itself derived from nwn2mdk's `gr2_decompress.cpp` | `toolkit/gr2.py` (the file keeps its MPL-2.0 header; MPL-2.0 applies to that file) | MPL-2.0 |

`toolkit/fh1_carbin.py` follows the carbin layout documented in [rustybusgaming/fh1_rewrite](https://github.com/rustybusgaming/fh1_rewrite) (a reference for the layout; no code copied). Everything else is original work under the MIT license in `LICENSE`.

"Forza", "Forza Horizon" and "Xbox 360" are trademarks of Microsoft. This project is not affiliated with or endorsed by Microsoft, Turn 10 Studios or Playground Games. It contains no game files, textures, models or audio.
