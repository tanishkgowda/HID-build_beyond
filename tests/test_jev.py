import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from app import jev


def answer(choice, probabilities):
    return {"model": "jev-1.14.0", "answers": {jev.QUESTION_ID: {
        "type": "choice", "choice": choice, "probabilities": probabilities, "confidence": 0.9,
    }, jev.OBJECT_QUESTION_ID: {"type": "noul", "noul": .04}},
        "usage": {"input_tokens": 300, "output_tokens": 1}}


@pytest.fixture
def server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["headers"] = self.headers  # case-insensitive lookup
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, payload = seen.get("reply", (200, answer("live_person", {"live_person": 0.95})))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1/systemone", seen
    httpd.shutdown()


def test_request_sends_image_with_opt_in_header(server):
    url, seen = server
    result = jev.JevClient(api_key="k", model="jev-1.14.0", url=url).check(b"\xff\xd8jpeg")
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["headers"]["TypeSafe-Danger-Zone-Images"] == "true"
    body = seen["body"]
    assert body["model"] == "jev-1.14.0"
    asset = body["assets"][body["state"]["photo"]]
    assert asset["format"] == "jpeg" and base64.b64decode(asset["data_base64"]) == b"\xff\xd8jpeg"
    assert set(body["questions"][jev.QUESTION_ID]["criteria"]) == set(jev.LABELS)
    assert result.label == "live_person" and result.outcome() == "ALLOW" and result.latency_ms >= 0


def test_api_errors_are_returned_not_raised(server):
    url, seen = server
    seen["reply"] = (429, {"error": "rate limited"})
    result = jev.JevClient(api_key="k", url=url).check(b"x")
    assert result.error.startswith("http_429") and result.outcome() is None


def test_unreachable_api_is_an_error():
    result = jev.JevClient(api_key="k", url="http://127.0.0.1:9/v1/systemone", timeout_ms=300).check(b"x")
    assert result.error.startswith("network") and result.outcome() is None


def test_unexpected_choice_is_rejected():
    with pytest.raises(ValueError):
        jev.parse_response(answer("maybe", {}))


@pytest.mark.parametrize("probabilities, expected", [
    ({"live_person": 0.9, "printed_photo": 0.05, "screen_or_video": 0.05}, "ALLOW"),
    ({"live_person": 0.05, "printed_photo": 0.5, "screen_or_video": 0.45}, "DENY"),
    ({"live_person": 0.5, "printed_photo": 0.2, "screen_or_video": 0.1, "unclear": 0.2}, "REQUEST_VERIFICATION"),
])
def test_outcome_thresholds(probabilities, expected):
    result = jev.parse_response(answer(max(probabilities, key=probabilities.get), probabilities))
    assert result.outcome() == expected


def test_missing_key_is_reported(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        jev.JevClient()


def test_scene_jpeg_preserves_the_full_frame():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    data = jev.scene_jpeg(frame, max_side=320)
    crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert data[:2] == b"\xff\xd8" and crop.shape[:2] == (240, 320)


def test_visible_phone_or_paper_overrides_a_high_live_score():
    payload = answer("live_person", {"live_person": .98})
    payload["answers"]["visible_phone_or_paper"] = {"type": "noul", "noul": .91}
    assert jev.parse_response(payload).outcome() == "DENY"
