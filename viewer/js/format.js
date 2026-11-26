// Number formatting and axis helpers shared by the views.

const cache = new Map();

/** Thousands-separated number with a fixed number of decimals. */
export function fmt(x, digits = 0) {
  let f = cache.get(digits);
  if (!f) {
    f = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    cache.set(digits, f);
  }
  const v = f.format(x);
  return v === "-0" || /^-0\.0*$/.test(v) ? v.slice(1) : v;
}

/** A round step (1, 2 or 5 × 10ⁿ) close to `rough`. */
export function niceStep(rough) {
  const p = Math.pow(10, Math.floor(Math.log10(rough)));
  const m = rough / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

/** Ticks covering [lo, hi] with about `count` round steps. */
export function ticks(lo, hi, count) {
  const step = niceStep((hi - lo) / count);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return { step, values: out };
}

/** "T+12.3 s", "T+4 min 05 s". */
export function missionTime(t) {
  if (t < 100) return `T+${fmt(t, 1)} s`;
  const m = Math.floor(t / 60);
  const s = Math.floor(t - m * 60);
  return `T+${m} min ${String(s).padStart(2, "0")} s`;
}
