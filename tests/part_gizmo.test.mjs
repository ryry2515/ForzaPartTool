import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../toolkit/carbin_editor/vendor/three/build/three.module.js';
import { createPartGizmo } from '../toolkit/carbin_editor/part_gizmo.mjs';

function setup(commit = async () => true) {
  const listeners = new Map();
  const control = {enabled: true, mode: 'translate', attach(o) {this.object=o;}, detach() {this.object=null;},
    addEventListener(name, fn) {listeners.set(name, fn);}, emit(name) {return listeners.get(name)?.();}};
  const group = new THREE.Group(), root = new THREE.Group(); root.add(group);
  const target = {index: 2, name: 'hooda', group, pivot: [1, 2, 3]};
  const gizmo = createPartGizmo({THREE, control, parent: root, getTarget: () => target, commit, previewChanged() {}});
  return {gizmo, control, group, target};
}

test('drag previews around the part pivot and commits one world matrix', async () => {
  const edits=[];
  const {gizmo, control, group}=setup(async request=>{edits.push(request);return true;});
  gizmo.select(2);
  assert.deepEqual(control.object.position.toArray(),[1,2,3]);
  control.emit('mouseDown');
  control.object.position.x += 0.5;
  control.object.rotation.z = Math.PI/2;
  control.emit('objectChange');
  group.updateMatrixWorld(true);
  const actual=new THREE.Vector3(2,2,3).applyMatrix4(group.matrixWorld);
  assert.ok(actual.distanceTo(new THREE.Vector3(1.5,3,3))<1e-8);
  await control.emit('mouseUp');
  assert.equal(edits.length,1);
  const saved=new THREE.Matrix4().fromArray(edits[0].matrix);
  assert.ok(new THREE.Vector3(2,2,3).applyMatrix4(saved).distanceTo(actual)<1e-8);
  assert.deepEqual(edits[0].sections,[2]);
  assert.equal(edits[0].move_pivot,true);
  assert.equal(gizmo.busy,false);
  assert.deepEqual(group.position.toArray(),[0,0,0]);
  await control.emit('mouseUp');
  assert.equal(edits.length,1);
});

test('cancel and unchanged clicks create no backend edits', async () => {
  let commits=0;
  const {gizmo,control,group}=setup(async()=>{commits++;return true;});
  gizmo.select(2);control.emit('mouseDown');await control.emit('mouseUp');
  assert.equal(commits,0);
  control.emit('mouseDown');control.object.scale.set(2,3,4);control.emit('objectChange');
  assert.equal(group.getWorldScale(new THREE.Vector3()).x,2);
  gizmo.cancel();await control.emit('mouseUp');
  assert.equal(commits,0);assert.deepEqual(group.scale.toArray(),[1,1,1]);
});

test('failed save restores preview; custom model events are left alone', async () => {
  const {gizmo,control,group}=setup(async()=>false);
  gizmo.select(2);control.emit('mouseDown');control.object.position.y+=1;control.emit('objectChange');
  await control.emit('mouseUp');
  assert.deepEqual(group.position.toArray(),[0,0,0]);assert.equal(control.enabled,true);
  gizmo.detach();const custom=new THREE.Group();control.attach(custom);
  control.emit('mouseDown');custom.position.x=4;control.emit('objectChange');await control.emit('mouseUp');
  assert.equal(custom.position.x,4);assert.equal(control.object,custom);
});
