// Typed client for the kiosk API. Types mirror docs/api.md exactly. If the
// backend returns something that doesn't match, tell A rather than patching
// around it here.

import { createMockApi } from './mock';

// ---------------------------------------------------------------------------
// Switches
// ---------------------------------------------------------------------------

/** Fake data instead of the real backend. `npm run dev:mock` turns it on (see .env.mock). */
export const USE_MOCK: boolean = import.meta.env.VITE_USE_MOCK === 'true';

/** Measured from the Verify click. Past this, the UI closes the attempt itself. */
export const UI_DEADLINE_MS = 2000;

// ---------------------------------------------------------------------------
// Fixed values (spellings from docs/api.md)
// ---------------------------------------------------------------------------

export const OUTCOMES = ['ALLOW', 'REQUEST_VERIFICATION', 'DENY'] as const;
export type Outcome = (typeof OUTCOMES)[number];

export const REASONS = [
  'live_match',
  'spoof_detected',
  'identity_mismatch',
  'no_face',
  'multiple_faces',
  'poor_quality',
  'uncertain_liveness',
  'uncertain_match',
  'deadline_exceeded',
  'not_enrolled',
  'camera_unavailable',
] as const;
export type Reason = (typeof REASONS)[number];

export const SCENARIOS = [
  'unlabeled',
  'genuine',
  'wrong_identity',
  'print_photo',
  'screen_photo',
  'video_replay',
  'poor_light',
] as const;
export type Scenario = (typeof SCENARIOS)[number];

export function isReason(code: string): code is Reason {
  return (REASONS as readonly string[]).includes(code);
}

// ---------------------------------------------------------------------------
// Payloads
// ---------------------------------------------------------------------------

export interface Health {
  models_loaded: boolean;
  camera_running: boolean;
  model_versions: { detector: string; recognizer: string; liveness: string };
}

export interface CameraStarted {
  running: true;
  device_index: number;
}

export interface CameraStopped {
  running: false;
}

export interface Participant {
  participant_id: string;
  name: string;
  enrolled_at: string;
}

export interface EnrollResult {
  participant_id: string;
  name: string;
  frames_used: number;
  liveness_ok: boolean;
}

export interface FrameResult {
  index: number;
  face_found: boolean;
  quality_ok: boolean;
  // A frame without a usable face may have no score.
  liveness_score: number | null;
  match_score: number | null;
}

export interface StageMs {
  capture: number;
  detect: number;
  liveness: number;
  match: number;
  policy: number;
}

export interface Attempt {
  attempt_id: string;
  participant_id: string;
  created_at: string;
  outcome: Outcome;
  reason: Reason;
  liveness_score: number | null;
  match_score: number | null;
  frames: FrameResult[];
  stage_ms: Partial<StageMs>;
  elapsed_ms: number;
  deadline_missed: boolean;
  scenario: Scenario;
  click_to_render_ms: number | null;
  ui_deadline_missed: boolean | null;
}

export interface ScenarioResult {
  attempt_id: string;
  scenario: Scenario;
}

export interface ClientTimingResult {
  attempt_id: string;
}

/** Error body from the API: {"error": {"code", "message"}}. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// One function per endpoint
// ---------------------------------------------------------------------------

export interface KioskApi {
  health(): Promise<Health>;
  startCamera(deviceIndex?: number): Promise<CameraStarted>;
  stopCamera(): Promise<CameraStopped>;
  /** URL for the MJPEG <img>. `nonce` forces a fresh stream connection after a restart. */
  previewUrl(nonce: number): string;
  participants(): Promise<Participant[]>;
  enroll(name: string, participantId?: string): Promise<EnrollResult>;
  verify(participantId: string): Promise<Attempt>;
  attempts(limit?: number): Promise<Attempt[]>;
  setScenario(attemptId: string, scenario: Scenario): Promise<ScenarioResult>;
  sendClientTiming(
    attemptId: string,
    clickToRenderMs: number,
    uiDeadlineMissed: boolean,
  ): Promise<ClientTimingResult>;
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      'network_error',
      'Cannot reach the kiosk service. Check that the backend is running on port 8000.',
      0,
    );
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    // FastAPI's own validation errors use {"detail": ...} instead of our shape.
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      err?.code ?? `http_${res.status}`,
      err?.message ??
        (typeof detail === 'string' ? detail : `Request failed with status ${res.status}.`),
      res.status,
    );
  }
  return data as T;
}

const realApi: KioskApi = {
  health: () => request<Health>('GET', '/health'),
  startCamera: (deviceIndex) =>
    request<CameraStarted>(
      'POST',
      '/camera/start',
      deviceIndex === undefined ? {} : { device_index: deviceIndex },
    ),
  stopCamera: () => request<CameraStopped>('POST', '/camera/stop'),
  previewUrl: (nonce) => `/api/preview?n=${nonce}`,
  participants: () => request<Participant[]>('GET', '/participants'),
  enroll: (name, participantId) =>
    request<EnrollResult>(
      'POST',
      '/enroll',
      participantId === undefined ? { name } : { name, participant_id: participantId },
    ),
  verify: (participantId) =>
    request<Attempt>('POST', '/verify', { participant_id: participantId }),
  attempts: (limit = 50) => request<Attempt[]>('GET', `/attempts?limit=${limit}`),
  setScenario: (attemptId, scenario) =>
    request<ScenarioResult>('POST', `/attempts/${encodeURIComponent(attemptId)}/scenario`, {
      scenario,
    }),
  sendClientTiming: (attemptId, clickToRenderMs, uiDeadlineMissed) =>
    request<ClientTimingResult>(
      'POST',
      `/attempts/${encodeURIComponent(attemptId)}/client_timing`,
      { click_to_render_ms: clickToRenderMs, ui_deadline_missed: uiDeadlineMissed },
    ),
};

export const api: KioskApi = USE_MOCK
  ? createMockApi((code, message, status) => new ApiError(code, message, status))
  : realApi;
