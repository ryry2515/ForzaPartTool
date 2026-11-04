import { strict as assert } from 'node:assert';
import { preferredModel } from '../toolkit/carbin_editor/library_actions.mjs';
const models = [
  { member: 'media/cars/ABC/ABC_lod0.carbin' },
  { member: 'media/cars/ABC/ABC.carbin' },
  { member: 'media/cars/ABC/ABC_cockpit.carbin' },
];
assert.equal(preferredModel('ABC', models).member, 'media/cars/ABC/ABC.carbin');
assert.equal(preferredModel('abc', models).member, 'media/cars/ABC/ABC.carbin');
assert.equal(preferredModel('ABC', []), null);

