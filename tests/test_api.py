from time import sleep

import numpy as np
from fastapi.testclient import TestClient

from app import main
from app.main import create_app
from app.vision import FrameAnalysis

PARTICIPANT_ID = "84770d45-f072-4889-a899-d627b37b10db"


def test_missing_models_and_stopped_camera(tmp_path):
    with TestClient(create_app(tmp_path, tmp_path / "models")) as client:
        health = client.get("/api/health").json()
        assert health["models_loaded"] is False
        assert health["camera_running"] is False
        assert set(health["model_versions"]) == {"detector", "recognizer", "liveness"}
        preview = client.get("/api/preview")
        assert preview.status_code == 409
        assert preview.json()["error"]["code"] == "camera_unavailable"
        assert client.get("/api/participants").json() == []
        assert client.get("/api/attempts").json() == []


def test_verifying_an_unenrolled_participant_has_contract_error(tmp_path):
    with TestClient(create_app(tmp_path, tmp_path / "models")) as client:
        response = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_enrolled"


def test_invalid_enrollment_and_cross_origin_mutation(tmp_path):
    with TestClient(create_app(tmp_path, tmp_path / "models")) as client:
        response = client.post("/api/enroll", json={"name": "   "})
        assert response.status_code == 422
        assert set(response.json()["error"]) == {"code", "message"}
        assert client.post("/api/camera/stop", headers={"Origin": "https://example.com"}).status_code == 403


def use_test_frames(monkeypatch):
    # Only the physical camera and neural nets are replaced. The API, policy and DB run normally.
    monkeypatch.setattr(main.Camera, "sample", lambda self, count, deadline: [None] * count)
    template = np.zeros(128, dtype=np.float32)
    template[0] = 1

    class TestVision:
        liveness = .98
        delay = 0
        match_index = 0
        blinked = True

        def capture_blink(self, camera, deadline):
            return camera.sample(3, deadline), self.blinked

        def analyze(self, frame, timings, check_deadline):
            sleep(self.delay)
            check_deadline()
            return FrameAnalysis(
                face_found=True, liveness_score=self.liveness, embedding=np.roll(template, self.match_index),
                face_box=np.array([0, 0, 100, 100]),
            )

    vision = TestVision()
    monkeypatch.setattr(main, "Vision", lambda model_dir: vision)
    return vision


def test_enrollment_verify_and_rehearsal_metadata_persist(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    use_test_frames(monkeypatch)
    with TestClient(app) as client:
        enrollment = client.post("/api/enroll", json={"name": "Teammate", "participant_id": PARTICIPANT_ID})
        assert enrollment.json() == {
            "participant_id": PARTICIPANT_ID, "name": "Teammate", "frames_used": 3, "liveness_ok": True
        }
        attempt = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID}).json()
        assert (attempt["outcome"], attempt["reason"]) == ("ALLOW", "live_match")
        assert len(attempt["frames"]) == 3
        assert set(attempt["stage_ms"]) == {"capture", "detect", "liveness", "match", "policy"}
        assert attempt["scenario"] == "unlabeled"
        url = f"/api/attempts/{attempt['attempt_id']}"
        assert client.post(url + "/scenario", json={"scenario": "genuine"}).status_code == 200
        assert client.post(url + "/client_timing", json={
            "click_to_render_ms": 670, "ui_deadline_missed": False,
        }).status_code == 200

    with TestClient(create_app(tmp_path, tmp_path / "missing")) as client:
        assert client.get("/api/participants").json()[0]["participant_id"] == PARTICIPANT_ID
        saved, = client.get("/api/attempts?limit=1").json()
        assert saved == {**attempt, "scenario": "genuine", "click_to_render_ms": 670, "ui_deadline_missed": False}


def test_failed_liveness_leaves_no_enrollment(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    vision = use_test_frames(monkeypatch)
    vision.liveness = .8
    with TestClient(app) as client:
        response = client.post("/api/enroll", json={"name": "Teammate"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "spoof_detected"
        assert client.get("/api/participants").json() == []


def test_deadline_returns_retry_and_never_saves_late_allow(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    vision = use_test_frames(monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/enroll", json={"name": "Teammate", "participant_id": PARTICIPANT_ID}).status_code == 200
        monkeypatch.setattr(main, "ATTEMPT_DEADLINE_MS", 50)
        vision.delay = .4
        response = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID})
        attempt = response.json()
        assert (attempt["outcome"], attempt["reason"]) == ("REQUEST_VERIFICATION", "deadline_exceeded")
        assert attempt["deadline_missed"] is True
        assert attempt["elapsed_ms"] < 350
        # The timed-out request must leave the model lock with the running worker.
        assert client.post("/api/camera/stop").status_code == 409
        sleep(.45)
        assert client.get("/api/attempts").json() == [attempt]


def test_jev_second_check_is_opt_in_matches_identity_and_runs_once(tmp_path, monkeypatch):
    from app.jev import JevResult

    app = create_app(tmp_path)
    vision = use_test_frames(monkeypatch)
    calls = []

    class Client:
        def check(self, jpeg):
            calls.append(jpeg)
            return JevResult(label="live_person", probabilities={"live_person": .96}, latency_ms=700)

    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture")
    monkeypatch.setattr(main, "JevClient", Client, raising=False)
    monkeypatch.setattr(main, "face_crop_jpeg", lambda frame, box: b"jpeg", raising=False)

    with TestClient(app) as client:
        assert client.post("/api/enroll", json={"name": "Teammate", "participant_id": PARTICIPANT_ID}).status_code == 200
        first = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID}).json()
        url = f"/api/attempts/{first['attempt_id']}/second_check"
        assert client.post(url, json={"consent": True}).status_code == 409

        vision.liveness = .8
        uncertain = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID}).json()
        assert uncertain["reason"] == "uncertain_liveness"
        assert calls == []
        url = f"/api/attempts/{uncertain['attempt_id']}/second_check"
        assert client.post(url, json={"consent": False}).status_code == 422
        assert calls == []
        response = client.post(url, json={"consent": True})
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "REQUEST_VERIFICATION"
        assert response.json()["jev_label"] == "live_person"
        assert response.json()["jev_ms"] == 700
        assert client.post(url, json={"consent": True}).status_code == 409
        assert calls == [b"jpeg"]
        assert client.get("/api/attempts").json()[0]["reason"] == "uncertain_liveness"

        another = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID}).json()
        vision.match_index = 1
        failed = client.post(f"/api/attempts/{another['attempt_id']}/second_check", json={"consent": True})
        assert failed.json()["reason"] == "second_check_identity_unconfirmed"
        assert len(calls) == 1


def test_high_scores_without_a_blink_cannot_allow(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    vision = use_test_frames(monkeypatch)
    vision.blinked = False
    with TestClient(app) as client:
        assert client.post("/api/enroll", json={"name": "Teammate", "participant_id": PARTICIPANT_ID}).status_code == 200
        result = client.post("/api/verify", json={"participant_id": PARTICIPANT_ID}).json()
        assert result["liveness_score"] >= .90
        assert result["match_score"] >= .50
        assert result["outcome"] == "REQUEST_VERIFICATION"
        assert result["reason"] == "blink_required"


def test_blink_requires_both_eyes_to_close_and_reopen_on_valid_frames():
    from app.vision import BlinkEvidence

    tracker = BlinkEvidence()
    for scores in ((.04, .05), (.04, .05), (.8, .06), (.05, .04)):
        assert not tracker.update(scores)
    assert not tracker.update((.8, .9))
    assert not tracker.update(None)
    assert not tracker.update((.04, .05))
    assert not tracker.update((.8, .9))
    assert tracker.update((.04, .05))


def test_presence_describes_geometry_only_for_one_face(tmp_path, monkeypatch):
    vision = use_test_frames(monkeypatch)
    frame = np.zeros((480, 640, 3), np.uint8)
    monkeypatch.setattr(main.Camera, "latest_frame", lambda self: frame, raising=False)
    vision.detect_faces = lambda image: np.array([[160, 120, 120, 120]])
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/presence")
        assert response.status_code == 200
        assert response.json() == {
            "count": 1, "width": 640, "height": 480,
            "face": {"x": .25, "y": .25, "width": .1875, "height": .25},
            "ready": True,
        }
        vision.detect_faces = lambda image: np.array([[160, 120, 120, 120], [400, 100, 100, 100]])
        presence = client.get("/api/presence").json()
        assert presence["count"] == 2 and presence["face"] is None and not presence["ready"]
        assert client.get("/api/attempts").json() == []
