// Viewport approximations of FH1 material families. An entire section does not share one sampler.
// Shared carbon/grille and lamp sampler names are present in the game's compiled shader metadata.
export function previewMaterial(name, pool = 'lod', context = 'body') {
  const n = name.toLowerCase();
  const p = { atlas: 'nodamage', color: 0xffffff, roughness: 0.65, metalness: 0.05,
    transparent: false, opacity: 1, depthWrite: true, alphaTest: 0 };
  // Imported cockpit files keep these shader names unprefixed. Their UV0 reads
  // interior_LOD0 (including its swatches and badges), rather than the body map.
  // Require explicit model context: exterior LOD0 files use the same vertex pool.
  // Real FH1 cockpit files (checked on the Fiesta) also use bump_plastic2, mottled, digital_gauge, mirrorMiddle and gauge_* shaders, which read the
  // interior atlas as well; in the body atlas those texels are plain white, which made the dash look white.
  if (context === 'cockpit') {
    // Dial faces: the interior atlas is their dark base, and interior_emissive holds the lit numbers and icons (speedometer, rev counter, warning lights).
    if (/^(?:z_)?gauge_emissive(?:_alpha)?$/.test(n)) return { ...p, atlas: 'interior', emissiveAtlas: 'interior_emissive', roughness: 0.4 };
    // The digital display is drawn live by the game from many small textures, so only a dark screen can be shown.
    if (n === 'digital_gauge') return { ...p, atlas: null, color: 0x0b0d10, roughness: 0.15, metalness: 0.3 };
    // The rear-view mirror glass: its UVs span the whole interior atlas (it is a live reflection in the game), so sampling the atlas painted the
    // dial faces and every other texture onto the mirror ("the speedo is on the mirror"). Shown as a dark reflective glass instead.
    if (n === 'mirrormiddle') return { ...p, atlas: null, color: 0x14181d, roughness: 0.08, metalness: 0.9 };
    // The steering wheel's "reflector" is the AIRBAG label (its UVs sit on that label in the interior atlas), not a lamp.
    if (n === 'reflector') return { ...p, atlas: 'interior' };
    // The roof lining lies right under the body's roof panel. Drawn at the same depth it flickered through the roof as a pale patch (light
    // headliners on classics and some modern cars), so it is pushed back in depth: the body panel always wins, the lining shows inside.
    if (n === 'headliner') return { ...p, atlas: 'interior', polygonOffset: true, polygonOffsetFactor: 4, polygonOffsetUnits: 4 };
    // Other real cockpit shaders (checked on the Fiesta and by sampling 45 cars): bump_plastic2, mottled, mirrorMiddle, gauge_needle...
    // all read the interior atlas; in the body atlas those texels are plain white, which made the dash look white.
    if (/^(?:cloth|leather|plastic|badge|headliner|rubber|solid|metal\d*|mottled|mirrormiddle|gauge_\w+|bump_\w+|misc|stitching|wood|matte_colors|race_kit_\w+)$/.test(n)) {
      return { ...p, atlas: 'interior' };
    }
  }
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
  // The floor panel ("undercarriagea", 130 tris on a typical car) has UVs for its own shader, not for the body atlas. Sampling the atlas
  // printed livery and a washed-out blue on the underside, so it is shown as a plain dark panel.
  if (/^under(?:carriage|body|tray)(?:_|$)/.test(n)) return { ...p, atlas: null, color: 0x1c1f22, roughness: 0.9, metalness: 0 };
  if (/^(?:black|plastic\d*|rubber(?:_trim)?|weatherstrip|tire|tyre|tread|bottom|frame)(?:_|$)/.test(n)) {
    return { ...p, atlas: null, color: /tire|tyre|tread|bottom/.test(n) ? 0x17191b : 0x282c30, roughness: 0.85 };
  }
  // Low-detail "interior" and seat badges read the body atlas. Cockpit shaders use the hi-res interior atlas above.
  return p;
}
