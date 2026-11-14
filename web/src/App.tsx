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
  type Presence,
  type Reason,
  type Scenario,
  type SecondCheck,
} from './api';
import {
  OUTCOME_TITLE,
  REASON_MESSAGE,
  SCENARIO_LABEL,
  UI_DEADLINE_MESSAGE,
} from './messages';
import { CameraView } from './CameraView';
import { ThemeToggle } from './theme';

const HEALTH_POLL_MS = 5000;
const PRESENCE_POLL_MS = 150;
const REARM_AFTER_MS = 700;
const SECOND_CHECK_MESSAGE: Record<SecondCheck['reason'], string> = {
  second_check_spoof: 'The second check found evidence of a spoof.',
  second_check_recapture: 'The second check could not establish liveness. Try a fresh capture.',
  second_check_unavailable: 'The second check was unavailable. Try a fresh capture.',
  second_check_identity_unconfirmed: 'The fresh frame did not confirm the selected identity. Try again.',
};
const JEV_LABEL: Record<string, string> = {
  live_person: 'Looks live',
  printed_photo: 'Printed photo',
  screen_or_video: 'Screen or video',
  unclear: 'Unclear',
};

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
  const [presence, setPresence] = useState<Presence | null>(null);
  const [operatorOpen, setOperatorOpen] = useState(false);
  const arrival = useRef({ seen: 0, emptySince: null as number | null, checked: false });

  // Participants and enrollment
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [enrollName, setEnrollName] = useState('');
  const [enrolling, setEnrolling] = useState(false);
  const [enrollNote, setEnrollNote] = useState<{ ok: boolean; text: string } | null>(null);

  // Verification
  const [verifying, setVerifying] = useState(false); // UI attempt open
  const [serverBusy, setServerBusy] = useState(false); // request still in flight
  const [jevBusy, setJevBusy] = useState(false);
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
      setAttempts(await api.attempts(50));
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
    if (!name || enrolling || verifying || jevBusy || !cameraRunning) return;
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

  const show = useCallback((ctx: AttemptCtx, s: Omit<Shown, 'seq' | 'clickToRenderMs'>) => {
    setShown({ ...s, seq: ctx.seq, clickToRenderMs: null });
    setVerifying(false);
  }, []);

  const saveClientTiming = useCallback((attemptId: string, ms: number, missed: boolean) => {
    void api
      .sendClientTiming(attemptId, ms, missed)
      .then(() => refreshAttempts())
      .catch((e) => setNotice(`Could not save the client timing: ${errMessage(e)}`));
  }, [refreshAttempts]);

  const verify = useCallback(async () => {
    if (!selectedId || !cameraRunning || previewFailed || verifying || serverBusy || jevBusy || enrolling || cameraBusy) return;
    arrival.current.checked = true;
    arrival.current.emptySince = null;
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
  }, [selectedId, cameraRunning, previewFailed, verifying, serverBusy, jevBusy, enrolling, cameraBusy,
    show, saveClientTiming, refreshAttempts]);

  useEffect(() => {
    arrival.current = { seen: 0, emptySince: null, checked: false };
    setShown(null);
  }, [selectedId, cameraRunning, previewNonce]);

  useEffect(() => {
    if (!cameraRunning || previewFailed || !health?.models_loaded) {
      setPresence(null);
      return;
    }
    const busy = verifying || serverBusy || enrolling || jevBusy || cameraBusy;
    let cancelled = false;
    let timer: number;
    const poll = async () => {
      try {
        const next = await api.presence();
        if (cancelled) return;
        setPresence(next);
        const state = arrival.current;
        if (busy) {
          state.seen = 0;
          state.emptySince = null;
        } else if (next.count === 0) {
          state.seen = 0;
          state.emptySince ??= performance.now();
          if (state.checked && performance.now() - state.emptySince >= REARM_AFTER_MS) {
            state.checked = false;
            setShown(null);
          }
        } else {
          state.emptySince = null;
          state.seen = next.ready ? state.seen + 1 : 0;
          if (state.seen >= 2 && !state.checked && !operatorOpen && selectedId &&
              document.visibilityState === 'visible') {
            void verify();
            return;
          }
        }
      } catch {
        if (cancelled) return;
        setPresence(null);
        arrival.current.seen = 0;
        arrival.current.emptySince = null;
      }
      if (!cancelled) timer = window.setTimeout(poll, PRESENCE_POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [cameraRunning, previewFailed, health?.models_loaded, selectedId, verifying, serverBusy,
    enrolling, jevBusy, cameraBusy, operatorOpen, verify]);

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

  const canRunJev = !!(
    health?.jev_enabled && cameraRunning && !verifying && !serverBusy &&
    shown?.attempt && shown.attempt.participant_id === selectedId &&
    shown.attempt.reason === 'uncertain_liveness' &&
    shown.clickToRenderMs !== null && !shown.uiDeadlineMissed && !shown.attempt.second_check
  );

  const runJev = async () => {
    if (!canRunJev || !shown?.attempt || jevBusy) return;
    const id = shown.attempt.attempt_id;
    setJevBusy(true);
    try {
      const result = await api.secondCheck(id);
      setShown((current) => current?.attempt?.attempt_id === id
        ? { ...current, attempt: { ...current.attempt, second_check: result } }
        : current);
      void refreshAttempts();
    } catch (e) {
      setNotice(`Could not run the second check: ${errMessage(e)}`);
    } finally {
      setJevBusy(false);
    }
  };

  const displayOutcome = shown?.attempt?.second_check?.outcome ?? shown?.outcome ?? null;
  const instruction = previewFailed ? 'Camera preview unavailable'
    : !cameraRunning ? 'Start the camera to begin'
    : enrolling ? 'Look straight ahead'
    : verifying ? 'Blink once now'
    : jevBusy ? 'Hold still for the second check'
    : presence && presence.count > 1 ? 'One person at a time'
    : operatorOpen ? 'Automatic verification paused'
    : shown && presence?.count === 1 ? (displayOutcome === 'ALLOW' ? 'You may proceed' : 'Step away to reset')
    : !selectedId ? 'Enroll or select a participant'
    : presence?.face && !presence.ready ? 'Move closer and center your face'
    : 'Step into view and look straight ahead';
  const readyForRetry = cameraRunning && !previewFailed && !!selectedId && !!presence?.ready &&
    !!health?.models_loaded && !verifying && !serverBusy && !enrolling && !jevBusy && !cameraBusy;


  return (
    <div className="app">
      <header className="page-header">
        <div className="page-title">
          <span className="brand-mark" aria-hidden="true">
            HID
          </span>
          <h1>Access kiosk</h1>
          {USE_MOCK && <span className="badge badge-warning">Mock data</span>}
        </div>
        <div className="header-actions">
          <StatusLine health={health} error={healthError} cameraRunning={cameraRunning} />
          <button className="btn btn-secondary" aria-expanded={operatorOpen} aria-controls="operator-tools"
            onClick={() => setOperatorOpen((open) => !open)}>
            {operatorOpen ? 'Close operator tools' : 'Operator tools'}
          </button>
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
        <section className="camera-panel" aria-label="Camera">
          <CameraView src={api.previewUrl(previewNonce)} nonce={previewNonce} presence={presence}
            running={cameraRunning} failed={previewFailed} active={verifying} outcome={presence?.count === 1 ? displayOutcome : null}
            instruction={instruction} onError={() => setPreviewFailed(true)}
            onReconnect={() => {
              setPreviewFailed(false);
              setPreviewNonce((nonce) => nonce + 1);
            }} />
          <div className="camera-controls">
            <span>{operatorOpen ? 'Operator mode' : 'Automatic verification'}</span>
            <button className={`btn ${cameraRunning ? 'btn-secondary' : 'btn-primary'}`}
              onClick={cameraRunning ? stopCamera : startCamera}
              disabled={cameraBusy || verifying || serverBusy || enrolling || jevBusy}>
              {cameraBusy ? (cameraRunning ? 'Stopping…' : 'Starting…')
                : cameraRunning ? 'Stop camera' : 'Start camera'}
            </button>
          </div>
        </section>

        <section className="surface control-panel" aria-label="Verification">
          <div className="section-body stack kiosk-instructions">
            <p className="kiosk-label">{selectedId ? 'Verifying for' : 'Verification'}</p>
            <h2 className="kiosk-person">{selectedId ? nameOf(selectedId) : 'Select a participant'}</h2>
            {!selectedId && (
              <button className="btn btn-secondary" onClick={() => setOperatorOpen(true)}>Set up participant</button>
            )}
            {!shown && !verifying && (
              <div className="kiosk-welcome" aria-live="polite">
                <strong>{instruction}</strong>
                {cameraRunning && selectedId && !operatorOpen && (presence?.count ?? 0) < 2 &&
                  <p>Blink once when prompted.</p>}
              </div>
            )}
            <OutcomeCard shown={shown} verifying={verifying} onLabel={labelScenario} />
            {shown?.attempt?.second_check && (
              <div className="stack" aria-live="polite">
                <strong>Jev second check · {OUTCOME_TITLE[shown.attempt.second_check.outcome]}</strong>
                <p className="hint">{SECOND_CHECK_MESSAGE[shown.attempt.second_check.reason]}</p>
                <p className="hint">
                  {shown.attempt.second_check.jev_label &&
                    `${JEV_LABEL[shown.attempt.second_check.jev_label] ?? 'Unclear'} · `}
                  {fmtMs(shown.attempt.second_check.second_check_ms)} total
                  {shown.attempt.second_check.jev_ms !== null &&
                    ` · ${fmtMs(shown.attempt.second_check.jev_ms)} from TypeSafe`}
                </p>
              </div>
            )}
            {(canRunJev || jevBusy) && (
              <div className="stack">
                <p className="hint">With your consent, the kiosk sends one fresh camera crop to TypeSafe.</p>
                <button className="btn btn-secondary" onClick={runJev} disabled={jevBusy}>
                  {jevBusy ? 'Running second check…' : 'Consent and run Jev'}
                </button>
              </div>
            )}
            {shown && displayOutcome === 'REQUEST_VERIFICATION' && (
              <button className="btn btn-secondary" disabled={!readyForRetry} onClick={verify}>Try again</button>
            )}
          </div>
        </section>

        {operatorOpen && (
          <section className="operator-panel" id="operator-tools" aria-label="Operator tools">
            <div className="surface operator-settings">
              <div className="section-head"><h2>Operator tools</h2><ThemeToggle /></div>
              <div className="section-body stack">
                <div className="field">
                  <label htmlFor="participant">Selected participant</label>
                  <select
                    id="participant"
                    value={selectedId}
                    onChange={(e) => setSelectedId(e.target.value)}
                    disabled={verifying || serverBusy || enrolling || jevBusy || participants.length === 0}
                  >
                    {participants.length === 0 && <option value="">No one enrolled yet</option>}
                    {participants.map((p) => (
                      <option key={p.participant_id} value={p.participant_id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>

                <label className="field-inline">
                  <span>Camera device</span>
                  <input type="number" min={0} max={255} value={deviceIndex} disabled={cameraRunning || cameraBusy}
                    onChange={(e) => setDeviceIndex(Math.max(0, Math.min(255, Math.trunc(Number(e.target.value) || 0))))} />
                </label>
                <button className="btn btn-secondary" disabled={!readyForRetry} onClick={verify}>Verify manually</button>
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
                      disabled={enrolling || verifying || serverBusy || jevBusy}
                      aria-label="Name to enroll"
                    />
                    <button
                      className="btn btn-secondary"
                      onClick={enroll}
                      disabled={enrolling || verifying || serverBusy || jevBusy || !cameraRunning || !enrollName.trim()}
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
            </div>

            <section className="surface attempts-panel" aria-label="Recent attempts">
              <div className="section-head">
                <h2>
                  Recent attempts
                  {attempts.length > 0 && <span className="count">{attempts.length}</span>}
                </h2>
                <button className="btn btn-secondary btn-sm" onClick={() => void refreshAttempts()}>
                  Refresh
                </button>
              </div>
              <AttemptsTable attempts={attempts} nameOf={nameOf} onLabel={labelScenario} />
            </section>
          </section>
        )}
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
  onLabel: (attemptId: string, scenario: Scenario) => void;
}) {
  const { shown, verifying, onLabel } = props;

  if (verifying) {
    return (
      <div className="outcome outcome-pending" aria-live="polite">
        <div className="outcome-head">
          <span className="spinner" aria-hidden="true" />
          <div className="outcome-title">Checking…</div>
        </div>
        <p className="outcome-msg">Look straight at the camera and blink once now.</p>
      </div>
    );
  }
  if (!shown) return null;

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

      <dl className="metrics">
        <div>
          <dt>Liveness</dt>
          <dd>{fmtScore(a?.liveness_score)}</dd>
        </div>
        <div>
          <dt>Match</dt>
          <dd>{fmtScore(a?.match_score)}</dd>
        </div>
        <div>
          <dt>Start → result</dt>
          <dd>{fmtMs(shown.clickToRenderMs)}</dd>
        </div>
        <div>
          <dt>Server</dt>
          <dd>{fmtMs(a?.elapsed_ms)}</dd>
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
          <p className="stages">
            {Object.entries(a.stage_ms)
              .map(([k, v]) => `${k} ${v} ms`)
              .join(' · ')}
          </p>
          <label className="field-inline">
            <span>Scenario</span>
            <ScenarioSelect value={a.scenario} onChange={(s) => onLabel(a.attempt_id, s)} />
          </label>
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
            <th className="num">Start → result</th>
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
