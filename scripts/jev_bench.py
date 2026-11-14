"""Measure Jev as the second verification step, scenario by scenario (Person C).

Grabs a frame, crops around the face, asks Jev whether it is a live person or an attack,
and appends one row to results/jev/jev_attempts.csv. No images are saved: only labels,
probabilities and latency. Run from the project root with TYPESAFE_API_KEY set.

Frame sources:
  --source api       (default) the running kiosk's /api/preview; start the camera in the kiosk first
  --source camera:0  open the webcam directly (stop the kiosk camera first)
  --images DIR       every .jpg/.png in a folder, for offline checks

Examples:
  .venv/bin/python scripts/jev_bench.py --scenario genuine
  .venv/bin/python scripts/jev_bench.py --scenario print_photo --auto 20
  .venv/bin/python scripts/jev_bench.py --summary

Face images are sent to TypeSafe's hosted API. Only run this with consenting participants.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import sleep
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.jev import ATTACK_LABELS, LABELS, JevClient, face_crop_jpeg  # noqa: E402
from scripts.evaluate import ATTACKS, SCENARIOS, percentile  # noqa: E402

OUT = ROOT / "results" / "jev"
CSV_PATH = OUT / "jev_attempts.csv"
FIELDS = ["created_at", "scenario", "source", "face_issue", "local_liveness", "jev_label",
          *[f"p_{label}" for label in LABELS], "confidence", "jev_outcome", "latency_ms",
          "crop_bytes", "model", "error"]


# ---------------------------------------------------------------- frame sources

def frame_from_preview(base):
    import cv2
    import numpy as np

    with urlopen(base.rstrip("/") + "/api/preview", timeout=5) as stream:
        buffer = b""
        while True:
            chunk = stream.read(16384)
            if not chunk:
                raise RuntimeError("Preview stream ended. Is the kiosk camera running?")
            buffer += chunk
            start = buffer.find(b"\xff\xd8")
            end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if end >= 0:
                data = np.frombuffer(buffer[start:end + 2], dtype=np.uint8)
                return cv2.imdecode(data, cv2.IMREAD_COLOR)


class DirectCamera:
    def __init__(self, index):
        import cv2
        self.capture = cv2.VideoCapture(index)
        if not self.capture.isOpened():
            raise RuntimeError(f"Could not open camera {index}. Close other camera apps.")

    def frame(self):
        for _ in range(4):  # drop buffered frames so the capture is fresh
            self.capture.grab()
        ok, frame = self.capture.read()
        if not ok:
            raise RuntimeError("Camera read failed")
        return frame


# ---------------------------------------------------------------- one measurement

def detect(vision, frame):
    height, width = frame.shape[:2]
    vision.detector.setInputSize((width, height))
    _, faces = vision.detector.detect(frame)
    if faces is None or len(faces) == 0:
        return None, "no_face"
    if len(faces) > 1:
        return None, "multiple_faces"
    return faces[0], None


def measure(frame, scenario, source, vision, client):
    from app.vision import _liveness_input

    row = {"created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "scenario": scenario, "source": source}
    face, issue = detect(vision, frame)
    if issue:
        row["face_issue"] = issue
        return row
    row["local_liveness"] = f"{vision._live_score(_liveness_input(frame, face)):.4f}"
    jpeg = face_crop_jpeg(frame, face)
    row["crop_bytes"] = len(jpeg)
    result = client.check(jpeg)
    row.update({"jev_label": result.label, "jev_outcome": result.outcome(), "latency_ms": result.latency_ms,
                "model": result.model, "error": result.error,
                "confidence": None if result.confidence is None else f"{result.confidence:.4f}"})
    for label in LABELS:
        value = result.probabilities.get(label)
        row[f"p_{label}"] = None if value is None else f"{value:.4f}"
    return row


def append(row):
    OUT.mkdir(parents=True, exist_ok=True)
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def show(row):
    if row.get("face_issue"):
        print(f"  {row['face_issue']}: not sent to Jev")
    elif row.get("error"):
        print(f"  Jev error after {row['latency_ms']} ms: {row['error']}")
    else:
        print(f"  Jev: {row['jev_label']} (live {row['p_live_person']}) → {row['jev_outcome']}, "
              f"{row['latency_ms']} ms | local liveness {row['local_liveness']}")


# ---------------------------------------------------------------- summary

def summarize(path=CSV_PATH):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    lines = ["# Jev second-step benchmark\n",
             f"Source: `{path}`, {len(rows)} rows. Probabilities and timings only, no images.\n",
             "| Scenario | Sent | Errors | Jev ALLOW | Jev REQ. VERIF. | Jev DENY | Said live | Said attack "
             "| Latency median / p95 / max (ms) |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    latencies_all = []
    for scenario in SCENARIOS:
        group = [r for r in rows if r["scenario"] == scenario and not r["face_issue"]]
        if not group:
            continue
        ok = [r for r in group if not r["error"]]
        lat = [int(r["latency_ms"]) for r in group if r["latency_ms"]]
        latencies_all += lat
        outcomes = [r["jev_outcome"] for r in ok]
        live = sum(r["jev_label"] == "live_person" for r in ok)
        attack = sum(r["jev_label"] in ATTACK_LABELS for r in ok)
        mark = " (attack)" if scenario in ATTACKS else ""
        lines.append(
            f"| {scenario}{mark} | {len(group)} | {len(group) - len(ok)} | {outcomes.count('ALLOW')} "
            f"| {outcomes.count('REQUEST_VERIFICATION')} | {outcomes.count('DENY')} | {live} | {attack} "
            f"| {median(lat) if lat else '—'} / {percentile(lat, 95) or '—'} / {max(lat) if lat else '—'} |")
    skipped = sum(bool(r["face_issue"]) for r in rows)
    lines.append(f"\nFrames without exactly one face (not sent): {skipped}.")
    if latencies_all:
        lines.append(f"All requests: median {median(latencies_all)} ms, p95 {percentile(latencies_all, 95)} ms, "
                     f"max {max(latencies_all)} ms.")
    lines.append("\nJev ALLOW on an attack row is an accepted attack. Jev DENY on genuine or poor_light rows "
                 "is a wrongly rejected person.")
    text = "\n".join(lines)
    print(text)
    (OUT / "summary.md").write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------- main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles may not default to UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--source", default="api", help="api (default) or camera:<index>")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="Kiosk address for --source api")
    parser.add_argument("--images", type=Path, help="Folder of .jpg/.png frames instead of a live source")
    parser.add_argument("--auto", type=int, metavar="N", help="Capture N frames automatically, 1.5 s apart")
    parser.add_argument("--summary", action="store_true", help="Summarize results/jev/jev_attempts.csv and exit")
    args = parser.parse_args(argv)

    if args.summary:
        return summarize()
    if not args.scenario:
        parser.error("--scenario is required unless --summary is given")

    from app.vision import Vision
    vision = Vision(ROOT / "models")
    client = JevClient()
    print(f"Jev model {client.model} at {client.url}")

    if args.images:
        files = sorted(p for p in args.images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        import cv2
        for path in files:
            print(path.name)
            row = measure(cv2.imread(str(path)), args.scenario, f"file:{path.name}", vision, client)
            append(row)
            show(row)
        return summarize()

    if args.source.startswith("camera:"):
        camera = DirectCamera(int(args.source.split(":", 1)[1]))
        grab, label = camera.frame, args.source
    else:
        grab, label = (lambda: frame_from_preview(args.api)), "kiosk_preview"

    count = 0
    while True:
        if args.auto:
            if count >= args.auto:
                break
            if count:
                sleep(1.5)
        elif input(f"[{args.scenario} #{count + 1}] Enter to capture, q to stop: ").strip().lower() == "q":
            break
        row = measure(grab(), args.scenario, label, vision, client)
        append(row)
        show(row)
        count += 1
    summarize()


if __name__ == "__main__":
    main()
