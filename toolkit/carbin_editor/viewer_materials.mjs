// Viewport approximations of FH1 material families. An entire section does not share one sampler.
// Shared carbon/grille and lamp sampler names are present in the game's compiled shader metadata.
export function previewMaterial(name, pool = 'lod') {
  const n = name.toLowerCase();
  const p = { atlas: 'nodamage', color: 0xffffff, roughness: 0.65, metalness: 0.05,
    transparent: false, opacity: 1, depthWrite: true, alphaTest: 0 };
  if (/^(?:window|black_glass|glass)(?:_|$)/.test(n) && !/^glass_lights/.test(n)) {
    return { ...p, atlas: null, color: n === 'black_glass' ? 0x111a22 : 0x667c8c,
      transparent: true, opacity: n === 'black_glass' ? 0.65 : 0.25, depthWrite: false, roughness: 0.18, metalness: 0.1 };
  }
  if (/^(?:lights?_gls|detail_glass)/.test(n)) {
    return { ...p, atlas: 'lights', transparent: true, opacity: 0.65, depthWrite: false, roughness: 0.22 };
  }
  if (/light|reflector|indicator|^fog|^hidhead|^xenonhead|^oldhead|^slorange|^drl/.test(n)) return { ...p, atlas: 'lights', roughness: 0.3, metalness: 0.2 };
  if (/carbon_fiber/.test(n)) return { ...p, atlas: 'shared:carbonFiber.xds', color: 0x555555, roughness: 0.38, metalness: 0.15 };
  const grille = n.match(/(?:^|_)grille([123])(?:_|$)/);
  if (grille) return { ...p, atlas: `shared:grille${grille[1] === '1' ? '1' : '2'}.xds`, color: 0x555555, alphaTest: 0.3 };
  if (/^cockpit_/.test(n)) return { ...p, atlas: 'interior' };
  if (/^body(?:_|$)|paint/.test(n)) return { ...p, color: 0xc62828, roughness: 0.35, metalness: 0.15 };
  if (/chrome|(?:^|_)rim(?:_|$)|brushed_metal|smooth_metal/.test(n)) return { ...p, atlas: null, color: 0xb4bbc4, roughness: 0.24, metalness: 0.85 };
  if (/^(?:black|plastic\d*|rubber(?:_trim)?|weatherstrip|tire|tyre|tread|bottom|frame|underbody)(?:_|$)/.test(n)) {
    return { ...p, atlas: null, color: /tire|tyre|tread|bottom/.test(n) ? 0x17191b : 0x282c30, roughness: 0.85 };
  }
  // Low-detail "interior" and seat badges read the body atlas. Cockpit shaders use the hi-res interior atlas above.
  return p;
}
