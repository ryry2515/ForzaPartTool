// Preview an absolute world-space delta; bake it only once when the drag ends.
export function createPartGizmo({THREE, control, parent, getTarget, commit, previewChanged = () => {}, stateChanged = () => {}}) {
  const anchor = new THREE.Object3D(); anchor.rotation.order = 'ZYX'; parent.add(anchor);
  let index = null, target = null, drag = null, busy = false;
  const identity = new THREE.Matrix4();
  const ownsControl = () => control.object === anchor;
  function restore(edit) {
    if (!edit) return;
    edit.target.group.matrix.copy(edit.original);
    edit.original.decompose(edit.target.group.position, edit.target.group.quaternion, edit.target.group.scale);
    edit.target.group.matrixAutoUpdate = edit.autoUpdate;
    edit.target.group.updateMatrixWorld(true); previewChanged();
  }
  function select(next) {
    if (busy) return;
    if (drag) cancel();
    index = next;
    const found = getTarget(next);
    if (!found) {
      target = null; if (ownsControl()) control.detach(); stateChanged(); return;
    }
    if (target?.group === found.group && ownsControl() && anchor.position.toArray().every((v,i) => v === found.pivot[i])) return;
    target = found;
    anchor.position.fromArray(target.pivot); anchor.quaternion.identity(); anchor.scale.set(1,1,1);
    anchor.updateMatrixWorld(true); control.attach(anchor); stateChanged();
  }
  function cancel() {
    if (!drag || busy) return false;
    const edit = drag; drag = null; restore(edit);
    control.detach(); control.dragging = false;
    anchor.position.fromArray(edit.target.pivot); anchor.quaternion.identity(); anchor.scale.set(1,1,1);
    control.attach(anchor); stateChanged(); return true;
  }
  function detach() {
    cancel(); index = null; target = null;
    if (ownsControl()) control.detach(); stateChanged();
  }
  control.addEventListener('mouseDown', () => {
    if (busy || !target || !ownsControl()) return;
    anchor.updateMatrixWorld(true); target.group.updateMatrix();
    drag = {target, inverse: anchor.matrixWorld.clone().invert(), original: target.group.matrix.clone(),
      autoUpdate: target.group.matrixAutoUpdate, delta: identity.clone()};
  });
  control.addEventListener('objectChange', () => {
    if (!drag || busy || !ownsControl()) return;
    anchor.updateMatrixWorld(true);
    drag.delta.multiplyMatrices(anchor.matrixWorld, drag.inverse);
    drag.target.group.matrixAutoUpdate = false;
    drag.target.group.matrix.multiplyMatrices(drag.delta, drag.original);
    drag.target.group.updateMatrixWorld(true); previewChanged();
  });
  control.addEventListener('mouseUp', async () => {
    if (!drag || busy || !ownsControl()) return;
    const edit = drag; drag = null;
    if (edit.delta.elements.every((v,i) => Math.abs(v - identity.elements[i]) < 1e-8)) {restore(edit); return;}
    busy = true; control.enabled = false; control.detach(); stateChanged();
    try { await commit({sections: [edit.target.index], matrix: [...edit.delta.elements], move_pivot: true}); }
    finally {
      restore(edit); busy = false; control.enabled = true;
      target = null; select(index); stateChanged();
    }
  });
  return {select, detach, cancel, refresh() {if (index !== null && !drag && !busy) select(index);},
    get busy() {return busy;}, get index() {return index;}, get available() {return !!target;}};
}
