// Snapping for the move / rotate / scale gizmo. All of it is relative to where the drag started, so a part whose
// pivot is not on a round number does not jump when you grab the gizmo.
export const SNAP = Object.freeze({rotation: Math.PI / 180, move: 0.01, scale: 0.01});   // 1 degree, 1 cm, 1 %

export function snapRelative(value, start, step) {
  return start + Math.round((value - start) / step) * step;
}

export function snapPosition(now, start, step = SNAP.move) {
  return now.map((v, i) => snapRelative(v, start[i], step));
}

// now / start are [x, y, z] scales. Returns the scale to show.
//  snap    : round each ratio (now / start) to `step`
//  uniform : the axis the user dragged (the largest change) is applied to all three axes
export function adjustScale(start, now, {snap = true, uniform = false, step = SNAP.scale} = {}) {
  const ratios = now.map((v, i) => (start[i] === 0 ? 1 : v / start[i]));
  const round = r => {
    if (!snap) return r;
    const q = Math.round(r / step) * step;
    return q > 0 ? q : step;                       // never 0 or negative: that would flatten or mirror the part
  };
  if (uniform) {
    let pick = 0;
    for (let i = 1; i < 3; i++) if (Math.abs(ratios[i] - 1) > Math.abs(ratios[pick] - 1)) pick = i;
    const r = round(ratios[pick]);
    return start.map(s => s * r);
  }
  return start.map((s, i) => (s === 0 ? now[i] : s * round(ratios[i])));
}

// Which snaps are on: the checkbox, unless Alt is held right now.
export function snapOn({enabled, altHeld}) { return !!enabled && !altHeld; }
// Uniform scaling: the checkbox, flipped while Shift is held.
export function uniformOn({enabled, shiftHeld}) { return !!enabled !== !!shiftHeld; }
