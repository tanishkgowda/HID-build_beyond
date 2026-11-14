# Jev phone and paper check

Jev receives one fresh full camera frame after participant consent. It checks for any visible phone or loose paper, even when a live person is holding the object or the screen is blank. This signal overrides the presentation classifier. An uncertain result asks for another capture.

The local phone detector can deny an attempt before Jev. When Jev runs, an object score of 0.25 or higher also denies the attempt.

## Start a check

Start the backend with `TYPESAFE_API_KEY` set. With a Jev key configured on the server, local passes always require a scene check. Use **Consent and run Jev** for the current attempt or, with participant consent, enable **Consent to automatic Jev checks** for a rehearsal. A local pass becomes `REQUEST_VERIFICATION` with reason `jev_required`, then the kiosk runs the scene check with consent. Local denials are final. The backend checks the selected identity again before sending the frame. A final Allow requires a local pass and a clear Jev result. An uncertain local liveness result can lead to a Deny or a recapture.

The initial local decision and the hosted result have separate timings. If TypeSafe is unavailable or times out, the kiosk keeps access pending. The evaluator lists Jev results separately from the initial local decisions.

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

## API and camera data

`POST /api/verify` accepts `require_jev: true`. An eligible attempt can call `POST /api/attempts/{attempt_id}/second_check` with `{"consent": true}` once, within 60 seconds. The result includes the phone or paper probability, the Jev model and label, outcome and reason, and timing. The database stores scores and timings.

Only run hosted checks with consenting teammates. The full frame, including background, goes to TypeSafe. Keep the key out of Git.
