import math

import pytest

from app.policy import decide


def test_allow_requires_both_strong_live_and_matching_identity():
    assert decide(liveness=0.98, similarity=0.8).outcome == "ALLOW"


@pytest.mark.parametrize("liveness,similarity", [(0.5, 0.8), (0.98, 0.4), (None, 0.8)])
def test_ambiguous_or_missing_evidence_requests_verification(liveness, similarity):
    assert decide(liveness=liveness, similarity=similarity).outcome == "REQUEST_VERIFICATION"


@pytest.mark.parametrize("liveness,similarity", [(0.1, 0.8), (0.98, 0.1)])
def test_strong_spoof_or_identity_mismatch_is_denied(liveness, similarity):
    assert decide(liveness=liveness, similarity=similarity).outcome == "DENY"


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_invalid_model_scores_never_allow(invalid):
    assert decide(liveness=invalid, similarity=0.8).outcome == "REQUEST_VERIFICATION"


def test_poor_capture_cannot_be_interpreted_as_identity_mismatch():
    assert decide(liveness=0.1, similarity=0.1, issue="Move into better light.").outcome == "REQUEST_VERIFICATION"


def test_a_timeout_never_allows_even_with_high_scores():
    assert decide(liveness=0.99, similarity=0.9, timed_out=True).outcome == "REQUEST_VERIFICATION"


@pytest.mark.parametrize(
    "scores, issue, outcome, reason",
    [
        ([(.98, .6), (.97, .8), (.96, .7)], None, "ALLOW", "live_match"),
        ([(.98, .7), (.1, .7), (.98, .7)], None, "DENY", "spoof_detected"),
        ([(.98, .7), (.8, .7), (.98, .7)], None, "REQUEST_VERIFICATION", "uncertain_liveness"),
        ([(.98, .7), (.1, .7), (None, None)], "no_face", "REQUEST_VERIFICATION", "no_face"),
        ([(.98, .7), (.98, .7)], None, "REQUEST_VERIFICATION", "uncertain_liveness"),
    ],
)
def test_three_frame_outcome(scores, issue, outcome, reason):
    from app.policy import decide_frames

    decision, _, _ = decide_frames(scores, issue=issue)
    assert (decision.outcome, decision.reason) == (outcome, reason)


def test_three_frame_scores_use_minimum_liveness_and_mean_similarity():
    from app.policy import decide_frames

    decision, live, match = decide_frames([(.98, .28), (.93, .55), (.96, .82)])
    assert decision.outcome == "ALLOW"
    assert live == .93
    assert match == pytest.approx(.55)
