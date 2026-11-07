export class CoordinateClipboard {
  copy(position, name = '') {
    if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) throw new Error('Coordinates must contain three finite numbers');
    this.position = [...position]; this.name = name;
  }
  read() { return this.position ? [...this.position] : null; }
}

export const mergeTargets = (sections, source) => sections.filter(section => section.index !== source);

export function createPartTools({ $, el, st, place, run, status, setTab, selectSection, syncPlaceFields }) {
  const clipboard = new CoordinateClipboard();
  const placementUndo = [], placementRedo = [];
  function undoPlacement() {
    if (!place.group || placementUndo.at(-1)?.group !== place.group) return false;
    const edit = placementUndo.pop(); placementRedo.push(edit);
    place.group.position.fromArray(edit.before); syncPlaceFields(); status('Undid coordinate paste on imported model.'); return true;
  }
  function redoPlacement() {
    if (!place.group || placementRedo.at(-1)?.group !== place.group) return false;
    const edit = placementRedo.pop(); placementUndo.push(edit);
    place.group.position.fromArray(edit.after); syncPlaceFields(); status('Redid coordinate paste on imported model.'); return true;
  }
  let closeMenu = () => {};
  function copy(index) {
    const imported = index === 'import';
    clipboard.copy(imported ? place.group.position.toArray() : st.sum.sections[index].offset,
      imported ? place.name : st.sum.sections[index].name);
    status(`Copied coordinates from ${clipboard.name}: ${clipboard.read().map(v => v.toFixed(4)).join(', ')} m`);
  }
  async function paste(index) {
    const position = clipboard.read();
    if (!position) return status('Copy coordinates from a part or imported model first.', true);
    if (index === 'import') {
      placementUndo.push({group: place.group, before: place.group.position.toArray(), after: position});
      if (placementUndo.length > 20) placementUndo.shift();
      placementRedo.length = 0;
      place.group.position.fromArray(position); syncPlaceFields();
      status('Pasted position onto imported model. Rotation and scale kept.');
    } else {
      await run('set_coordinates', {section: index, coordinates: position}, [index]);
    }
  }
  function mergeDialog(source) {
    selectSection(source);
    const target = el('select', {id: 'mergeTarget'}, mergeTargets(st.sum.sections, source).map(s =>
      el('option', {value: s.index}, s.name + (s.subs.length ? '' : ' (empty slot)'))));
    const both = el('input', {id: 'mergeBoth', type: 'checkbox', checked: !!st.canPair?.(), disabled: !st.canPair?.()});
    const dialog = el('div', {id: 'mergeDialog'}, el('h3', {}, 'Merge into existing part / upgrade'),
      el('p', {class: 'muted'}, 'The target keeps its name and existing upgrade assignment. The source becomes an empty slot. Geometry stays in the same position.'),
      el('div', {class: 'field'}, el('label', {}, 'Target part'), target),
      el('label', {class: 'chk'}, both, ' Main + LOD0 (stage both for export)'),
      el('p', {class: 'muted'}, 'Both model files must contain the named source and target. This does not create a new shop upgrade.'),
      el('button', {class: 'primary', onclick: async event => {
        event.target.disabled = true;
        const index = Number(target.value), paired = both.checked;
        if (await run('merge_parts', {source_section: source, target_section: index, both_models: paired}, 'all')) {
          selectSection(index); status(paired ? 'Merged driving + photo models. Apply to existing car or export car ZIP to save.' : 'Merged into ' + st.sum.sections[index].name);
        } else event.target.disabled = false;
      }}, 'Merge'));
    $('#mergeDialog')?.remove(); $('#tab-part').append(dialog); dialog.scrollIntoView({block: 'nearest'});
  }
  function menu(event, index) {
    event.preventDefault(); event.stopPropagation(); closeMenu();
    const imported = index === 'import';
    const menu = el('div', {id: 'partContextMenu', class: 'context-menu', role: 'menu', style:
      `left:${Math.max(0, Math.min(event.clientX, innerWidth - 260))}px;top:${Math.max(0, Math.min(event.clientY, innerHeight - 140))}px`},
      el('button', {role: 'menuitem', onclick: () => { closeMenu(); copy(index); }}, 'Copy coordinates'),
      el('button', {role: 'menuitem', disabled: !clipboard.read(), onclick: () => { closeMenu(); paste(index); }}, 'Paste coordinates'),
      el('button', {role: 'menuitem', onclick: () => {
        closeMenu(); if (imported) { setTab('model'); $('#mTarget')?.focus(); }
        else mergeDialog(index);
      }}, 'Merge into…'));
    const outside = e => { if (!menu.contains(e.target)) closeMenu(); };
    const escape = e => { if (e.key === 'Escape') closeMenu(); };
    closeMenu = () => { menu.remove(); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
    document.body.append(menu);
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    menu.querySelector('button').focus();
  }
  return {menu, copy, paste, mergeDialog, undoPlacement, redoPlacement};
}
