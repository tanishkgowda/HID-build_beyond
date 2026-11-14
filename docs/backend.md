# Plan A backend handoff

Run these commands from the project root with Python 3.12. On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/fetch_models.py
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use [FastAPI's test page](http://127.0.0.1:8000/docs) to check health, start camera index 0, enroll a consenting teammate and verify with the returned `participant_id`. Stop the camera after testing. If the device is unavailable, check OS camera permission and close other camera apps. Frontend development uses the Vite proxy. For the demo, build the frontend before starting the server so it mounts `web/dist`.

Use [the shared contract](api.md) for API integration. A successful verification returns a saved attempt, including retries and deadline misses. Unknown participants return `404 not_enrolled`. When evidence is unavailable, scores are `null`. A server deadline can leave `frames` empty. The kiosk keeps the 1,800 ms server deadline separate from the 2,000 ms deadline measured from the click. If the browser times out first, it can ignore the late response for display and use its attempt ID to report client timing.

The default database is `data/kiosk.sqlite`. It stores participant records, normalized SFace templates and attempts. The `attempts` table has the same column names as the API object. `frames` and `stage_ms` are JSON text, and booleans are SQLite integers. The kiosk does not save raw camera frames. Use the API to label scenarios and report browser timings. `HID_DATA_DIR` selects a separate directory for a rehearsal database.

The checkpoints are pinned in [the model downloader](../scripts/fetch_models.py). YuNet uses five landmarks for SFace alignment. The liveness model uses a 1.5× square RGB crop with reflected padding at the edges and a 128×128 input scaled to 0–1. Its live score is the softmax of the class-0 logit against class 1. Liveness and identity thresholds keep their previous values of 0.90 / 0.20 and 0.50 / 0.30 for pass and deny respectively. These are starting thresholds for rehearsal.

The smoke test ran all three models through enrollment and verification with a public sample image fed in place of the webcam. The image scored as live, so count this only as an integration check. Use genuine, wrong-identity, printed-photo, screen-photo, video-replay and poor-light attempts on the selected demo camera to measure actual behavior.
