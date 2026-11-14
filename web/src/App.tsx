import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  api,
  ApiError,
  isReason,
  SCENARIOS,
  UI_DEADLINE_MS,
  USE_MOCK,
  type Attempt,
  type Health,
  type Outcome,
  type Participant,
  type Reason,
  type Scenario,
} from './api';
import {
  OUTCOME_TITLE,
  REASON_MESSAGE,
  SCENARIO_LABEL,
  UI_DEADLINE_MESSAGE,
} from './messages';
import { ThemeToggle } from './theme';
import { ScoreMeter, SessionMetrics, TimeBreakdown } from './Metrics';
import type { Thresholds } from './api';

/** Session metrics read this many attempts (the API maximum); the table shows the newest few. */
const METRICS_LIMIT = 1000;
const TABLE_ROWS = 50;

const HEALTH_POLL_MS = 5000;

/** What the outcome card is showing. */
interface Shown {
  seq: number;
  outcome: Outcome;
  reason: Reason | null;
  message: string;
  attempt: Attempt | null;
  /** True when the UI closed the attempt at UI_DEADLINE_MS. */
  uiDeadlineMissed: boolean;
  /** Error from the API when no attempt object came back. */
  errorCode: string | null;
  clickToRenderMs: number | null;
}

/** Bookkeeping for the attempt in progress. Lives in a ref, so late callbacks see it. */
interface AttemptCtx {
  seq: number;
  start: number;
  closed: boolean;
  renderedMs: number | null;
  lateAttemptId: string | null;
}

const fmtScore = (n: number | null | undefined) => (n == null ? '—' : n.toFixed(2));
const fmtMs = (n: number | null | undefined) => (n == null ? '—' : `${Math.round(n)} ms`);
const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const errMessage = (e: unknown) =>
  e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);

export default function App() {
  // Service and camera
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [cameraRunning, setCameraRunning] = useState(false);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [deviceIndex, setDeviceIndex] = useState(0);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [previewFailed, setPreviewFailed] = useState(false);

  // Participants and enrollment
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [enrollName, setEnrollName] = useState('');
  const [enrolling, setEnrolling] = useState(false);
  const [enrollNote, setEnrollNote] = useState<{ ok: boolean; text: string } | null>(null);

  // Verification
  const [verifying, setVerifying] = useState(false); // UI attempt open
  const [serverBusy, setServerBusy] = useState(false); // request still in flight
  const [shown, setShown] = useState<Shown | null>(null);
  const seqRef = useRef(0);
  const ctxRef = useRef<AttemptCtx | null>(null);

  // Review
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const nameOf = useCallback(
    (id: string) => participants.find((p) => p.participant_id === id)?.name ?? id.slice(0, 8),
    [participants],
  );

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------

  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      setHealthError(null);
      setCameraRunning(h.camera_running);
    } catch (e) {
      setHealth(null);
      setHealthError(errMessage(e));
    }
  }, []);

  const refreshParticipants = useCallback(async () => {
    try {
      const list = await api.participants();
      setParticipants(list);
      setSelectedId((cur) =>
        cur && list.some((p) => p.participant_id === cur) ? cur : (list[0]?.participant_id ?? ''),
      );
    } catch (e) {
      setNotice(`Could not load participants: ${errMessage(e)}`);
    }
  }, []);

  const refreshAttempts = useCallback(async () => {
    try {
      setAttempts(await api.attempts(METRICS_LIMIT));
    } catch (e) {
      setNotice(`Could not load recent attempts: ${errMessage(e)}`);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
    void refreshParticipants();
    void refreshAttempts();
    const id = window.setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshHealth, refreshParticipants, refreshAttempts]);

  // ---------------------------------------------------------------------------
  // Camera
  // ---------------------------------------------------------------------------

  const startCamera = async () => {
    setCameraBusy(true);
    try {
      await api.startCamera(deviceIndex);
      setCameraRunning(true);
      setPreviewFailed(false);
      setPreviewNonce((n) => n + 1);
    } catch (e) {
      setNotice(`Could not start the camera: ${errMessage(e)}`);
    } finally {
      setCameraBusy(false);
    }
  };

  const stopCamera = async () => {
    setCameraBusy(true);
    try {
      await api.stopCamera();
      setCameraRunning(false);
    } catch (e) {
      setNotice(`Could not stop the camera: ${errMessage(e)}`);
    } finally {
      setCameraBusy(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Enrollment
  // ---------------------------------------------------------------------------

  const enroll = async () => {
    const name = enrollName.trim();
    if (!name) return;
    setEnrolling(true);
    setEnrollNote(null);
    try {
      const r = await api.enroll(name);
      setEnrollNote({ ok: true, text: `Enrolled ${r.name} from ${r.frames_used} frames.` });
      setEnrollName('');
      await refreshParticipants();
      setSelectedId(r.participant_id);
    } catch (e) {
      const text =
        e instanceof ApiError && e.code === 'spoof_detected'
          ? 'Enrollment rejected: the frames failed the liveness check. Face the camera in good light and try again.'
          : e instanceof ApiError && isReason(e.code)
            ? REASON_MESSAGE[e.code]
            : errMessage(e);
      setEnrollNote({ ok: false, text });
    } finally {
      setEnrolling(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Verification
  // ---------------------------------------------------------------------------

  const show = (ctx: AttemptCtx, s: Omit<Shown, 'seq' | 'clickToRenderMs'>) => {
    setShown({ ...s, seq: ctx.seq, clickToRenderMs: null });
    setVerifying(false);
  };

  const saveClientTiming = useCallback((attemptId: string, ms: number, missed: boolean) => {
    void api
      .sendClientTiming(attemptId, ms, missed)
      .then(() => refreshAttempts())
      .catch((e) => setNotice(`Could not save the client timing: ${errMessage(e)}`));
  }, [refreshAttempts]);

  const verify = async () => {
    if (!selectedId || verifying || serverBusy) return;
    const ctx: AttemptCtx = {
      seq: ++seqRef.current,
      start: performance.now(),
      closed: false,
      renderedMs: null,
      lateAttemptId: null,
    };
    ctxRef.current = ctx;
    setVerifying(true);
    setServerBusy(true);
    setShown(null);

    // UI deadline: close the attempt ourselves and keep access closed.
    const closeAtDeadline = () => {
      if (ctx.closed) return;
      ctx.closed = true;
      show(ctx, {
        outcome: 'REQUEST_VERIFICATION',
        reason: null,
        message: UI_DEADLINE_MESSAGE,
        attempt: null,
        uiDeadlineMissed: true,
        errorCode: null,
      });
    };
    const timer = window.setTimeout(closeAtDeadline, UI_DEADLINE_MS);

    try {
      const attempt = await api.verify(selectedId);
      if (performance.now() - ctx.start >= UI_DEADLINE_MS) closeAtDeadline();
      window.clearTimeout(timer);
      if (!ctx.closed) {
        ctx.closed = true;
        show(ctx, {
          outcome: attempt.outcome,
          reason: attempt.reason,
          message: REASON_MESSAGE[attempt.reason] ?? attempt.reason,
          attempt,
          uiDeadlineMissed: false,
          errorCode: null,
        });
      } else {
        // Late response: don't display it. Report the miss, with the time at
        // which the fallback REQUEST_VERIFICATION reached the screen.
        ctx.lateAttemptId = attempt.attempt_id;
        if (ctx.renderedMs !== null) {
          saveClientTiming(attempt.attempt_id, ctx.renderedMs, true);
        }
      }
    } catch (e) {
      if (performance.now() - ctx.start >= UI_DEADLINE_MS) closeAtDeadline();
      window.clearTimeout(timer);
      if (!ctx.closed) {
        ctx.closed = true;
        const code = e instanceof ApiError ? e.code : 'error';
        show(ctx, {
          outcome: 'REQUEST_VERIFICATION',
          reason: isReason(code) ? code : null,
          message: isReason(code) ? REASON_MESSAGE[code] : errMessage(e),
          attempt: null,
          uiDeadlineMissed: false,
          errorCode: code,
        });
      }
    } finally {
      setServerBusy(false);
      void refreshAttempts();
    }
  };

  // Click-to-render timing. The layout effect runs after React has written the
  // outcome to the DOM. The rAF callback runs at the start of the next frame,
  // just before the browser paints it, so it is the moment the outcome appears.
  useLayoutEffect(() => {
    if (!shown || shown.clickToRenderMs !== null) return;
    const ctx = ctxRef.current;
    if (!ctx || ctx.seq !== shown.seq) return;
    const frame = requestAnimationFrame(() => {
      const ms = Math.round(performance.now() - ctx.start);
      ctx.renderedMs = ms;
      setShown((s) => (s && s.seq === ctx.seq ? { ...s, clickToRenderMs: ms } : s));
      const attemptId = shown.attempt?.attempt_id ?? ctx.lateAttemptId;
      if (attemptId) saveClientTiming(attemptId, ms, shown.uiDeadlineMissed);
    });
    return () => cancelAnimationFrame(frame);
  }, [shown, saveClientTiming]);

  // ---------------------------------------------------------------------------
  // Scenario labels (used by C during rehearsal)
  // ---------------------------------------------------------------------------

  const labelScenario = async (attemptId: string, scenario: Scenario) => {
    const before = attempts;
    setAttempts((list) => list.map((a) => (a.attempt_id === attemptId ? { ...a, scenario } : a)));
    setShown((s) =>
      s?.attempt?.attempt_id === attemptId ? { ...s, attempt: { ...s.attempt, scenario } } : s,
    );
    try {
      await api.setScenario(attemptId, scenario);
    } catch (e) {
      setAttempts(before);
      setNotice(`Could not save the scenario label: ${errMessage(e)}`);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const verifyBlocker = !cameraRunning
    ? 'Start the camera first.'
    : !selectedId
      ? 'Enroll or select a participant first.'
      : serverBusy && !verifying
        ? 'Waiting for the previous attempt to finish…'
        : null;

  return (
    <div className="app">
      <header className="page-header">
        <div className="page-title">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <path d="M3 7V4.5A1.5 1.5 0 0 1 4.5 3H7M13 3h2.5A1.5 1.5 0 0 1 17 4.5V7M17 13v2.5a1.5 1.5 0 0 1-1.5 1.5H13M7 17H4.5A1.5 1.5 0 0 1 3 15.5V13" />
              <circle cx="10" cy="9" r="2.5" />
              <path d="M6 14.5c.9-1.5 2.3-2.2 4-2.2s3.1.7 4 2.2" />
            </svg>
          </span>
          <h1>Face verification kiosk</h1>
          {USE_MOCK && <span className="badge badge-warning">Mock data</span>}
        </div>
        <div className="header-actions">
          <StatusLine health={health} error={healthError} cameraRunning={cameraRunning} />
          <ThemeToggle />
        </div>
      </header>

      {notice && (
        <div className="notice" role="alert">
          <span>{notice}</span>
          <button className="btn btn-ghost btn-sm" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      <main className="layout">
        <section className="surface camera-panel" aria-label="Camera">
          <div className="section-head">
            <h2>Camera</h2>
            <span className={`badge ${cameraRunning ? 'badge-success' : 'badge-neutral'}`}>
              <span className="badge-dot" />
              {cameraRunning ? 'Live' : 'Off'}
            </span>
          </div>
          <div className="section-body">
            <div className="preview">
              {cameraRunning && !previewFailed ? (
                <img
                  key={previewNonce}
                  src={api.previewUrl(previewNonce)}
                  alt="Live camera preview"
                  onError={() => setPreviewFailed(true)}
                />
              ) : (
                <div className="preview-empty">
                  {cameraRunning ? (
                    <>
                      <p>Preview unavailable.</p>
                      <button
                        className="btn btn-secondary btn-sm btn-on-dark"
                        onClick={() => {
                          setPreviewFailed(false);
                          setPreviewNonce((n) => n + 1);
                        }}
                      >
                        Reconnect preview
                      </button>
                    </>
                  ) : (
                    <p>Camera is off.</p>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="section-foot">
            {cameraRunning ? (
              <button className="btn btn-secondary" onClick={stopCamera} disabled={cameraBusy || verifying}>
                Stop camera
              </button>
            ) : (
              <button className="btn btn-primary" onClick={startCamera} disabled={cameraBusy}>
                {cameraBusy ? 'Starting…' : 'Start camera'}
              </button>
            )}
            <label className="field-inline">
              <span>Device</span>
              <input
                type="number"
                min={0}
                value={deviceIndex}
                disabled={cameraRunning || cameraBusy}
                onChange={(e) => setDeviceIndex(Math.max(0, Number(e.target.value) || 0))}
              />
            </label>
          </div>
        </section>

        <section className="surface control-panel" aria-label="Verification">
          <div className="section-head">
            <h2>Verification</h2>
          </div>

          <div className="section-body stack">
            <div className="field">
              <label htmlFor="participant">Badge / participant</label>
              <select
                id="participant"
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                disabled={verifying || participants.length === 0}
              >
                {participants.length === 0 && <option value="">No one enrolled yet</option>}
                {participants.map((p) => (
                  <option key={p.participant_id} value={p.participant_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="verify-action">
              <button
                className="btn btn-primary btn-lg"
                onClick={verify}
                disabled={verifyBlocker !== null || verifying}
              >
                {verifying ? 'Verifying…' : 'Verify'}
              </button>
              {verifyBlocker && <p className="hint">{verifyBlocker}</p>}
            </div>

            <OutcomeCard
              shown={shown}
              verifying={verifying}
              thresholds={health?.thresholds}
              onLabel={labelScenario}
            />
          </div>

          <details className="enroll">
            <summary>
              <span>Enroll a new person</span>
              <svg className="chevron" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </summary>
            <div className="enroll-body">
              <div className="input-row">
                <input
                  type="text"
                  placeholder="Full name"
                  value={enrollName}
                  onChange={(e) => setEnrollName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void enroll()}
                  disabled={enrolling}
                  aria-label="Name to enroll"
                />
                <button
                  className="btn btn-secondary"
                  onClick={enroll}
                  disabled={enrolling || verifying || !cameraRunning || !enrollName.trim()}
                >
                  {enrolling ? 'Enrolling…' : 'Enroll'}
                </button>
              </div>
              <p className="hint">
                {cameraRunning
                  ? 'Look at the camera. Several frames are captured and checked for liveness.'
                  : 'Start the camera to enroll.'}
              </p>
              {enrollNote && (
                <p className={enrollNote.ok ? 'note note-ok' : 'note note-bad'}>{enrollNote.text}</p>
              )}
            </div>
          </details>
        </section>

        <SessionMetrics attempts={attempts} />

        <section className="surface attempts-panel" aria-label="Recent attempts">
          <div className="section-head">
            <h2>
              Recent attempts
              {attempts.length > 0 && (
                <span className="count">
                  {attempts.length > TABLE_ROWS ? `${TABLE_ROWS} of ${attempts.length}` : attempts.length}
                </span>
              )}
            </h2>
            <button className="btn btn-secondary btn-sm" onClick={() => void refreshAttempts()}>
              Refresh
            </button>
          </div>
          <AttemptsTable
            attempts={attempts.slice(0, TABLE_ROWS)}
            nameOf={nameOf}
            onLabel={labelScenario}
          />
        </section>
      </main>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Pieces
// -----------------------------------------------------------------------------

function StatusLine(props: { health: Health | null; error: string | null; cameraRunning: boolean }) {
  const { health, error, cameraRunning } = props;
  if (error) {
    return (
      <div className="status">
        <span className="badge badge-danger">
          <span className="badge-dot" />
          Service offline
        </span>
      </div>
    );
  }
  if (!health) {
    return (
      <div className="status">
        <span className="badge badge-neutral">
          <span className="badge-dot" />
          Connecting…
        </span>
      </div>
    );
  }
  const v = health.model_versions;
  return (
    <div className="status" title={`${v.detector} · ${v.recognizer} · ${v.liveness}`}>
      <span className={`badge ${health.models_loaded ? 'badge-success' : 'badge-warning'}`}>
        <span className="badge-dot" />
        {health.models_loaded ? 'Models ready' : 'Models loading'}
      </span>
      <span className={`badge ${cameraRunning ? 'badge-success' : 'badge-neutral'}`}>
        <span className="badge-dot" />
        {cameraRunning ? 'Camera on' : 'Camera off'}
      </span>
    </div>
  );
}

function OutcomeIcon({ outcome }: { outcome: Outcome }) {
  const path =
    outcome === 'ALLOW'
      ? 'M5.5 10.5l3 3 6-7'
      : outcome === 'DENY'
        ? 'M6.5 6.5l7 7M13.5 6.5l-7 7'
        : 'M10 5.5v5.5M10 14.2v.3';
  return (
    <span className="outcome-icon" aria-hidden="true">
      <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d={path} />
      </svg>
    </span>
  );
}

function OutcomeCard(props: {
  shown: Shown | null;
  verifying: boolean;
  thresholds: Thresholds | undefined;
  onLabel: (attemptId: string, scenario: Scenario) => void;
}) {
  const { shown, verifying, thresholds, onLabel } = props;

  if (verifying) {
    return (
      <div className="outcome outcome-pending" aria-live="polite">
        <div className="outcome-head">
          <span className="spinner" aria-hidden="true" />
          <div className="outcome-title">Checking…</div>
        </div>
        <p className="outcome-msg">Keep looking at the camera.</p>
      </div>
    );
  }
  if (!shown) {
    return (
      <div className="outcome outcome-idle" aria-live="polite">
        <p className="outcome-msg">Select your badge and press Verify.</p>
      </div>
    );
  }

  const a = shown.attempt;
  const cls =
    shown.outcome === 'ALLOW'
      ? 'outcome-allow'
      : shown.outcome === 'DENY'
        ? 'outcome-deny'
        : 'outcome-request';

  return (
    <div className={`outcome ${cls}`} aria-live="assertive">
      <div className="outcome-head">
        <OutcomeIcon outcome={shown.outcome} />
        <div className="outcome-title">{OUTCOME_TITLE[shown.outcome]}</div>
      </div>
      <p className="outcome-msg">{shown.message}</p>

      <div className="scores">
        <ScoreMeter
          label="Liveness"
          value={a?.liveness_score}
          pass={thresholds?.live_pass}
          deny={thresholds?.spoof_deny}
        />
        <ScoreMeter
          label="Identity match"
          value={a?.match_score}
          pass={thresholds?.match_pass}
          deny={thresholds?.mismatch_deny}
        />
      </div>

      <dl className="metrics">
        <div>
          <dt>Decision time</dt>
          <dd>{fmtMs(shown.clickToRenderMs)}</dd>
        </div>
        <div>
          <dt>Server</dt>
          <dd>{fmtMs(a?.elapsed_ms)}</dd>
        </div>
        <div>
          <dt>Usable frames</dt>
          <dd>
            {a && a.frames.length > 0
              ? `${a.frames.filter((f) => f.face_found && f.quality_ok).length} / ${a.frames.length}`
              : '—'}
          </dd>
        </div>
      </dl>

      {(shown.uiDeadlineMissed || a?.deadline_missed) && (
        <p className="flag">
          {shown.uiDeadlineMissed
            ? `No response within ${UI_DEADLINE_MS} ms. Attempt closed by the kiosk.`
            : 'Server deadline missed.'}
        </p>
      )}
      {!a && shown.errorCode && !shown.uiDeadlineMissed && (
        <p className="flag">No attempt recorded ({shown.errorCode}).</p>
      )}

      {a && (
        <label className="field-inline scenario-row">
          <span>Scenario</span>
          <ScenarioSelect value={a.scenario} onChange={(s) => onLabel(a.attempt_id, s)} />
        </label>
      )}

      {a && (
        <details className="outcome-details">
          <summary>
            <span>Details</span>
            <svg className="chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </summary>
          <table className="frames">
            <thead>
              <tr>
                <th>Frame</th>
                <th>Face</th>
                <th className="num">Live</th>
                <th className="num">Match</th>
              </tr>
            </thead>
            <tbody>
              {a.frames.map((f) => (
                <tr key={f.index} className={f.face_found && f.quality_ok ? '' : 'frame-bad'}>
                  <td>{f.index + 1}</td>
                  <td>{f.face_found ? (f.quality_ok ? 'Face OK' : 'Low quality') : 'No face'}</td>
                  <td className="num">{fmtScore(f.liveness_score)}</td>
                  <td className="num">{fmtScore(f.match_score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <TimeBreakdown attempt={a} clickToRenderMs={shown.clickToRenderMs} />
        </details>
      )}
    </div>
  );
}

function ScenarioSelect(props: { value: Scenario; onChange: (s: Scenario) => void }) {
  return (
    <select
      className="select-sm"
      value={props.value}
      onChange={(e) => props.onChange(e.target.value as Scenario)}
      aria-label="Scenario label"
    >
      {SCENARIOS.map((s) => (
        <option key={s} value={s}>
          {SCENARIO_LABEL[s]}
        </option>
      ))}
    </select>
  );
}

const OUTCOME_BADGE: Record<Outcome, string> = {
  ALLOW: 'badge-success',
  REQUEST_VERIFICATION: 'badge-warning',
  DENY: 'badge-danger',
};

function AttemptsTable(props: {
  attempts: Attempt[];
  nameOf: (id: string) => string;
  onLabel: (attemptId: string, scenario: Scenario) => void;
}) {
  const { attempts, nameOf, onLabel } = props;
  if (attempts.length === 0) {
    return <p className="empty">No attempts yet.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Person</th>
            <th>Outcome</th>
            <th>Reason</th>
            <th className="num">Live</th>
            <th className="num">Match</th>
            <th className="num">Server</th>
            <th className="num">Click → result</th>
            <th>Deadline</th>
            <th>Scenario</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.attempt_id}>
              <td className="mono-num">{fmtTime(a.created_at)}</td>
              <td>{nameOf(a.participant_id)}</td>
              <td>
                <span className={`badge ${OUTCOME_BADGE[a.outcome]}`}>
                  <span className="badge-dot" />
                  {a.outcome.replace('_', ' ')}
                </span>
              </td>
              <td className="code">{a.reason}</td>
              <td className="num">{fmtScore(a.liveness_score)}</td>
              <td className="num">{fmtScore(a.match_score)}</td>
              <td className="num">{fmtMs(a.elapsed_ms)}</td>
              <td className="num">{fmtMs(a.click_to_render_ms)}</td>
              <td>
                {a.deadline_missed && <span className="badge badge-warning">Server</span>}
                {a.ui_deadline_missed && <span className="badge badge-warning">UI</span>}
                {!a.deadline_missed && !a.ui_deadline_missed && <span className="muted">OK</span>}
              </td>
              <td>
                <ScenarioSelect value={a.scenario} onChange={(s) => onLabel(a.attempt_id, s)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
