import json
import random

import numpy as np

from app.policy import decide_frames
from app.schemas import Attempt, FrameEvidence, Participant, StageTimes
from app.store import Store
from scripts import evaluate


def frames(live, match):
    return [FrameEvidence(index=i, face_found=True, quality_ok=True, liveness_score=live, match_score=match)
            for i in range(3)]


def seed(path):
    store = Store(path)
    store.enroll(Participant(participant_id="p1", name="Asha", enrolled_at="2026-11-14T08:00:00Z"),
                 np.ones(128, dtype=np.float32) / np.sqrt(128))
    cases = [
        ("genuine", "ALLOW", "live_match", frames(0.97, 0.62), 600, False, 700, False),
        ("genuine", "REQUEST_VERIFICATION", "poor_quality", [], 900, False, 1000, False),
        ("print_photo", "DENY", "spoof_detected", frames(0.05, 0.6), 650, False, 760, False),
        ("screen_photo", "ALLOW", "live_match", frames(0.95, 0.58), 640, False, 730, False),
        ("video_replay", "REQUEST_VERIFICATION", "deadline_exceeded", [], 1800, True, 2100, True),
        ("wrong_identity", "DENY", "identity_mismatch", frames(0.96, 0.1), 610, False, None, None),
        ("unlabeled", "ALLOW", "live_match", frames(0.97, 0.7), 600, False, None, None),
    ]
    for i, (scenario, outcome, reason, fr, elapsed, missed, render, ui_missed) in enumerate(cases):
        live = min((f.liveness_score for f in fr), default=None)
        match = sum(f.match_score for f in fr) / 3 if fr else None
        store.save_attempt(Attempt(
            attempt_id=f"a{i}", participant_id="p1", created_at=f"2026-11-14T09:00:{i:02d}Z",
            outcome=outcome, reason=reason, liveness_score=live, match_score=match, frames=fr,
            stage_ms=StageTimes(capture=400, detect=60, liveness=90, match=40, policy=1),
            elapsed_ms=elapsed, deadline_missed=missed, scenario=scenario,
            click_to_render_ms=render, ui_deadline_missed=ui_missed,
        ))


def test_report_counts_by_scenario(tmp_path):
    db = tmp_path / "kiosk.sqlite"
    seed(db)
    out = tmp_path / "results"
    evaluate.main(["--db", str(db), "--out", str(out), "--sweep"])
    report = json.loads((out / "metrics.json").read_text())

    assert report["total_attempts"] == 7 and report["unlabeled"] == 1
    assert report["all_attacks"]["n"] == 3
    assert report["all_attacks"]["outcomes"]["ALLOW"] == 1
    assert [a["scenario"] for a in report["accepted_attacks"]] == ["screen_photo"]
    assert report["scenarios"]["genuine"]["capture_retries"] == 1
    assert report["all_labeled"]["server_deadline_missed"] == 1
    assert report["all_labeled"]["ui_deadline_missed"] == 1
    assert report["all_labeled"]["within_ui_deadline"] == 4
    summary = (out / "summary.md").read_text()
    assert "1 attempt(s) are unlabeled" in summary and "Threshold sweep" in summary
    assert (out / "attempts.csv").read_text().count("\n") == 8


def test_percentile_is_nearest_rank():
    assert evaluate.percentile(list(range(1, 21)), 95) == 19
    assert evaluate.percentile([5], 95) == 5
    assert evaluate.percentile([], 95) is None


def test_redecide_matches_backend_policy():
    rng = random.Random(7)
    for _ in range(2000):
        pairs = [(rng.random(), rng.uniform(-1, 1)) for _ in range(3)]
        expected, _, _ = decide_frames(pairs)
        fr = [{"face_found": True, "quality_ok": True, "liveness_score": l, "match_score": m} for l, m in pairs]
        assert evaluate.redecide(fr, evaluate.DEFAULT_THRESHOLDS) == expected.outcome
