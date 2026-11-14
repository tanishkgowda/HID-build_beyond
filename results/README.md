# Results

`scripts/evaluate.py` writes one folder per run: `summary.md` (the report), `metrics.json` (the same numbers for scripts) and `attempts.csv` (one row per attempt with per-frame scores).

- `results/tuning/` — tuning captures. Threshold sweeps live here.
- `results/final/` — final evaluation captures, collected after the last threshold change. These are the numbers we present.
- `results/latest/` — default output when `--out` is not given.

These files hold scores and timings only, no images, so they can be committed and kept after the event. Participant names appear in `attempts.csv` and the accepted-attacks table. Remove them before sharing outside the team.

## Mentor update template

> **Status:** live kiosk runs locally on the demo laptop with ALLOW / REQUEST VERIFICATION / DENY.
> **Attacks:** print ___/___, screen ___/___, replay ___/___ accepted. Wrong identity ___/___ accepted.
> **Genuine users:** ___% allowed first time, ___% denied, ___% asked to recapture.
> **Speed:** click to result median ___ ms, p95 ___ ms. ___ UI and ___ server deadline misses out of ___ attempts.
> **Next:** ___ (Jev / AWS Face Liveness / flash challenge).
> **Help needed:** ___
