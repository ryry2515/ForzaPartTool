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
// the roof lining of the sharp interior is pushed back in depth so it cannot flicker through the body's roof panel
assert.equal(previewMaterial('headliner', 'lod0', 'cockpit').atlas, 'interior');
assert.equal(previewMaterial('headliner', 'lod0', 'cockpit').polygonOffset, true);
assert.notEqual(previewMaterial('leather', 'lod0', 'cockpit').polygonOffset, true);
assert.equal(previewMaterial('cockpit_smooth_plastic', 'lod0').atlas, 'interior');
assert.equal(previewMaterial('body_2').color, previewMaterial('body').color);
assert.equal(previewMaterial('window').depthWrite, false);
assert.equal(previewMaterial('plastic2').atlas, null);
// The floor panel has its own UVs: it must not sample the body atlas (that printed livery on the underside).
assert.equal(previewMaterial('undercarriage').atlas, null);
assert.equal(previewMaterial('undercarriage', 'lod0').atlas, null);
// Imported Focus cockpit carbins use unprefixed interior shader names. Their
// seat badge UV0 (0.2566..0.3756, 0.7962..0.8227) covers RECARO in interior_LOD0.
// The same coordinates in nodamage_LOD0 cover unrelated instrument imagery.
for (const name of ['cloth', 'leather', 'plastic', 'badge', 'bump_leather', 'bump_leather2',
  'bump_plastic', 'bump_stitching', 'headliner', 'rubber', 'solid', 'metal', 'gauge_emissive']) {
  assert.equal(previewMaterial(name, 'lod0', 'cockpit').atlas, 'interior', name);
  assert.deepEqual(previewMaterial(name, 'lod0', 'body'), previewMaterial(name, 'lod0'), name);
  assert.deepEqual(previewMaterial(name, 'lod', 'body'), previewMaterial(name, 'lod'), name);
}
// Other shaders of real FH1 cockpit files (Fiesta): these also read the interior atlas, not the white body atlas.
for (const name of ['bump_plastic2', 'mottled', 'mirrorMiddle', 'gauge_needle', 'reflector']) {
  assert.equal(previewMaterial(name, 'lod0', 'cockpit').atlas, 'interior', name);
}
// Dial faces are lit by the emissive atlas; the digital display is a plain dark screen.
assert.equal(previewMaterial('gauge_emissive', 'lod0', 'cockpit').emissiveAtlas, 'interior_emissive');
assert.equal(previewMaterial('digital_gauge', 'lod0', 'cockpit').atlas, null);
// ...and outside a cockpit file the airbag/reflector rules are unchanged.
assert.equal(previewMaterial('reflector', 'lod0').atlas, 'lights');
// LOD0 is also used for exterior body models, so the pool alone cannot opt in.
assert.equal(previewMaterial('badge', 'lod0').atlas, 'nodamage');
assert.equal(previewMaterial('cloth', 'lod0').atlas, 'nodamage');
assert.equal(previewMaterial('interior', 'lod', 'body').atlas, 'nodamage');
for (const name of ['body', 'emblem', 'window', 'black', 'black_glass', 'plastic2',
  'rubber_trim', 'chrome', 'detail_glass_clear']) {
  assert.deepEqual(previewMaterial(name, 'lod0', 'cockpit'), previewMaterial(name, 'lod0'), name);
}
console.log('Per-material atlas, paint, glass and procedural texture regressions passed.');
