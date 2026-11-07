# Custom model and same-car save

User authorized implementation of the requested editor changes, including a custom-model button and exported double-sided geometry.

1. Add regression tests for opposite-facing exported triangles and paired model import.
2. Add atomic multi-member ZIP replacement; save staged models/textures and regenerated stripped companions without changing the car identity.
3. Add Custom model and Apply to existing car toolbar actions; default merges to both model files; keep coordinate and empty-slot tools.
4. Verify upgrade schema before offering new shop upgrades; preserve existing upgrade assignments.
5. Port the tested v2 clipboard/settings reliability fixes, run tests and native smoke, review, build a new tester ZIP.

Read-only GoD/ISO/STFS inputs must receive a useful explanation or a portable replacement ZIP, never a false same-ID DLC override. Installed game files are not changed during automated tests.
