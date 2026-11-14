# Jev second verification step

Jev is TypeSafe's hosted decision model. It returns a typed answer with probabilities instead of text. We use it on one webcam crop to ask: live person, printed photo, screen or video replay, or unclear. Image input is an experimental ("danger zone") TypeSafe feature, so we measure it before relying on it.

## Where it fits

```
Verify ──► local pipeline (< 2 s) ──► ALLOW / DENY ──► done
                                  └─► REQUEST_VERIFICATION (uncertain_liveness, identity matched)
                                          └─► Jev check on a fresh frame ──► ALLOW / DENY / ask to recapture
```

- Jev runs only when the local result is `REQUEST_VERIFICATION` with reason `uncertain_liveness` and the identity score passed. It judges liveness, not identity.
- Jev never overrides a local `DENY` and is never called on the fast path, so the two-second initial outcome is unaffected.
- If Jev errors, times out or answers `unclear`, the kiosk keeps asking for a recapture.
- Report the initial outcome time and the Jev step time separately, as for AWS Face Liveness.

## Step 1 — benchmark (C, Milestone 2)

1. Get an API key from the TypeSafe console and set it:
   - Mac: `export TYPESAFE_API_KEY=...`
   - Windows PowerShell: `$env:TYPESAFE_API_KEY = "..."`
2. Start the kiosk and its camera, so the bench can read `/api/preview`.
3. For each scenario, with consent:
   ```sh
   .venv/bin/python scripts/jev_bench.py --scenario genuine --auto 20
   .venv/bin/python scripts/jev_bench.py --scenario print_photo      # Enter for each capture
   .venv/bin/python scripts/jev_bench.py --summary
   ```
4. Results go to `results/jev/jev_attempts.csv` and `results/jev/summary.md`: Jev's label and probabilities, the local liveness score for the same frame, and request latency. No images are kept.
5. Decide with the team: adopt Jev if it denies attacks that the local model was unsure about, rarely denies genuine users, and its p95 latency is acceptable from the venue network. Tune `JEV_LIVE_PASS` and `JEV_ATTACK_DENY` in `app/jev.py` on tuning captures only, then pin `JEV_MODEL` to the version you measured.

Settings (environment variables): `TYPESAFE_API_KEY`, `JEV_MODEL` (default `jev-latest`), `JEV_URL` (default `https://api.typesafe.ai/v1/systemone`), `JEV_TIMEOUT_MS` (default 1500).

## Step 2 — kiosk integration (A and B, after C confirms latency)

This changes `docs/api.md`, so all three agree first. Proposed addition:

| Method and path | Request | Response |
| --- | --- | --- |
| `POST /api/attempts/{attempt_id}/second_check` | — | `{attempt_id, method: "jev", jev_label, jev_live_probability, outcome, reason, second_check_ms}` |

- **A:** In `app/main.py`, take one fresh frame under the camera lock, require exactly one face and an SFace match at or above `MATCH_PASS` against the same participant (so nobody swaps in after the first check), call `face_crop_jpeg` and `JevClient.check`, then map `JevResult.outcome()` to the final outcome. Add `second_check` columns to the attempts table so `evaluate.py` can report them. Allow only one second check per attempt.
- **New reason values to agree:** `second_check_passed` (ALLOW after Jev), `second_check_spoof` (DENY after Jev), `second_check_unavailable` (Jev error or timeout, stays REQUEST_VERIFICATION).
- **B:** When an attempt returns `REQUEST_VERIFICATION` with `uncertain_liveness`, show "Running an additional check…", call the endpoint, then show the final outcome and both times.

## Privacy

Face crops leave the laptop and go to TypeSafe's API. Tell participants before they take part, and only run the bench and the kiosk step with consenting teammates. TypeSafe states it does not use requests to train Jev. Keep the API key out of Git (`.env` is already ignored).
