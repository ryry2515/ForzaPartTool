import test from 'node:test';
import assert from 'node:assert/strict';
import {SNAP, snapRelative, snapPosition, adjustScale, snapOn, uniformOn} from '../toolkit/carbin_editor/gizmo_snap.mjs';

const near = (a, b, e = 1e-9) => assert.ok(Math.abs(a - b) < e, `${a} != ${b}`);

test('rotation snap is one degree, so 90 degrees is hit exactly', () => {
  near(SNAP.rotation, Math.PI / 180);
  // what TransformControls does with the snap: round the dragged angle to a multiple of the step
  const dragged = 89.7 * Math.PI / 180;
  near(Math.round(dragged / SNAP.rotation) * SNAP.rotation, Math.PI / 2);
});

test('move snap is relative to where the drag started', () => {
  near(snapRelative(0.5234, 0.5, 0.01), 0.52);
  near(snapRelative(0.5, 0.5, 0.01), 0.5);                      // no jump at the start of a drag
  const out = snapPosition([1.2349, 0.3333, -2.0], [1.2, 0.3333, -2.0]);
  near(out[0], 1.23); near(out[1], 0.3333); near(out[2], -2.0); // untouched axes stay exactly as they were
});

test('scale snap rounds the ratio, never to zero or negative', () => {
  const out = adjustScale([2, 2, 2], [2.0431, 2, 2], {snap: true, uniform: false});
  near(out[0], 2 * 1.02); near(out[1], 2); near(out[2], 2);
  const tiny = adjustScale([1, 1, 1], [0.001, 1, 1], {snap: true});
  assert.ok(tiny[0] > 0);
  const free = adjustScale([1, 1, 1], [1.2345, 1, 1], {snap: false});
  near(free[0], 1.2345);                                        // Alt: no rounding
});

test('uniform scale applies the dragged axis to all three', () => {
  const out = adjustScale([1, 1, 1], [1.5, 1, 1], {snap: true, uniform: true});
  out.forEach(v => near(v, 1.5));
  const y = adjustScale([2, 4, 1], [2, 5, 1], {snap: false, uniform: true});   // ratio 1.25 from the y axis
  near(y[0], 2.5); near(y[1], 5); near(y[2], 1.25);
  const none = adjustScale([1, 1, 1], [1, 1, 1], {snap: true, uniform: true});
  none.forEach(v => near(v, 1));
});

test('alt turns snapping off only while held; shift flips uniform scale', () => {
  assert.equal(snapOn({enabled: true, altHeld: false}), true);
  assert.equal(snapOn({enabled: true, altHeld: true}), false);
  assert.equal(snapOn({enabled: true, altHeld: false}), true);   // let go: back on
  assert.equal(snapOn({enabled: false, altHeld: false}), false);
  assert.equal(uniformOn({enabled: true, shiftHeld: false}), true);
  assert.equal(uniformOn({enabled: true, shiftHeld: true}), false);
  assert.equal(uniformOn({enabled: false, shiftHeld: true}), true);
});
