# Kiosk API format

All three teammates build against this file. If anything here needs to change, all three must agree first, then update `docs/api.md` on `main`.

- Base URL: `http://127.0.0.1:8000`. Every endpoint starts with `/api`. FastAPI serves the built frontend at `/`.
- During development, Vite runs on port `5173` and proxies `/api` to port `8000`.
- Times are whole milliseconds (`_ms`). Timestamps are ISO 8601 in UTC.
- `liveness_score` is between 0 and 1, where 1 means a real person. `match_score` is SFace cosine similarity, where higher means more alike.
- Errors use this shape: `{"error": {"code": "<reason code>", "message": "<text for people>"}}`, with an HTTP status of 4xx or 5xx.

## Fixed values (use these spellings exactly)

| Field | Allowed values |
| --- | --- |
| `outcome` | `ALLOW`, `REQUEST_VERIFICATION`, `DENY` |
| `reason` | `live_match`, `spoof_detected`, `identity_mismatch`, `no_face`, `multiple_faces`, `poor_quality`, `uncertain_liveness`, `uncertain_match`, `deadline_exceeded`, `not_enrolled`, `camera_unavailable`, `blink_required` |
| `scenario` | `unlabeled`, `genuine`, `wrong_identity`, `print_photo`, `screen_photo`, `video_replay`, `poor_light` |

## Endpoints

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /api/health` | — | `{models_loaded, camera_running, model_versions: {detector, recognizer, liveness}}` |
| `POST /api/camera/start` | `{device_index?: number}` | `{running: true, device_index}` |
| `POST /api/camera/stop` | — | `{running: false}` |
| `GET /api/preview` | — | MJPEG stream. Returns `409 camera_unavailable` if the camera is not running |
| `GET /api/participants` | — | `[{participant_id, name, enrolled_at}]` |
| `POST /api/enroll` | `{name, participant_id?}` | `{participant_id, name, frames_used, liveness_ok}`. Returns `422 spoof_detected` if the enrollment frames fail liveness |
| `POST /api/verify` | `{participant_id}` | Attempt object (below) |
| `GET /api/attempts?limit=50` | — | `[attempt object]`, newest first |
| `POST /api/attempts/{attempt_id}/scenario` | `{scenario}` | `{attempt_id, scenario}` |
| `POST /api/attempts/{attempt_id}/client_timing` | `{click_to_render_ms, ui_deadline_missed}` | `{attempt_id}` |

## Attempt object

```json
{
  "attempt_id": "uuid",
  "participant_id": "uuid",
  "created_at": "2026-11-14T07:00:00Z",
  "outcome": "ALLOW",
  "reason": "live_match",
  "liveness_score": 0.97,
  "match_score": 0.62,
  "frames": [
    {"index": 0, "face_found": true, "quality_ok": true, "liveness_score": 0.98, "match_score": 0.61}
  ],
  "stage_ms": {"capture": 410, "detect": 60, "liveness": 90, "match": 40, "policy": 1},
  "elapsed_ms": 620,
  "deadline_missed": false,
  "scenario": "unlabeled",
  "click_to_render_ms": null,
  "ui_deadline_missed": null
}
```

## Three-frame rule

- Every frame must contain exactly one face of usable quality. If any frame doesn't, the outcome is `REQUEST_VERIFICATION` with reason `no_face`, `multiple_faces` or `poor_quality`.
- If any single frame has a definitive spoof score, the outcome is `DENY` with reason `spoof_detected`.
- Liveness passes only if all three frames pass. The attempt's `liveness_score` is the lowest frame score.
- The attempt's `match_score` is the mean of the three frame scores against the enrolled template.

## Blink check

Verification asks for one blink as soon as the check starts. A local Allow also requires an open → closed → open eye sequence on fresh frames. Missing blink evidence returns `REQUEST_VERIFICATION` with `blink_required` when passive liveness would otherwise pass or be uncertain. Jev is offered only after a blink when the local model is unsure about liveness and identity matches.

## Deadlines

- The server deadline is `ATTEMPT_DEADLINE_MS = 1800`, set in `app/main.py`. If evidence is incomplete by then, the server returns `REQUEST_VERIFICATION` with reason `deadline_exceeded` and `deadline_missed: true`.
- The UI deadline is `UI_DEADLINE_MS = 2000`, set in `web/src/api.ts`, measured from the Verify click. If the response hasn't arrived by then, the UI shows `REQUEST_VERIFICATION`, ignores the late response, and reports `ui_deadline_missed: true`.
