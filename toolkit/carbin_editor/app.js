import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { createWorkflow } from './editor_workflow.js';
import { previewMaterial } from './viewer_materials.mjs';

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
  sum: null, sel: -1, hidden: new Set(), lods: new Set([0, 1, 2, 3, 4, 5]), groups: new Map(), geom: new Map(),
  matCache: new Map(), atlas: new Map(), texMats: new Map(), texVer: 0, arch: null, filter: '', selLight: -1, objText: null, objInfo: null, objName: '',
};
const status = (msg, err = false) => { const s = $('#status'); s.textContent = msg; s.className = err ? 'err' : ''; };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const type = r.headers.get('Content-Type') || '';
  if (!r.ok) {
    let j = {}; try { j = await r.json(); } catch (e) { /* not json */ }
    const e = new Error(j.error || r.statusText); e.problems = j.problems; throw e;
  }
  return type.includes('json') ? r.json() : r;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

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
tc.addEventListener('dragging-changed', e => { controls.enabled = !e.value; });
tc.addEventListener('objectChange', () => syncPlaceFields());
scene.add(tc);
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(-3, 6, -4); scene.add(sun);
const sun2 = new THREE.DirectionalLight(0xffffff, 0.5); sun2.position.set(4, 2, 5); scene.add(sun2);
const grid = new THREE.GridHelper(12, 24, dark ? 0x394149 : 0xaab2ba, dark ? 0x23292f : 0xcfd5db); scene.add(grid);
scene.add(new THREE.AxesHelper(0.6));
const front = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 0.02, -2.6), 1.0, 0xffcc00, 0.25, 0.15); scene.add(front);
const world = new THREE.Group(); scene.add(world);
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
async function loadSection(si, generation = st.viewGeneration) {
  const buf = await (await api('/api/geometry?sec=' + si)).arrayBuffer();
  if (generation !== st.viewGeneration || !st.sum?.sections[si]) return;
  const dv = new DataView(buf); const hl = dv.getUint32(0, true);
  const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
  const base = 4 + hl, n = head.verts;
  const pos = new Float32Array(buf.slice(base, base + n * 12));
  const nrm = new Float32Array(buf.slice(base + n * 12, base + n * 24));
  const old = st.groups.get(si);
  if (old) { world.remove(old); old.traverse(o => o.geometry && o.geometry.dispose()); }
  const g = new THREE.Group(); g.userData.si = si;
  for (const s of head.subs) {
    if (!s.tris) continue;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('normal', new THREE.BufferAttribute(nrm, 3));
    if (s.uvOffset !== undefined) geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(buf.slice(base + s.uvOffset, base + s.uvOffset + n * 8)), 2));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(buf.slice(base + s.offset, base + s.offset + s.tris * 12)), 1));
    const m = new THREE.Mesh(geo, material(s.material));
    m.userData = { si, k: s.k, lod: s.lod, mat: s.material };
    g.add(m);
  }
  st.groups.set(si, g); world.add(g);
  applyVisibility();
}

async function loadAll(indices) {
  const generation = st.viewGeneration;
  const list = indices ?? st.sum.sections.map(s => s.index);
  status(`loading ${list.length} parts...`);
  let i = 0;
  const worker = async () => { while (i < list.length && generation === st.viewGeneration) { const si = list[i++]; try { await loadSection(si, generation); } catch (e) { if (generation === st.viewGeneration) status(e.message, true); } } };
  await Promise.all([worker(), worker(), worker(), worker()]);
  if (generation === st.viewGeneration) status(`${st.sum.name}: ${st.sum.sections.length} parts`);
}

function applyVisibility() {
  const showUndrawn = $('#optHidden').checked;
  for (const [si, g] of st.groups) {
    const sec = st.sum.sections[si];
    const sectionOn = !st.hidden.has(si) && matchesFilter(sec);
    g.visible = sectionOn;
    for (const m of g.children) {
      const sub = sec.subs[m.userData.k];
      const drawn = !st.sum.body_header || (sub && sub.drawn_in.length > 0);
      m.visible = st.lods.has(m.userData.lod) && (drawn || showUndrawn);
      m.material = meshMaterial(m, sec);
      m.material.wireframe = $('#optWire').checked;
    }
  }
  drawSelection(); drawOverlays(); drawExtras();
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
canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
  const r = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, camera);
  const meshes = []; world.traverse(o => { if (o.isMesh && o.visible && o.parent.visible) meshes.push(o); });
  const hit = ray.intersectObjects(meshes, false)[0];
  if (hit) selectSection(hit.object.userData.si);
});

// ---------------------------------------------------------------------------------------------- UI: tree, lods
function buildLodBar() {
  const bar = $('#lodBar'); bar.replaceChildren(el('span', { class: 'muted' }, 'LOD'));
  const present = new Set(); for (const s of st.sum.sections) for (const b of s.subs) present.add(b.lod);
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
    const row = el('div', { class: 'row', onclick: () => selectSection(s.index) }, eye,
      el('span', { class: 'name' + (notDrawn ? ' warn' : ''), title: notDrawn ? 'not in any draw list: the game does not draw this part' : '' }, s.name),
      el('span', { class: 'tag' }, `${s.verts}v · ${s.subs.length}`));
    const subs = el('div', { class: 'subs' }, s.subs.map(b => el('div', {}, el('span', {}, b.material + (b.swatch ? ' •' : '')), el('span', {}, `L${b.lod} · ${b.tris}t${st.sum.body_header && !b.drawn_in.length ? ' · hidden' : ''}`))));
    t.append(el('div', { class: 'sec' + (s.index === st.sel ? ' sel' : ''), 'data-si': s.index }, row, st.sel === s.index ? subs : ''));
  }
}

function selectSection(si) { st.sel = si; buildTree(); drawSelection(); drawExtras(); renderPart(); setTab('part'); }

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
    el('div', { class: 'field' }, el('label', {}, 'name'), el('input', { id: 'pName', value: s.name }), el('button', { onclick: () => run('rename_section', { section: s.index, name: $('#pName').value }, []) }, 'Rename')),
    el('p', { class: 'muted' }, `pivot (${s.offset.map(v => v.toFixed(3)).join(', ')}) · half extents (${s.bmax.map(v => v.toFixed(3)).join(', ')}) · ${s.verts} vertices · pool ${s.pool}. The game finds some parts by name (for example hooda, exhaustLa): rename with care.`),
    el('h3', {}, 'Move, rotate, scale'),
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
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => { st.modelTarget = s.index; $('#modelIn').click(); } }, 'Add a model to this part (FBX, GLB, OBJ)...')),
    el('div', { id: 'objDialog' }));
  renderObjDialog();
}

async function doTransform(mirror) {
  const secs = $('#pScope').value === 'visible' ? st.sum.sections.filter(s => !st.hidden.has(s.index) && matchesFilter(s)).map(s => s.index) : [st.sel];
  await run('transform', {
    sections: secs, translate: ['x', 'y', 'z'].map(a => val('tr' + a)), rotate: ['x', 'y', 'z'].map(a => val('ro' + a)),
    scale: ['x', 'y', 'z'].map(a => { const v = parseFloat($('#sc' + a).value); return isNaN(v) ? 1 : v; }),
    pivot: $('#pOrigin').checked ? 'origin' : 'section', move_pivot: $('#pMovePivot').checked, mirror,
  }, secs);
}

async function run(op, args, reload) {
  try {
    st.sum = await post('/api/op', { op, ...args });
    if (args.texture) invalidateTextures();
    afterChange(reload);
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
  if (reload === 'all') { loadAll(); } else if (reload && reload.length) { Promise.all(reload.map(index => loadSection(index))).then(() => status('updated')); }
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
const place = { group: null, name: '', ext: '', mats: [], tris: 0 };
const ghostMat = new THREE.MeshStandardMaterial({ color: 0xffa726, transparent: true, opacity: 0.85, side: THREE.DoubleSide, roughness: 0.5 });

async function loadModelFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  let root;
  try {
    if (ext === 'fbx') root = new FBXLoader().parse(await file.arrayBuffer(), '');
    else if (ext === 'glb') root = (await new GLTFLoader().parseAsync(await file.arrayBuffer(), '')).scene;
    else if (ext === 'gltf') root = (await new GLTFLoader().parseAsync(await file.text(), '')).scene;
    else if (ext === 'obj') root = new OBJLoader().parse(await file.text());
    else throw new Error('unsupported file type .' + ext + ' (use FBX, GLB, glTF or OBJ)');
  } catch (e) { status('could not read the model: ' + e.message, true); return; }
  startPlacement(root, file.name, ext);
}

function clearPlacement() {
  tc.detach();
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
  overlay.add(group); tc.attach(group);
  Object.assign(place, { group, name, ext, tris, mats: [...counts.entries()].map(([n, t]) => ({ name: n, tris: t })) });
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

function renderModel() {
  const box = $('#tab-model'); box.replaceChildren();
  if (!st.sum) { box.append(el('p', { class: 'muted' }, 'Open a .carbin first.')); return; }
  if (!place.group) {
    box.append(el('h3', {}, 'Add a model to the car'),
      el('p', {}, 'Drop an FBX, GLB / glTF or OBJ file anywhere on this page (or choose one). It appears in orange; drag it into place with the gizmo, then add it to a part. Your model is never sent anywhere except this program on your computer.'),
      el('div', { class: 'field' }, el('button', { class: 'primary', onclick: () => $('#modelIn').click() }, 'Choose a model file...')),
      el('p', { class: 'muted' }, 'Tips: the car is about 4.5 m long, front toward -z (yellow arrow). Most FBX files are in centimetres (the editor guesses and scales by 0.01). Give each material a name from the car (body, chrome, black, glass...): see the table that appears.'));
    return;
  }
  const lodsPresent = [...new Set(st.sum.sections.flatMap(s => s.subs.map(b => b.lod)))].sort();
  const dl = $('#materials'); dl.replaceChildren(...[...new Set(st.sum.sections.flatMap(x => x.subs.map(b => b.material)))].sort().map(m => el('option', { value: m })));
  const f3 = (pre, vals, step) => el('div', { class: 'grid3' }, ['x', 'y', 'z'].map((a, i) => el('input', { type: 'number', id: pre + a, value: vals[i], step, onchange: readPlaceFields })));
  box.append(
    el('h3', {}, `${place.name}`), el('p', { class: 'muted' }, `${place.tris} triangles, ${place.mats.length} material(s)`),
    el('div', { class: 'field' }, ['translate', 'rotate', 'scale'].map((m, i) => el('button', { title: ['W', 'E', 'R'][i], onclick: () => tc.setMode(m) }, m))),
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
    el('div', { class: 'field' }, el('label', {}, 'part'), el('select', { id: 'mTarget' }, st.sum.sections.map(s => el('option', { value: s.index, selected: s.index === (st.modelTarget ?? st.sel) }, s.name)))),
    el('div', { class: 'field' }, el('label', {}, 'how'), el('select', { id: 'mMode' }, el('option', { value: 'add' }, 'add onto the part (keep what it has)'), el('option', { value: 'replace' }, 'replace the part\'s geometry'))),
    el('div', { class: 'field' }, el('label', {}, 'LODs'), el('div', {}, lodsPresent.map(l => el('label', { class: 'chk' }, el('input', { type: 'checkbox', 'data-lod': l, checked: true }), ' ' + l + '  ')))),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'mAuto', checked: true }), ' automatic LODs: the lowest ticked LOD gets the full model, higher LODs get simplified copies sized like this part\'s own LODs')),
    el('div', { class: 'field' }, el('label', {}, ''), el('label', { class: 'chk' }, el('input', { type: 'checkbox', id: 'mFlip', checked: place.ext !== 'gltf' && place.ext !== 'glb' }), ' flip V (FBX / OBJ have a bottom-left origin)')),
    el('div', { class: 'field' }, el('label', {}, 'uv0 (atlas)'), el('select', { id: 'mUv' }, el('option', { value: 'swatch' }, 'flat colour for materials the car draws flat'), el('option', { value: 'obj' }, 'always the model\'s uv'))),
    el('h3', {}, 'Materials'),
    el('table', {}, el('tr', {}, ['in your model', 'tris', 'FH1 material'].map(h => el('th', {}, h))),
      place.mats.map(m => el('tr', {}, el('td', {}, m.name), el('td', {}, String(m.tris)), el('td', {}, el('input', { 'data-mat': m.name, value: guessMaterial(m.name), list: 'materials' }))))),
    el('p', { class: 'muted' }, 'A material name must exist in the car\'s ShaderSettings.xml, and its look comes from the car\'s atlas textures. An added model has no texture of its own: it uses the flat colour of the material you pick.'),
    el('h3', {}, 'Texture (optional)'),
    el('p', { class: 'muted' }, 'Give the model its own picture. Every exterior material of the car reads one body atlas (the game\'s shaders), and that atlas is about 97% full, so a picture needs the texture area of some parts: tick the parts you do not need (the list shows how much area only that part reads), press Auto place, and the picture is painted into the largest free rectangle (all mip levels, both atlas sizes). The ticked parts will then look wrong. Red on the atlas = still read by a part you keep. You can also drag a rectangle yourself. For a car in a DLC the atlas files are rewritten in place (a backup is made); for a base-game car they go into the new car you save.'),
    el('div', { class: 'field' }, el('input', { type: 'file', accept: 'image/*', id: 'mTexFile', onchange: e => { st.texFile = e.target.files[0] || null; drawTexCanvas(); } }), el('button', { onclick: () => { st.texFile = null; st.texRect = null; document.getElementById('mTexFile').value = ''; drawTexCanvas(); } }, 'No texture')),
    el('div', { class: 'field' }, el('button', { onclick: autoPlace }, 'Auto place'), el('span', { class: 'muted', id: 'mFreeInfo' }, '')),
    el('div', { id: 'mParts', style: 'max-height:140px;overflow:auto;border:1px solid var(--line);padding:4px;margin:4px 0' }),
    el('canvas', { id: 'mTexCanvas', width: 300, height: 300, style: 'max-width:100%;cursor:crosshair;border:1px solid var(--line)' }),
    el('p', { class: 'muted', id: 'mTexInfo' }, 'no texture chosen'),
    el('div', { class: 'field' }, el('button', { class: 'primary', onclick: commitModel }, 'Add to car'), el('button', { onclick: () => { clearPlacement(); renderModel(); status('cancelled'); } }, 'Cancel')));
  syncPlaceFields(); setupTexCanvas();
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
  const committed = await run('add_model', { section: target, mode: $('#mMode').value, lods, flip_v: $('#mFlip').checked, auto_lods: $('#mAuto').checked, texture, uv0_mode: $('#mUv').value, meshes }, [target]);
  if (!committed) return;
  clearPlacement(); st.modelTarget = target; selectSection(target); setTab('model'); renderModel();
}

$('#modelIn').addEventListener('change', async e => { const f = e.target.files[0]; e.target.value = ''; if (f) await loadModelFile(f); });
addEventListener('keydown', e => {
  if (!place.group || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (e.key === 'w') tc.setMode('translate'); if (e.key === 'e') tc.setMode('rotate'); if (e.key === 'r') tc.setMode('scale');
});

// ---------------------------------------------------------------------------------------------- open (game + DLC) and save
async function refreshLibrary() {
  st.lib = await api('/api/library');
  workflow.renderSettings();
  renderFile();
  await workflow.loadCars();
}

async function saveBack(force = false) {
  try {
    const r = await post('/api/library/save', { force });
    status(`saved into ${r.saved.zip.split(/[\\/]/).pop()} (${r.saved.bytes} bytes); backup ${r.saved.backup || 'already existed'}`);
  } catch (e) {
    if (e.problems) { if (confirm('Validation problems:\n' + e.problems.join('\n') + '\n\nSave anyway?')) saveBack(true); }
    else status(e.message, true);
  }
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
    const r = await post('/api/clone', { new_media: v('ncName'), offer: parseInt(v('ncOffer')), out_dir: v('ncOut'), include_open: document.getElementById('ncOpen').checked, display_name: v('ncTitle'), short_name: v('ncShort'),
      formats: ['xenia', 'stfs'].filter(f => document.getElementById('nc_' + f).checked) });
    st.cloneResult = r; st.cloneDef = null; loadCloneDefaults(); status(`new car built: id ${r.car_id}, ${r.rows} database rows, ${r.files} files`);
  } catch (e) { st.cloneResult = { error: e.message, problems: e.problems || [] }; renderFile(); status(e.message, true); }
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
    el('div', { class: 'field' }, el('label', {}, 'output'), el('input', { id: 'ncOut', value: d.out_dir })),
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
      o ? el('div', { class: 'field' }, el('button', { class: 'primary', disabled: !(src && src.writable), onclick: () => saveBack(false) }, o.source === 'game' ? 'Save into game' : 'Save into DLC'),
        src && !src.writable ? el('span', { class: 'muted' }, src.why) : '') : '',
      o && o.source === 'game' ? el('p', { class: 'muted' }, 'This replaces the car\'s file inside the extracted game (a backup <zip>.bak is made once). A DLC cannot replace a base-game car: the game uses its own files first, so edits to a game car only take effect when saved into the game itself.') : '',
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
}
for (const b of document.querySelectorAll('#tabs button')) b.addEventListener('click', () => { setTab(b.dataset.tab); if (b.dataset.tab === 'archive') renderArchive(); if (b.dataset.tab === 'strings') renderStrings(); if (b.dataset.tab === 'physics') renderPhysics(); if (b.dataset.tab === 'skeleton') renderSkeleton(); if (b.dataset.tab === 'parts') workflow.renderClipboard(); });

function resetView() {
  st.viewGeneration++;
  clearPlacement();
  st.sel = -1; st.hidden.clear(); st.lodReset = true; st.selLight = -1; st.arch = null; st.atlas.clear(); st.texMats.clear(); st.texVer++; st.free = null; st.overwrite = new Set(); st.texRect = null;
  for (const g of st.groups.values()) { world.remove(g); g.traverse(object => object.geometry?.dispose()); }
  st.groups.clear();
  buildLodBar(); buildTree(); renderPart(); renderLights(); renderExhausts(); renderFile();
  $('#fileName').textContent = `${st.sum.name} · ${(st.sum.size / 1024).toFixed(0)} KB`;
  loadAll().then(frame);
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
  if (/\.(fbx|glb|gltf|obj)$/i.test(f.name)) { if (!st.sum) { status('open a .carbin first, then drop the model', true); return; } await loadModelFile(f); return; }
  try { st.sum = await api('/api/open', { method: 'POST', body: await f.arrayBuffer(), headers: { 'X-Name': f.name } }); resetView(); } catch (err) { status('could not open: ' + err.message, true); }
});
$('#btnUndo').addEventListener('click', async () => { st.sum = await post('/api/undo', {}); afterChange('all'); });
$('#btnRedo').addEventListener('click', async () => { st.sum = await post('/api/redo', {}); afterChange('all'); });
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
for (const id of ['optWire', 'optBox', 'optLights', 'optEx', 'optHidden', 'optTex', 'optNormals', 'optBoxes']) $('#' + id).addEventListener('change', applyVisibility);

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
  if (mode === 'damage') return profile.atlas === 'nodamage' ? 'damage' : profile.atlas;
  return mode; // Explicitly labelled raw-atlas inspection modes.
}
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
  }, undefined, () => { if (revision === st.texVer) { st.atlas.set(name, false); status('Preview texture unavailable: ' + name, true); } });
  return null;
}
function meshMaterial(m, sec) {
  if (!st.sum.origin || $('#optTex').value === 'off') return material(m.userData.mat);
  const profile = previewMaterial(m.userData.mat, sec.pool);
  const atlas = atlasFor(profile);
  const tex = atlas ? atlasTexture(atlas) : null;
  if (atlas && !tex) return material(m.userData.mat);
  const key = (atlas || 'solid') + '|' + m.userData.mat;
  if (!st.texMats.has(key)) {
    const { atlas: unused, ...options } = profile;
    st.texMats.set(key, new THREE.MeshStandardMaterial({ ...options, map: tex, side: THREE.DoubleSide }));
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

const workflow = createWorkflow({ $, el, st, api, post, status, setTab, resetView, afterChange, selectSection });
$('#btnCars').addEventListener('click', () => setTab('cars'));
$('#btnSettings').addEventListener('click', () => { workflow.renderSettings(); setTab('settings'); });
$('#btnDlc').addEventListener('click', () => { renderFile(); setTab('file'); if (!st.sum) status('Import a car before exporting DLC.', true); });

async function init() {
  renderPart(); renderModel();
  const s = await api('/api/status');
  await refreshLibrary();
  if (s.open) { st.sum = await api('/api/summary'); resetView(); }
  workflow.renderClipboard();
  if (st.lib.needs_setup) { setTab('settings'); status(st.lib.setup_error || 'Set your FH1 game path in Settings to start.', true); }
  else if (!s.open) setTab('cars');
}
init().catch(e => status(e.message, true));
