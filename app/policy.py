from dataclasses import dataclass
from math import isfinite
from statistics import fmean

LIVE_PASS = 0.9
SPOOF_DENY = 0.2
MATCH_PASS = 0.5
MISMATCH_DENY = 0.3
FRAME_COUNT = 3


@dataclass(frozen=True)
class Decision:
    outcome: str
    reason: str


def valid_liveness(value):
    return value is not None and isfinite(value) and 0 <= value <= 1


def valid_match(value):
    return value is not None and isfinite(value) and -1 <= value <= 1


def decide(liveness=None, similarity=None, issue=None, timed_out=False):
    """Experimental thresholds. Scores require validation on the demo camera."""
    if timed_out:
        return Decision("REQUEST_VERIFICATION", "deadline_exceeded")
    if issue:
        return Decision("REQUEST_VERIFICATION", issue)
    if not valid_liveness(liveness):
        return Decision("REQUEST_VERIFICATION", "uncertain_liveness")
    if liveness <= SPOOF_DENY:
        return Decision("DENY", "spoof_detected")
    if not valid_match(similarity):
        return Decision("REQUEST_VERIFICATION", "uncertain_match")
    if similarity <= MISMATCH_DENY:
        return Decision("DENY", "identity_mismatch")
    if liveness < LIVE_PASS:
        return Decision("REQUEST_VERIFICATION", "uncertain_liveness")
    if similarity < MATCH_PASS:
        return Decision("REQUEST_VERIFICATION", "uncertain_match")
    return Decision("ALLOW", "live_match")


def decide_frames(scores, *, issue=None, timed_out=False):
    """Return the decision and aggregates for three (liveness, cosine) pairs."""
    if timed_out or issue:
        return decide(issue=issue, timed_out=timed_out), None, None
    if len(scores) != FRAME_COUNT:
        return decide(), None, None
    live_scores, match_scores = zip(*scores)
    live = min(live_scores) if all(map(valid_liveness, live_scores)) else None
    match = fmean(match_scores) if all(map(valid_match, match_scores)) else None
    if any(valid_liveness(score) and score <= SPOOF_DENY for score in live_scores):
        return Decision("DENY", "spoof_detected"), live, match
    return decide(live, match), live, match
