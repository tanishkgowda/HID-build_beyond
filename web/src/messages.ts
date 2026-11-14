// Human-facing text for outcome, reason and scenario codes.

import type { Outcome, Reason, Scenario } from './api';

export const OUTCOME_TITLE: Record<Outcome, string> = {
  ALLOW: 'Access allowed',
  REQUEST_VERIFICATION: 'Verification needed',
  DENY: 'Access denied',
};

/** What the person at the kiosk should read (and do next). */
export const REASON_MESSAGE: Record<Reason, string> = {
  live_match: 'Identity confirmed. You may proceed.',
  spoof_detected: 'The camera did not see a live person.',
  identity_mismatch: 'Your face does not match the selected badge.',
  no_face: 'No face found. Stand in front of the camera and look straight at it, then try again.',
  multiple_faces: 'More than one face is in view. Only the person verifying should be in frame.',
  poor_quality: 'Move closer and face the light, then try again.',
  uncertain_liveness: 'We could not confirm a live person. Hold still, face the camera and try again.',
  uncertain_match: 'We could not confirm your identity. Remove a hat or sunglasses, face the camera and try again.',
  deadline_exceeded: 'That took too long. Please try again.',
  not_enrolled: 'This person is not enrolled yet. Enroll first, then verify.',
  blink_required: 'No blink detected. Look straight ahead and blink once as soon as the check starts.',
  camera_unavailable: 'The camera is not running. Press Start camera, then try again.',
};

/** Shown when the UI closes an attempt itself after UI_DEADLINE_MS. */
export const UI_DEADLINE_MESSAGE = 'Please try again.';

export const SCENARIO_LABEL: Record<Scenario, string> = {
  unlabeled: 'Unlabeled',
  genuine: 'Genuine',
  wrong_identity: 'Wrong identity',
  print_photo: 'Printed photo',
  screen_photo: 'Screen photo',
  video_replay: 'Video replay',
  poor_light: 'Poor light',
};
