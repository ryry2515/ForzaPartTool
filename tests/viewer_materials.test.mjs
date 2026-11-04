import { strict as assert } from 'node:assert';
import { previewMaterial } from '../toolkit/carbin_editor/viewer_materials.mjs';

// One bumper contains these separate shaders. Its part name cannot select their maps.
assert.equal(previewMaterial('badge').atlas, 'nodamage');
assert.equal(previewMaterial('reverse_light').atlas, 'lights');
assert.equal(previewMaterial('textured_reflector').atlas, 'lights');
assert.equal(previewMaterial('detail_glass_clear').atlas, 'lights');
assert.equal(previewMaterial('chrome').atlas, null);
assert.equal(previewMaterial('window').atlas, null);
assert.equal(previewMaterial('black_glass').atlas, null);
assert.equal(previewMaterial('carbon_fiber').atlas, 'shared:carbonFiber.xds');
assert.equal(previewMaterial('grille1').atlas, 'shared:grille1.xds');
// Low-detail seat/dash images live in nodamage, not the separate cockpit atlas.
assert.equal(previewMaterial('interior', 'lod').atlas, 'nodamage');
assert.equal(previewMaterial('cockpit_smooth_plastic', 'lod0').atlas, 'interior');
assert.equal(previewMaterial('body_2').color, previewMaterial('body').color);
assert.equal(previewMaterial('window').depthWrite, false);
assert.equal(previewMaterial('plastic2').atlas, null);
console.log('Per-material atlas, paint, glass and procedural texture regressions passed.');
