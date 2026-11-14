"""TypeSafe Jev as the second verification step (experimental).

Jev is a hosted typed-decision model. We send one webcam crop and ask a single choice
question: is this a live person, a printed photo, or a screen (photo or video replay)?
Image input is a TypeSafe "danger zone" feature, so results are measured before use and
Jev can only resolve REQUEST_VERIFICATION. It never overrides a local DENY.

Standard library only, so it adds no dependency. Configure with environment variables:
  TYPESAFE_API_KEY   required
  JEV_MODEL          default jev-latest (pin a version, e.g. jev-1.14.0, once measured)
  JEV_URL            default https://api.typesafe.ai/v1/systemone
  JEV_TIMEOUT_MS     default 1500
"""
import base64
import json
import os
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
QUESTION_ID = "presentation"
LIVE = "live_person"
ATTACK_LABELS = ("printed_photo", "screen_or_video")
LABELS = (LIVE, *ATTACK_LABELS, "unclear")

# Experimental. C measures these on tuning captures, like the local thresholds.
JEV_LIVE_PASS = 0.85
JEV_ATTACK_DENY = 0.85

QUESTION = {
    "type": "choice",
    "instructions": (
        "`photo` is a frame from an entry kiosk webcam, cropped around a face. Decide whether "
        "the camera is seeing a real person who is physically present, or a presentation attack "
        "held up to the camera."
    ),
    "criteria": {
        LIVE: "A real, physically present human face: natural skin texture and 3D depth, lighting "
              "consistent with the room, no paper or device edges around the face.",
        "printed_photo": "A face printed on paper or card: paper edges or curl, flat texture, print "
                         "dots, glare on the paper, or fingers holding the print.",
        "screen_or_video": "A face shown on a phone, tablet or monitor, still or playing a video: "
                           "device bezel, moire or pixel patterns, screen glare or reflections, "
                           "unnatural brightness.",
        "unclear": "Too dark, blurry, cropped or ambiguous to judge.",
    },
}


@dataclass
class JevResult:
    label: str | None = None
    probabilities: dict = field(default_factory=dict)
    confidence: float | None = None
    latency_ms: int = 0
    error: str | None = None
    model: str | None = None

    @property
    def live_probability(self):
        return self.probabilities.get(LIVE)

    @property
    def attack_probability(self):
        if not self.probabilities:
            return None
        return sum(self.probabilities.get(label, 0.0) for label in ATTACK_LABELS)

    def outcome(self, live_pass=JEV_LIVE_PASS, attack_deny=JEV_ATTACK_DENY):
        """ALLOW / DENY / REQUEST_VERIFICATION from Jev alone. None if Jev failed."""
        if self.error or not self.probabilities:
            return None
        if (self.live_probability or 0) >= live_pass:
            return "ALLOW"
        if (self.attack_probability or 0) >= attack_deny:
            return "DENY"
        return "REQUEST_VERIFICATION"


def build_request(jpeg_bytes, model):
    return {
        "model": model,
        "state": {"photo": "kiosk-frame"},
        "assets": {"kiosk-frame": {
            "type": "image", "format": "jpeg",
            "data_base64": base64.b64encode(jpeg_bytes).decode("ascii"),
        }},
        "questions": {QUESTION_ID: QUESTION},
    }


def parse_response(payload):
    answer = payload["answers"][QUESTION_ID]
    probabilities = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if any(key not in LABELS or not isfinite(value) or not 0 <= value <= 1
           for key, value in probabilities.items()) or sum(probabilities.values()) > 1.01:
        raise ValueError("Invalid Jev choice probabilities")
    label = answer.get("choice")
    if label not in LABELS:
        raise ValueError(f"Unexpected Jev choice: {label!r}")
    return JevResult(label=label, probabilities=probabilities, confidence=answer.get("confidence"),
                     model=payload.get("model"))


class JevClient:
    def __init__(self, api_key=None, model=None, url=None, timeout_ms=None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.model = model or os.environ.get("JEV_MODEL", DEFAULT_MODEL)
        self.url = url or os.environ.get("JEV_URL", DEFAULT_URL)
        self.timeout = int(timeout_ms if timeout_ms is not None else os.environ.get("JEV_TIMEOUT_MS", 1500)) / 1000
        if self.timeout <= 0:
            raise ValueError("Jev timeout must be positive.")
        if not self.api_key:
            raise RuntimeError("Set TYPESAFE_API_KEY to use Jev.")

    def check(self, jpeg_bytes):
        """Never raises for network or API problems: the error is returned in the result."""
        body = json.dumps(build_request(jpeg_bytes, self.model)).encode()
        request = Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "TypeSafe-Danger-Zone-Images": "true",
        })
        started = monotonic()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = parse_response(json.load(response))
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            result = JevResult(error=f"http_{exc.code}: {detail}")
        except (URLError, TimeoutError, OSError) as exc:
            result = JevResult(error=f"network: {getattr(exc, 'reason', exc)}")
        except (KeyError, ValueError, TypeError) as exc:
            result = JevResult(error=f"bad_response: {exc}")
        result.latency_ms = round((monotonic() - started) * 1000)
        return result


def face_crop_jpeg(frame, box, scale=2.5, max_side=384, quality=85):
    """Wide square crop around a YuNet box, so paper and screen edges stay in view."""
    import cv2  # Imported here so the client works without OpenCV.

    x, y, w, h = (float(v) for v in box[:4])
    side = scale * max(w, h)
    cx, cy = x + w / 2, y + h / 2
    height, width = frame.shape[:2]
    left, top = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    right, bottom = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    crop = frame[top:bottom, left:right]
    longest = max(crop.shape[:2])
    if longest > max_side:
        factor = max_side / longest
        crop = cv2.resize(crop, (round(crop.shape[1] * factor), round(crop.shape[0] * factor)),
                          interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Could not encode the crop")
    return jpeg.tobytes()
