// Single owner of the clearance maths. Risk grid, keel view and advisory all call this.
// Squat is Barrass simplified and indicative only. Ignored: heel, waves, trim, survey age.

// Placeholder thresholds, % of draft. Marine engineer on the team must confirm.
export const RED_PCT = 10;
export const AMBER_PCT = 50;

export function squat(speedKn, Cb, confined) {
  const open = (Cb * speedKn * speedKn) / 100;
  return confined ? 2 * open : open;
}

// depth: charted depth below LAT (m). tide: height above LAT (m). draft (m). speed (kn).
export function ukc(depth, tide, draft, speedKn, Cb, confined) {
  const water = depth + tide;
  const sq = squat(Math.abs(speedKn), Cb, confined);
  const metres = water - draft - sq;
  const pct = (metres / draft) * 100;
  return { water, squat: sq, metres, pct, level: riskLevel(pct) };
}

export function riskLevel(pct) {
  if (pct < 0) return 'aground';
  if (pct < RED_PCT) return 'red';
  if (pct < AMBER_PCT) return 'amber';
  return 'green';
}

// Highest speed (kn) that keeps UKC at or above minPct of draft. 0 when even stopped is not enough.
export function maxSafeSpeed(depth, tide, draft, Cb, confined, minPct = RED_PCT) {
  const spare = depth + tide - draft - (minPct / 100) * draft;
  if (spare <= 0) return 0;
  return Math.sqrt(spare / (((confined ? 2 : 1) * Cb) / 100));
}
