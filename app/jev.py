"""TypeSafe Jev as the second verification step (experimental).

Jev is a hosted typed-decision model. The full camera frame supports both a
presentation classification and an independent phone or paper check.

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
OBJECT_QUESTION_ID = "visible_phone_or_paper"
OBJECT_DENY = 0.25
OBJECT_CLEAR_MAX = 0.15
LIVE = "live_person"
ATTACK_LABELS = ("printed_photo", "screen_or_video")
LABELS = (LIVE, *ATTACK_LABELS, "unclear")

# Experimental. C measures these on tuning captures, like the local thresholds.
JEV_LIVE_PASS = 0.85
JEV_ATTACK_DENY = 0.85

QUESTION = {
    "type": "choice",
    "instructions": (
        "The image is a full frame from an entry kiosk webcam. Decide whether "
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


OBJECT_QUESTION = {
    "type": "noul",
    "instructions": (
        "Inspect the entire camera image, including the edges and anything in a hand. "
        "Is an actual mobile phone or loose sheet of paper visibly present anywhere? "
        "Count phones with a lit, blank or rear-facing side, even partially visible or held "
        "by a live person, and loose paper, prints or paper photo cutouts, whether blank or "
        "printed. Count the object independently of whether the person looks live. "
        "Do not infer an object from a hand alone or treat the image's own border as an object."
    ),
    "criteria": {
        "true": "A visible phone or piece of paper anywhere in the frame, including in a hand.",
        "false": "No visible phone or loose paper in the camera scene.",
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
    object_probability: float | None = None

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
        if self.error:
            return None
        if self.object_probability is not None and self.object_probability >= OBJECT_DENY:
            return "DENY"
        if (self.attack_probability or 0) >= attack_deny:
            return "DENY"
        if (
            self.object_probability is not None
            and self.object_probability <= OBJECT_CLEAR_MAX
            and (self.live_probability or 0) >= live_pass
        ):
            return "ALLOW"
        return "REQUEST_VERIFICATION"


def build_request(jpeg_bytes, model):
    return {
        "model": model,
        "state": {"photo": "kiosk-frame"},
        "assets": {"kiosk-frame": {
            "type": "image", "format": "jpeg",
            "data_base64": base64.b64encode(jpeg_bytes).decode("ascii"),
        }},
        "questions": {OBJECT_QUESTION_ID: OBJECT_QUESTION, QUESTION_ID: QUESTION},
    }


def parse_response(payload):
    answers = payload["answers"]
    object_answer = answers[OBJECT_QUESTION_ID]
    object_probability = float(object_answer["noul"])
    if object_answer.get("type") != "noul" or not 0 <= object_probability <= 1:
        raise ValueError("Invalid Jev phone or paper probability")
    answer = answers[QUESTION_ID]
    probabilities = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if any(key not in LABELS or not isfinite(value) or not 0 <= value <= 1
           for key, value in probabilities.items()) or sum(probabilities.values()) > 1.01:
        raise ValueError("Invalid Jev choice probabilities")
    label = answer.get("choice")
    if label not in LABELS:
        raise ValueError(f"Unexpected Jev choice: {label!r}")
    return JevResult(label=label, probabilities=probabilities, confidence=answer.get("confidence"),
                     model=payload.get("model"), object_probability=object_probability)


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


def scene_jpeg(frame, max_side=640, quality=88):
    """Encode the full frame so a phone or a sheet of paper stays in view."""
    import cv2

    height, width = frame.shape[:2]
    longest = max(height, width)
    if longest > max_side:
        scale = max_side / longest
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Could not encode the camera frame")
    return jpeg.tobytes()
