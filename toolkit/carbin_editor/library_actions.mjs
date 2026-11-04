export function preferredModel(car, members) {
  const expected = `${car}.carbin`.toLowerCase();
  const lod0 = `${car}_lod0.carbin`.toLowerCase();
  const name = item => item.member.split(/[\\/]/).pop().toLowerCase();
  return members.find(item => name(item) === expected) || members.find(item => name(item) === lod0) || null;
}
