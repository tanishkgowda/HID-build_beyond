from dataclasses import dataclass
from math import exp, isfinite
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from .schemas import ModelVersions

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
        for name in ("yunet.onnx", "sface.onnx", "minifasnet.onnx"):
            if not (model_dir / name).is_file():
                raise FileNotFoundError(model_dir / name)

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
