// In-memory stand-in for A's backend, used when USE_MOCK is on.
// It follows the docs/api.md shapes and the three-frame rule, and about 1 in 8
// verify calls answers after the 2 s UI deadline so that path can be tested.

import type {
  Attempt,
  FrameResult,
  KioskApi,
  Outcome,
  Participant,
  Reason,
  Scenario,
} from './api';

type MakeError = (code: string, message: string, status: number) => Error;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const rand = (min: number, max: number) => min + Math.random() * (max - min);
const round2 = (n: number) => Math.round(n * 100) / 100;

const PREVIEW_SVG =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
      <ellipse cx="320" cy="225" rx="105" ry="135" fill="none" stroke="#527b8c" stroke-width="2" stroke-dasharray="8 8"/>
      <text x="320" y="430" fill="#8996a3" font-family="Inter, system-ui, sans-serif" font-size="15" letter-spacing="0.5" text-anchor="middle">Mock camera preview</text>
    </svg>`,
  );

interface Plan {
  outcome: Outcome;
  reason: Reason;
  liveness: [number, number];
  match: [number, number];
  frameFault?: 'no_face' | 'multiple_faces' | 'poor_quality';
}

// Weighted mix of results the real pipeline can produce.
const PLANS: Array<[number, Plan]> = [
  [45, { outcome: 'ALLOW', reason: 'live_match', liveness: [0.9, 0.99], match: [0.5, 0.75] }],
  [10, { outcome: 'DENY', reason: 'spoof_detected', liveness: [0.02, 0.2], match: [0.45, 0.7] }],
  [10, { outcome: 'DENY', reason: 'identity_mismatch', liveness: [0.85, 0.98], match: [0.05, 0.2] }],
  [8, { outcome: 'REQUEST_VERIFICATION', reason: 'poor_quality', liveness: [0.5, 0.8], match: [0.3, 0.5], frameFault: 'poor_quality' }],
  [5, { outcome: 'REQUEST_VERIFICATION', reason: 'no_face', liveness: [0.75, 0.95], match: [0.4, 0.6], frameFault: 'no_face' }],
  [4, { outcome: 'REQUEST_VERIFICATION', reason: 'multiple_faces', liveness: [0.7, 0.9], match: [0.3, 0.5], frameFault: 'multiple_faces' }],
  [9, { outcome: 'REQUEST_VERIFICATION', reason: 'uncertain_liveness', liveness: [0.45, 0.7], match: [0.45, 0.7] }],
  [9, { outcome: 'REQUEST_VERIFICATION', reason: 'uncertain_match', liveness: [0.85, 0.97], match: [0.28, 0.36] }],
];

function pickPlan(): Plan {
  const total = PLANS.reduce((sum, [w]) => sum + w, 0);
  let r = Math.random() * total;
  for (const [w, plan] of PLANS) {
    r -= w;
    if (r <= 0) return plan;
  }
  return PLANS[0][1];
}

function buildFrames(plan: Plan): FrameResult[] {
  const badIndex = Math.floor(Math.random() * 3);
  return [0, 1, 2].map((index) => {
    const faulty = plan.frameFault !== undefined && index === badIndex;
    const faceFound = !(faulty && plan.frameFault === 'no_face');
    return {
      index,
      face_found: faceFound,
      quality_ok: !faulty,
      liveness_score: faceFound ? round2(rand(...plan.liveness)) : null,
      match_score: faceFound ? round2(rand(...plan.match)) : null,
    };
  });
}

export function createMockApi(makeError: MakeError): KioskApi {
  let cameraRunning = false;
  let deviceIndex = 0;
  const participants: Participant[] = [
    {
      participant_id: crypto.randomUUID(),
      name: 'Demo Visitor',
      enrolled_at: new Date(Date.now() - 3_600_000).toISOString(),
    },
  ];
  const attempts: Attempt[] = [];

  const findAttempt = (id: string) => {
    const a = attempts.find((x) => x.attempt_id === id);
    if (!a) throw makeError('not_found', 'Attempt not found.', 404);
    return a;
  };

  return {
    async health() {
      await sleep(30);
      return {
        models_loaded: true,
        camera_running: cameraRunning,
        model_versions: {
          detector: 'yunet-2023mar (mock)',
          recognizer: 'sface-2021dec (mock)',
          liveness: 'minifasnet (mock)',
        },
      };
    },

    async secondCheck() {
      throw makeError('jev_unavailable', 'Jev is unavailable in mock mode.', 503);
    },

    async startCamera(index) {
      await sleep(rand(300, 700));
      cameraRunning = true;
      deviceIndex = index ?? 0;
      return { running: true, device_index: deviceIndex };
    },

    async stopCamera() {
      await sleep(100);
      cameraRunning = false;
      return { running: false };
    },

    previewUrl() {
      return PREVIEW_SVG;
    },

    async participants() {
      await sleep(40);
      return [...participants];
    },

    async enroll(name, participantId) {
      await sleep(rand(600, 1100));
      if (!cameraRunning) {
        throw makeError('camera_unavailable', 'The camera is not running.', 409);
      }
      if (Math.random() < 0.1) {
        throw makeError('spoof_detected', 'Enrollment frames failed the liveness check.', 422);
      }
      const existing = participantId
        ? participants.find((p) => p.participant_id === participantId)
        : undefined;
      const p: Participant = existing ?? {
        participant_id: crypto.randomUUID(),
        name,
        enrolled_at: new Date().toISOString(),
      };
      if (!existing) participants.push(p);
      return { participant_id: p.participant_id, name: p.name, frames_used: 5, liveness_ok: true };
    },

    async verify(participantId) {
      const late = Math.random() < 0.12;
      const elapsed = Math.round(late ? rand(2100, 2800) : rand(350, 1400));
      await sleep(elapsed);
      if (!cameraRunning) {
        throw makeError('camera_unavailable', 'The camera is not running.', 409);
      }
      if (!participants.some((p) => p.participant_id === participantId)) {
        throw makeError('not_enrolled', 'This participant is not enrolled.', 404);
      }

      const plan = pickPlan();
      const frames = buildFrames(plan);
      const live = frames.map((f) => f.liveness_score).filter((s): s is number => s !== null);
      const match = frames.map((f) => f.match_score).filter((s): s is number => s !== null);
      const capture = Math.round(elapsed * 0.55);
      const attempt: Attempt = {
        attempt_id: crypto.randomUUID(),
        participant_id: participantId,
        created_at: new Date().toISOString(),
        outcome: plan.outcome,
        reason: plan.reason,
        liveness_score: live.length ? Math.min(...live) : null,
        match_score: match.length ? round2(match.reduce((a, b) => a + b, 0) / match.length) : null,
        frames,
        stage_ms: {
          capture,
          detect: Math.round(elapsed * 0.1),
          liveness: Math.round(elapsed * 0.2),
          match: Math.round(elapsed * 0.12),
          policy: 1,
        },
        elapsed_ms: elapsed,
        deadline_missed: false,
        scenario: 'unlabeled' as Scenario,
        click_to_render_ms: null,
        ui_deadline_missed: null,
      };
      attempts.unshift(attempt);
      return structuredClone(attempt);
    },

    async attempts(limit = 50) {
      await sleep(40);
      return structuredClone(attempts.slice(0, limit));
    },

    async setScenario(attemptId, scenario) {
      await sleep(60);
      findAttempt(attemptId).scenario = scenario;
      return { attempt_id: attemptId, scenario };
    },

    async sendClientTiming(attemptId, clickToRenderMs, uiDeadlineMissed) {
      await sleep(30);
      const a = findAttempt(attemptId);
      a.click_to_render_ms = clickToRenderMs;
      a.ui_deadline_missed = uiDeadlineMissed;
      return { attempt_id: attemptId };
    },
  };
}
