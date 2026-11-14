from dataclasses import dataclass
from math import exp, isfinite
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
import onnxruntime as ort

from .policy import FRAME_COUNT
from .schemas import ModelVersions

# Leave time for the existing face detector and three identity/liveness checks.
BLINK_CAPTURE_SECONDS = 1.30
BLINK_INFERENCE_RESERVE_SECONDS = 0.25
EYES_OPEN_MAX = 0.24
EYES_CLOSED_MIN = 0.52


class BlinkEvidence:
    def __init__(self):
        self.saw_open = False
        self.saw_closed = False

    def update(self, eyes):
        if eyes is None:
            self.saw_open = self.saw_closed = False
            return False
        if max(eyes) <= EYES_OPEN_MAX:
            confirmed = self.saw_open and self.saw_closed
            self.saw_open = True
            return confirmed
        if self.saw_open and min(eyes) >= EYES_CLOSED_MIN:
            self.saw_closed = True
        return False


MODEL_VERSIONS = ModelVersions(
    detector="YuNet 2023mar",
    recognizer="SFace 2021dec",
    liveness="Facenox MiniFASNetV2-SE 98.20 FP32",
)

MIN_FACE_PIXELS = 80
MIN_VISIBLE_FACE_FRACTION = 0.9
MIN_BRIGHTNESS = 45
MAX_BRIGHTNESS = 225
MIN_BLUR_VARIANCE = 35


@dataclass
class FrameAnalysis:
    face_found: bool
    issue: str | None = None
    liveness_score: float | None = None
    embedding: np.ndarray | None = None
    face_box: np.ndarray | None = None


def unit_embedding(vector):
    norm = float(np.linalg.norm(vector))
    if not isfinite(norm) or norm <= 1e-8:
        return None
    return vector / norm


def _liveness_input(frame, box):
    """Match Facenox's square 1.5x crop, reflected padding and RGB scaling."""
    x, y, w, h = box[:4]
    side = int(1.5 * max(w, h))
    left = int(x + w / 2 - 1.5 * max(w, h) / 2)
    top = int(y + h / 2 - 1.5 * max(w, h) / 2)
    right, bottom = left + side, top + side
    height, width = frame.shape[:2]
    crop = frame[max(0, top):min(bottom, height), max(0, left):min(right, width)]
    crop = cv2.copyMakeBorder(
        crop, max(0, -top), max(0, bottom - height),
        max(0, -left), max(0, right - width), cv2.BORDER_REFLECT_101,
    )
    interpolation = cv2.INTER_LANCZOS4 if side < 128 else cv2.INTER_AREA
    resized = cv2.resize(crop, (128, 128), interpolation=interpolation)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0


def _quality_ok(frame, face):
    x, y, w, h = face[:4]
    if min(w, h) < MIN_FACE_PIXELS:
        return False
    height, width = frame.shape[:2]
    crop = frame[max(0, int(y)):min(height, int(y + h)), max(0, int(x)):min(width, int(x + w))]
    if crop.size == 0 or crop.shape[0] * crop.shape[1] < w * h * MIN_VISIBLE_FACE_FRACTION:
        return False
    gray = cv2.cvtColor(cv2.resize(crop, (112, 112), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return (
        MIN_BRIGHTNESS <= gray.mean() <= MAX_BRIGHTNESS
        and cv2.Laplacian(gray, cv2.CV_32F).var() >= MIN_BLUR_VARIANCE
    )


class Vision:
    def __init__(self, model_dir: Path):
        for name in ("yunet.onnx", "sface.onnx", "minifasnet.onnx", "face_landmarker.task"):
            if not (model_dir / name).is_file():
                raise FileNotFoundError(model_dir / name)

        import mediapipe as mp

        self.mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / "face_landmarker.task")),
            num_faces=1,
            output_face_blendshapes=True,
        )
        self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

        cv2.setNumThreads(2)
        self.detector = cv2.FaceDetectorYN.create(str(model_dir / "yunet.onnx"), "", (640, 480))
        self.recognizer = cv2.FaceRecognizerSF.create(str(model_dir / "sface.onnx"), "")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.liveness = ort.InferenceSession(
            str(model_dir / "minifasnet.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.liveness_input = self.liveness.get_inputs()[0].name

        # Initialize the native execution paths before the first timed attempt.
        self.detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
        self.recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8))
        self._live_score(np.zeros((1, 3, 128, 128), dtype=np.float32))
        self._eye_scores(np.zeros((240, 320, 3), dtype=np.uint8))

    def _eye_scores(self, frame):
        height, width = frame.shape[:2]
        if width > 480:
            frame = cv2.resize(frame, (480, round(height * 480 / width)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self.landmarker.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb))
        if not result.face_blendshapes:
            return None
        eyes = {cat.category_name: cat.score for cat in result.face_blendshapes[0]}
        if "eyeBlinkLeft" not in eyes or "eyeBlinkRight" not in eyes:
            return None
        return eyes["eyeBlinkLeft"], eyes["eyeBlinkRight"]

    def capture_blink(self, camera, deadline):
        start = monotonic()
        end = min(start + BLINK_CAPTURE_SECONDS, deadline - BLINK_INFERENCE_RESERVE_SECONDS)
        evidence = BlinkEvidence()
        selected = []
        last_selected = 0.0
        blink_closed = None
        fallback = None
        for frame in camera.fresh_frames(end):
            fallback = frame
            now = monotonic()
            eyes = self._eye_scores(frame)
            confirmed = evidence.update(eyes)
            if eyes is None:
                selected.clear()
                last_selected = 0.0
                blink_closed = None
            elif max(eyes) <= EYES_OPEN_MAX:
                if confirmed and blink_closed is not None:
                    return [*blink_closed, frame], True
                if not selected or now - last_selected >= 0.15:
                    selected.append(frame)
                    last_selected = now
            elif evidence.saw_open and min(eyes) >= EYES_CLOSED_MIN and blink_closed is None:
                if selected:
                    blink_closed = (selected[-1], frame)

        step = max(1, len(selected) // FRAME_COUNT)
        # Keep a sample even if the landmarker failed, for normal capture guidance.
        frames = selected[::step][:FRAME_COUNT] or ([fallback] if fallback is not None else [])
        return frames, False


    def _live_score(self, tensor):
        # The pinned Facenox export uses index 0 for real and 1 for spoof.
        logits = self.liveness.run(None, {self.liveness_input: tensor})[0]
        if logits.shape != (1, 2) or not np.isfinite(logits).all():
            raise ValueError("Invalid MiniFASNet logits")
        margin = float(logits[0, 0]) - float(logits[0, 1])
        if margin >= 0:
            return 1 / (1 + exp(-margin))
        odds = exp(margin)
        return odds / (1 + odds)

    def analyze(self, frame, timings, check_deadline):
        check_deadline()
        with timings.measure("detect"):
            height, width = frame.shape[:2]
            self.detector.setInputSize((width, height))
            _, faces = self.detector.detect(frame)
            if faces is None or len(faces) == 0:
                return FrameAnalysis(False, "no_face")
            if len(faces) != 1:
                return FrameAnalysis(True, "multiple_faces")
            face = faces[0]
            if not np.isfinite(face).all() or not _quality_ok(frame, face):
                return FrameAnalysis(True, "poor_quality")

        check_deadline()
        with timings.measure("liveness"):
            liveness = self._live_score(_liveness_input(frame, face))
        check_deadline()
        with timings.measure("match"):
            aligned = self.recognizer.alignCrop(frame, face)
            embedding = unit_embedding(self.recognizer.feature(aligned).reshape(-1))
        check_deadline()
        return FrameAnalysis(True, liveness_score=liveness, embedding=embedding, face_box=face[:4])
