import test from 'node:test';
import assert from 'node:assert/strict';
import { CoordinateClipboard, mergeTargets, createPartTools } from '../toolkit/carbin_editor/part_tools.mjs';

test('coordinate clipboard owns its data and rejects nonfinite positions', () => {
  const clipboard = new CoordinateClipboard();
  assert.equal(clipboard.read(), null);
  const position = [1, 2, 3];
  clipboard.copy(position, 'hood');
  position[0] = 99;
  assert.deepEqual(clipboard.read(), [1, 2, 3]);
  clipboard.read()[1] = 99;
  assert.deepEqual(clipboard.read(), [1, 2, 3]);
  assert.throws(() => clipboard.copy([1, NaN, 3]), /finite/);
  assert.deepEqual(clipboard.read(), [1, 2, 3]);
});

test('merge targets retain empty upgrade slots and exclude the source', () => {
  const sections = [{index: 0, name: 'body', subs: [{}]}, {index: 1, name: 'bumperFa', subs: []}, {index: 2, name: 'import', subs: [{}]}];
  assert.deepEqual(mergeTargets(sections, 2).map(s => s.name), ['body', 'bumperFa']);
});

test('pasting onto an imported model has independent undo and redo', async () => {
  let position = [4, 5, 6];
  const place = {group: {position: {toArray: () => [...position], fromArray: value => { position = [...value]; }}}, name: 'model'};
  const tools = createPartTools({place, st: {sum: {sections: [{offset: [1, 2, 3], name: 'hood'}]}}, status() {}, syncPlaceFields() {}});
  tools.copy(0);
  await tools.paste('import');
  assert.deepEqual(position, [1, 2, 3]);
  assert.equal(tools.undoPlacement(), true);
  assert.deepEqual(position, [4, 5, 6]);
  assert.equal(tools.redoPlacement(), true);
  assert.deepEqual(position, [1, 2, 3]);
  place.group = null;
  assert.equal(tools.undoPlacement(), false);
});
