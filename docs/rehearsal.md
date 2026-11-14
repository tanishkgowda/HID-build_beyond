# Rehearsal protocol (Person C)

This is how we collect the attempts that `scripts/evaluate.py` turns into the numbers we show. Every attempt is labeled with its scenario in the kiosk, so an attack that got through stays visible in the results.

## 1. Demo laptop setup

Do this on the exact laptop and camera used for the demo. Rehearse on the Mac first if the Windows setup is still in progress.

| Check | Mac | Windows (PowerShell) |
| --- | --- | --- |
| Python 3.12 | `python3.12 --version` | `py -3.12 --version` |
| Node LTS | `node --version` | `node --version` |
| Fresh environment | `python3.12 -m venv .venv` | `py -3.12 -m venv .venv` |
| Dependencies | `.venv/bin/python -m pip install -r requirements.txt` | `.venv\Scripts\python -m pip install -r requirements.txt` |
| Models | `.venv/bin/python scripts/fetch_models.py` | `.venv\Scripts\python scripts\fetch_models.py` |
| Tests | `.venv/bin/python -m pytest -q` | `.venv\Scripts\python -m pytest -q` |
| Frontend build | `npm --prefix web install && npm --prefix web run build` | same |

- **Camera permission:** macOS System Settings → Privacy & Security → Camera → allow Terminal (or your IDE). Windows Settings → Privacy & security → Camera → allow desktop apps.
- **Camera selection:** close Zoom, Teams, the Camera app and browser tabs using the camera. If index 0 is the wrong camera (for example a laptop with an IR camera or an external webcam), try `device_index` 1 and 2 from the kiosk and note which one is right: index = ____.
- **Health:** open `http://127.0.0.1:8000/api/health` and confirm `models_loaded: true`.
- **Display:** check the kiosk layout at the demo laptop's resolution and with the screen brightness we will use on stage.

## 2. Keep tuning and final captures apart

Thresholds may change only on tuning captures. Final numbers come from a separate database collected after the last threshold change. Start the server with a different data folder for each:

```sh
# Mac
HID_DATA_DIR=data/tuning .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
HID_DATA_DIR=data/final  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Windows
$env:HID_DATA_DIR = "data\tuning"; .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
$env:HID_DATA_DIR = "data\final";  .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Participants are stored per database, so enroll again in each one. Record the thresholds in `app/policy.py` (or the commit hash) when the final run starts: ____.

## 3. Attack materials

Prepare these for the enrolled teammate (the "target") before the session:

| Scenario | Material | Notes |
| --- | --- | --- |
| `print_photo` | Colour print of a front-facing photo of the target, A4 and A5, matte and glossy if possible | Hold flat and also slightly curved. Try with and without cutting around the face |
| `screen_photo` | Same photo shown full-screen on a phone and on a tablet or second laptop | Brightness at maximum. Try with and without the screen tilted |
| `video_replay` | 10–20 s video of the target blinking and turning slightly, played on a phone and on a laptop | Loop the video. Record it on the day in similar light |
| `poor_light` | The target themselves (genuine) with the room lights dimmed or a strong backlight | Expected: ALLOW or REQUEST_VERIFICATION, never DENY |
| `wrong_identity` | A different, consenting person verifying against the target's participant ID | Expected: DENY or REQUEST_VERIFICATION, never ALLOW |
| `genuine` | The target in normal light | Expected: ALLOW |

3D and printed masks are out of scope.

## 4. Consent and data

- Ask every person who is enrolled or appears in an attack material for verbal consent, and write their first name in the table below. Explain that the kiosk stores a face template and scores, not images.
- Attack photos and videos are taken only of teammates who agreed.
- After the event: delete `data/` (all databases), the printed photos and the replay videos. Keep only `results/` (counts, scores and timings).

| Person | Role (target / other) | Consent given | Enrolled in tuning | Enrolled in final |
| --- | --- | --- | --- | --- |
| | | | | |
| | | | | |

## 5. Running a session

1. Start the server with the right `HID_DATA_DIR` and open the kiosk at `http://127.0.0.1:8000`.
2. Start the camera and enroll the target in normal light. If enrollment returns `spoof_detected`, improve the light and retry.
3. Pick one scenario. Do a block of attempts for that scenario, then move to the next. Between attempts, step out of frame and back in so frames are fresh.
4. **Label every attempt right after it appears** using the scenario dropdown on the outcome card. Mislabeled attempts make the results meaningless, so fix any mistake in the recent attempts table.
5. Note anything unusual (someone walked behind, the camera refocused) in the log below with the time.
6. Run `scripts/evaluate.py` after each block to check counts and spot surprises early.

Order for each round: genuine → wrong identity → print photo → screen photo → video replay → poor light.

## 6. Attempt counts

Target: at least 20 attempts per scenario in the final database. Fill in as you go, or read the counts from the evaluation report.

| Scenario | Tuning | Final | Target |
| --- | ---: | ---: | ---: |
| genuine | | | 20 |
| wrong_identity | | | 20 |
| print_photo | | | 20 |
| screen_photo | | | 20 |
| video_replay | | | 20 |
| poor_light | | | 20 |

## 7. Session log

| Time | Database | Scenario | What happened |
| --- | --- | --- | --- |
| | | | |

## 8. Reports

```sh
# Mac (Windows: .venv\Scripts\python and backslashes)
.venv/bin/python scripts/evaluate.py --db data/tuning/kiosk.sqlite --out results/tuning --sweep
.venv/bin/python scripts/evaluate.py --db data/final/kiosk.sqlite  --out results/final
```

- Use the `--sweep` tables on tuning captures to propose threshold changes to A. Do not sweep the final database.
- Every row in "Accepted attacks and wrong identities" needs an explanation in the demo.
- Use `--since 2026-11-14T09:00:00Z` (UTC) to report only one round.
