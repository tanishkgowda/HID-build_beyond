// Session metrics computed from attempts, using the same definitions as C's
// scripts/evaluate.py, so the numbers on screen agree with the written report:
// - median: middle value, or the mean of the two middle values (Python statistics.median)
// - p95: nearest-rank, so the reported value is one that was actually observed
// - attacks = print_photo + screen_photo + video_replay; genuine = genuine + poor_light
// - "within 2 s" counts attempts with a click-to-render time, no UI deadline miss,
//   and click_to_render_ms <= UI_DEADLINE_MS

import { UI_DEADLINE_MS, type Attempt, type Scenario } from './api';

export const ATTACK_SCENARIOS: Scenario[] = ['print_photo', 'screen_photo', 'video_replay'];
export const GENUINE_SCENARIOS: Scenario[] = ['genuine', 'poor_light'];
/** Report order, same as evaluate.py. */
export const REPORT_SCENARIOS: Scenario[] = [
  'genuine',
  'poor_light',
  'wrong_identity',
  'print_photo',
  'screen_photo',
  'video_replay',
];

export interface Latency {
  n: number;
  median: number | null;
  p95: number | null;
  max: number | null;
}

export interface Summary {
  n: number;
  allow: number;
  request: number;
  deny: number;
  allowRate: number | null;
  requestRate: number | null;
  denyRate: number | null;
  serverMissed: number;
  uiMissed: number;
  /** Attempts that have a click-to-render time. */
  timed: number;
  withinDeadline: number;
  clickToRender: Latency;
  server: Latency;
}

export interface Report {
  total: number;
  unlabeled: number;
  scenarios: Record<Scenario, Summary>;
  attacks: Summary;
  genuine: Summary;
  all: Summary;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.max(0, Math.ceil((p / 100) * s.length) - 1)];
}

export function latency(values: Array<number | null | undefined>): Latency {
  const v = values.filter((x): x is number => typeof x === 'number' && Number.isFinite(x));
  return {
    n: v.length,
    median: median(v),
    p95: percentile(v, 95),
    max: v.length ? Math.max(...v) : null,
  };
}

const rate = (part: number, whole: number) => (whole === 0 ? null : part / whole);

export function summarize(group: Attempt[]): Summary {
  const n = group.length;
  const count = (o: Attempt['outcome']) => group.filter((a) => a.outcome === o).length;
  const allow = count('ALLOW');
  const request = count('REQUEST_VERIFICATION');
  const deny = count('DENY');
  const timed = group.filter((a) => a.click_to_render_ms != null);
  return {
    n,
    allow,
    request,
    deny,
    allowRate: rate(allow, n),
    requestRate: rate(request, n),
    denyRate: rate(deny, n),
    serverMissed: group.filter((a) => a.deadline_missed).length,
    uiMissed: group.filter((a) => a.ui_deadline_missed).length,
    timed: timed.length,
    withinDeadline: timed.filter(
      (a) => !a.ui_deadline_missed && (a.click_to_render_ms as number) <= UI_DEADLINE_MS,
    ).length,
    clickToRender: latency(group.map((a) => a.click_to_render_ms)),
    server: latency(group.map((a) => a.elapsed_ms)),
  };
}

export function buildReport(attempts: Attempt[]): Report {
  const by = (s: Scenario) => attempts.filter((a) => a.scenario === s);
  const scenarios = {} as Record<Scenario, Summary>;
  for (const s of [...REPORT_SCENARIOS, 'unlabeled' as Scenario]) scenarios[s] = summarize(by(s));
  return {
    total: attempts.length,
    unlabeled: scenarios.unlabeled.n,
    scenarios,
    attacks: summarize(attempts.filter((a) => ATTACK_SCENARIOS.includes(a.scenario))),
    genuine: summarize(attempts.filter((a) => GENUINE_SCENARIOS.includes(a.scenario))),
    all: summarize(attempts),
  };
}

export interface Bin {
  start: number;
  end: number;
  count: number;
  /** Last bin collects everything at or above `start`. */
  open: boolean;
}

/**
 * Fixed-width bins from 0 ms. The range always reaches past the 2 s limit so the
 * limit line has room on both sides; anything beyond `capMs` goes in an open last bin.
 */
export function histogram(values: number[], binMs = 250, capMs = 4000): Bin[] {
  const top = Math.min(
    capMs,
    Math.max(UI_DEADLINE_MS + 2 * binMs, Math.ceil((Math.max(0, ...values) + 1) / binMs) * binMs),
  );
  const bins: Bin[] = [];
  for (let start = 0; start < top; start += binMs) {
    bins.push({ start, end: start + binMs, count: 0, open: false });
  }
  const overflow = values.some((v) => v >= top);
  if (overflow) bins.push({ start: top, end: top + binMs, count: 0, open: true });
  for (const v of values) {
    const i = v >= top ? bins.length - 1 : Math.floor(v / binMs);
    bins[Math.max(0, i)].count += 1;
  }
  return bins;
}
