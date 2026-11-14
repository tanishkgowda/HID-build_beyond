import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

import numpy as np

from .camera import Camera, CameraUnavailable
from .jev import JevClient, face_crop_jpeg
from .policy import FRAME_COUNT, LIVE_PASS, MATCH_PASS, SPOOF_DENY, decide_frames, valid_liveness, valid_match
from .schemas import (
    Attempt, AttemptReference, CameraStart, CameraStarted, CameraStopped,
    ClientTiming, Enrollment, EnrollmentRequest, FrameEvidence, Health, Participant,
    ScenarioUpdate, ScenarioUpdated, SecondCheck, SecondCheckRequest, StageTimes, VerificationRequest,
)
from .store import Store
from .vision import MODEL_VERSIONS, Vision, unit_embedding

ROOT = Path(__file__).resolve().parents[1]
ATTEMPT_DEADLINE_MS = 1800
SECOND_CHECK_WINDOW_SECONDS = 60
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
LOCAL_ORIGINS = {
    f"http://{host}:{port}"
    for host in ("127.0.0.1", "localhost", "[::1]")
    for port in (8000, 5173)
}
LOGGER = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def error_response(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        self.message = message


class Timings:
    def __init__(self):
        self._done = {}
        self._current = None
        self._lock = Lock()

    @contextmanager
    def measure(self, stage):
        start = monotonic()
        with self._lock:
            self._current = stage, start
        try:
            yield
        finally:
            with self._lock:
                self._done[stage] = self._done.get(stage, 0) + monotonic() - start
                self._current = None

    def snapshot(self):
        with self._lock:
            values = self._done.copy()
            if self._current is not None:
                stage, start = self._current
                values[stage] = values.get(stage, 0) + monotonic() - start
        return StageTimes(**{stage: round(duration * 1000) for stage, duration in values.items()})


def check_deadline(deadline):
    if monotonic() >= deadline:
        raise TimeoutError


def capture_and_analyze(camera, vision, deadline, timings):
    with timings.measure("capture"):
        frames = camera.sample(FRAME_COUNT, deadline)
    return [vision.analyze(frame, timings, lambda: check_deadline(deadline)) for frame in frames]


def verify_frames(camera, vision, template, deadline, timings):
    try:
        readings = capture_and_analyze(camera, vision, deadline, timings)
    except CameraUnavailable:
        with timings.measure("policy"):
            return (*decide_frames([], issue="camera_unavailable"), [])

    evidence = []
    issue = None
    with timings.measure("match"):
        for index, reading in enumerate(readings):
            issue = issue or reading.issue
            live = reading.liveness_score
            match = None
            if reading.embedding is not None:
                match = float(np.clip(np.dot(reading.embedding, template), -1, 1))
            evidence.append(FrameEvidence(
                index=index,
                face_found=reading.face_found,
                quality_ok=reading.issue is None,
                liveness_score=live if valid_liveness(live) else None,
                match_score=match if valid_match(match) else None,
            ))
    with timings.measure("policy"):
        decision, live, match = decide_frames(
            [(frame.liveness_score, frame.match_score) for frame in evidence], issue=issue,
        )
    check_deadline(deadline)
    return decision, live, match, evidence


def create_app(data_dir=None, model_dir=None, web_dir=None):
    data_dir = Path(data_dir or os.environ.get("HID_DATA_DIR", ROOT / "data"))
    model_dir = Path(model_dir or ROOT / "models")
    web_dir = Path(web_dir or ROOT / "web" / "dist")
    camera = Camera()
    operation = Lock()
    jev_operation = Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(data_dir / "kiosk.sqlite")
        app.state.jev = None
        if os.environ.get("TYPESAFE_API_KEY", "").strip():
            try:
                app.state.jev = JevClient()
            except (RuntimeError, ValueError):
                LOGGER.warning("Jev configuration is invalid. Local verification is still available.")
        app.state.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verification")
        try:
            app.state.vision = await run_in_threadpool(Vision, model_dir)
        except Exception as exc:
            app.state.vision = None
            LOGGER.warning("Vision models unavailable: %s", exc)
        try:
            yield
        finally:
            try:
                await run_in_threadpool(camera.stop)
            except CameraUnavailable as exc:
                LOGGER.warning("Camera shutdown: %s", exc)
            await run_in_threadpool(app.state.worker.shutdown, wait=True, cancel_futures=True)

    app = FastAPI(title="HID Kiosk API", lifespan=lifespan)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        request.state.started = monotonic()
        host = request.url.hostname
        if host not in LOCAL_HOSTS:
            return error_response(403, "forbidden", "Use the local kiosk address.")
        origin = request.headers.get("origin")
        if request.method == "POST" and origin is not None and origin not in LOCAL_ORIGINS:
            return error_response(403, "forbidden", "Use the local kiosk to make changes.")
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(_, exc):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        detail = exc.errors()[0]
        field = ".".join(str(part) for part in detail["loc"])
        return error_response(422, "invalid_request", f"{field}: {detail['msg']}")

    @app.exception_handler(HTTPException)
    async def http_error(_, exc):
        response = error_response(exc.status_code, "http_error", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def internal_error(_, exc):
        LOGGER.error("Kiosk request failed", exc_info=exc)
        return error_response(500, "internal_error", "The kiosk could not complete this request.")

    @contextmanager
    def exclusive_operation():
        if not operation.acquire(blocking=False):
            raise ApiError(409, "busy", "Another camera operation is in progress.")
        try:
            yield
        finally:
            operation.release()

    def loaded_vision():
        vision = app.state.vision
        if vision is None:
            raise ApiError(503, "models_unavailable", "Load the kiosk models and restart the service.")
        return vision

    @app.get("/api/health", response_model=Health)
    def health():
        return Health(
            models_loaded=app.state.vision is not None,
            camera_running=camera.running,
            model_versions=MODEL_VERSIONS,
            jev_enabled=app.state.jev is not None,
        )

    @app.post("/api/camera/start", response_model=CameraStarted)
    def start_camera(body: Annotated[CameraStart | None, Body()] = None):
        device_index = 0 if body is None else body.device_index
        with exclusive_operation():
            try:
                camera.start(device_index)
            except CameraUnavailable as exc:
                raise ApiError(409, "camera_unavailable", str(exc)) from exc
        return CameraStarted(device_index=device_index)

    @app.post("/api/camera/stop", response_model=CameraStopped)
    def stop_camera():
        with exclusive_operation():
            try:
                camera.stop()
            except CameraUnavailable as exc:
                raise ApiError(409, "camera_unavailable", str(exc)) from exc
        return CameraStopped()

    @app.get("/api/preview", response_class=StreamingResponse)
    def preview():
        if not camera.running:
            raise ApiError(409, "camera_unavailable", "Start the camera to see the preview.")
        return StreamingResponse(
            camera.preview(), media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/participants", response_model=list[Participant])
    def participants():
        return app.state.store.participants()

    @app.post("/api/enroll", response_model=Enrollment)
    def enroll(body: EnrollmentRequest):
        with exclusive_operation():
            vision = loaded_vision()
            try:
                readings = capture_and_analyze(camera, vision, monotonic() + 3, Timings())
            except CameraUnavailable as exc:
                raise ApiError(409, "camera_unavailable", str(exc)) from exc
            except TimeoutError as exc:
                raise ApiError(408, "deadline_exceeded", "Enrollment timed out. Please retry.") from exc

            for frame in readings:
                if frame.issue:
                    raise ApiError(422, frame.issue, "Keep one clearly lit face in the frame and retry.")
            if len(readings) != FRAME_COUNT or any(
                not valid_liveness(frame.liveness_score) or frame.liveness_score < LIVE_PASS
                for frame in readings
            ):
                raise ApiError(422, "spoof_detected", "Enrollment did not pass liveness. Please retry in even light.")
            if any(frame.embedding is None for frame in readings):
                raise ApiError(422, "uncertain_match", "Could not create an identity template. Please retry.")
            template = unit_embedding(np.mean([frame.embedding for frame in readings], axis=0))
            if template is None:
                raise ApiError(422, "uncertain_match", "Could not create an identity template. Please retry.")
            participant = Participant(
                participant_id=body.participant_id or str(uuid4()),
                name=body.name,
                enrolled_at=utc_now(),
            )
            app.state.store.enroll(participant, template)
        return Enrollment(participant_id=participant.participant_id, name=participant.name, frames_used=FRAME_COUNT)

    @app.post("/api/verify", response_model=Attempt)
    async def verify(body: VerificationRequest, request: Request):
        started = request.state.started
        deadline = started + ATTEMPT_DEADLINE_MS / 1000
        created_at = utc_now()
        store = app.state.store
        timings = Timings()
        deadline_missed = False
        if not operation.acquire(blocking=False):
            raise ApiError(409, "busy", "Another camera operation is in progress.")
        try:
            template = await run_in_threadpool(store.template, body.participant_id)
            if template is None:
                raise ApiError(404, "not_enrolled", "Enroll this participant before verifying.")
            vision = loaded_vision()
            worker = app.state.worker.submit(verify_frames, camera, vision, template, deadline, timings)
        except BaseException:
            operation.release()
            raise
        worker.add_done_callback(lambda _: operation.release())

        # The deadline closes the request even if OpenCV is still returning from native
        # inference. The worker owns the lock until exit and never writes an attempt.
        future = asyncio.wrap_future(worker)
        future.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)
        try:
            decision, live, match, frames = await asyncio.wait_for(
                asyncio.shield(future), timeout=max(0, deadline - monotonic())
            )
            check_deadline(deadline)
        except TimeoutError:
            deadline_missed = True
            decision, live, match = decide_frames([], timed_out=True)
            frames = []
        attempt = Attempt(
            attempt_id=str(uuid4()),
            participant_id=body.participant_id,
            created_at=created_at,
            outcome=decision.outcome,
            reason=decision.reason,
            liveness_score=live,
            match_score=match,
            frames=frames,
            stage_ms=timings.snapshot(),
            elapsed_ms=round((monotonic() - started) * 1000),
            deadline_missed=deadline_missed,
        )
        await run_in_threadpool(store.save_attempt, attempt)
        return attempt

    @app.post("/api/attempts/{attempt_id}/second_check", response_model=SecondCheck)
    def second_check(attempt_id: str, body: SecondCheckRequest):
        if body.consent is not True:
            raise ApiError(422, "consent_required", "Confirm that the camera crop may be sent to TypeSafe.")
        client = app.state.jev
        if client is None:
            raise ApiError(503, "jev_unavailable", "Jev is not configured on this kiosk.")
        if not jev_operation.acquire(blocking=False):
            raise ApiError(409, "busy", "Another Jev check is in progress.")
        try:
            return run_second_check(attempt_id, client)
        finally:
            jev_operation.release()

    def run_second_check(attempt_id, client):
        start = monotonic()
        store = app.state.store
        source = store.second_check_source(attempt_id)
        if source is None:
            raise ApiError(404, "attempt_not_found", "This attempt could not be found.")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(source["created_at"])).total_seconds()
        if (
            source["outcome"] != "REQUEST_VERIFICATION"
            or source["reason"] != "uncertain_liveness"
            or not valid_match(source["match_score"])
            or source["match_score"] < MATCH_PASS
            or source["ui_deadline_missed"]
            or age > SECOND_CHECK_WINDOW_SECONDS
        ):
            raise ApiError(409, "jev_not_eligible", "Run a fresh local check before trying Jev.")

        with exclusive_operation():
            vision = loaded_vision()
            if not store.claim_second_check(attempt_id):
                raise ApiError(409, "jev_already_checked", "Jev already checked this attempt. Run a fresh local check.")
            template = store.template(source["participant_id"])
            reason = "second_check_unavailable"
            outcome = "REQUEST_VERIFICATION"
            jpeg = None
            try:
                deadline = monotonic() + 1
                frame, = camera.sample(1, deadline)
                reading = vision.analyze(frame, Timings(), lambda: check_deadline(deadline))
                if reading.issue is None and reading.embedding is not None and template is not None:
                    similarity = float(np.dot(reading.embedding, template))
                    if not np.isfinite(similarity) or similarity < MATCH_PASS:
                        reason = "second_check_identity_unconfirmed"
                    elif valid_liveness(reading.liveness_score) and reading.liveness_score <= SPOOF_DENY:
                        reason = "second_check_spoof"
                        outcome = "DENY"
                    elif valid_liveness(reading.liveness_score) and reading.face_box is not None:
                        jpeg = face_crop_jpeg(frame, reading.face_box)
                elif reading.embedding is None and reading.issue is None:
                    reason = "second_check_identity_unconfirmed"
            except (CameraUnavailable, TimeoutError):
                pass

        # The hosted call does not own the camera. The original attempt stays intact.
        result = client.check(jpeg) if jpeg is not None else None
        if result is not None and not result.error:
            if result.outcome() == "DENY":
                reason = "second_check_spoof"
                outcome = "DENY"
            else:
                reason = "second_check_recapture"
        live_probability = result.live_probability if result is not None else None
        check = SecondCheck(
            attempt_id=attempt_id, outcome=outcome, reason=reason,
            jev_label=result.label if result is not None else None,
            jev_live_probability=live_probability if valid_liveness(live_probability) else None,
            model=result.model if result is not None else None,
            jev_ms=result.latency_ms if result is not None else None,
            second_check_ms=round((monotonic() - start) * 1000),
        )
        store.save_second_check(check)
        return check

    @app.get("/api/attempts", response_model=list[Attempt])
    def attempts(limit: Annotated[int, Query(ge=1, le=1000)] = 50):
        return app.state.store.attempts(limit)

    @app.post("/api/attempts/{attempt_id}/scenario", response_model=ScenarioUpdated)
    def set_scenario(attempt_id: str, body: ScenarioUpdate):
        if not app.state.store.set_scenario(attempt_id, body.scenario):
            raise ApiError(404, "attempt_not_found", "This attempt could not be found.")
        return ScenarioUpdated(attempt_id=attempt_id, scenario=body.scenario)

    @app.post("/api/attempts/{attempt_id}/client_timing", response_model=AttemptReference)
    def set_client_timing(attempt_id: str, body: ClientTiming):
        if not app.state.store.set_client_timing(attempt_id, body.click_to_render_ms, body.ui_deadline_missed):
            raise ApiError(404, "attempt_not_found", "This attempt could not be found.")
        return AttemptReference(attempt_id=attempt_id)

    if (web_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="kiosk")
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return {"message": "The kiosk API is running. Visit /docs to try it."}

    return app


app = create_app()
