// Metric displays: score meters and time breakdown for one attempt, plus the
// session metrics section (stat tiles, per-scenario table, decision-time histogram).

import { useMemo, useRef, useState } from 'react';
import { UI_DEADLINE_MS, type Attempt, type Scenario } from './api';
import { SCENARIO_LABEL } from './messages';
import {
  ATTACK_SCENARIOS,
  buildReport,
  histogram,
  REPORT_SCENARIOS,
  type Summary,
} from './stats';

const nf = new Intl.NumberFormat();
const pct = (r: number | null) => (r == null ? '—' : `${Math.round(r * 100)}%`);
const ms = (v: number | null | undefined) => (v == null ? '—' : `${nf.format(Math.round(v))} ms`);
const score = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2));

// -----------------------------------------------------------------------------
// Score meter
// -----------------------------------------------------------------------------

type Zone = 'pass' | 'uncertain' | 'deny' | 'none';

/**
 * A 0–1 score on a thin track. With thresholds, the fill takes the zone's status
 * colour and the pass/deny marks are drawn; without them it is a neutral bar.
 */
export function ScoreMeter(props: {
  label: string;
  value: number | null | undefined;
  pass?: number;
  deny?: number;
}) {
  const { label, value, pass, deny } = props;
  const has = typeof value === 'number' && Number.isFinite(value);
  const clamped = has ? Math.min(1, Math.max(0, value)) : 0;
  const zone: Zone =
    !has || pass == null || deny == null
      ? 'none'
      : value >= pass
        ? 'pass'
        : value < deny
          ? 'deny'
          : 'uncertain';
  const zoneText =
    zone === 'pass' ? 'pass' : zone === 'deny' ? 'deny' : zone === 'uncertain' ? 'uncertain' : null;

  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-label">{label}</span>
        <span className="meter-value">
          {score(value)}
          {zoneText && <span className={`meter-zone zone-${zone}`}>{zoneText}</span>}
        </span>
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={has ? Number(value.toFixed(2)) : undefined}
        aria-valuetext={has ? `${score(value)}${zoneText ? `, ${zoneText}` : ''}` : 'no score'}
      >
        {has && <div className={`meter-fill zone-${zone}`} style={{ width: `${clamped * 100}%` }} />}
        {deny != null && <span className="meter-tick" style={{ left: `${deny * 100}%` }} />}
        {pass != null && <span className="meter-tick" style={{ left: `${pass * 100}%` }} />}
      </div>
      {pass != null && deny != null && (
        <div className="meter-caption">
          Deny below {deny.toFixed(2)} · pass at {pass.toFixed(2)}+
        </div>
      )}
    </div>
  );
}

// -----------------------------------------------------------------------------
// Where the time went (one attempt)
// -----------------------------------------------------------------------------

const STAGES: Array<[keyof Attempt['stage_ms'], string]> = [
  ['capture', 'Capture'],
  ['detect', 'Detect'],
  ['liveness', 'Liveness'],
  ['match', 'Match'],
  ['policy', 'Policy'],
];

export function TimeBreakdown(props: { attempt: Attempt; clickToRenderMs: number | null }) {
  const { attempt, clickToRenderMs } = props;
  const rows: Array<{ label: string; value: number }> = [];
  let stageSum = 0;
  for (const [key, label] of STAGES) {
    const v = attempt.stage_ms[key];
    if (typeof v === 'number') {
      rows.push({ label, value: v });
      stageSum += v;
    }
  }
  const otherServer = attempt.elapsed_ms - stageSum;
  if (otherServer > 0) rows.push({ label: 'Other server work', value: otherServer });
  if (clickToRenderMs != null && clickToRenderMs > attempt.elapsed_ms) {
    rows.push({ label: 'Network and render', value: clickToRenderMs - attempt.elapsed_ms });
  }
  const total = clickToRenderMs ?? attempt.elapsed_ms;
  const scale = Math.max(UI_DEADLINE_MS, total, ...rows.map((r) => r.value));

  return (
    <div className="breakdown">
      <div className="breakdown-title">Where the time went</div>
      <ul className="bar-list">
        {rows.map((r) => (
          <li key={r.label}>
            <span className="bar-label">{r.label}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(r.value / scale) * 100}%` }} />
            </span>
            <span className="bar-value">{ms(r.value)}</span>
          </li>
        ))}
      </ul>
      <div className="breakdown-total">
        {clickToRenderMs != null ? 'Click to result' : 'Server total'} {ms(total)} of{' '}
        {nf.format(UI_DEADLINE_MS)} ms budget
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Session metrics
// -----------------------------------------------------------------------------

type Scope = 'all' | 'session';

export function SessionMetrics(props: { attempts: Attempt[] }) {
  const { attempts } = props;
  const [scope, setScope] = useState<Scope>('all');
  const openedAt = useRef(Date.now());

  const inScope = useMemo(
    () =>
      scope === 'all'
        ? attempts
        : attempts.filter((a) => Date.parse(a.created_at) >= openedAt.current),
    [attempts, scope],
  );
  const report = useMemo(() => buildReport(inScope), [inScope]);
  const labeled = report.total - report.unlabeled;

  return (
    <section className="surface metrics-panel" aria-label="Session metrics">
      <div className="section-head">
        <h2>
          Session metrics
          <span className="count">{report.total}</span>
        </h2>
        <div className="segmented" role="radiogroup" aria-label="Metrics scope">
          {(
            [
              ['all', 'All attempts'],
              ['session', 'Since page opened'],
            ] as Array<[Scope, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={scope === value}
              className={scope === value ? 'is-active' : undefined}
              onClick={() => setScope(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {report.total === 0 ? (
        <p className="empty">No attempts in this scope yet.</p>
      ) : (
        <>
          <div className="tiles">
            <Tile
              label="Attack acceptance"
              value={pct(report.attacks.allowRate)}
              sub={
                report.attacks.n
                  ? `${report.attacks.allow} of ${report.attacks.n} print, screen and replay attacks allowed`
                  : 'No labelled attacks yet'
              }
            />
            <Tile
              label="Wrong identity accepted"
              value={pct(report.scenarios.wrong_identity.allowRate)}
              sub={
                report.scenarios.wrong_identity.n
                  ? `${report.scenarios.wrong_identity.allow} of ${report.scenarios.wrong_identity.n} attempts`
                  : 'No labelled attempts yet'
              }
            />
            <Tile
              label="Genuine rejection"
              value={pct(report.genuine.denyRate)}
              sub={
                report.genuine.n
                  ? `${report.genuine.deny} of ${report.genuine.n} genuine and poor-light denied`
                  : 'No labelled genuine attempts yet'
              }
            />
            <Tile
              label="Genuine recapture"
              value={pct(report.genuine.requestRate)}
              sub={
                report.genuine.n
                  ? `${report.genuine.request} of ${report.genuine.n} asked to verify again`
                  : 'No labelled genuine attempts yet'
              }
            />
            <Tile
              label="Median decision time"
              value={ms(report.all.clickToRender.median)}
              sub={`p95 ${ms(report.all.clickToRender.p95)} · max ${ms(report.all.clickToRender.max)}`}
            />
            <Tile
              label="Within 2 seconds"
              value={pct(report.all.timed ? report.all.withinDeadline / report.all.timed : null)}
              sub={`${report.all.uiMissed} UI and ${report.all.serverMissed} server deadline misses`}
            />
          </div>

          {labeled === 0 && (
            <p className="metrics-note">
              Label attempts with a scenario to fill in acceptance and rejection rates.
            </p>
          )}

          <div className="metrics-block">
            <div className="block-head">
              <h3>By scenario</h3>
              <OutcomeLegend />
            </div>
            <ScenarioTable report={report} />
          </div>

          <div className="metrics-block">
            <div className="block-head">
              <h3>Decision time</h3>
              <span className="block-sub">Click to result, {report.all.clickToRender.n} timed attempts</span>
            </div>
            <LatencyHistogram
              values={inScope
                .map((a) => a.click_to_render_ms)
                .filter((v): v is number => typeof v === 'number')}
            />
          </div>
        </>
      )}
    </section>
  );
}

function Tile(props: { label: string; value: string; sub: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{props.label}</div>
      <div className="tile-value">{props.value}</div>
      <div className="tile-sub">{props.sub}</div>
    </div>
  );
}

function OutcomeLegend() {
  return (
    <ul className="legend" aria-label="Outcome colours">
      <li>
        <span className="swatch swatch-allow" /> Allow
      </li>
      <li>
        <span className="swatch swatch-request" /> Verify
      </li>
      <li>
        <span className="swatch swatch-deny" /> Deny
      </li>
    </ul>
  );
}

/** Thin segmented bar: one attempt mix split into allow / verify / deny. */
function OutcomeMix({ s }: { s: Summary }) {
  if (s.n === 0) return <span className="mix mix-empty" />;
  const parts: Array<[string, number, string]> = [
    ['allow', s.allow, 'allowed'],
    ['request', s.request, 'verify again'],
    ['deny', s.deny, 'denied'],
  ];
  const label = parts.map(([, c, t]) => `${c} ${t}`).join(', ');
  return (
    <span className="mix" role="img" aria-label={label} title={label}>
      {parts
        .filter(([, c]) => c > 0)
        .map(([k, c]) => (
          <span key={k} className={`mix-part mix-${k}`} style={{ flexGrow: c }} />
        ))}
    </span>
  );
}

function ScenarioTable({ report }: { report: ReturnType<typeof buildReport> }) {
  const rows: Scenario[] = [...REPORT_SCENARIOS, 'unlabeled'];
  return (
    <div className="table-wrap">
      <table className="data-table metrics-table">
        <thead>
          <tr>
            <th>Scenario</th>
            <th className="num">Attempts</th>
            <th>Outcomes</th>
            <th className="num">Allow</th>
            <th className="num">Verify</th>
            <th className="num">Deny</th>
            <th className="num">Error rate</th>
            <th className="num">Median</th>
            <th className="num">p95</th>
            <th className="num">Deadline misses</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((sc) => {
            const s = report.scenarios[sc];
            const attackLike = ATTACK_SCENARIOS.includes(sc) || sc === 'wrong_identity';
            const error =
              sc === 'unlabeled' || s.n === 0
                ? '—'
                : attackLike
                  ? `${pct(s.allowRate)} accepted`
                  : `${pct(s.denyRate)} denied`;
            return (
              <tr key={sc} className={s.n === 0 ? 'row-empty' : undefined}>
                <td>{SCENARIO_LABEL[sc]}</td>
                <td className="num">{s.n}</td>
                <td>
                  <OutcomeMix s={s} />
                </td>
                <td className="num">{s.allow}</td>
                <td className="num">{s.request}</td>
                <td className="num">{s.deny}</td>
                <td className="num">{error}</td>
                <td className="num">{ms(s.clickToRender.median)}</td>
                <td className="num">{ms(s.clickToRender.p95)}</td>
                <td className="num">{s.n ? s.uiMissed + s.serverMissed : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Column histogram of click-to-result times with the 2 s limit marked. */
function LatencyHistogram({ values }: { values: number[] }) {
  if (values.length === 0) {
    return <p className="metrics-note">No timed attempts yet.</p>;
  }
  const bins = histogram(values);
  const top = Math.max(...bins.map((b) => b.count));
  const span = bins[bins.length - 1].end;
  const limitPos = (UI_DEADLINE_MS / span) * 100;
  const rangeText = (b: (typeof bins)[number]) =>
    b.open ? `${nf.format(b.start)} ms or more` : `${nf.format(b.start)}–${nf.format(b.end)} ms`;

  return (
    <div className="histogram">
      <div className="hist-plot">
        <span className="hist-ymax">{top}</span>
        <div className="hist-cols">
          {bins.map((b) => (
            <div
              key={b.start}
              className="hist-col"
              tabIndex={0}
              aria-label={`${rangeText(b)}: ${b.count} attempts`}
            >
              {b.count > 0 && (
                <span className="hist-bar" style={{ height: `${(b.count / top) * 100}%` }} />
              )}
              <span className="hist-tip" role="tooltip">
                <strong>
                  {b.count} {b.count === 1 ? 'attempt' : 'attempts'}
                </strong>
                <span>{rangeText(b)}</span>
              </span>
            </div>
          ))}
          <span className="hist-limit" style={{ left: `${limitPos}%` }}>
            <span className="hist-limit-label">2 s limit</span>
          </span>
        </div>
      </div>
      <div className="hist-axis" aria-hidden="true">
        {bins
          .filter((b) => b.start % 1000 === 0)
          .map((b) => (
            <span key={b.start} style={{ left: `${(b.start / span) * 100}%` }}>
              {b.start === 0 ? '0' : `${b.start / 1000} s`}
            </span>
          ))}
      </div>
      <table className="sr-only">
        <caption>Decision time distribution</caption>
        <thead>
          <tr>
            <th>Range</th>
            <th>Attempts</th>
          </tr>
        </thead>
        <tbody>
          {bins.map((b) => (
            <tr key={b.start}>
              <td>{rangeText(b)}</td>
              <td>{b.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
