# Jev second verification step

Jev is TypeSafe's hosted decision model. It returns a typed answer with probabilities instead of text. We use it on one webcam crop to ask: live person, printed photo, screen or video replay, or unclear. Image input is an experimental ("danger zone") TypeSafe feature, so we measure it before relying on it.

## Where it fits

```
Verify ──► local pipeline (< 2 s) ──► ALLOW / DENY ──► done
                                  └─► REQUEST_VERIFICATION (uncertain_liveness, identity matched)
                                          └─► Jev check on a fresh frame ──► DENY / ask to recapture
```

- Jev runs only when the local result is `REQUEST_VERIFICATION` with reason `uncertain_liveness` and the identity score passed. It judges liveness, not identity.
- Jev runs after the initial local decision. The hosted request has its own timer.
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

## Run the second check in the kiosk

Start the backend with `TYPESAFE_API_KEY` set. The kiosk shows **Consent and run Jev** after a fresh local attempt with uncertain liveness and a matching identity. The button sends one crop to TypeSafe only after consent. The backend verifies the identity again and allows one check per attempt, within 60 seconds.

A strong spoof result can deny the second check. A result that looks live still asks for a recapture until the team measures how this model handles real attacks. The kiosk shows both timings, and the evaluator reports the second check separately from the original local decision.

The extension adds `POST /api/attempts/{attempt_id}/second_check` with `{"consent": true}`, `jev_enabled` in health, and a nullable `second_check` result on saved attempts. The result has the Jev label, live probability, resolved model, separate outcome and reason, total time, and TypeSafe request time. The original outcome and timing stay intact. The key is only read by the backend, and local verification remains available without it.

## Privacy

Face crops leave the laptop and go to TypeSafe's API. Tell participants before they take part, and only run the bench and the kiosk step with consenting teammates. TypeSafe states it does not use requests to train Jev. Keep the API key out of Git (`.env` is already ignored).
