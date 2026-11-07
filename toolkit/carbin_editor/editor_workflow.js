import { preferredModel } from './library_actions.mjs';

export function createWorkflow({ $, el, st, api, post, status, setTab, resetView, afterChange, selectSection, busy, diag }) {
  let rows = [], query = '', selected = null, donor = null, clip = null, generation = 0;
  const showError = error => status(error.message, true);

  async function choosePath(kind, input) {
    try {
      if (!window.pywebview?.api) throw new Error('Enter the full path in the field. Native Browse is available in the desktop app.');
      const value = await window.pywebview.api.choose_path(kind, kind === 'dlc' ? '' : input.value || '');
      if (value) input.value = kind === 'dlc' && input.value.trim() ? input.value + ';' + value : value;
    } catch (error) { showError(error); }
  }

  function renderSettings() {
    const lib = st.lib || { config: { game: '', dlc: [] }, needs_setup: true };
    const game = el('input', { id: 'cfgGame', value: lib.config.game || '', placeholder: 'FH1 game folder, GoD container, or ISO' });
    const dlc = el('input', { id: 'cfgDlc', value: (lib.config.dlc || []).join(';'), placeholder: 'DLC folders, separated by ;' });
    const notice = el('p', { class: lib.needs_setup || st.libraryError ? 'warn' : 'ok', id: 'setupMessage' }, st.libraryError || lib.setup_error || (lib.needs_setup ? 'Set your FH1 game path to start. Add your DLC folder to include its cars.' : 'Game path is ready and saved on this computer.'));
    $('#tab-settings').replaceChildren(el('h3', {}, 'Settings'), notice,
        el('label', { for: 'cfgGame' }, 'Forza Horizon 1 base game'),
      el('div', { class: 'field' }, game),
      el('div', { class: 'field' }, el('button', { onclick: () => choosePath('game', game) }, 'Browse folder'), el('button', { onclick: () => choosePath('image', game) }, 'Browse game file')),
        el('p', { class: 'muted' }, 'Choose the base game: ISO, GoD container/.data folder, or extracted game/default.xex. Xenia content folders belong in the DLC field below.'),
      el('label', { for: 'cfgDlc' }, 'DLC content folders'), el('div', { class: 'field' }, dlc),
      el('button', { onclick: () => choosePath('dlc', dlc) }, 'Add DLC folder'),
      el('p', { class: 'muted' }, 'For Xenia, use content\\0000000000000000\\4D5309C9\\00000002. Only cars from your own game and installed DLC are listed.'),
      el('button', { class: 'primary', id: 'applySettings', onclick: async event => {
        event.target.disabled = true;
        try {
          st.lib = await post('/api/library/config', { game: game.value, dlc: dlc.value.split(';') });
          st.libraryError = ''; $('#errorBanner').hidden = true;
          st.libSel = null; selected = null; donor = null;
          renderSettings();
          await loadCars();
            if (!st.lib.needs_setup) { setTab('cars'); status(st.lib.path_notice || 'Paths saved. Right-click a car and choose Import.'); }
        } catch (error) { notice.textContent = error.message; notice.className = 'warn'; showError(error); }
        finally { event.target.disabled = false; }
      } }, 'Save paths & load cars'));
  }

  async function loadCars() {
    const mine = ++generation;
    rows = []; renderCars();
    const sources = st.lib?.sources || [];
    if (!sources.length) return;
    status('Indexing game and DLC cars...');
    const t0 = performance.now(); busy.start('Indexing game and DLC cars…');
    const results = await Promise.allSettled(sources.map(async source => {
      const { cars } = await api('/api/library/cars?source=' + encodeURIComponent(source.id));
      return cars.map(car => ({ source: source.id, car, label: source.kind === 'game' ? 'Base game' : source.label }));
    }));
    if (mine !== generation) return;
    busy.end(); diag('cars_indexed', { ms: Math.round(performance.now() - t0), sources: sources.length });
    const errors = [];
    for (let i = 0; i < results.length; i++) {
      const result = results[i];
      if (result.status === 'fulfilled') rows.push(...result.value);
      else errors.push(sources[i].label + ': ' + result.reason.message);
    }
    rows.sort((a, b) => a.car.localeCompare(b.car) || a.label.localeCompare(b.label));
    renderCars();
    status(errors.length ? errors.join('; ') : `${rows.length} cars available. Right-click a car to import.`, errors.length > 0);
  }

  function renderCars() {
    const box = $('#tab-cars');
    const input = el('input', { id: 'carSearch', type: 'search', placeholder: 'Search base game and DLC cars', value: query });
    const list = el('div', { class: 'car-list', id: 'carList' });
    const count = el('p', { class: 'muted', id: 'carCount' });
    const fill = () => {
      const filtered = rows.filter(row => (row.car + ' ' + row.label).toLowerCase().includes(query.toLowerCase()));
      count.textContent = `${filtered.length} of ${rows.length} cars`;
      list.replaceChildren(...filtered.map(row => el('button', {
        class: 'car-row' + (selected?.source === row.source && selected?.car === row.car ? ' selected' : ''),
        onclick: () => { selected = row; renderCars(); showModels(row); },
        ondblclick: () => importCar(row),
        oncontextmenu: event => contextMenu(event, row),
        onkeydown: event => { if (event.key === 'Enter') importCar(row); if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) contextMenu(event, row); }
      }, el('span', {}, row.car), el('small', {}, row.label))));
    };
    input.addEventListener('input', () => { query = input.value; fill(); });
    box.replaceChildren(el('h3', {}, 'Cars'), input, count, list, el('div', { id: 'carModels' }),
      el('p', { class: 'muted' }, 'Right-click → Import. To borrow a part without changing your current car, choose Copy parts from this car.'),
      el('button', { onclick: () => setTab('settings') }, 'Game & DLC settings'));
    fill();
    if (selected) showModels(selected);
  }

  async function models(row) {
    return (await api(`/api/library/members?source=${encodeURIComponent(row.source)}&car=${encodeURIComponent(row.car)}`)).members;
  }

  async function showModels(row) {
    try {
      const members = await models(row);
      if (selected !== row) return;
      const box = $('#carModels');
      box.replaceChildren(el('h3', {}, row.car),
        el('button', { class: 'primary', onclick: () => importCar(row) }, 'Import car'),
        el('button', { onclick: () => inspectDonor(row) }, 'Copy parts…'),
        el('p', { class: 'muted' }, 'Or choose a specific model:'),
        ...members.map(member => el('button', { class: 'model-choice', onclick: () => importCar(row, member) }, member.label)));
    } catch (error) { showError(error); }
  }

  function contextMenu(event, row) {
    event.preventDefault();
    $('#carContextMenu')?.remove();
    const rect = event.target.getBoundingClientRect();
    const x = event.clientX || rect.left, y = event.clientY || rect.bottom;
    const menu = el('div', { id: 'carContextMenu', class: 'context-menu', role: 'menu', style: `left:${Math.max(0, Math.min(x, innerWidth - 230))}px;top:${Math.max(0, Math.min(y, innerHeight - 95))}px` },
      el('button', { role: 'menuitem', onclick: () => { menu.remove(); importCar(row); } }, 'Import'),
      el('button', { role: 'menuitem', onclick: () => { menu.remove(); inspectDonor(row); } }, 'Copy parts from this car…'));
    document.body.append(menu);
    const close = () => { menu.remove(); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
    const outside = e => { if (!menu.contains(e.target)) close(); };
    const escape = e => { if (e.key === 'Escape') close(); };
    setTimeout(() => { document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape); }, 0);
    menu.querySelector('button').focus();
  }

  async function importCar(row, model) {
    try {
      if (st.sum?.undo && !confirm('Import another model? Unsaved edits to the current model will be discarded. Export or stage them first if you want to keep them.')) return;
      status(`Importing ${row.car}...`);
      busy.start(`Opening ${row.car}…`);
      model ||= preferredModel(row.car, await models(row));
      if (!model) throw new Error('No main car model was found');
      const t0 = performance.now();
      st.sum = await post('/api/library/open', { source: row.source, archive: model.archive, member: model.member });
      st.loadMarks = { car: row.car, serverMs: Math.round(performance.now() - t0), parts: st.sum.sections.length };
      resetView(); setTab('part');
      renderClipboard();
    } catch (error) { busy.end(); showError(error); }
  }

  async function inspectDonor(row, model) {
    try {
      status(`Reading parts from ${row.car}...`);
      const members = await models(row);
      model ||= preferredModel(row.car, members);
      if (!model) throw new Error('No donor car model was found');
      const request = { source: row.source, archive: model.archive, member: model.member };
      const info = await post('/api/parts/inspect', request);
      donor = { row, members, model, request, info };
      renderClipboard(); setTab('parts'); status('Select a donor part to copy. Your current car is still open.');
    } catch (error) { showError(error); }
  }

  async function copyCurrent() {
    if (!st.sum || st.sel < 0) return status('Select a part in your current car first.', true);
    await copy({ section: st.sel });
  }

  async function copy(request) {
    try {
      clip = (await post('/api/parts/copy', request)).clipboard;
      renderClipboard(); setTab('parts'); status(`Copied ${clip.name} from ${clip.source}.`);
    } catch (error) { showError(error); }
  }

  function renderClipboard() {
    const box = $('#tab-parts');
    box.replaceChildren(el('h3', {}, 'Copy parts between cars'),
      el('p', { class: 'muted' }, 'Copy the selected part, or right-click another car in Cars → Copy parts from this car. Your current car stays open.'),
      el('button', { disabled: !st.sum || st.sel < 0, onclick: copyCurrent }, 'Copy selected part'),
      el('button', { onclick: () => setTab('cars') }, 'Choose donor car'));
    if (donor) {
      const search = el('input', { type: 'search', placeholder: 'Filter donor parts' });
      const list = el('div', { class: 'donor-list' });
      const fill = () => list.replaceChildren(...donor.info.sections.filter(sec => sec.verts && sec.name.toLowerCase().includes(search.value.toLowerCase())).map(sec =>
        el('div', { class: 'donor-row' }, el('span', {}, `${sec.name} · ${sec.verts} vertices`), el('button', { onclick: () => copy({ ...donor.request, section: sec.index }) }, 'Copy'))));
      search.addEventListener('input', fill);
      box.append(el('h3', {}, 'Donor: ' + donor.row.car),
        el('select', { onchange: event => inspectDonor(donor.row, donor.members[Number(event.target.value)]) },
          donor.members.map((member, index) => el('option', { value: index, selected: member.member === donor.model.member }, member.label))), search, list);
      fill();
    }
    if (!clip) { box.append(el('p', { class: 'muted' }, 'Clipboard is empty.')); return; }
    box.append(el('h3', {}, `Clipboard: ${clip.name}`), el('p', { class: 'muted' }, `From ${clip.source} · ${clip.vertices} vertices. Clipboard stays available when you import another car.`));
    if (!st.sum) { box.append(el('p', {}, 'Import the destination car, then paste here.')); return; }
    const materials = [...new Set(st.sum.sections.flatMap(sec => sec.subs.map(sub => sub.material)))].sort();
    const name = el('input', { id: 'pasteName', value: clip.name + '_copy' });
    const mappings = {};
    box.append(el('p', { class: 'warn' }, 'Pasted geometry uses the destination car’s materials and colour swatches. Donor textures, animations and collision are not copied.'),
      el('div', { class: 'field' }, el('label', {}, 'Part name'), name));
    for (const material of clip.materials) {
      const fallback = materials.includes(material) ? material : materials.includes('body') ? 'body' : materials[0];
      const select = el('select', {}, materials.map(value => el('option', { value, selected: value === fallback }, value)));
      mappings[material] = select;
      box.append(el('div', { class: 'field' }, el('label', { title: material }, material), select));
    }
    box.append(el('button', { class: 'primary', id: 'pastePart', onclick: async event => {
      event.target.disabled = true;
      try {
        st.sum = await post('/api/op', { op: 'paste_part', name: name.value, material_map: Object.fromEntries(Object.entries(mappings).map(([key, select]) => [key, select.value])) });
        const index = st.sum.last_pasted;
        afterChange([index]); selectSection(index); status(`Pasted ${st.sum.sections[index].name}. Use Move, rotate, scale to position it.`);
      } catch (error) { showError(error); }
      finally { event.target.disabled = false; }
    } }, 'Paste into current car'));
  }

  function openPaste() { renderClipboard(); setTab('parts'); }
  addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.target.closest('input,textarea,select,[contenteditable=true]')) return;
    if (event.key.toLowerCase() === 'c' && getSelection()?.toString()) return;
    if (event.key.toLowerCase() === 'c') { event.preventDefault(); copyCurrent(); }
    if (event.key.toLowerCase() === 'v') { event.preventDefault(); openPaste(); }
  });
  return { loadCars, renderCars, renderSettings, renderClipboard, copyCurrent, openPaste, importCar, models };
}
