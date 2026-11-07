import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { createWorkflow } from './editor_workflow.js';
import { previewMaterial } from './viewer_materials.mjs';
import { createPartTools } from './part_tools.mjs';
import { createPartGizmo } from './part_gizmo.mjs';

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
};

// ---------------------------------------------------------------------------------------------- state
const st = {
  viewGeneration: 0,
  sum: null, sel: -1, hidden: new Set(), touched: new Set(), lods: new Set([0, 1, 2, 3, 4, 5]), groups: new Map(), geom: new Map(),
  matCache: new Map(), atlas: new Map(), texMats: new Map(), texVer: 0, arch: null, filter: '', selLight: -1, objText: null, objInfo: null, objName: '',
};
const status = (msg, err = false) => {
  const s = $('#status'); s.textContent = msg; s.className = err ? 'err' : '';
  if (err) { $('#errorText').value = msg; $('#errorBanner').hidden = false; $('#copyError').textContent = 'Copy error'; }
};
$('#dismissError').addEventListener('click', () => { $('#errorBanner').hidden = true; });
$('#copyError').addEventListener('click', async () => {
  $('#errorText').focus(); $('#errorText').select();
  try {
    if (window.pywebview?.api?.copy_text) await window.pywebview.api.copy_text($('#errorText').value);
    else if (navigator.clipboard?.writeText) await navigator.clipboard.writeText($('#errorText').value);
    else if (!document.execCommand('copy')) throw new Error('Select and copy the text');
    $('#copyError').textContent = 'Copied';
  } catch { $('#copyError').textContent = 'Press Ctrl+C'; }
});

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const type = r.headers.get('Content-Type') || '';
  if (!r.ok) {
    let j = {}; try { j = await r.json(); } catch (e) { /* not json */ }
    const message = r.status === 401 ? 'The editor connection expired. Reopen the app to reconnect; your saved game paths are kept.' : j.error || r.statusText;
    const e = new Error(message); e.problems = j.problems; e.requires_discard = j.requires_discard; throw e;
  }
  return type.includes('json') ? r.json() : r;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

// ---------------------------------------------------------------------------------------------- loading bar and timing log
// busy.start('text', total) shows the bar (indeterminate without a total), busy.step(done, 'text') fills it, busy.end() hides it.
const busy = (() => {
  let total = 0;
  const set = (done, text) => { const f = $('#loadFill'); if (total) f.style.width = Math.min(100, (done / total) * 100).toFixed(1) + '%'; if (text) $('#loadText').textContent = text; };
  return {
    start(text, n = 0) { total = n; const b = $('#loading'); if (!b) return; b.hidden = false; b.classList.toggle('indeterminate', !n); $('#loadFill').style.width = '0%'; $('#loadText').textContent = text; wake(1000); },
    step(done, text) { set(done, text); },
    end() { const b = $('#loading'); if (b) b.hidden = true; total = 0; wake(500); },
  };
})();
// Timings of loads go to %APPDATA%\FH1CarbinEditor\load-times.log (this computer only), so a slow step can be found from numbers instead of guesses.
const diag = (event, data = {}) => { post('/api/diag', { event, ...data }).catch(() => {}); };

// ---------------------------------------------------------------------------------------------- three.js scene
const canvas = $('#gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'low-power' });     // light on the graphics card: no anti-aliasing, the low-power GPU where there are two
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
const scene = new THREE.Scene();
const dark = !matchMedia('(prefers-color-scheme: light)').matches;
scene.background = new THREE.Color(dark ? 0x101215 : 0xe8ebee);
const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 200);
camera.position.set(-4.6, 2.3, -5.6);
const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 0.6, 0);
controls.enableDamping = true;
const tc = new TransformControls(camera, canvas);
let gizmoGesture = false;
tc.addEventListener('dragging-changed', e => { controls.enabled = !e.value; if (e.value) gizmoGesture = true; });
tc.addEventListener('objectChange', () => { if (place.group && tc.object === place.group) syncPlaceFields(); });
scene.add(tc);
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(-3, 6, -4); scene.add(sun);
const sun2 = new THREE.DirectionalLight(0xffffff, 0.5); sun2.position.set(4, 2, 5); scene.add(sun2);
const grid = new THREE.GridHelper(12, 24, dark ? 0x394149 : 0xaab2ba, dark ? 0x23292f : 0xcfd5db); scene.add(grid);
scene.add(new THREE.AxesHelper(0.6));
const front = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 0.02, -2.6), 1.0, 0xffcc00, 0.25, 0.15); scene.add(front);
const world = new THREE.Group(); scene.add(world);
// Testing aid (not shown anywhere): opening the page with #debug lets a test script place the camera exactly.
if (location.hash === '#debug') window.__editor = { camera, controls, scene, get st() { return st; }, wake: () => wake(3000) };
const overlay = new THREE.Group(); scene.add(overlay);
let selBox = null;

function resize() {
  const v = $('#view'); const w = v.clientWidth, h = v.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  wake(1500);
}
new ResizeObserver(resize).observe($('#view'));
// Draw only while something changes (a drag, a click, a finished download, a loaded picture) and for a moment after it, at most 30 times a second.
// The first version redrew every frame forever, which keeps the graphics card at full load even when nothing moves.
let awakeUntil = performance.now() + 3000, lastDraw = 0, glLost = false;
const wake = (ms = 2000) => { awakeUntil = Math.max(awakeUntil, performance.now() + ms); };
for (const ev of ['pointerdown', 'pointermove', 'pointerup', 'wheel', 'keydown', 'input', 'change', 'click', 'resize']) addEventListener(ev, () => wake(), true);
addEventListener('load', () => wake(), true);                           // pictures and textures finishing in the background
controls.addEventListener('change', () => wake());
tc.addEventListener('change', () => wake());
const _fetch = window.fetch.bind(window);
window.fetch = (...a) => _fetch(...a).then(r => { wake(2500); return r; }, e => { wake(); throw e; });
canvas.addEventListener('webglcontextlost', e => { e.preventDefault(); glLost = true; status('The graphics context was lost (a graphics driver reset). Reload the page; if this repeats, restart the editor (it draws with the processor unless you start it with --gpu).', true); });
canvas.addEventListener('webglcontextrestored', () => { glLost = false; wake(3000); });
(function loop() {
  requestAnimationFrame(loop);
  const now = performance.now();
  if (glLost || now > awakeUntil || now - lastDraw < 33) return;
  lastDraw = now;
  controls.update();
  renderer.render(scene, camera);
})();

function matColor(name) {
  const n = name.toLowerCase();
  if (/glass|window/.test(n)) return [0x7fb3d5, 0.45];
  if (/chrome|badge|emblem|alum|mirror/.test(n)) return [0xcfd8dc, 1];
  if (/^body|paint/.test(n)) return [0xc62828, 1];
  if (/light|lens|head|tail|indicator|reverse|orange|red/.test(n)) return [0xff7043, 1];
  if (/black|rubber|plastic|frame|under|interior|leather|carbon|trim|numplate/.test(n)) return [0x3b4046, 1];
  let h = 0; for (const c of n) h = (h * 31 + c.charCodeAt(0)) % 360;
  return [new THREE.Color().setHSL(h / 360, 0.45, 0.5).getHex(), 1];
}
function material(name) {
  if (!st.matCache.has(name)) {
    const [color, op] = matColor(name);
    st.matCache.set(name, new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide, transparent: op < 1, opacity: op, metalness: 0.15, roughness: 0.6, wireframe: $('#optWire').checked }));
  }
  return st.matCache.get(name);
}

// ---------------------------------------------------------------------------------------------- loading geometry
// Typed-array views on the downloaded buffer (no copies) when the server aligned it; the position and normal buffers are shared by every sub-mesh of a part, so
// the graphics card gets them once per part instead of once per sub-mesh.
const view = (Type, buf, offset, count) => offset % Type.BYTES_PER_ELEMENT === 0 ? new Type(buf, offset, count) : new Type(buf.slice(offset, offset + count * Type.BYTES_PER_ELEMENT));
// One part's geometry blob (see geometry_blob in server.py) as a group of meshes, one per sub-mesh.
function sectionGroup(buf, userData) {
  const dv = new DataView(buf); const hl = dv.getUint32(0, true);
  const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  const base = 4 + hl, n = head.verts;
  const posAttr = new THREE.BufferAttribute(view(Float32Array, buf, base, n * 3), 3);
  const nrmAttr = new THREE.BufferAttribute(view(Float32Array, buf, base + n * 12, n * 3), 3);
  const g = new THREE.Group(); g.userData = { ...userData };
  for (const s of head.subs) {
    if (!s.tris) continue;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', posAttr);
    geo.setAttribute('normal', nrmAttr);
    if (s.uvOffset !== undefined) geo.setAttribute('uv', new THREE.BufferAttribute(view(Float32Array, buf, base + s.uvOffset, n * 2), 2));
    geo.setIndex(new THREE.BufferAttribute(view(Uint32Array, buf, base + s.offset, s.tris * 3), 1));
    const m = new THREE.Mesh(geo, material(s.material));
    m.userData = { ...userData, k: s.k, lod: s.lod, mat: s.material };
    g.add(m);
  }
  return g;
}
async function loadSection(si, generation = st.viewGeneration, quick = false) {
  const buf = await (await api('/api/geometry?sec=' + si)).arrayBuffer();
  if (generation !== st.viewGeneration || !st.sum?.sections[si]) return;
  const old = st.groups.get(si);
  if (old) { world.remove(old); old.traverse(o => o.geometry && o.geometry.dispose()); }
  const g = sectionGroup(buf, { si });
  st.groups.set(si, g); world.add(g);
  if (quick) applySectionVisibility(si, g, viewOptions()); else applyVisibility();
}

async function loadAll(indices) {
  const generation = st.viewGeneration;
  const list = indices ?? st.sum.sections.map(s => s.index);
  const t0 = performance.now(), marks = st.loadMarks; st.loadMarks = null;
  status(`loading ${list.length} parts...`);
  busy.start(`Loading ${st.sum.name}: 0 of ${list.length} parts`, list.length);
  const layoutReady = indices ? Promise.resolve() : loadWheelLayout(generation);
  let i = 0, done = 0;
  const worker = async () => {
    while (i < list.length && generation === st.viewGeneration) {
      const si = list[i++];
      try { await loadSection(si, generation, true); } catch (e) { if (generation === st.viewGeneration) status(e.message, true); }
      done++; busy.step(done, `Loading ${st.sum.name}: ${done} of ${list.length} parts`);
    }
  };
  await Promise.all([worker(), worker(), worker(), worker(), worker(), worker()]);
  if (generation !== st.viewGeneration) return;                       // another car was opened meanwhile: that load owns the bar
  await layoutReady;
  applyVisibility();                                                  // one pass over everything, not one per part
  busy.end();
  const ms = Math.round(performance.now() - t0);
  status(`${st.sum.name}: ${st.sum.sections.length} parts` + (indices ? '' : ` (scene built in ${(ms / 1000).toFixed(1)} s)`));
  if (!indices) loadCockpit(generation);                              // in the background: the car is already usable
  if (!indices) diag('car_loaded', { car: st.sum.name, parts: st.sum.sections.length, scene_ms: ms, ...(marks || {}) });
}

// The page options are read once per pass (a lookup per sub-mesh made a car with hundreds of them crawl).
// cockpit: the sharp interior of <car>_cockpit.carbin is drawn in place of the driving model's low-detail interior (preview only; the files are not changed)
const viewOptions = () => ({ showUndrawn: $('#optHidden').checked, wire: $('#optWire').checked,
  cockpit: !!st.cockpit && $('#optCockpit').checked && st.lods.has(st.minLod) });
function applySectionVisibility(si, g, opts) {
  const sec = st.sum.sections[si]; if (!sec) return;
  g.visible = !st.hidden.has(si) && matchesFilter(sec) && !(opts.cockpit && st.cockpit.replaces.has(sec.name) && !st.touched.has(sec.name) && st.sel !== si);
  for (const m of g.children) {
    const sub = sec.subs[m.userData.k];
    const drawn = !st.sum.body_header || (sub && sub.drawn_in.length > 0);
    m.visible = st.lods.has(m.userData.lod) && (drawn || opts.showUndrawn);
    m.material = meshMaterial(m, sec);
    m.material.wireframe = opts.wire;
  }
}
// The car file holds one wheel at the centre of the car; the game puts it at the four corners. The preview does the same (copies that share the
// wheel's geometry), and shows the real part only while it is selected, so it can still be moved.
let wheelGhosts = [];
function clearWheels() { for (const g of wheelGhosts) world.remove(g); wheelGhosts = []; }
function drawWheels() {
  clearWheels();
  const layout = st.wheelLayout, src = layout?.has ? st.groups.get(layout.section) : null, mode = $('#optWheels').value;
  if (!src || mode === 'file') return;
  const sec = st.sum.sections[layout.section];
  const on = !st.hidden.has(layout.section) && matchesFilter(sec);
  src.visible = on && st.sel === layout.section;          // the real part (at the centre) only while it is selected, so it can be moved
  if (mode === 'none') return;
  for (const w of layout.wheels) {
    const g = new THREE.Group(); g.position.set(w.x, w.y, w.z); if (w.flip) g.rotation.y = Math.PI;
    for (const m of src.children) { const c = new THREE.Mesh(m.geometry, m.material); c.userData = m.userData; c.visible = m.visible; g.add(c); }
    g.visible = on; world.add(g); wheelGhosts.push(g);
  }
}
let cockpitGroup = null;
function clearCockpit() { if (cockpitGroup) { overlay.remove(cockpitGroup); cockpitGroup.traverse(o => o.geometry && o.geometry.dispose()); cockpitGroup = null; } st.cockpit = null; }
async function loadCockpit(generation) {
  let info = null;
  try { info = await api('/api/cockpit_info'); } catch (e) { return; }
  if (!info?.has || generation !== st.viewGeneration) return;
  const parts = info.sections, loaded = [];
  let next = 0;
  const worker = async () => {
    while (next < parts.length && generation === st.viewGeneration) {
      const p = parts[next++];
      try { loaded.push(sectionGroup(await (await api('/api/cockpit_geometry?sec=' + p.index)).arrayBuffer(), { cockpit: true, name: p.name })); } catch (e) { /* a missing part is simply not drawn */ }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  if (generation !== st.viewGeneration) { for (const g of loaded) g.traverse(o => o.geometry && o.geometry.dispose()); return; }
  const group = new THREE.Group();
  if (!loaded.length) return;
  const best = Math.min(...loaded.flatMap(g => g.children.map(m => m.userData.lod)));          // the sharpest level of detail only
  for (const g of loaded) { for (const m of [...g.children]) if (m.userData.lod !== best) { g.remove(m); m.geometry.dispose(); } group.add(g); }
  overlay.add(group); cockpitGroup = group;
  st.cockpit = { replaces: new Set(info.replaces) };
  applyVisibility();
}
function drawCockpit(opts) {
  if (!cockpitGroup) return;
  cockpitGroup.visible = opts.cockpit;
  if (!opts.cockpit) return;
  // a cockpit part follows the driving model's part of the same name (Hide, Isolate and the filter box); gauges have no twin and follow the interior
  const shown = name => {
    const si = st.sum.sections.findIndex(s => s.name === name);
    return si < 0 ? null : !st.hidden.has(si) && matchesFilter(st.sum.sections[si]);
  };
  const interior = shown('interior');
  for (const g of cockpitGroup.children) {
    const own = shown(g.userData.name);
    g.visible = !st.touched.has(g.userData.name) && (own ?? interior ?? true);
    for (const m of g.children) { m.material = meshMaterial(m, { pool: 'lod0' }, true); m.material.wireframe = opts.wire; }
  }
}
async function loadWheelLayout(generation) {
  let layout = null;
  try { layout = await api('/api/wheel_layout'); } catch (e) { layout = null; }
  if (generation === st.viewGeneration) st.wheelLayout = layout;
}
// Where the floor grid is: under the tyres (wheels at the corners), under the lowest visible part of the body (no wheels), or at 0 (the file's own centre).
function floorHeight() {
  const layout = st.wheelLayout, mode = $('#optWheels').value;
  if (!layout?.has || mode === 'file') return 0;
  if (mode === 'corners') return layout.ground;
  let low = Infinity;
  for (const [si, g] of st.groups) {
    if (si === layout.section || !g.visible) continue;
    for (const m of g.children) {
      if (!m.visible) continue;
      if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
      low = Math.min(low, m.geometry.boundingBox.min.y);
    }
  }
  return Number.isFinite(low) ? low : 0;
}
function applyVisibility() {
  const opts = viewOptions();
  for (const [si, g] of st.groups) applySectionVisibility(si, g, opts);
  drawWheels(); drawCockpit(opts);
  grid.position.y = floorHeight();
  drawSelection(); drawOverlays(); drawExtras();
  partGizmo.refresh(); updateGizmoUI();
}

function matchesFilter(sec) {
  const f = st.filter.trim().toLowerCase();
  if (!f) return true;
  return sec.name.toLowerCase().includes(f) || sec.subs.some(s => s.material.toLowerCase().includes(f));
}

function drawSelection() {
  if (selBox) { overlay.remove(selBox); selBox = null; }
  if (st.sel < 0 || !$('#optBox').checked) return;
  const g = st.groups.get(st.sel); if (!g) return;
  const box = new THREE.Box3().setFromObject(g);
  if (box.isEmpty()) return;
  selBox = new THREE.Box3Helper(box, 0x4aa3ff); overlay.add(selBox);
}

// lights are stored with +z = front; meshes use -z = front, so lights and arrows are drawn with z negated
let lightObjs = [];
function drawOverlays() {
  for (const o of lightObjs) { overlay.remove(o); o.traverse(x => { x.geometry && x.geometry.dispose(); }); }
  lightObjs = [];
  if (!st.sum) return;
  if ($('#optLights').checked) {
    for (const l of st.sum.lights) {
      const c = new THREE.Color(l.rgb[0] / 255, l.rgb[1] / 255, l.rgb[2] / 255);
      const m = new THREE.Mesh(new THREE.SphereGeometry(l.index === st.selLight ? 0.045 : 0.03, 12, 8), new THREE.MeshBasicMaterial({ color: c }));
      m.position.set(l.pos[0], l.pos[1], -l.pos[2]);
      const d = new THREE.Vector3(l.dir[0], l.dir[1], -l.dir[2]).divideScalar(32767);
      if (d.length() > 0.01) m.add(new THREE.ArrowHelper(d.normalize(), new THREE.Vector3(), 0.18, c.getHex(), 0.05, 0.03));
      overlay.add(m); lightObjs.push(m);
    }
  }
  if ($('#optEx').checked) {
    for (const e of st.sum.exhausts) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.12, 10), new THREE.MeshBasicMaterial({ color: 0xffaa00 }));
      m.rotation.x = Math.PI / 2; m.position.set(e.pos[0], e.pos[1], e.pos[2]);
      overlay.add(m); lightObjs.push(m);
    }
  }
}

// click to select
let down = null;
canvas.addEventListener('pointerdown', e => { down = tc.dragging || partGizmo.busy ? null : [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', e => {
  if (gizmoGesture) { gizmoGesture = false; down = null; return; }
  if (partGizmo.busy) return;
  if (e.button !== 0) return;
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
  const r = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, camera);
  const meshes = []; world.traverse(o => { if (o.isMesh && o.visible && o.parent.visible) meshes.push(o); });
  if (place.group) place.group.traverse(o => { if (o.isMesh && o.visible) meshes.push(o); });
  const hit = ray.intersectObjects(meshes, false)[0];
  if (hit) {
    if (hit.object.userData.si !== undefined) selectSection(hit.object.userData.si);
    else { activatePlacement(); setTab('model'); }
  }
});

canvas.addEventListener('contextmenu', event => {
  if (!st.sum) return;
  const rect = canvas.getBoundingClientRect();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
  const meshes = [];
  world.traverse(o => { if (o.isMesh && o.visible && o.parent.visible) meshes.push(o); });
  if (place.group) place.group.traverse(o => { if (o.isMesh && o.visible) meshes.push(o); });
  const hit = ray.intersectObjects(meshes, false)[0];
  if (hit) partTools.menu(event, hit.object.userData.si ?? 'import');
});

// ---------------------------------------------------------------------------------------------- UI: tree, lods
function buildLodBar() {
  const bar = $('#lodBar'); bar.replaceChildren(el('span', { class: 'muted' }, 'LOD'));
  const present = new Set(); for (const s of st.sum.sections) for (const b of s.subs) present.add(b.lod);
  st.minLod = present.size ? Math.min(...present) : 0;
  if (st.lodReset) { st.lodReset = false; st.lods = new Set(present.size ? [Math.min(...present)] : []); }
  else st.lods = new Set([...st.lods].filter(l => present.has(l)));
  if (!st.lods.size && present.size) st.lods.add(Math.min(...present));
  for (const l of [0, 1, 2, 3, 4, 5]) {
    const cb = el('input', { type: 'checkbox', checked: st.lods.has(l), disabled: !present.has(l), onchange: e => { e.target.checked ? st.lods.add(l) : st.lods.delete(l); applyVisibility(); } });
    bar.append(el('label', {}, cb, ' ' + l));
  }
}
function buildTree() {
  const t = $('#tree'); t.replaceChildren();
  for (const s of st.sum.sections) {
    if (!matchesFilter(s)) continue;
    const eye = el('input', { type: 'checkbox', checked: !st.hidden.has(s.index), onclick: e => e.stopPropagation(), onchange: e => { e.target.checked ? st.hidden.delete(s.index) : st.hidden.add(s.index); applyVisibility(); } });
    const notDrawn = st.sum.body_header && s.subs.length && s.subs.every(b => !b.drawn_in.length);
    const row = el('div', { class: 'row', onclick: () => selectSection(s.index), oncontextmenu: event => partTools.menu(event, s.index) }, eye,
      el('span', { class: 'name' + (notDrawn ? ' warn' : ''), title: notDrawn ? 'not in any draw list: the game does not draw this part' : '' }, s.name),
      el('span', { class: 'tag' }, `${s.verts}v · ${s.subs.length}`));
    const subs = el('div', { class: 'subs' }, s.subs.map(b => el('div', {}, el('span', {}, b.material + (b.swatch ? ' •' : '')), el('span', {}, `L${b.lod} · ${b.tris}t${st.sum.body_header && !b.drawn_in.length ? ' · hidden' : ''}`))));
    t.append(el('div', { class: 'sec' + (s.index === st.sel ? ' sel' : ''), 'data-si': s.index }, row, st.sel === s.index ? subs : ''));
  }
}

function selectSection(si) {
  if (partGizmo.busy) return;
  st.sel = si; partGizmo.select(si); buildTree(); drawSelection(); drawExtras(); renderPart(); setTab('part');
}

function updateGizmoUI() {
  const imported = !!place.group && tc.object === place.group;
  const available = imported || partGizmo.available;
  $('#gizmoTarget').textContent = imported ? 'Custom model: ' + place.name :
    available ? 'Part: ' + st.sum.sections[partGizmo.index].name : 'Select a part to move it';
  for (const button of document.querySelectorAll('[data-gizmo-mode]')) {
    button.disabled = !available || partGizmo.busy;
    button.classList.toggle('on', button.dataset.gizmoMode === tc.mode);
  }
  $('#partBoth').disabled = imported || !canPairModels() || partGizmo.busy;
  $('#btnUndo').disabled = $('#btnRedo').disabled = partGizmo.busy;
}

function setGizmoMode(mode) { tc.setMode(mode); updateGizmoUI(); wake(); }
function activatePlacement() {
  if (!place.group || partGizmo.busy) return;
  partGizmo.detach(); tc.attach(place.group); updateGizmoUI();
}

// ---------------------------------------------------------------------------------------------- part tab
const num = (id, v, step = 0.01) => el('input', { type: 'number', id, value: v, step });
function vec(prefix, vals, step) { return el('div', { class: 'grid3' }, ['x', 'y', 'z'].map((a, i) => num(`${prefix}${a}`, vals[i], step))); }
const val = id => parseFloat($('#' + id).value) || 0;

function renderPart() {
  const box = $('#tab-part'); box.replaceChildren();
  if (!st.sum) { box.append(el('p', { class: 'muted' }, 'Open Cars and right-click a car → Import.')), box.append(el('button', { onclick: () => setTab('cars') }, 'Browse cars')); return; }
  const s = st.sum.sections[st.sel];
  if (!s) { box.append(el('p', { class: 'muted' }, 'Click a part in the list or in the view.')); return; }
  const dl = $('#materials'); dl.replaceChildren(...[...new Set(st.sum.sections.flatMap(x => x.subs.map(b => b.material)))].sort().map(m => el('option', { value: m })));
  box.append(
    el('h3', {}, 'Part'),
    el('div', { class: 'field' }, el('button', { onclick: () => workflow.copyCurrent() }, 'Copy part (Ctrl+C)'), el('button', { onclick: () => workflow.openPaste() }, 'Paste part… (Ctrl+V)')),
    el('div', { class: 'field' }, el('button', { onclick: () => partTools.copy(s.index) }, 'Copy coordinates'), el('button', { onclick: () => partTools.paste(s.index) }, 'Paste coordinates'), el('button', { onclick: () => partTools.mergeDialog(s.index) }, 'Merge into…')),
    el('div', { class: 'field' }, el('label', {}, 'name'), el('input', { id: 'pName', value: s.name }), el('button', { onclick: () => run('rename_section', { section: s.index, name: $('#pName').value }, []) }, 'Rename')),
    el('p', { class: 'muted' }, `pivot (${s.offset.map(v => v.toFixed(3)).join(', ')}) · half extents (${s.bmax.map(v => v.toFixed(3)).join(', ')}) · ${s.verts} vertices · pool ${s.pool}. The game finds some parts by name (for example hooda, exhaustLa): rename with care.`),
    el('h3', {}, 'Move, rotate, scale'),
    el('div', {class: 'field'}, ['translate','rotate','scale'].map((mode,i) => el('button', {
      'data-gizmo-mode': mode, onclick: () => { partGizmo.select(s.index); setGizmoMode(mode); }
    }, ['Move (W)','Rotate (E)','Scale (R)'][i]))),
    el('p', {class: 'muted'}, 'Drag the coloured arrows, rotation rings or scale handles in the viewport. Release to apply; Escape cancels. Each drag is one Undo step.'),
    el('div', { class: 'field' }, el('label', {}, 'move (m)'), vec('tr', [0, 0, 0], 0.01)),
    el('div', { class: 'field' }, el('label', {}, 'rotate (deg)'), vec('ro', [0, 0, 0], 1)),
    el('div', { class: 'field' }, el('label', {}, 'scale'), vec('sc', [1, 1, 1], 0.01)),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'pOrigin' }), ' about the world origin (default: the part pivot)')),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'pMovePivot', checked: true }), ' move the pivot with the part')),
    el('div', { class: 'field' }, el('label', {}, 'apply to'), el('select', { id: 'pScope' }, el('option', { value: 'one' }, 'this part'), el('option', { value: 'visible' }, 'all shown parts'))),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => doTransform(null) }, 'Apply'),
      ['x', 'y', 'z'].map(a => el('button', { title: `mirror across the ${a} axis (flips triangle winding)`, onclick: () => doTransform(a) }, 'Mirror ' + a.toUpperCase()))),
    el('h3', {}, 'Sub-meshes (material and LOD)'),
    el('table', {}, el('tr', {}, ['material', 'LOD', 'tris', ''].map(h => el('th', {}, h))),
      s.subs.map(b => el('tr', {},
        el('td', {}, el('input', { value: b.material, list: 'materials', onchange: e => run('set_sub', { section: s.index, sub: b.index, material: e.target.value }, [s.index]) })),
        el('td', { style: 'width:46px' }, el('input', { type: 'number', value: b.lod, min: 0, max: 5, onchange: e => run('set_sub', { section: s.index, sub: b.index, lod: parseInt(e.target.value) }, [s.index]) })),
        el('td', {}, String(b.tris)),
        el('td', {}, el('button', { class: 'danger', title: 'delete this sub-mesh', onclick: () => confirm(`Delete sub-mesh ${b.material} (LOD ${b.lod})?`) && run('delete_sub', { section: s.index, subs: [b.index] }, [s.index]) }, 'x'))))),
    el('p', { class: 'muted' }, 'A material name must exist in the car\'s ShaderSettings.xml to behave as expected. • = reads one flat swatch colour.'),
    el('h3', {}, 'In the game'),
    el('div', { class: 'field' },
      el('button', { onclick: () => run('hide_section', { section: s.index }, []) }, 'Do not draw this part'),
      el('button', { onclick: () => run('show_section', { section: s.index }, []) }, 'Draw it again')),
    el('h3', {}, 'OBJ'),
    el('div', { class: 'field' },
      el('button', { onclick: () => exportObj([s.index]) }, 'Export this part'),
      el('button', { onclick: () => { st.importTarget = s.index; $('#objIn').click(); } }, 'Replace its geometry from OBJ...')),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => { st.modelTarget = s.index; $('#modelIn').click(); } }, 'Add a model to this part (FBX, GLB, OBJ, .modelbin)...')),
    el('div', { id: 'objDialog' }));
  renderObjDialog();
  updateGizmoUI();
}

async function doTransform(mirror) {
  const secs = $('#pScope').value === 'visible' ? st.sum.sections.filter(s => !st.hidden.has(s.index) && matchesFilter(s)).map(s => s.index) : [st.sel];
  await run('transform', {
    sections: secs, translate: ['x', 'y', 'z'].map(a => val('tr' + a)), rotate: ['x', 'y', 'z'].map(a => val('ro' + a)),
    scale: ['x', 'y', 'z'].map(a => { const v = parseFloat($('#sc' + a).value); return isNaN(v) ? 1 : v; }),
    pivot: $('#pOrigin').checked ? 'origin' : 'section', move_pivot: $('#pMovePivot').checked, mirror,
    both_models: $('#partBoth').checked && canPairModels(),
  }, secs);
}

async function run(op, args, reload) {
  try {
    // Parts the user edits stop being replaced by the cockpit model's copy in the preview (it would hide the edit).
    const touched = [args.sections, args.section, args.source_section, args.target_section].flat().filter(Number.isInteger).map(i => st.sum.sections[i]?.name).filter(Boolean);
    st.sum = await post('/api/op', { op, ...args });
    for (const name of touched) st.touched.add(name);
    if (op === 'add_model' && args.mode === 'new' && st.sum.last_pasted != null) reload = [st.sum.last_pasted];   // a part of its own: load its geometry
    if (args.texture) invalidateTextures();
    await afterChange(reload);
    status(`${op} done`);
    return true;
  } catch (e) { status(`${op} failed: ${e.message}`, true); return false; }
}

function afterChange(reload) {
  st.viewGeneration++;
  for (const [index, group] of st.groups) {
    if (index >= st.sum.sections.length) {
      world.remove(group); group.traverse(object => object.geometry?.dispose()); st.groups.delete(index);
    }
  }
  if (st.sel >= st.sum.sections.length) st.sel = -1;
  buildLodBar(); buildTree(); renderPart(); if (!place.group) renderModel(); renderLights(); renderExhausts(); renderFile();
  $('#fileName').textContent = `${st.sum.name} · ${(st.sum.size / 1024).toFixed(0)} KB`;
  if (reload === 'all') { return loadAll(); } else if (reload && reload.length) { return Promise.all(reload.map(index => loadSection(index))).then(() => status('updated')); }
  else applyVisibility();
}

// ---------------------------------------------------------------------------------------------- OBJ
function exportObj(sections) {
  const lods = [...st.lods].join(',');
  const q = new URLSearchParams({ lods, uv: 'auto', flip_v: '1' });
  if (sections) q.set('sections', sections.join(','));
  location.href = '/api/export_obj?' + q;
}

$('#objIn').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  st.objText = await f.text(); st.objName = f.name;
  try { st.objInfo = await post('/api/obj_info', { text: st.objText }); } catch (err) { status(err.message, true); return; }
  e.target.value = ''; renderObjDialog();
});

function renderObjDialog() {
  const box = $('#objDialog'); if (!box) return;
  box.replaceChildren();
  if (!st.objInfo) return;
  const objs = Object.keys(st.objInfo.objects);
  const mats = new Set(); for (const o of Object.values(st.objInfo.objects)) for (const lod of Object.values(o)) for (const m of Object.keys(lod)) mats.add(m);
  box.append(
    el('h3', {}, `Import ${st.objName} into ${st.sum.sections[st.importTarget]?.name ?? '?'}`),
    el('div', { class: 'field' }, el('label', {}, 'object'), el('select', { id: 'oObj' }, objs.map(o => el('option', { value: o }, o)))),
    el('div', { class: 'field' }, el('label', {}, 'default LOD'), el('input', { type: 'number', id: 'oLod', value: Math.max(1, Math.min(...[...st.lods, 5])), min: 0, max: 5 }), el('span', { class: 'muted' }, 'for groups without _L<n>')),
    el('div', { class: 'field' }, el('label', {}, 'uv0 (atlas)'), el('select', { id: 'oUv' }, el('option', { value: 'swatch' }, 'flat colour for materials the car already draws flat'), el('option', { value: 'obj' }, 'always the OBJ uv'))),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'oFlip', checked: true }), ' flip V (OBJ bottom-left origin)')),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'oDraw' }), ' draw the part in the game even if it was hidden')),
    el('table', {}, el('tr', {}, ['OBJ material', 'FH1 material'].map(h => el('th', {}, h))), [...mats].map(m => el('tr', {}, el('td', {}, m), el('td', {}, el('input', { 'data-m': m, value: m, list: 'materials' }))))),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: doImport }, 'Replace geometry'), el('button', { onclick: () => { st.objInfo = null; renderObjDialog(); } }, 'Cancel')),
    el('p', { class: 'muted' }, 'The new geometry replaces ALL sub-meshes of the part. Baked lighting is fitted from the car\'s own vertices; the damage data of the part is cleared. Untested in the game: test every imported part.'));
}

async function doImport() {
  const mm = {}; for (const i of $('#objDialog').querySelectorAll('input[data-m]')) mm[i.dataset.m] = i.value;
  const si = st.importTarget;
  await run('import_obj', { section: si, text: st.objText, object: $('#oObj').value, default_lod: parseInt($('#oLod').value) || 1, uv0_mode: $('#oUv').value, flip_v: $('#oFlip').checked, force_draw: $('#oDraw').checked, material_map: mm }, [si]);
  st.objInfo = null; renderObjDialog();
}

// ---------------------------------------------------------------------------------------------- lights, exhausts
function renderLights() {
  const box = $('#tab-lights'); box.replaceChildren();
  if (!st.sum || !st.sum.body_header) { box.append(el('p', { class: 'muted' }, 'This file has no light records (only the main carbin has them).')); return; }
  box.append(el('p', { class: 'muted' }, 'Light records. Positions use +z = front, so the view draws them with z flipped. Put each record on the real bulb. Role 4 = beam; glow size above 15 gives huge halos.'),
    el('div', { class: 'field' }, el('button', { onclick: () => run('light_add', {}, []) }, 'Add light'), el('span', { class: 'muted' }, `${st.sum.lights.length} records`)));
  for (const l of st.sum.lights) {
    const set = (fields) => run('light_set', { index: l.index, fields }, []);
    const hex = '#' + l.rgb.map(v => v.toString(16).padStart(2, '0')).join('');
    box.append(el('div', { class: 'light' + (l.index === st.selLight ? ' sel' : ''), onclick: () => { st.selLight = l.index; drawOverlays(); } },
      el('div', { class: 'field' }, el('strong', {}, '#' + l.index), el('span', { class: 'pill' }, 'role ' + l.role),
        el('button', { onclick: ev => { ev.stopPropagation(); run('light_add', { like: l.index }, []); } }, 'Copy'),
        el('button', { class: 'danger', onclick: ev => { ev.stopPropagation(); confirm('Remove light #' + l.index + '?') && run('light_remove', { index: l.index }, []); } }, 'Remove')),
      el('div', { class: 'field' }, el('label', {}, 'position'), el('div', { class: 'grid3' }, [0, 1, 2].map(i => el('input', { type: 'number', step: 0.005, value: l.pos[i].toFixed(4), onchange: e => { const p = [...l.pos]; p[i] = parseFloat(e.target.value); set({ pos: p }); } })))),
      el('div', { class: 'field' }, el('label', {}, 'direction'), el('div', { class: 'grid3' }, [0, 1, 2].map(i => el('input', { type: 'number', step: 1000, value: l.dir[i], onchange: e => { const d = [...l.dir]; d[i] = parseInt(e.target.value); set({ dir: d }); } })))),
      el('div', { class: 'field' }, el('label', {}, 'role/flags'), el('div', { class: 'grid3' },
        ['b18', 'role', 'b20'].map(k => el('input', { type: 'number', step: 1, min: 0, max: 255, title: k, value: l[k], onchange: e => set({ [k]: parseInt(e.target.value) }) })))),
      el('div', { class: 'field' }, el('label', {}, 'b21 / glow'), el('div', { class: 'grid3' },
        el('input', { type: 'number', min: 0, max: 255, value: l.b21, onchange: e => set({ b21: parseInt(e.target.value) }) }),
        el('input', { type: 'number', min: 0, max: 255, value: l.glow, title: 'glow size', onchange: e => set({ glow: parseInt(e.target.value) }) }),
        el('input', { type: 'color', value: hex, onchange: e => { const v = e.target.value; set({ rgb: [1, 3, 5].map(i => parseInt(v.slice(i, i + 2), 16)) }); } }))),
    ));
  }
}

function renderExhausts() {
  const box = $('#tab-exhausts'); box.replaceChildren();
  if (!st.sum || !st.sum.body_header) { box.append(el('p', { class: 'muted' }, 'This file has no exhaust emitters.')); return; }
  box.append(el('p', { class: 'muted' }, 'Exhaust and backfire emitters. Move positions only: the count and the side flags bind each emitter to an exhaustL*/exhaustR* part, and changing them crashed the game.'));
  for (const e of st.sum.exhausts) {
    box.append(el('div', { class: 'light' }, el('div', { class: 'field' }, el('strong', {}, '#' + e.index), el('span', { class: 'pill' }, e.flag === 2 ? 'left' : e.flag === 3 ? 'right' : 'flag ' + e.flag)),
      el('div', { class: 'field' }, el('label', {}, 'position'), el('div', { class: 'grid3' }, [0, 1, 2].map(i => el('input', { type: 'number', step: 0.005, value: e.pos[i].toFixed(4), onchange: ev => { const p = [...e.pos]; p[i] = parseFloat(ev.target.value); run('exhaust_set', { index: e.index, pos: p }, []); } }))))));
  }
}

// ---------------------------------------------------------------------------------------------- add a model (FBX / GLB / OBJ), placed with a gizmo
const place = { group: null, name: '', ext: '', mats: [], tris: 0, doubleSided: false };
const ghostMat = new THREE.MeshStandardMaterial({ color: 0xffa726, transparent: true, opacity: 0.85, side: THREE.FrontSide, roughness: 0.5 });

function canPairModels() {
  const origin = st.sum?.origin; if (!origin) return false;
  const media = origin.source === 'game' ? origin.archive.split(/[\\/]/).pop().replace(/\.zip$/i, '') : origin.member.split('/')[2];
  const name = origin.member.split('/').pop().toLowerCase();
  const pair = [media?.toLowerCase() + '.carbin', media?.toLowerCase() + '_lod0.carbin'];
  if (!media || !pair.includes(name)) return false;
  return !st.siblingNames || pair.every(n => st.siblingNames.has(n));        // a few cars have no close-up model: nothing to pair with
}

st.canPair = canPairModels;                      // part_tools uses it to decide whether the merge dialog can edit both models
async function loadModelFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  let root;
  try {
    if (ext === 'fbx') root = new FBXLoader().parse(await file.arrayBuffer(), '');
    else if (ext === 'glb') root = (await new GLTFLoader().parseAsync(await file.arrayBuffer(), '')).scene;
    else if (ext === 'gltf') root = (await new GLTFLoader().parseAsync(await file.text(), '')).scene;
    else if (ext === 'obj') root = new OBJLoader().parse(await file.text());
    else if (ext === 'modelbin') {                       // experimental: a modern Forza model; the server converts it to OBJ in FH1 coordinates (front = -z)
      const r = await fetch('/api/modelbin/obj?lod=0', { method: 'POST', body: await file.arrayBuffer() });
      if (!r.ok) { let m = 'conversion failed'; try { m = (await r.json()).error || m; } catch (e) { /* not JSON */ } throw new Error(m); }
      root = new OBJLoader().parse(await r.text());
      status(`${file.name}: converted from a modern Forza .modelbin (experimental), sharpest LOD only`);
    }
    else throw new Error('unsupported file type .' + ext + ' (use FBX, GLB, glTF, OBJ or a modern Forza .modelbin)');
  } catch (e) { status('could not read the model: ' + e.message, true); return; }
  startPlacement(root, file.name, ext);
}

function clearPlacement() {
  if (tc.object === place.group) tc.detach();
  if (place.group) { overlay.remove(place.group); place.group.traverse(o => o.geometry && o.geometry.dispose && o.userData.own && o.geometry.dispose()); }
  place.group = null; place.mats = [];
}

function startPlacement(root, name, ext) {
  clearPlacement();
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) { status('the model has no geometry', true); return; }
  const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
  const group = new THREE.Group(); group.add(root); root.position.sub(center);
  let tris = 0; const counts = new Map();
  root.traverse(o => {
    if (!o.isMesh || !o.geometry.attributes.position) return;
    o.userData.matNames = [].concat(o.material).map(m => (m && m.name) || 'material');
    const g = o.geometry, n = g.index ? g.index.count : g.attributes.position.count;
    const groups = g.groups.length ? g.groups : [{ start: 0, count: n, materialIndex: 0 }];
    for (const gr of groups) { const nm = o.userData.matNames[Math.min(gr.materialIndex || 0, o.userData.matNames.length - 1)]; counts.set(nm, (counts.get(nm) || 0) + Math.floor(gr.count / 3)); tris += Math.floor(gr.count / 3); }
    o.material = ghostMat;
  });
  const maxDim = Math.max(size.x, size.y, size.z);
  group.scale.setScalar(maxDim > 20 ? 0.01 : 1);          // most FBX files are in centimetres
  let at = new THREE.Vector3(0, 0.7, 0);
  const g0 = st.groups.get(st.sel);
  if (g0) { const b = new THREE.Box3().setFromObject(g0); if (!b.isEmpty()) at = b.getCenter(new THREE.Vector3()); }
  group.position.copy(at);
  overlay.add(group); partGizmo.detach(); tc.attach(group);
  Object.assign(place, { group, name, ext, tris, mats: [...counts.entries()].map(([n, t]) => ({ name: n, tris: t })) });
  updateGizmoUI();
  st.modelTarget = st.modelTarget ?? st.sel;
  setTab('model'); renderModel();
  status(`${name}: ${tris} triangles. Drag the gizmo to place it, then add it to the car.`);
}

function guessMaterial(n) {
  const have = new Set(st.sum.sections.flatMap(s => s.subs.map(b => b.material)));
  const pick = (...c) => c.find(x => have.has(x));
  const l = n.toLowerCase();
  let r;
  if (have.has(n)) return n;
  if (/glass|window|windshield/.test(l)) r = pick('detail_glass_clear', 'glass', 'window');
  else if (/chrome|metal|alum|badge|emblem/.test(l)) r = pick('chrome', 'badge');
  else if (/paint|body|car/.test(l)) r = pick('body');
  else if (/rubber|tire|tyre/.test(l)) r = pick('rubber_trim', 'black');
  else if (/carbon/.test(l)) r = pick('carbon_fiber', 'black');
  else if (/light|lamp|lens/.test(l)) r = pick('detail_glass_red', 'head_light', 'black');
  return r || pick('black', 'plastic2', 'plastic') || [...have][0] || n;
}

const fld = (id, v, step = 0.01) => el('input', { type: 'number', id, value: v, step });
function syncPlaceFields() {
  const g = place.group; if (!g) return;
  const set = (id, v) => { const e = document.getElementById(id); if (e && document.activeElement !== e) e.value = (+v).toFixed(4); };
  set('mpx', g.position.x); set('mpy', g.position.y); set('mpz', g.position.z);
  set('mrx', THREE.MathUtils.radToDeg(g.rotation.x)); set('mry', THREE.MathUtils.radToDeg(g.rotation.y)); set('mrz', THREE.MathUtils.radToDeg(g.rotation.z));
  set('msx', g.scale.x); set('msy', g.scale.y); set('msz', g.scale.z);
  const box = new THREE.Box3().setFromObject(g), sz = box.getSize(new THREE.Vector3());
  const e = document.getElementById('mSize'); if (e) e.textContent = `${sz.x.toFixed(3)} x ${sz.y.toFixed(3)} x ${sz.z.toFixed(3)} m (width x height x length)`;
}
function readPlaceFields() {
  const g = place.group, v = id => parseFloat(document.getElementById(id).value) || 0;
  g.position.set(v('mpx'), v('mpy'), v('mpz'));
  g.rotation.set(THREE.MathUtils.degToRad(v('mrx')), THREE.MathUtils.degToRad(v('mry')), THREE.MathUtils.degToRad(v('mrz')));
  const s = id => { const x = parseFloat(document.getElementById(id).value); return x || 1; };
  g.scale.set(s('msx'), s('msy'), s('msz')); syncPlaceFields();
}

const defaultPartName = name => (String(name || 'custom_model').replace(/\.[^.]*$/, '').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase() || 'custom_model').slice(0, 40);
function syncModelMode() {
  const mode = $('#mMode')?.value; if (!mode) return;
  $('#mNameRow').style.display = mode === 'new' ? '' : 'none';
  $('#mTargetLabel').textContent = mode === 'new' ? 'copy settings from' : 'part';
}

function renderModel() {
  const box = $('#tab-model'); box.replaceChildren();
  if (!st.sum) { box.append(el('p', { class: 'muted' }, 'Open a .carbin first.')); return; }
  if (!place.group) {
    box.append(el('h3', {}, 'Add a model to the car'),
      el('p', {}, 'Drop an FBX, GLB / glTF or OBJ file anywhere on this page (or choose one). A part from a modern Forza game (a .modelbin file) also works, as an experiment: only its sharpest LOD is read and its textures are not. It appears in orange; drag it into place with the gizmo, then add it to a part. Your model is never sent anywhere except this program on your computer.'),
      el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => $('#modelIn').click() }, 'Choose a model file...')),
      el('p', { class: 'muted' }, 'Tips: the car is about 4.5 m long, front toward -z (yellow arrow). Most FBX files are in centimetres (the editor guesses and scales by 0.01). Give each material a name from the car (body, chrome, black, glass...): see the table that appears.'));
    return;
  }
  const lodsPresent = [...new Set(st.sum.sections.flatMap(s => s.subs.map(b => b.lod)))].sort();
  const dl = $('#materials'); dl.replaceChildren(...[...new Set(st.sum.sections.flatMap(x => x.subs.map(b => b.material)))].sort().map(m => el('option', { value: m })));
  const f3 = (pre, vals, step) => el('div', { class: 'grid3' }, ['x', 'y', 'z'].map((a, i) => el('input', { type: 'number', id: pre + a, value: vals[i], step, onchange: readPlaceFields })));
  box.append(
    el('h3', {}, `${place.name}`), el('p', { class: 'muted' }, `${place.tris} triangles, ${place.mats.length} material(s)`),
    el('div', {class: 'field'}, el('button', {onclick: () => partTools.copy('import')}, 'Copy coordinates'), el('button', {onclick: () => partTools.paste('import')}, 'Paste coordinates')),
    el('div', { class: 'field' }, ['translate', 'rotate', 'scale'].map((m, i) => el('button', { title: ['W', 'E', 'R'][i], onclick: () => { activatePlacement(); setGizmoMode(m); } }, m))),
    el('div', { class: 'field' }, el('label', {}, 'position'), f3('mp', [0, 0, 0], 0.01)),
    el('div', { class: 'field' }, el('label', {}, 'rotation'), f3('mr', [0, 0, 0], 1)),
    el('div', { class: 'field' }, el('label', {}, 'scale'), f3('ms', [1, 1, 1], 0.01)),
    el('p', { class: 'muted', id: 'mSize' }, ''),
    el('div', { class: 'field' },
      el('button', { onclick: () => { place.group.rotateY(Math.PI); syncPlaceFields(); } }, 'Turn around (front/back)'),
      el('button', { onclick: () => { place.group.rotateX(-Math.PI / 2); syncPlaceFields(); } }, 'Z-up to Y-up'),
      el('button', { onclick: () => { place.group.scale.x *= -1; syncPlaceFields(); } }, 'Mirror X')),
    el('div', { class: 'field' }, el('label', {}, 'longest side'), el('input', { type: 'number', id: 'mFit', value: 1, step: 0.05 }), el('span', { class: 'muted' }, 'm'),
      el('button', { onclick: () => { const b = new THREE.Box3().setFromObject(place.group), m = Math.max(...b.getSize(new THREE.Vector3()).toArray()); const k = (parseFloat($('#mFit').value) || 1) / m; place.group.scale.multiplyScalar(k); syncPlaceFields(); } }, 'Set size')),
    el('h3', {}, 'Where it goes'),
    el('p', {class: 'muted'}, 'A separate part is the easiest: it appears in the part list, you can select and move it again at any time, and it stays its own part when you save and in Forza editors. Merging puts it inside an existing named slot (including an empty bumper, hood or side skirt slot) and keeps that slot\'s upgrade assignment, but then it can no longer be moved on its own.'),
    el('div', { class: 'field' }, el('label', {}, 'how'), el('select', { id: 'mMode', onchange: syncModelMode }, el('option', { value: 'new' }, 'as a separate part (recommended)'), el('option', { value: 'add' }, 'merge onto an existing part (keep what it has)'), el('option', { value: 'replace' }, 'replace an existing part\'s geometry'))),
    el('div', { class: 'field', id: 'mNameRow' }, el('label', {}, 'part name'), el('input', { id: 'mName', value: defaultPartName(place.name), maxlength: 48 })),
    el('div', { class: 'field' }, el('label', { id: 'mTargetLabel' }, 'copy settings from'), el('select', { id: 'mTarget' }, st.sum.sections.map(s => el('option', { value: s.index, selected: s.index === (st.modelTarget ?? st.sel) }, s.name + (s.subs.length ? '' : ' (empty slot)'))))),
    el('label', {class: 'chk'}, el('input', {id: 'mBothModels', type: 'checkbox', checked: canPairModels(), disabled: !canPairModels()}), ' Driving + close-up / photo model (main + LOD0)'),
    el('label', {class: 'chk'}, el('input', {id: 'mDoubleSided', type: 'checkbox', checked: place.doubleSided, onchange: e => {
      place.doubleSided = e.target.checked; ghostMat.side = place.doubleSided ? THREE.DoubleSide : THREE.FrontSide; ghostMat.needsUpdate = true;
    }}), ' Double-sided — visible from both sides in FH1'),
    el('p', {class: 'muted'}, 'Double-sided adds back faces when saving. It doubles the imported triangle count.'),
    el('div', { class: 'field' }, el('label', {}, 'LODs'), el('div', {}, lodsPresent.map(l => el('label', { class: 'chk' }, el('input', { type: 'checkbox', 'data-lod': l, checked: true }), ' ' + l + '  ')))),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'mAuto', checked: true }), ' automatic LODs: the lowest ticked LOD gets the full model, higher LODs get simplified copies sized like this part\'s own LODs')),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'mFlip', checked: place.ext !== 'gltf' && place.ext !== 'glb' }), ' flip V (FBX / OBJ have a bottom-left origin)')),
    el('div', { class: 'field' }, el('label', {}, 'uv0 (atlas)'), el('select', { id: 'mUv' }, el('option', { value: 'swatch' }, 'flat colour for materials the car draws flat'), el('option', { value: 'obj' }, 'always the model\'s uv'))),
    el('h3', {}, 'Materials'),
    el('table', {}, el('tr', {}, ['in your model', 'tris', 'FH1 material'].map(h => el('th', {}, h))),
      place.mats.map(m => el('tr', {}, el('td', {}, m.name), el('td', {}, String(m.tris)), el('td', {}, el('input', { 'data-mat': m.name, value: guessMaterial(m.name), list: 'materials' }))))),
    el('p', { class: 'muted' }, 'A material name must exist in the car\'s ShaderSettings.xml, and its look comes from the car\'s atlas textures. An added model has no texture of its own: it uses the flat colour of the material you pick.'),
    el('h3', {}, 'Texture (optional)'),
    el('p', { class: 'muted' }, 'Choose an image, then Auto place or drag a rectangle on the texture. Red areas are already used by the car. Free space by ticking parts you no longer need; those parts may lose their original texture. Changes stay staged until you apply or export the car.'),
    el('div', { class: 'field' }, el('input', { type: 'file', accept: 'image/*', id: 'mTexFile', onchange: e => { st.texFile = e.target.files[0] || null; drawTexCanvas(); } }), el('button', { onclick: () => { st.texFile = null; st.texRect = null; document.getElementById('mTexFile').value = ''; drawTexCanvas(); } }, 'No texture')),
    el('div', { class: 'field' }, el('button', { onclick: autoPlace }, 'Auto place'), el('span', { class: 'muted', id: 'mFreeInfo' }, '')),
    el('div', { id: 'mParts', style: 'max-height:140px;overflow:auto;border:1px solid var(--line);padding:4px;margin:4px 0' }),
    el('canvas', { id: 'mTexCanvas', width: 300, height: 300, style: 'max-width:100%;cursor:crosshair;border:1px solid var(--line)' }),
    el('p', { class: 'muted', id: 'mTexInfo' }, 'no texture chosen'),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: commitModel }, 'Add to car'), el('button', { onclick: () => { clearPlacement(); renderModel(); status('cancelled'); } }, 'Cancel')));
  syncPlaceFields(); setupTexCanvas(); syncModelMode();
  const g = place.group;
  ['mpx', 'mpy', 'mpz'].forEach((id, i) => { document.getElementById(id).value = g.position.getComponent(i).toFixed(4); });
}

let texAtlasImg = null;
const ATLAS = 'nodamage';
st.overwrite = new Set();
async function loadFree() {
  st.freeLoading = true;
  try {
    const aspect = st.texAspect ? '&aspect=' + st.texAspect.toFixed(4) : '';
    st.free = { name: ATLAS, data: await api('/api/atlas_free?overwrite=' + encodeURIComponent([...st.overwrite].join('|')) + aspect) };
  } catch (e) { st.free = { name: ATLAS, data: { res: 1, used: '', used_pct: 0, free_pct: 0, best: null, parts: [] } }; status(e.message, true); }
  st.freeLoading = false; renderParts(); drawTexCanvas();
}
function renderParts() {
  const box = document.getElementById('mParts'); if (!box || !st.free) return;
  box.replaceChildren(...st.free.data.parts.map(p => el('label', { class: 'chk', style: 'display:block' },
    el('input', { type: 'checkbox', checked: st.overwrite.has(p.name), onchange: e => { e.target.checked ? st.overwrite.add(p.name) : st.overwrite.delete(p.name); st.free = null; st.texRect = null; loadFree(); } }),
    ` ${p.name}  (reads ${p.pct}%, only it: ${p.only_pct}%)`)));
}
async function autoPlace() {
  if (st.texFile) { const b = await createImageBitmap(st.texFile); st.texAspect = b.width / b.height; }
  st.free = null; await loadFree();
  const best = st.free && st.free.data.best;
  if (!best) { status('no free area: tick parts to give up first', true); return; }
  st.texRect = best; drawTexCanvas(); status(`placed in the largest free area (${st.free.data.free_pct}% of the atlas)`);
}
function drawTexCanvas() {
  const c = document.getElementById('mTexCanvas'); if (!c) return;
  const name = ATLAS;
  const draw = () => {
    const x = c.getContext('2d'); x.clearRect(0, 0, c.width, c.height);
    if (texAtlasImg && texAtlasImg.complete && texAtlasImg.naturalWidth) x.drawImage(texAtlasImg, 0, 0, c.width, c.height); else { x.fillStyle = '#333'; x.fillRect(0, 0, c.width, c.height); }
    // red = already read by a part of the car (the server rasterises every sub-mesh that reads this atlas); the rest is free
    const fr = st.free && st.free.name === name ? st.free.data : null;
    if (fr && fr.used) {
      const R = fr.res, bytes = Uint8Array.from(atob(fr.used), ch => ch.charCodeAt(0)), img = x.createImageData(R, R);
      for (let i = 0; i < R * R; i++) if ((bytes[i >> 3] >> (7 - (i & 7))) & 1) { img.data[i * 4] = 255; img.data[i * 4 + 1] = 40; img.data[i * 4 + 2] = 40; img.data[i * 4 + 3] = 120; }
      const off = document.createElement('canvas'); off.width = R; off.height = R; off.getContext('2d').putImageData(img, 0, 0);
      x.imageSmoothingEnabled = false; x.drawImage(off, 0, 0, c.width, c.height);
    } else if (!fr && !st.freeLoading) loadFree();
    const fi = document.getElementById('mFreeInfo');
    if (fi) fi.textContent = fr ? `${fr.used_pct}% used, largest free area ${fr.free_pct}% of the atlas` : 'measuring...';
    if (st.texRect) { const [u0, v0, u1, v1] = st.texRect; x.strokeStyle = '#00e676'; x.lineWidth = 2; x.fillStyle = 'rgba(0,230,118,.25)'; x.fillRect(u0 * c.width, v0 * c.height, (u1 - u0) * c.width, (v1 - v0) * c.height); x.strokeRect(u0 * c.width, v0 * c.height, (u1 - u0) * c.width, (v1 - v0) * c.height); }
    const info = document.getElementById('mTexInfo');
    if (info) info.textContent = !st.texFile ? 'no texture chosen' : (st.texRect ? `image "${st.texFile.name}" goes into uv rectangle ${st.texRect.map(v => v.toFixed(3)).join(', ')}` : `image "${st.texFile.name}": now drag a rectangle on the atlas`);
  };
  if (!texAtlasImg || texAtlasImg.dataset.name !== name) { texAtlasImg = new Image(); texAtlasImg.dataset.name = name; texAtlasImg.onload = draw; texAtlasImg.onerror = draw; texAtlasImg.src = '/api/atlas?name=' + name + '&v=' + st.texVer; }
  draw();
}
function setupTexCanvas() {
  const c = document.getElementById('mTexCanvas'); if (!c) return;
  let start = null;
  const pos = e => { const r = c.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  c.addEventListener('pointerdown', e => { start = pos(e); c.setPointerCapture(e.pointerId); });
  c.addEventListener('pointermove', e => { if (!start) return; const p = pos(e); st.texRect = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])]; drawTexCanvas(); });
  c.addEventListener('pointerup', () => { start = null; if (st.texRect && (st.texRect[2] - st.texRect[0] < 0.01 || st.texRect[3] - st.texRect[1] < 0.01)) st.texRect = null; drawTexCanvas(); });
  texAtlasImg = null; drawTexCanvas();
}

function b64(arr) {
  const u8 = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength); let s = '';
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}

function collectMeshes(mapping) {
  place.group.updateMatrixWorld(true);
  const out = [], nm = new THREE.Matrix3(), v = new THREE.Vector3(), n = new THREE.Vector3();
  place.group.traverse(o => {
    if (!o.isMesh || !o.userData.matNames) return;
    const g = o.geometry, pos = g.attributes.position, nrm = g.attributes.normal, uv = g.attributes.uv, idx = g.index;
    nm.getNormalMatrix(o.matrixWorld);
    const total = idx ? idx.count : pos.count;
    const groups = g.groups.length ? g.groups : [{ start: 0, count: total, materialIndex: 0 }];
    for (const gr of groups) {
      const matName = o.userData.matNames[Math.min(gr.materialIndex || 0, o.userData.matNames.length - 1)];
      const ids = [];
      for (let i = gr.start; i + 2 < gr.start + gr.count && i + 2 < total; i += 3) for (let k = 0; k < 3; k++) ids.push(idx ? idx.getX(i + k) : i + k);
      const map = new Map(); for (const id of ids) if (!map.has(id)) map.set(id, map.size);
      const P = new Float32Array(map.size * 3), N = new Float32Array(map.size * 3), U = new Float32Array(map.size * 2), I = new Uint32Array(ids.length);
      for (const [id, j] of map) {
        v.fromBufferAttribute(pos, id).applyMatrix4(o.matrixWorld); P.set([v.x, v.y, v.z], j * 3);
        if (nrm) { n.fromBufferAttribute(nrm, id).applyMatrix3(nm).normalize(); N.set([n.x, n.y, n.z], j * 3); }
        if (uv) U.set([uv.getX(id), uv.getY(id)], j * 2);
      }
      ids.forEach((id, k) => { I[k] = map.get(id); });
      out.push({ material: mapping[matName] || guessMaterial(matName), positions: b64(P), normals: nrm ? b64(N) : '', uvs: uv ? b64(U) : '', indices: b64(I) });
    }
  });
  return out;
}

async function commitModel() {
  const mapping = {}; for (const i of $('#tab-model').querySelectorAll('input[data-mat]')) mapping[i.dataset.mat] = i.value.trim();
  const lods = [...$('#tab-model').querySelectorAll('input[data-lod]')].filter(i => i.checked).map(i => parseInt(i.dataset.lod));
  if (!lods.length) { status('choose at least one LOD', true); return; }
  const target = parseInt($('#mTarget').value);
  if ($('#mMode').value === 'replace' && !confirm(`Replace ALL geometry of ${st.sum.sections[target].name} with this model?`)) return;
  let texture = null;
  if (st.texFile) {
    if (!st.texRect) { status('drag a rectangle on the atlas for the texture first (or press No texture)', true); return; }
    const bmp = await createImageBitmap(st.texFile); const sc = Math.min(1, 512 / Math.max(bmp.width, bmp.height)); const w = Math.max(1, Math.round(bmp.width * sc)), h = Math.max(1, Math.round(bmp.height * sc));
    texture = { w, h, rgba: await imageToRGBA(st.texFile, w, h), rect: st.texRect, atlases: ['nodamage', 'damage'] };
  }
  const meshes = collectMeshes(mapping);
  status(`sending ${meshes.length} mesh group(s)...`);
  const both_models = $('#mBothModels').checked && canPairModels();
  const mode = $('#mMode').value, partName = ($('#mName').value || '').trim() || defaultPartName(place.name);
  const committed = await run('add_model', { section: target, mode, name: partName, lods, both_models, double_sided: $('#mDoubleSided').checked, flip_v: $('#mFlip').checked, auto_lods: $('#mAuto').checked, texture, uv0_mode: $('#mUv').value, meshes }, [target]);
  if (!committed) return;
  clearPlacement(); st.modelTarget = target; renderModel();
  if (mode === 'new' && st.sum.last_pasted != null) {
    const made = st.sum.last_pasted; selectSection(made); setTab('part');       // selected, so the gizmo can move it right away
    status(`Added "${st.sum.sections[made].name}" as its own part. Select it in the part list any time to move, rotate or scale it.`);
    return;
  }
  selectSection(target); setTab('model');
  if (both_models) status('Merged into driving + photo models. Apply to existing car or export the car ZIP to save.');
}

$('#modelIn').addEventListener('change', async e => { const f = e.target.files[0]; e.target.value = ''; if (f) await loadModelFile(f); });
addEventListener('keydown', e => {
  if (e.ctrlKey || e.altKey || e.metaKey || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable) return;
  if (e.key === 'Escape' && partGizmo.cancel()) {status('Drag cancelled.'); return;}
  if (!tc.object || partGizmo.busy) return;
  const mode = {w:'translate',e:'rotate',r:'scale'}[e.key.toLowerCase()];
  if (mode) {e.preventDefault(); setGizmoMode(mode);}
});

// ---------------------------------------------------------------------------------------------- open (game + DLC) and save
async function refreshLibrary() {
  try { st.lib = await api('/api/library'); st.libraryError = ''; }
  catch (error) { st.libraryError = error.message; workflow.renderSettings(); setTab('settings'); throw error; }
  workflow.renderSettings();
  renderFile();
  workflow.loadCars();                      // in the background: the car that is already open must not wait for the list of all cars
}

async function saveBack() {
  try {
    const r = await post('/api/library/save', {});
    st.sum = await api('/api/summary'); afterChange([]);
    status(`Updated the existing car: ${r.saved.members.length} files in ${r.saved.zip.split(/[\\/]/).pop()}. Backup: ${r.saved.backup}`);
  } catch (e) { status(e.message, true); }
}

async function applyExistingCar() {
  if (!st.sum?.origin) return status('Right-click a car in Cars and choose Import first.', true);
  const source = st.lib?.sources.find(s => s.id === st.sum.origin.source);
  if (!source?.writable) {
    renderFile(); setTab('file');
    return status('This install is a read-only GoD, ISO or STFS package. Export car ZIP, then replace the ZIP in an extracted game or DLC copy. Set that extracted folder in Settings to apply directly.', true);
  }
  await saveBack();
}

async function exportCarZip() {
  if (!st.sum?.origin) return status('Import a car from Cars first.', true);
  try {
    const start = st.lib?.output_dir || '';                 // the folder used last time: the chooser opens there
    const out_dir = window.pywebview?.api?.choose_path ? await window.pywebview.api.choose_path('output', start) : prompt('Folder for the replacement car ZIP:', start);
    if (!out_dir) return;
    status('Building replacement ZIP with all staged edits…');
    busy.start('Building the replacement ZIP…');
    let result;
    try { result = await post('/api/library/export_zip', {out_dir}); } finally { busy.end(); }
    if (st.lib && result.output_dir) { st.lib.output_dir = result.output_dir; st.cloneDef = null; }      // remembered: offered again next time
    status(`Replacement ZIP saved: ${result.saved.zip}. Car identity kept. Use it in your extracted game / DLC copy.`);
  } catch (error) { status(error.message, true); }
}

// ---------------------------------------------------------------------------------------------- save as a new car
async function loadCloneDefaults() {
  try { st.cloneDef = await api('/api/clone/defaults'); st.cloneKey = st.sum.origin.member + st.sum.origin.archive; } catch (e) { st.cloneDef = null; }
  renderFile();
}
async function stageFile(clear) {
  try {
    const r = await post('/api/stage', clear ? { clear: true } : {});
    if (clear) {
      invalidateTextures(); texAtlasImg = null; st.free = null;
      applyVisibility(); if ($('#mTexCanvas')) drawTexCanvas();
    }
    st.cloneDef = null; status(clear ? 'staged files cleared' : 'staged: ' + r.staged.join(', ')); loadCloneDefaults();
  }
  catch (e) { status(e.message, true); }
}
async function buildNewCar() {
  const v = id => document.getElementById(id).value.trim();
  try {
    status('building the new car (copying files, checking the database)...');
    busy.start('Building the new car (copying files, checking the database)…');
    const r = await post('/api/clone', { new_media: v('ncName'), offer: parseInt(v('ncOffer')), out_dir: v('ncOut'), include_open: document.getElementById('ncOpen').checked, display_name: v('ncTitle'), short_name: v('ncShort'),
      formats: ['xenia', 'stfs'].filter(f => document.getElementById('nc_' + f).checked) });
    st.cloneResult = r; st.cloneDef = null; loadCloneDefaults(); status(`new car built: id ${r.car_id}, ${r.rows} database rows, ${r.files} files`);
  } catch (e) { st.cloneResult = { error: e.message, problems: e.problems || [] }; renderFile(); status(e.message, true); }
  finally { busy.end(); }
}
function cloneSection() {
  const o = st.sum.origin;
  if (!o || !st.lib || !st.lib.config.game) return [];
  if (st.cloneKey !== o.member + o.archive) { st.cloneKey = o.member + o.archive; st.cloneDef = null; loadCloneDefaults(); }
  const d = st.cloneDef;
  if (!d) return [el('h3', {}, '4. Save as a new car'), el('p', { class: 'muted' }, 'loading...')];
  const r = st.cloneResult;
  return [el('h3', {}, '4. Export as DLC (a new car in its own package)'),
    el('p', { class: 'muted' }, `Copies ${d.donor} into a new car with its own database rows and files, using your edited files instead of the original ones. Give it a name to show in the game (plain text, no string table needed), or leave the name empty to keep the donor's. Edited files you want in it: open each one, edit, and press Stage (the file that is open now is included automatically).`),
    el('div', { class: 'field' }, el('label', {}, 'new name'), el('input', { id: 'ncName', value: d.new_media }), el('span', { class: 'muted' }, 'same length as ' + d.donor)),
    el('div', { class: 'field' }, el('label', {}, 'name in game'), el('input', { id: 'ncTitle', placeholder: "empty = the donor's name" }), el('span', { class: 'muted' }, 'shown in the Autoshow and garage')),
    el('div', { class: 'field' }, el('label', {}, 'short name'), el('input', { id: 'ncShort', placeholder: 'empty = same as the name' })),
    el('div', { class: 'field' }, el('label', {}, 'offer no.'), el('input', { id: 'ncOffer', type: 'number', value: d.offer })),
    el('div', { class: 'field' }, el('label', {}, 'output'), el('input', { id: 'ncOut', value: d.out_dir }),
      window.pywebview?.api?.choose_path ? el('button', { onclick: async () => { const box = document.getElementById('ncOut'); const picked = await window.pywebview.api.choose_path('output', box.value); if (picked) box.value = picked; } }, 'Browse…') : ''),
    el('p', { class: 'muted' }, 'The folder is remembered for next time.'),
    el('div', { class: 'field' }, el('label', {}, 'export as'), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'nc_xenia', checked: true }), ' Xenia folder  '), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'nc_stfs' }), ' console package (STFS, for a modded 360)')),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'ncOpen', checked: true }), ' include the file that is open now')),
    el('div', { class: 'field' }, el('button', { onclick: () => stageFile(false) }, 'Stage this file'), el('button', { onclick: () => stageFile(true) }, 'Clear staged'), el('span', { class: 'muted' }, d.staged.length ? 'staged: ' + d.staged.join(', ') : 'nothing staged')),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: buildNewCar }, 'Export as DLC')),
    r && r.error ? el('div', {}, el('p', { class: 'warn' }, r.error), r.problems && r.problems.length ? el('ul', { class: 'problems' }, r.problems.map(p => el('li', {}, p))) : '') : '',
    r && (r.folder || r.stfs) ? el('div', {}, el('p', { class: 'ok' }, `Exported: car id ${r.car_id}, ${r.rows} rows in ${r.tables} tables, ${r.files} files; edited files: ${r.edited.join(', ')}. ${r.folder ? (r.problems.length ? 'Package problems: ' + r.problems.join('; ') : 'Package checks passed.') : 'Console package written and read back.'}`),
      r.folder ? el('div', {}, el('p', {}, 'Xenia (with Xenia closed): back up your save, copy the folder'), el('p', { class: 'muted' }, r.folder), el('p', {}, 'to  ...\\4D5309C9\\00000002\\  and the file'), el('p', { class: 'muted' }, r.header), el('p', {}, 'to  ...\\4D5309C9\\Headers\\00000002\\ .')) : '',
      r.stfs ? el('div', {}, el('p', {}, 'Modded 360: the console package'), el('p', { class: 'muted' }, r.stfs), el('p', {}, 'goes on a USB stick like any content package.')) : '',
      el('p', {}, 'Then buy the new car in the Autoshow. Removing the pack later needs the save restored, because a saved car would point at nothing.')) : ''];
}

function renderFile() {
  const box = $('#tab-file'); box.replaceChildren();
  const lib = st.lib || { sources: [], config: { game: '', dlc: [] } };
  const o = st.sum && st.sum.origin;
  const src = o && lib.sources.find(s => s.id === o.source);
  if (st.sum) {
    const probs = st.sum.problems;
    box.append(el('h3', {}, '3. Save'),
      el('p', {}, o ? `Open: ${src ? src.label : o.source} - ${o.member}` : 'Open: a file from disk'),
      o ? el('div', { class: 'field' }, el('button', { class: 'primary', disabled: !(src && src.writable), onclick: () => saveBack(false) }, 'Apply to existing car'), el('button', {onclick: exportCarZip}, 'Export car ZIP…'),
        src && !src.writable ? el('span', { class: 'muted' }, src.why) : '') : '',
      o && o.source === 'game' ? el('p', { class: 'muted' }, 'Apply updates all staged models, textures and matching stripped companions inside the existing car ZIP; the car ID is kept. A backup <zip>.bak is made once. GoD/ISO: export a replacement ZIP and use it in an extracted game copy.') : '',
      o && o.source !== 'game' ? el('p', { class: 'muted' }, 'This replaces the file inside the DLC package (a backup <zip>.bak is made once). Close Xenia first: nothing is written while it is running. If Xenia has this pack installed from an earlier copy, copy the changed package over again.') : '',
      el('div', { class: 'field' }, el('input', { id: 'savePath', placeholder: 'C:\\path\\to\\Car.carbin', value: st.sum.path }), el('button', { onclick: savePath }, 'Save to path')),
      ...cloneSection(),
      el('h3', {}, 'Checks'),
      probs.length ? el('ul', { class: 'problems' }, probs.map(p => el('li', {}, p))) : el('p', { class: 'ok' }, 'No problems found: indexes, draw lists, buffer keys and a rebuild-and-parse all pass.'),
      el('h3', {}, 'Open by path'),
      el('div', { class: 'field' }, el('input', { id: 'openPath', placeholder: 'C:\\path\\to\\Car.carbin' }), el('button', { onclick: async () => { try { st.sum = await post('/api/open_path', { path: $('#openPath').value }); resetView(); } catch (e) { status(e.message, true); } } }, 'Open')),
      el('h3', {}, 'Statistics'),
      el('p', { class: 'muted' }, `${st.sum.sections.length} parts, ${st.sum.sections.reduce((a, s) => a + s.verts, 0)} vertices, ${st.sum.lights.length} lights, ${st.sum.exhausts.length} exhausts, ${st.sum.draw_arrays} draw arrays, pool ${st.sum.pool}.`));
  } else {
    box.append(el('h3', {}, 'Open by path'), el('div', { class: 'field' }, el('input', { id: 'openPath', placeholder: 'C:\\path\\to\\Car.carbin' }), el('button', { onclick: async () => { try { st.sum = await post('/api/open_path', { path: $('#openPath').value }); resetView(); } catch (e) { status(e.message, true); } } }, 'Open')));
  }
}

async function savePath() {
  const path = $('#savePath').value.trim(); if (!path) return;
  try {
    const r = await post('/api/save_path', { path });
    status(`saved ${r.saved} (${r.bytes} bytes)${r.backup ? ', backup ' + r.backup : ''}`);
  } catch (e) {
    if (e.problems) { if (confirm('Validation problems:\n' + e.problems.join('\n') + '\n\nSave anyway?')) { const r = await post('/api/save_path', { path, force: true }); status('saved ' + r.saved); } }
    else status(e.message, true);
  }
}
// ---------------------------------------------------------------------------------------------- physics (collision spheres, hull, body numbers)
st.phys = null;
let physObjs = [];
function clearPhys() { for (const o of physObjs) { overlay.remove(o); o.traverse(x => { x.geometry && x.geometry.dispose(); }); } physObjs = []; }
function drawPhysics() {
  clearPhys();
  const p = st.phys; if (!p || !p.has || $('#tab-physics').hidden) return;
  const fz = p.flip ? -1 : 1;
  p.spheres.forEach((s, i) => {
    const sel = i === p.sel, col = s.flags === 2 ? 0xff9a2e : s.flags === 1 ? 0x35d0ff : 0xb0b0b0;
    const m = new THREE.Mesh(new THREE.SphereGeometry(Math.max(s.r, 0.01), 14, 9), new THREE.MeshBasicMaterial({ color: sel ? 0xff2d95 : col, wireframe: true, transparent: true, opacity: sel ? 0.95 : 0.4 }));
    m.position.set(s.x, s.y, s.z * fz); overlay.add(m); physObjs.push(m);
  });
  if (p.hull.length) {
    const g = new THREE.BufferGeometry().setFromPoints(p.hull.map(h => new THREE.Vector3(h[0], h[1], h[2] * fz)));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0x7CFC00, size: 7, sizeAttenuation: false }));
    overlay.add(pts); physObjs.push(pts);
    const b = new THREE.Box3().setFromPoints(p.hull.map(h => new THREE.Vector3(h[0], h[1], h[2] * fz)));
    const bh = new THREE.Box3Helper(b, 0x7CFC00); overlay.add(bh); physObjs.push(bh);
  }
}
async function renderPhysics(reload) {
  const box = $('#tab-physics');
  if (!st.sum) { box.replaceChildren(el('p', { class: 'muted' }, 'Open a car file first.')); return; }
  if (reload || !st.phys || st.physKey !== st.sum.name + (st.sum.origin ? st.sum.origin.archive : '')) {
    try { const d = await api('/api/physics'); st.phys = Object.assign(d, { flip: false, sel: -1, hull: d.has ? d.hull.map(h => h.slice()) : [], spheres: d.has ? d.spheres.map(s => ({ ...s })) : [] }); st.physKey = st.sum.name + (st.sum.origin ? st.sum.origin.archive : ''); }
    catch (e) { box.replaceChildren(el('p', { class: 'warn' }, e.message)); return; }
  }
  const p = st.phys; box.replaceChildren();
  if (!p.has) { box.append(el('p', { class: 'muted' }, 'This car has no physicsdefinition.bin (open a file from a car archive of the game or a DLC).')); clearPhys(); return; }
  const num = (v, w = 62) => v.toFixed(3);
  const f = (id, label, val) => el('label', { class: 'chk' }, label + ' ', el('input', { id, value: val, style: 'width:56px' }), ' ');
  box.append(
    el('h3', {}, 'Collision shapes'),
    el('p', { class: 'muted' }, `${p.spheres.length} collision spheres (orange = body, blue = corner) and a hull of ${p.hull.length} points (green) are what the game uses for crashes. The physics files use the car's own metres. If the shapes look mirrored front to back on this car, tick the flip box (it only changes the drawing).`),
    el('div', { class: 'field' }, el('label', { class: 'chk' }, el('input', { type: 'checkbox', checked: p.flip, onchange: e => { p.flip = e.target.checked; drawPhysics(); } }), ' draw with z flipped'),
      el('button', { onclick: () => renderPhysics(true) }, 'Reload from file')),
    el('div', { class: 'field' }, f('pSx', 'scale x', '1'), f('pSy', 'y', '1'), f('pSz', 'z', '1')),
    el('div', { class: 'field' }, f('pOx', 'move x', '0'), f('pOy', 'y', '0'), f('pOz', 'z', '0'), el('button', { onclick: physScale }, 'Apply to all shapes')),
    el('div', { class: 'field' }, el('button', { onclick: physFit }, 'Fit shapes to my model edits'), el('span', { class: 'muted' }, 'stretches the spheres and hull by how much the model\'s size changed since it was opened')),
    el('div', { class: 'field' }, el('button', { onclick: () => { const s = p.spheres[p.sel] || { flags: 2, x: 0, y: 0.5, z: 0, r: 0.3 }; p.spheres.push({ ...s }); p.sel = p.spheres.length - 1; renderPhysics(); } }, 'Add sphere (copy of selected)')),
    el('div', { style: 'max-height:260px;overflow:auto;border:1px solid var(--line)' },
      el('table', {}, el('tr', {}, ['#', 'flags', 'x', 'y', 'z', 'radius', ''].map(h => el('th', {}, h))),
        p.spheres.map((s, i) => el('tr', { style: i === p.sel ? 'background:rgba(255,45,149,.2)' : '', onclick: () => { p.sel = i; drawPhysics(); } },
          el('td', {}, String(i)),
          ...['flags', 'x', 'y', 'z', 'r'].map(k => el('td', {}, el('input', { value: k === 'flags' ? s[k] : s[k].toFixed(3), style: 'width:54px', onfocus: () => { p.sel = i; drawPhysics(); }, onchange: e => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) { s[k] = k === 'flags' ? Math.round(v) : v; drawPhysics(); } } }))),
          el('td', {}, el('button', { onclick: ev => { ev.stopPropagation(); p.spheres.splice(i, 1); p.sel = -1; renderPhysics(); } }, 'x')))))),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => physSave(false) }, 'Save physics'), el('span', { class: 'muted' }, 'writes physicsdefinition.bin and Physics/MAXData.xml into the car\'s archive when it is in a DLC; for a game car they are staged for the new car you build')));
  if (p.body) {
    const names = Object.keys(p.body).filter(k => k !== 'Id');
    box.append(el('h3', {}, 'Body numbers (database)'),
      el('p', { class: 'muted' }, 'Wheelbase, track widths, ride heights and bounding box live in the database (Data_CarBody), which the base game cannot change; they go into the new car you build with Open / Save, section 4. Bounding box z is stored with +z = front, the opposite of the model (measured on the game\'s cars: x matches exactly, z is the model\'s z negated).'),
      el('div', { class: 'field' }, el('button', { onclick: physBodyFromModel }, 'Set bounding box from the model')),
      el('table', {}, names.map(k => el('tr', {}, el('td', {}, k), el('td', {}, el('input', { 'data-body': k, value: p.body[k], style: 'width:90px' }))))),
      el('div', { class: 'field' }, el('button', { class: 'primary', onclick: physBodySave }, 'Use these for the next new car')));
  }
  drawPhysics();
}
function physScale() {
  const p = st.phys, g = id => parseFloat(document.getElementById(id).value);
  const sc = [g('pSx'), g('pSy'), g('pSz')], of = [g('pOx'), g('pOy'), g('pOz')];
  if (![...sc, ...of].every(Number.isFinite) || sc.some(v => v <= 0)) { status('scale must be above 0 and every field a number', true); return; }
  physApply(sc, of); status('applied (not saved yet)');
}
function physApply(sc, of) {
  const p = st.phys, rs = (sc[0] + sc[1] + sc[2]) / 3;
  for (const s of p.spheres) { s.x = s.x * sc[0] + of[0]; s.y = s.y * sc[1] + of[1]; s.z = s.z * sc[2] + of[2]; s.r *= rs; }
  for (const h of p.hull) { h[0] = h[0] * sc[0] + of[0]; h[1] = h[1] * sc[1] + of[1]; h[2] = h[2] * sc[2] + of[2]; }
  renderPhysics();
}
function physFit() {
  const m = st.phys.mesh, sc = [], of = [];
  for (let i = 0; i < 3; i++) { const ol = m.original_lo[i], oh = m.original_hi[i], nl = m.lo[i], nh = m.hi[i]; const s = oh - ol > 1e-6 ? (nh - nl) / (oh - ol) : 1; sc.push(s); of.push(nl - ol * s); }
  physApply(sc, of); status(`fitted: scale ${sc.map(v => v.toFixed(3)).join(', ')} (not saved yet)`);
}
async function physSave(stage) {
  const p = st.phys;
  try {
    const r = await post('/api/physics/save', { spheres: p.spheres, hull: p.hull, stage_only: !!stage });
    status(`physics saved: bin ${r.bin}${r.xml ? ', xml ' + r.xml : ''}`);
  } catch (e) { status(e.message, true); }
}
function physBodyFromModel() {
  const m = st.phys.mesh, set = (k, v) => { const i = document.querySelector(`[data-body="${k}"]`); if (i) i.value = v.toFixed(6); };
  set('PristineBoundingBoxMinX', m.lo[0]); set('PristineBoundingBoxMaxX', m.hi[0]); set('PristineBoundingBoxMaxY', m.hi[1]);     // the floor (MinY) is below the body in the game's own values (wheels), so it is left alone
  set('PristineBoundingBoxMinZ', -m.hi[2]); set('PristineBoundingBoxMaxZ', -m.lo[2]);
}
async function physBodySave() {
  const vals = {}; for (const i of document.querySelectorAll('[data-body]')) { const v = parseFloat(i.value); if (!Number.isFinite(v)) { status(i.dataset.body + ' is not a number', true); return; } vals[i.dataset.body] = v; }
  try { await post('/api/physics/body', vals); status('body numbers kept for the next new car you build'); } catch (e) { status(e.message, true); }
}

// ---------------------------------------------------------------------------------------------- skeleton and animation (.gr2)
st.skel = null;
let skelObjs = [];
function clearSkel() { for (const o of skelObjs) { overlay.remove(o); o.traverse(x => { x.geometry && x.geometry.dispose(); }); } skelObjs = []; }
const maxToGame = w => [w[0] / 20, w[2] / 20, -w[1] / 20];            // 3ds Max units (20 per metre, z up, y forward) to the car's metres
function drawSkeleton() {
  clearSkel();
  const s = st.skel; if (!s || $('#tab-skeleton').hidden) return;
  s.files.forEach((fl, fi) => {
    if (!fl.bones.length) return;
    const pos = fl.bones.map(b => { const g = maxToGame(b.world); return new THREE.Vector3(g[0], g[1], g[2] * (s.flip ? -1 : 1)); });
    const pts = []; fl.bones.forEach((b, i) => { if (b.parent >= 0) { pts.push(pos[b.parent], pos[i]); } });
    const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xffd500 }));
    overlay.add(lines); skelObjs.push(lines);
    fl.bones.forEach((b, i) => {
      const sel = s.sel && s.sel[0] === fi && s.sel[1] === i;
      const m = new THREE.Mesh(new THREE.SphereGeometry(sel ? 0.04 : 0.025, 10, 7), new THREE.MeshBasicMaterial({ color: sel ? 0xff2d95 : 0xffd500 }));
      m.position.copy(pos[i]); overlay.add(m); skelObjs.push(m);
    });
  });
}
async function renderSkeleton(reload) {
  const box = $('#tab-skeleton');
  if (!st.sum) { box.replaceChildren(el('p', { class: 'muted' }, 'Open a car file first.')); return; }
  if (reload || !st.skel || st.skelKey !== st.sum.name + (st.sum.origin ? st.sum.origin.archive : '')) {
    try { const d = await api('/api/skeleton'); st.skel = Object.assign(d, { flip: st.skel ? st.skel.flip : false, sel: null }); st.skelKey = st.sum.name + (st.sum.origin ? st.sum.origin.archive : ''); }
    catch (e) { box.replaceChildren(el('p', { class: 'warn' }, e.message)); return; }
  }
  const s = st.skel; box.replaceChildren();
  if (!s.files.length) { box.append(el('p', { class: 'muted' }, 'This car has no .gr2 skeleton or animation files. Only cars with moving parts (pop-up headlights, vents, flaps) have them.')); clearSkel(); return; }
  box.append(el('p', { class: 'muted' }, 'Skeleton (bones, yellow) and animation files of the car. A bone\'s pose is edited in place (translation in 3ds Max units, 20 per metre; rotation as extra degrees about the bone\'s own x, y, z) and the matrices of the bone and its children are recomputed; an animation can be made longer or shorter. Saving rewrites the .gr2 in the car\'s archive (DLC) or stages it for the new car you build (game car).'),
    el('div', { class: 'field' }, el('label', { class: 'chk' }, el('input', { type: 'checkbox', checked: s.flip, onchange: e => { s.flip = e.target.checked; drawSkeleton(); } }), ' draw with z flipped'), el('button', { onclick: () => renderSkeleton(true) }, 'Reload from file')));
  s.files.forEach((fl, fi) => {
    box.append(el('h3', {}, fl.name));
    if (fl.bones.length) {
      box.append(el('table', {}, el('tr', {}, ['#', 'bone', 'parent', 'x', 'y', 'z', ''].map(h => el('th', {}, h))),
        fl.bones.map(b => el('tr', { style: s.sel && s.sel[0] === fi && s.sel[1] === b.index ? 'background:rgba(255,45,149,.2)' : '', onclick: () => { s.sel = [fi, b.index]; drawSkeleton(); renderSkeleton(); } },
          el('td', {}, String(b.index)), el('td', {}, b.name), el('td', {}, String(b.parent)), ...b.t.map(v => el('td', {}, v.toFixed(2))), el('td', {}, '')))));
      if (s.sel && s.sel[0] === fi) {
        const b = fl.bones[s.sel[1]], ip = (id, v) => el('input', { id, value: v, style: 'width:60px' });
        box.append(el('h3', {}, `bone ${b.index}: ${b.name}`),
          el('div', { class: 'field' }, el('label', {}, 'position'), ip('bX', b.t[0].toFixed(3)), ip('bY', b.t[1].toFixed(3)), ip('bZ', b.t[2].toFixed(3))),
          el('div', { class: 'field' }, el('label', {}, 'turn by deg'), ip('rX', '0'), ip('rY', '0'), ip('rZ', '0')),
          el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => skelBone(fl.name, b.index) }, 'Apply to bone and save')));
      }
    }
    for (const an of fl.animations) {
      box.append(el('p', {}, `animation ${an.name}: ${an.duration.toFixed(2)} s, ${an.tracks.length} tracks`),
        el('table', {}, el('tr', {}, ['track', 'orientation keys', 'first', 'last'].map(h => el('th', {}, h))),
          an.tracks.map(t => el('tr', {}, el('td', {}, t.name), el('td', {}, String(t.orientation.keys ?? '?')), el('td', { class: 'muted' }, JSON.stringify(t.orientation.first || '')), el('td', { class: 'muted' }, JSON.stringify(t.orientation.last || ''))))),
        el('div', { class: 'field' }, el('label', {}, 'make it'), el('input', { id: 'aF' + fi, value: '1.5', style: 'width:50px' }), el('span', {}, ' times as long '), el('button', { onclick: () => skelSpeed(fl.name, fi) }, 'Apply and save')));
    }
  });
  drawSkeleton();
}
async function skelBone(file, bone) {
  const g = id => parseFloat(document.getElementById(id).value);
  const t = [g('bX'), g('bY'), g('bZ')], rot = [g('rX'), g('rY'), g('rZ')];
  if (![...t, ...rot].every(Number.isFinite)) { status('every field must be a number', true); return; }
  try { const r = await post('/api/skeleton/edit', { op: 'bone', file, bone, t, rotate_deg: rot }); status('bone saved: ' + r.saved); renderSkeleton(true); } catch (e) { status(e.message, true); }
}
async function skelSpeed(file, fi) {
  const f = parseFloat(document.getElementById('aF' + fi).value);
  if (!(f > 0)) { status('the factor must be above 0', true); return; }
  try { const r = await post('/api/skeleton/edit', { op: 'speed', file, factor: f }); status('animation saved: ' + r.saved); renderSkeleton(true); } catch (e) { status(e.message, true); }
}

// ---------------------------------------------------------------------------------------------- top bar, tabs, open
function setTab(name) {
  if (name !== 'physics') clearPhys();
  if (name !== 'skeleton') clearSkel();
  for (const b of document.querySelectorAll('#tabs button')) b.classList.toggle('on', b.dataset.tab === name);
  for (const t of document.querySelectorAll('.tab')) t.hidden = t.id !== 'tab-' + name;
  if (name === 'model' && place.group) activatePlacement();
  if (name === 'part' && st.sel >= 0) partGizmo.select(st.sel);
}
for (const b of document.querySelectorAll('#tabs button')) b.addEventListener('click', () => { setTab(b.dataset.tab); if (b.dataset.tab === 'archive') renderArchive(); if (b.dataset.tab === 'strings') renderStrings(); if (b.dataset.tab === 'physics') renderPhysics(); if (b.dataset.tab === 'skeleton') renderSkeleton(); if (b.dataset.tab === 'parts') workflow.renderClipboard(); });

function resetView() {
  st.viewGeneration++;
  partGizmo.detach();
  clearPlacement();
  st.sel = -1; st.hidden.clear(); st.touched.clear(); st.lodReset = true; st.selLight = -1; st.arch = null; st.atlas.clear(); st.texMats.clear(); st.texVer++; st.free = null; st.overwrite = new Set(); st.texRect = null;
  for (const g of st.groups.values()) { world.remove(g); g.traverse(object => object.geometry?.dispose()); }
  st.groups.clear(); st.wheelLayout = null; clearWheels(); clearCockpit();
  buildLodBar(); buildTree(); renderPart(); renderModel(); renderLights(); renderExhausts(); renderFile();      // renderModel: the Custom models tab said "Open a .carbin first." after a car was opened
  $('#fileName').textContent = `${st.sum.name} · ${(st.sum.size / 1024).toFixed(0)} KB`;
  buildSiblings();
  loadAll().then(frame);
}

function clearCarView() {
  st.viewGeneration++;
  partGizmo.detach(); clearPlacement(); clearWheels(); clearCockpit(); clearPhys(); clearSkel();
  for (const group of st.groups.values()) {world.remove(group); group.traverse(object => object.geometry?.dispose());}
  st.groups.clear(); st.geom.clear(); invalidateTextures();
  st.sum = null; st.sel = -1; st.selLight = -1;
  st.hidden.clear(); st.touched.clear(); st.filter = ''; $('#filter').value = '';
  st.wheelLayout = null; st.phys = null; st.skel = null; st.arch = null; st.archSel = null;
  st.cloneDef = null; st.cloneResult = null; st.free = null; st.texFile = null; st.texRect = null; texAtlasImg = null;
  st.objText = null; st.objInfo = null; st.importTarget = null; st.modelTarget = null;
  st.overwrite.clear(); st.siblingNames = null;
  $('#tree').replaceChildren(); $('#lodBar').replaceChildren();
  $('#siblings').hidden = true; $('#siblings').replaceChildren();
  $('#fileName').textContent = 'no car open';
  grid.position.y = 0; drawSelection(); drawOverlays(); drawExtras();
  renderPart(); renderModel(); renderLights(); renderExhausts(); renderFile(); workflow.renderClipboard();
  updateGizmoUI(); busy.end(); setTab('cars'); wake();
}

async function unloadCar() {
  if (partGizmo.busy) return status('Wait for the current edit to finish.');
  let discard = false;
  if (place.group) {
    if (!confirm('Unload this car and discard the custom model you are positioning?')) return;
    discard = true;
  }
  try {await post('/api/unload', {discard});}
  catch (error) {
    if (!error.requires_discard) return status(error.message, true);
    if (!confirm(error.message)) return;
    try {await post('/api/unload', {discard: true});}
    catch (failure) {return status(failure.message, true);}
  }
  clearCarView(); status('Car unloaded. Your game paths and copied part are kept.');
}

// A car is several model files: the driving model (low detail interior), the close-up / photo model and the cockpit (sharp interior).
// This lets you switch between them without going back to the car list.
async function buildSiblings() {
  const box = $('#siblings'), o = st.sum?.origin; box.hidden = true; box.replaceChildren(); st.siblingNames = null;
  if (!o) return;
  const car = o.source === 'game' ? o.archive.split('/').pop().replace(/\.zip$/i, '') : (o.member.match(/^media\/cars\/([^/]+)\//i) || [])[1];
  if (!car) return;
  const generation = st.viewGeneration;
  try {
    const labels = [[`${car}.carbin`, 'driving model'], [`${car}_lod0.carbin`, 'close-up / photo model'], [`${car}_cockpit.carbin`, 'cockpit (sharp interior)']];
    const members = await workflow.models({ source: o.source, car });
    if (generation !== st.viewGeneration) return;
    const base = m => m.member.split(/[\\/]/).pop().toLowerCase();
    if (generation === st.viewGeneration) { st.siblingNames = new Set(members.map(base)); updateGizmoUI(); }
    const found = labels.map(([name, label]) => ({ label, member: members.find(m => base(m) === name.toLowerCase()) })).filter(x => x.member);
    if (found.length < 2) return;
    const current = o.member.split(/[\\/]/).pop().toLowerCase();
    box.append(...found.map(x => el('option', { value: x.member.member, selected: base(x.member) === current }, 'Model: ' + x.label)));
    box.onchange = () => {
      const pick = found.find(x => x.member.member === box.value);
      if (pick) workflow.importCar({ source: o.source, car }, pick.member).then(() => {});
      box.value = o.member;                                   // the list is rebuilt for the file that opens; if the switch is cancelled this keeps the right entry
    };
    box.hidden = false;
  } catch (e) { /* the switcher is optional */ }
}
function frame() {
  const box = new THREE.Box3().setFromObject(world);
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(c); camera.position.copy(c).add(new THREE.Vector3(-0.7, 0.35, -0.9).multiplyScalar(s));
}

$('#fileIn').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  try { st.sum = await api('/api/open', { method: 'POST', body: await f.arrayBuffer(), headers: { 'X-Name': f.name } }); resetView(); }
  catch (err) { status('could not open: ' + err.message, true); }
  e.target.value = '';
});
addEventListener('dragover', e => e.preventDefault());
addEventListener('drop', async e => {
  e.preventDefault(); const f = e.dataTransfer.files[0]; if (!f) return;
  if (/\.(fbx|glb|gltf|obj|modelbin)$/i.test(f.name)) { if (!st.sum) { status('open a .carbin first, then drop the model', true); return; } await loadModelFile(f); return; }
  try { st.sum = await api('/api/open', { method: 'POST', body: await f.arrayBuffer(), headers: { 'X-Name': f.name } }); resetView(); } catch (err) { status('could not open: ' + err.message, true); }
});
$('#btnUndo').addEventListener('click', async () => { if (partTools.undoPlacement()) return; st.sum = await post('/api/undo', {}); afterChange('all'); });
$('#btnRedo').addEventListener('click', async () => { if (partTools.redoPlacement()) return; st.sum = await post('/api/redo', {}); afterChange('all'); });
addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.ctrlKey && e.key.toLowerCase() === 'z') { e.preventDefault(); $('#btnUndo').click(); }
  if (e.ctrlKey && (e.key.toLowerCase() === 'y')) { e.preventDefault(); $('#btnRedo').click(); }
});
$('#btnSave').addEventListener('click', async () => {
  try { await api('/api/save'); location.href = '/api/save'; }
  catch (e) { if (e.problems && confirm('Validation problems:\n' + e.problems.join('\n') + '\n\nDownload anyway?')) location.href = '/api/save?force=1'; else if (!e.problems) status(e.message, true); }
});
$('#btnExport').addEventListener('click', () => exportObj(null));
$('#filter').addEventListener('input', e => { st.filter = e.target.value; buildTree(); applyVisibility(); });
for (const id of ['optCockpit', 'optWheels', 'optWire', 'optBox', 'optLights', 'optEx', 'optHidden', 'optTex', 'optNormals', 'optBoxes']) $('#' + id).addEventListener('change', applyVisibility);

// ---------------------------------------------------------------------------------------------- textures in the viewer, overlays, hide / isolate
function invalidateTextures() {
  st.texVer++;
  for (const texture of st.atlas.values()) if (texture) texture.dispose();
  for (const material of st.texMats.values()) material.dispose();
  st.atlas.clear(); st.texMats.clear();
}
function atlasFor(profile) {
  const mode = $('#optTex').value;
  if (mode === 'off') return null;
  if (mode === 'auto') return profile.atlas;
  if (mode === 'damage') return { nodamage: 'damage', lights: 'damagelights' }[profile.atlas] || profile.atlas;
  return profile.atlas === mode ? mode : null;     // "inspect ... atlas": only the parts that read that atlas show it (see meshMaterial)
}
const INSPECT_MODES = ['nodamage', 'lights', 'interior'];
function atlasTexture(name) {
  if (st.atlas.has(name)) return st.atlas.get(name) || null;
  const revision = st.texVer;
  st.atlas.set(name, null);
  new THREE.TextureLoader().load('/api/atlas?name=' + encodeURIComponent(name) + '&v=' + revision, tex => {
    if (revision !== st.texVer) { tex.dispose(); return; }
    tex.flipY = false; tex.colorSpace = THREE.SRGBColorSpace; tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.anisotropy = 4;
    st.atlas.set(name, tex);
    for (const material of st.texMats.values()) material.dispose();
    st.texMats.clear(); applyVisibility(); wake();
  }, undefined, () => { if (revision === st.texVer) { st.atlas.set(name, false); if (name !== 'interior_emissive') status('Preview texture unavailable: ' + name, true); } });   // 6 stock cars have no emissive atlas: not worth an error
  return null;
}
function meshMaterial(m, sec, cockpit = false) {
  const mode = $('#optTex').value;
  if (!st.sum.origin || mode === 'off') return material(m.userData.mat);
  const profile = previewMaterial(m.userData.mat, sec.pool, cockpit || /_cockpit\.carbin$/i.test(st.sum.origin?.member || '') ? 'cockpit' : 'body');
  if (INSPECT_MODES.includes(mode) && profile.atlas !== mode) {          // inspecting one atlas: the parts that do not read it stay plain grey
    if (!st.texMats.has('plain')) st.texMats.set('plain', new THREE.MeshStandardMaterial({ color: 0x555b61, roughness: 0.9, metalness: 0, side: THREE.DoubleSide }));
    return st.texMats.get('plain');
  }
  const atlas = atlasFor(profile);
  const tex = atlas ? atlasTexture(atlas) : null;
  if (atlas && !tex) return material(m.userData.mat);
  const glow = profile.emissiveAtlas && mode === 'auto' ? atlasTexture(profile.emissiveAtlas) : null;        // lit dial faces; false/null while it loads
  const key = (atlas || 'solid') + '|' + m.userData.mat + (glow ? '|lit' : '');
  if (!st.texMats.has(key)) {
    const { atlas: unused, emissiveAtlas: unusedGlow, ...options } = profile;
    const extra = glow ? { emissive: 0xffffff, emissiveMap: glow, emissiveIntensity: 1.3 } : {};
    st.texMats.set(key, new THREE.MeshStandardMaterial({ ...options, ...extra, map: tex, side: THREE.DoubleSide }));
  }
  return st.texMats.get(key);
}

let extraObjs = [];
function drawExtras() {
  for (const o of extraObjs) { overlay.remove(o); o.geometry && o.geometry.dispose(); }
  extraObjs = [];
  if (!st.sum) return;
  if ($('#optBoxes').checked) {
    for (const g of st.groups.values()) {
      if (!g.visible) continue;
      const b = new THREE.Box3().setFromObject(g); if (b.isEmpty()) continue;
      const h = new THREE.Box3Helper(b, 0x6d7b8d); overlay.add(h); extraObjs.push(h);
    }
  }
  if ($('#optNormals').checked && st.sel >= 0) {
    const g = st.groups.get(st.sel); if (!g) return;
    const pts = [];
    for (const m of g.children) {
      if (!m.visible) continue;
      const p = m.geometry.attributes.position, n = m.geometry.attributes.normal, idx = m.geometry.index, seen = new Set();
      for (let i = 0; i < idx.count; i++) {
        const v = idx.getX(i); if (seen.has(v)) continue; seen.add(v);
        pts.push(p.getX(v), p.getY(v), p.getZ(v), p.getX(v) + n.getX(v) * 0.03, p.getY(v) + n.getY(v) * 0.03, p.getZ(v) + n.getZ(v) * 0.03);
      }
    }
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    const ln = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x00e5ff })); overlay.add(ln); extraObjs.push(ln);
  }
}
const hideSel = () => { if (st.sel >= 0) { st.hidden.add(st.sel); buildTree(); applyVisibility(); } };
const isoSel = () => { if (st.sel >= 0) { st.hidden = new Set(st.sum.sections.map(s => s.index).filter(i => i !== st.sel)); buildTree(); applyVisibility(); } };
const showAll = () => { st.hidden.clear(); buildTree(); applyVisibility(); };
$('#btnHide').addEventListener('click', hideSel); $('#btnIso').addEventListener('click', isoSel); $('#btnShowAll').addEventListener('click', showAll);
addEventListener('keydown', e => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || !e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === 'h' && e.shiftKey) { e.preventDefault(); showAll(); } else if (k === 'h') { e.preventDefault(); hideSel(); } else if (k === 'i') { e.preventDefault(); isoSel(); }
});

// ---------------------------------------------------------------------------------------------- archive tab: textures, text files, any file
function memberUrl(name, extra = '') { return '/api/member?name=' + encodeURIComponent(name) + extra; }
async function imageToRGBA(file, w, h) {
  const bmp = await createImageBitmap(file); const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(bmp, 0, 0, w, h);
  return b64(new Uint8Array(x.getImageData(0, 0, w, h).data.buffer));
}
async function afterArchiveWrite(r) {
  status(`wrote ${r.saved.member.split('/').pop()} into ${r.saved.zip.split(/[\\/]/).pop()}; backup ${r.saved.backup || 'already existed'}`);
  st.arch = null; invalidateTextures(); applyVisibility();
}
function archiveWritable() { const o = st.sum && st.sum.origin; const s = o && st.lib && st.lib.sources.find(x => x.id === o.source); return s && s.writable ? '' : (s ? s.why || 'read only' : 'unknown location'); }

function fillArchList() {
  const box = $('#archList'); if (!box || !st.arch) return;
  const q = (st.archQ || '').toLowerCase(), kind = st.archKind || '';
  box.replaceChildren(...st.arch.filter(m => (!q || m.name.toLowerCase().includes(q)) && (!kind || m.kind === kind)).map(m =>
    el('div', { class: 'mrow' + (m.name === st.archSel ? ' sel' : ''), onclick: () => showMember(m.name) }, el('span', {}, m.name), el('span', { class: 'tag' }, `${m.kind} · ${(m.size / 1024).toFixed(0)} KB`))));
}

async function renderArchive() {
  const box = $('#tab-archive'); box.replaceChildren();
  if (!st.sum || !st.sum.origin) { box.append(el('p', { class: 'muted' }, 'Open a car from the Game or DLC list (Open / Save tab) to browse its archive: textures, CarAttribs.xml, ShaderSettings.xml, gauges, livery masks.')); return; }
  if (!st.arch) { try { st.arch = (await api('/api/archive')).members; } catch (e) { status(e.message, true); return; } }
  box.append(el('h3', {}, 'Files in this car\'s archive'),
    el('div', { class: 'field' }, el('input', { placeholder: 'filter', value: st.archQ || '', oninput: e => { st.archQ = e.target.value; fillArchList(); } }),
      el('select', { onchange: e => { st.archKind = e.target.value; fillArchList(); } }, ['', 'texture', 'text', 'model', 'image', 'other'].map(k => el('option', { value: k, selected: k === (st.archKind || '') }, k || 'all')))),
    el('div', { class: 'mlist', id: 'archList' }), el('div', { id: 'archDetail' }));
  fillArchList();
  if (st.archSel) showMember(st.archSel);
}

async function showMember(name) {
  st.archSel = name; fillArchList();
  const d = $('#archDetail'); if (!d) return;
  const m = st.arch.find(x => x.name === name); if (!m) return;
  const why = archiveWritable(), url = memberUrl(name);
  const rep = (kind, extra) => async () => { try { afterArchiveWrite(await post('/api/member/replace', { name, kind, ...(await extra()) })); showMember(name); } catch (e) { status(e.message, true); } };
  const note = why ? el('p', { class: 'warn' }, 'Replacing is disabled: ' + why) : el('p', { class: 'muted' }, 'Replacing writes into the archive at once (a .bak copy is made once). Close Xenia first.');
  d.replaceChildren(el('h3', {}, name), el('p', { class: 'muted' }, `${m.size} bytes`));
  if (m.kind === 'texture') {
    let inf; try { inf = await api(url + '&info=1'); } catch (e) { d.append(el('p', { class: 'warn' }, e.message)); return; }
    const pick = el('input', { type: 'file', accept: 'image/*', hidden: true, onchange: async e => { const f = e.target.files[0]; if (f) await rep('texture', async () => ({ w: inf.w, h: inf.h, rgba: await imageToRGBA(f, inf.w, inf.h) }))(); } });
    d.append(el('p', { class: 'muted' }, `${inf.w} x ${inf.h}, format ${inf.fmt}, ${inf.mip_levels.length} mip level(s)` + (inf.mip_levels.length ? ' (all regenerated on replace except a tiny tail level the game packs differently)' : ' (mip layout not understood: only the top level would be replaced)')),
      el('img', { class: 'prev', src: url + '&png=1' }),
      el('div', { class: 'field' }, el('a', { class: 'btn', href: url + '&png=1', download: name.split('/').pop().replace(/\.xds$/i, '.png') }, 'Download PNG'),
        el('button', { disabled: !!why || ![6, 50, 18, 51, 20, 53].includes(inf.fmt), onclick: () => pick.click() }, 'Replace from an image...'), pick),
      el('p', { class: 'muted' }, 'The image is resized to the texture size. Paint over the downloaded PNG in any editor, keep its alpha (a lights texture\'s alpha is the glow mask and the glass opacity), then replace. DXT1 and DXT5 and 8888 textures can be replaced.'), note);
  } else if (m.kind === 'text') {
    const t = await api(url + '&text=1');
    const ta = el('textarea', { spellcheck: false }); ta.value = t.text;
    d.append(ta, el('div', { class: 'field' }, el('button', { class: 'primary', disabled: !!why, onclick: rep('text', async () => ({ text: ta.value })) }, 'Save text into the archive'), el('a', { class: 'btn', href: url, download: name.split('/').pop() }, 'Download')), note);
  } else {
    const pick = el('input', { type: 'file', hidden: true, onchange: async e => { const f = e.target.files[0]; if (f) await rep('raw', async () => ({ data: b64(new Uint8Array(await f.arrayBuffer())) }))(); } });
    d.append(el('div', { class: 'field' }, el('a', { class: 'btn', href: url, download: name.split('/').pop() }, 'Download'), el('button', { disabled: !!why, onclick: () => pick.click() }, 'Replace with a file...'), pick), note);
  }
}

// ---------------------------------------------------------------------------------------------- strings tab (read only)
async function renderStrings() {
  const box = $('#tab-strings'); box.replaceChildren();
  if (!st.lib || !st.lib.config.game) { box.append(el('p', { class: 'muted' }, 'Set the game folder in Settings to browse the game\'s string tables (car names, makes, upgrades...).')); return; }
  st.strLang = st.strLang || 'en';
  let tables = [];
  try { tables = (await api('/api/strings?lang=' + st.strLang)).tables; } catch (e) { box.append(el('p', { class: 'warn' }, e.message)); }
  st.strTable = st.strTable || (tables.includes('Data_Car') ? 'Data_Car' : tables[0]);
  box.append(el('h3', {}, 'String tables (read only)'),
    el('div', { class: 'field' }, el('select', { onchange: e => { st.strLang = e.target.value; st.strTable = null; renderStrings(); } }, ['en', 'fr', 'de', 'es', 'it', 'jp', 'ko', 'mx', 'pl', 'ru', 'br', 'cz', 'nl', 'nb', 'sv', 'da', 'fi', 'hu', 'cht'].map(l => el('option', { value: l, selected: l === st.strLang }, l))),
      el('select', { onchange: e => { st.strTable = e.target.value; fillStrings(); } }, tables.map(n => el('option', { value: n, selected: n === st.strTable }, n)))),
    el('div', { class: 'field' }, el('input', { id: 'strQ', placeholder: 'search key, name or text', value: st.strQ || '', oninput: e => { st.strQ = e.target.value; fillStrings(); } })),
    el('div', { id: 'strRows' }),
    el('p', { class: 'muted' }, 'Names in the game use "_&N" references into these tables. A DLC that changes a table must ship a superset of it.'));
  fillStrings();
}
async function fillStrings() {
  const box = $('#strRows'); if (!box || !st.strTable) return;
  try {
    const r = await api(`/api/strings?lang=${st.strLang}&table=${encodeURIComponent(st.strTable)}&q=${encodeURIComponent(st.strQ || '')}`);
    box.replaceChildren(el('p', { class: 'muted' }, `${r.total} match(es)${r.total > r.rows.length ? ', first ' + r.rows.length : ''}`),
      el('table', {}, el('tr', {}, ['key', 'name', 'text'].map(h => el('th', {}, h))), r.rows.map(x => el('tr', {}, el('td', {}, String(x.key)), el('td', {}, x.name), el('td', {}, x.text)))));
  } catch (e) { box.replaceChildren(el('p', { class: 'warn' }, e.message)); }
}

const partTools = createPartTools({ $, el, st, place, run, status, setTab, selectSection, syncPlaceFields });
const partGizmo = createPartGizmo({THREE, control: tc, parent: overlay,
  getTarget(index) {
    const section = st.sum?.sections[index], group = st.groups.get(index);
    if (!section || !group?.visible || !section.verts || !group.children.some(m => m.visible)) return null;
    return {index, group, pivot: section.offset, name: section.name};
  },
  commit: args => run('transform', {...args, both_models: $('#partBoth').checked && canPairModels()}, args.sections),
  previewChanged() {drawSelection(); wake();}, stateChanged: updateGizmoUI});
for (const button of document.querySelectorAll('#transformTools [data-gizmo-mode]')) button.addEventListener('click', () => setGizmoMode(button.dataset.gizmoMode));
const workflow = createWorkflow({ $, el, st, api, post, status, setTab, resetView, afterChange, selectSection, busy, diag });
$('#btnCars').addEventListener('click', () => setTab('cars'));
$('#btnUnload').addEventListener('click', unloadCar);
$('#btnSettings').addEventListener('click', () => { workflow.renderSettings(); setTab('settings'); });
$('#btnCustomModel').addEventListener('click', () => {
  if (!st.sum) { setTab('cars'); return status('Import a car from Cars, then add your custom model.', true); }
  setTab('model'); renderModel(); if (!place.group) $('#modelIn').click();
});
$('#btnApplyCar').addEventListener('click', applyExistingCar);
$('#btnCarZip').addEventListener('click', exportCarZip);
$('#btnDlc').addEventListener('click', () => { renderFile(); setTab('file'); if (!st.sum) status('Import a car before exporting DLC.', true); });

async function init() {
  renderPart(); renderModel();
  const s = await api('/api/status');
  await refreshLibrary();
  if (s.open) { st.sum = await api('/api/summary'); resetView(); }
  workflow.renderClipboard();
  if (st.lib.needs_setup) { setTab('settings'); status(st.lib.setup_error || 'Set your FH1 game path in Settings to start.', !!st.lib.setup_error); }
  else if (!s.open) setTab('cars');
}
init().catch(e => status(e.message, true));
// an optional logo: if toolkit/carbin_editor/logo.png exists it is shown in front of the name in the header
fetch('/logo.png').then(r => { if (!r.ok) return; const img = el('img', { src: '/logo.png', alt: '', width: 22, height: 22, style: 'vertical-align:-5px;margin-right:6px;border-radius:5px' }); $('#brand')?.before(img); }).catch(() => {});
if (window.__editor) window.__editor.reopen = async () => { st.sum = await api('/api/summary'); resetView(); };   // testing aid, see #debug above
