from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Outcome = Literal["ALLOW", "REQUEST_VERIFICATION", "DENY"]
Reason = Literal[
    "live_match", "spoof_detected", "identity_mismatch", "no_face",
    "multiple_faces", "poor_quality", "uncertain_liveness", "uncertain_match",
    "deadline_exceeded", "not_enrolled", "camera_unavailable",
]
Scenario = Literal[
    "unlabeled", "genuine", "wrong_identity", "print_photo", "screen_photo",
    "video_replay", "poor_light",
]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Milliseconds = Annotated[int, Field(ge=0, strict=True)]
LivenessScore = Annotated[float, Field(ge=0, le=1)]
MatchScore = Annotated[float, Field(ge=-1, le=1)]


class CameraStart(BaseModel):
    device_index: int = Field(default=0, ge=0, le=255, strict=True)


class CameraStarted(BaseModel):
    running: Literal[True] = True
    device_index: int


class CameraStopped(BaseModel):
    running: Literal[False] = False


class ModelVersions(BaseModel):
    detector: str
    recognizer: str
    liveness: str


class Health(BaseModel):
    models_loaded: bool
    camera_running: bool
    model_versions: ModelVersions
    jev_enabled: bool = False


class EnrollmentRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    participant_id: Identifier | None = None


class Enrollment(BaseModel):
    participant_id: Identifier
    name: str
    frames_used: int
    liveness_ok: Literal[True] = True


class VerificationRequest(BaseModel):
    participant_id: Identifier


class Participant(BaseModel):
    participant_id: Identifier
    name: str
    enrolled_at: str


class FrameEvidence(BaseModel):
    index: int
    face_found: bool
    quality_ok: bool
    liveness_score: LivenessScore | None = None
    match_score: MatchScore | None = None


class StageTimes(BaseModel):
    capture: Milliseconds = 0
    detect: Milliseconds = 0
    liveness: Milliseconds = 0
    match: Milliseconds = 0
    policy: Milliseconds = 0


class Attempt(BaseModel):
    attempt_id: Identifier
    participant_id: Identifier
    created_at: str
    outcome: Outcome
    reason: Reason
    liveness_score: LivenessScore | None
    match_score: MatchScore | None
    frames: list[FrameEvidence]
    stage_ms: StageTimes
    elapsed_ms: Milliseconds
    deadline_missed: bool
    scenario: Scenario = "unlabeled"
    click_to_render_ms: Milliseconds | None = None
    ui_deadline_missed: bool | None = None
    second_check: "SecondCheck | None" = None


class ScenarioUpdate(BaseModel):
    scenario: Scenario


class ClientTiming(BaseModel):
    click_to_render_ms: Milliseconds
    ui_deadline_missed: bool = Field(strict=True)


class AttemptReference(BaseModel):
    attempt_id: Identifier


class ScenarioUpdated(AttemptReference):
    scenario: Scenario


class SecondCheckRequest(BaseModel):
    consent: bool = Field(strict=True)


class SecondCheck(AttemptReference):
    method: Literal["jev"] = "jev"
    jev_label: str | None = None
    jev_live_probability: LivenessScore | None = None
    model: str | None = None
    outcome: Literal["DENY", "REQUEST_VERIFICATION"]
    reason: Literal[
        "second_check_spoof", "second_check_recapture", "second_check_unavailable",
        "second_check_identity_unconfirmed",
    ]
    second_check_ms: Milliseconds
    jev_ms: Milliseconds | None = None
