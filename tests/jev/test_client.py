"""Client behaviour: parsing, degraded responses, retries, terminal errors.

Every test uses the fake transport from ``conftest.py`` — no network, no API key
in the source (the tests use the literal ``"test-key"``).
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from system_one_dwarf.jev import (
    BAND_ABSTAIN,
    BAND_AUTO,
    BAND_HITL,
    JevAuthError,
    JevClient,
    JevQuotaError,
    JevRequestError,
    JevTransportError,
    build_action,
    choice,
    noul,
    score,
)

GOOD_NOUL = ("Is it a defect?", "Broken behaviour.", "A request for help.")


def _decide(harness, payload, **kwargs):
    """Call the client with a small valid two-question request."""
    return harness.client.decide(payload, {"is_bug": noul("is_bug", *GOOD_NOUL)}, **kwargs)


# --------------------------------------------------------------------------- #
# response parsing
# --------------------------------------------------------------------------- #
def test_parses_the_captured_response(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(200, captured_response))
    result = harness.client.decide({"ticket": "checkout is blank"}, triage_questions)

    assert sorted(result.answers) == ["is_bug", "team", "urgency"]
    assert result.attempts == 1
    assert result.latency_ms >= 0
    assert result.request_id == "gen-dec-1"
    assert result.provider == "TypeSafe"
    assert result.model == "typesafe/jev-1.13-20260917"
    assert result.raw["id"] == "gen-dec-1"
    assert result.issues == ()

    bug = result.answer("is_bug")
    assert (bug.type, bug.value, bug.confidence, bug.band) == ("noul", 0.96, None, BAND_AUTO)
    assert bug.certainty == pytest.approx(0.92)
    assert bug.available is True

    team = result.answer("team")
    assert (team.type, team.value, team.confidence, team.band) == (
        "choice",
        "payments",
        0.67,
        BAND_HITL,
    )
    assert team.probabilities == {"payments": 0.78, "frontend": 0.22, "account": 0.0}
    assert team.probability == pytest.approx(0.78)
    assert team.legend is None

    urgency = result.answer("urgency")
    assert (urgency.type, urgency.value, urgency.confidence, urgency.band) == (
        "score",
        1.99,
        0.99,
        BAND_AUTO,
    )
    assert urgency.legend == {"0": "Can wait", "1": "This week", "2": "Blocking now"}
    assert urgency.probabilities == {"0": 0.0, "1": 0.0, "2": 1.0}
    assert urgency.certainty is None

    assert result.value("team") == "payments"
    assert result.band("urgency") == BAND_AUTO
    with pytest.raises(JevRequestError, match="no answer named"):
        result.answer("nope")


def test_usage_is_normalised(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(200, captured_response))
    result = harness.client.decide("state", triage_questions)
    assert result.usage.input_tokens == 476
    assert result.usage.output_tokens == 70
    assert result.usage.cost_usd == pytest.approx(0.000019992)
    assert result.usage.raw["cost"] == pytest.approx(0.000019992)


def test_missing_usage_is_zero(make_client, make_response, captured_degraded, triage_questions):
    harness = make_client(make_response(200, captured_degraded))
    result = harness.client.decide("state", triage_questions)
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cost_usd) == (0, 0, 0.0)


def test_degraded_response_caps_band_at_hitl(make_client, make_response, captured_degraded, triage_questions):
    harness = make_client(make_response(200, captured_degraded))
    result = harness.client.decide("state", triage_questions)

    team = result.answer("team")
    assert team.value == "frontend"
    assert team.confidence is None
    assert team.band == BAND_HITL
    assert team.available is False
    assert any("confidence unavailable" in issue for issue in team.issues)

    urgency = result.answer("urgency")
    assert urgency.confidence is None
    assert urgency.band == BAND_HITL
    assert urgency.legend is None

    # noul never has a confidence: p=0.51 is near-total uncertainty -> abstain
    bug = result.answer("is_bug")
    assert bug.confidence is None
    assert bug.band == BAND_ABSTAIN
    assert bug.issues == ()

    assert any("team" in issue for issue in result.issues)


def test_missing_answer_is_flagged_and_never_auto(make_client, make_response, triage_questions):
    payload = {
        "id": "gen-dec-3",
        "model": "typesafe/jev-1.13",
        "answers": {"is_bug": {"type": "noul", "noul": 0.99}},
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)
    team = result.answer("team")
    assert team.value is None and team.band == BAND_HITL
    assert any("no answer" in issue for issue in team.issues)


def test_unexpected_answer_is_kept_but_never_auto(make_client, make_response, triage_questions):
    payload = {
        "id": "gen-dec-4",
        "answers": {
            "is_bug": {"type": "noul", "noul": 0.99},
            "team": {"type": "choice", "choice": "payments"},
            "urgency": {"type": "score", "score": 2.0, "confidence": 0.9},
            "ghost": {"type": "choice", "choice": "x", "confidence": 1.0},
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)
    ghost = result.answer("ghost")
    assert ghost.band == BAND_HITL
    assert any("was not requested" in issue for issue in ghost.issues)


def test_choice_value_outside_the_offered_options_is_capped(make_client, make_response, triage_questions):
    payload = {
        "id": "gen-dec-5",
        "answers": {
            "is_bug": {"type": "noul", "noul": 0.99},
            "team": {"type": "choice", "choice": "marketing", "confidence": 0.99},
            "urgency": {"type": "score", "score": 2.0, "confidence": 0.99},
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)
    team = result.answer("team")
    assert team.band == BAND_HITL
    assert any("not one of the offered options" in issue for issue in team.issues)


def test_score_outside_the_level_range_is_capped(make_client, make_response, triage_questions):
    payload = {
        "id": "gen-dec-6",
        "answers": {
            "is_bug": {"type": "noul", "noul": 0.99},
            "team": {"type": "choice", "choice": "payments", "confidence": 0.99},
            "urgency": {"type": "score", "score": 7.5, "confidence": 0.99},
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)
    urgency = result.answer("urgency")
    assert urgency.band == BAND_HITL
    assert any("level range" in issue for issue in urgency.issues)


def test_noul_out_of_range_is_unavailable(make_client, make_response, triage_questions):
    payload = {"id": "g", "answers": {"is_bug": {"type": "noul", "noul": 1.4}}}
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", {"is_bug": noul("is_bug", *GOOD_NOUL)})
    bug = result.answer("is_bug")
    assert bug.value is None and bug.band == BAND_HITL
    assert any("outside [0, 1]" in issue for issue in bug.issues)


def test_configured_thresholds_are_used(make_client, make_response, captured_response, triage_questions):
    harness = make_client(
        make_response(200, captured_response), auto_threshold=0.6, hitl_threshold=0.3
    )
    result = harness.client.decide("state", triage_questions)
    assert result.answer("team").band == BAND_AUTO  # 0.67 >= 0.60


@pytest.mark.parametrize("thresholds", [{"auto_threshold": 0.4, "hitl_threshold": 0.9}])
def test_invalid_thresholds_rejected_at_construction(make_client, thresholds):
    with pytest.raises(JevRequestError):
        make_client(**thresholds)


# --------------------------------------------------------------------------- #
# malformed payloads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"text": "not json at all"},
        {"payload": {"id": "x"}},  # answers missing
        {"payload": {"id": "x", "answers": []}},  # answers not an object
        {"payload": ["answers"]},  # top level not an object
    ],
)
def test_malformed_success_payloads_raise(make_client, make_response, response_kwargs, triage_questions):
    harness = make_client(make_response(200, **response_kwargs))
    with pytest.raises(JevRequestError, match="malformed decision response"):
        harness.client.decide("state", triage_questions)
    assert harness.transport.call_count == 1


# --------------------------------------------------------------------------- #
# retries
# --------------------------------------------------------------------------- #
def test_429_honours_retry_after_seconds(
    monkeypatch, make_client, make_response, captured_response, triage_questions
):
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda delay: slept.append(delay))

    harness = make_client(
        make_response(429, {"detail": "rate limited"}, headers={"Retry-After": "2"}),
        make_response(200, captured_response),
        sleep=None,  # use the real time.sleep path, which is monkeypatched
    )
    result = harness.client.decide("state", triage_questions)

    assert slept == [2.0]
    assert harness.transport.call_count == 2
    assert result.attempts == 2


def test_429_honours_retry_after_http_date(make_client, make_response, captured_response, triage_questions):
    harness = make_client(
        make_response(
            429,
            {"detail": "slow down"},
            headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},  # far in the past
        ),
        make_response(200, captured_response),
    )
    result = harness.client.decide("state", triage_questions)
    assert harness.sleeps == [0.0]
    assert result.attempts == 2


def test_retry_after_is_capped(make_client, make_response, captured_response, triage_questions):
    harness = make_client(
        make_response(429, {}, headers={"Retry-After": "9999"}),
        make_response(200, captured_response),
    )
    harness.client.max_retry_after = 30.0
    harness.client.decide("state", triage_questions)
    assert harness.sleeps == [30.0]


def test_529_backs_off_exponentially(make_client, make_response, captured_response, triage_questions):
    harness = make_client(
        make_response(529, {"detail": "overloaded"}),
        make_response(529, {"detail": "overloaded"}),
        make_response(200, captured_response),
    )
    result = harness.client.decide("state", triage_questions)
    assert harness.sleeps == [0.5, 1.0]
    assert result.attempts == 3


def test_500_is_transient(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(503, {"detail": "unavailable"}), make_response(200, captured_response))
    result = harness.client.decide("state", triage_questions)
    assert result.attempts == 2


def test_retries_are_bounded_and_then_raise(make_client, make_response, triage_questions):
    harness = make_client(make_response(429, {"detail": "rate limited"}), repeat_last=True)
    with pytest.raises(JevTransportError) as excinfo:
        harness.client.decide("state", triage_questions)
    assert excinfo.value.status_code == 429
    assert excinfo.value.attempts == 3
    assert harness.transport.call_count == 3
    assert harness.sleeps == [0.5, 1.0]  # no sleep after the final attempt


def test_transport_error_is_retried(make_client, make_response, captured_response, triage_questions):
    harness = make_client(httpx.ConnectError("dns exploded"), make_response(200, captured_response))
    result = harness.client.decide("state", triage_questions)
    assert result.attempts == 2
    assert harness.sleeps == [0.5]


def test_transport_errors_are_bounded(make_client, triage_questions):
    harness = make_client(httpx.ReadTimeout("too slow"), repeat_last=True)
    with pytest.raises(JevTransportError, match="transport error"):
        harness.client.decide("state", triage_questions)
    assert harness.transport.call_count == 3


def test_max_attempts_is_configurable(make_client, make_response, triage_questions):
    harness = make_client(
        make_response(429, {}), repeat_last=True, max_attempts=1, backoff_base=0.01
    )
    with pytest.raises(JevTransportError):
        harness.client.decide("state", triage_questions)
    assert harness.transport.call_count == 1
    assert harness.sleeps == []


# --------------------------------------------------------------------------- #
# terminal errors
# --------------------------------------------------------------------------- #
def test_402_is_terminal(make_client, make_response, triage_questions):
    harness = make_client(make_response(402, {"detail": "insufficient credits"}))
    with pytest.raises(JevQuotaError) as excinfo:
        harness.client.decide("state", triage_questions)
    assert "402" in str(excinfo.value)
    assert excinfo.value.status_code == 402
    assert harness.transport.call_count == 1
    assert harness.sleeps == []


def test_401_is_terminal(make_client, make_response, triage_questions):
    harness = make_client(make_response(401, {"detail": "no auth"}))
    with pytest.raises(JevAuthError):
        harness.client.decide("state", triage_questions)
    assert harness.transport.call_count == 1
    assert harness.sleeps == []


def test_422_is_not_retried(make_client, make_response, triage_questions):
    harness = make_client(make_response(422, {"detail": "bad question schema"}))
    with pytest.raises(JevRequestError) as excinfo:
        harness.client.decide("state", triage_questions)
    assert "bad question schema" in str(excinfo.value)
    assert harness.transport.call_count == 1
    assert harness.sleeps == []


def test_other_4xx_is_terminal(make_client, make_response, triage_questions):
    harness = make_client(make_response(400, {"detail": "nope"}))
    with pytest.raises(JevRequestError):
        harness.client.decide("state", triage_questions)
    assert harness.transport.call_count == 1


# --------------------------------------------------------------------------- #
# request construction
# --------------------------------------------------------------------------- #
def test_request_body_headers_and_url(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(200, captured_response))
    harness.client.decide({"ticket": "x"}, triage_questions, session_id="sess-1")

    call = harness.transport.calls[0]
    assert call.url == "https://openrouter.ai/api/alpha/decisions"
    assert call.headers["Authorization"] == "Bearer test-key"
    assert call.headers["Content-Type"] == "application/json"
    assert call.timeout == 20.0
    assert call.body["model"] == "typesafe/jev-1.13"
    assert call.body["state"] == {"ticket": "x"}
    assert call.body["session_id"] == "sess-1"
    assert call.body["questions"]["is_bug"]["type"] == "noul"
    assert "typesafe/jev-1.13" in json.dumps(call.body)


def test_session_id_omitted_when_not_given(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(200, captured_response))
    harness.client.decide("state", triage_questions)
    assert "session_id" not in harness.transport.last_body


def test_per_call_model_override(make_client, make_response, captured_response, triage_questions):
    harness = make_client(make_response(200, captured_response))
    harness.client.decide("state", triage_questions, model="typesafe/jev-1.14")
    assert harness.transport.last_body["model"] == "typesafe/jev-1.14"


def test_session_id_too_long_is_rejected_before_sending(make_client, triage_questions):
    harness = make_client()
    with pytest.raises(JevRequestError, match="session_id"):
        harness.client.decide("state", triage_questions, session_id="s" * 257)
    assert harness.transport.call_count == 0


def test_local_validation_blocks_bad_requests(make_client):
    harness = make_client()
    # built as a raw body on purpose: the client must validate what it is given,
    # not only what the builders produced
    bad_choice = {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {f"opt_{i}": f"Option {i}." for i in range(300)},
    }
    with pytest.raises(JevRequestError, match="255"):
        harness.client.decide("state", {"team": bad_choice})
    assert harness.transport.call_count == 0


def test_validation_can_be_skipped(make_client, make_response, captured_response):
    harness = make_client(make_response(200, captured_response))
    questions = {"team": choice("team", "Which?", {"a": "A.", "other": "None."})}
    result = harness.client.decide("state", questions, validate=False)
    assert "team" in result.answers
    assert harness.transport.call_count == 1


def test_decide_request_accepts_fleet_requests(make_client, make_response, captured_response):
    request = build_action(
        [
            {"id": "goto:river", "name": "Go to the river", "description": "Walk east."},
            {"id": "wait", "name": "Wait", "description": "Hold position."},
        ],
        "Standing in a clearing, night is falling.",
        shuffle_seed=1,
    )
    payload = {
        "id": "gen-dec-7",
        "answers": {
            "action": {"type": "choice", "choice": "wait", "confidence": 0.93},
            "urgency": {"type": "score", "score": 1.0, "confidence": 0.9},
            "needs_human": {"type": "noul", "noul": 0.1},
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide_request(request)
    assert result.value("action") == "wait"
    assert result.band("action") == BAND_AUTO
    assert harness.transport.last_body["questions"]["action"]["type"] == "choice"
    assert harness.transport.last_body["state"] == request["state"]


def test_decide_request_rejects_incomplete_mappings(make_client):
    harness = make_client()
    with pytest.raises(JevRequestError, match="state"):
        harness.client.decide_request({"questions": {}})
    assert harness.transport.call_count == 0


def test_callable_transport_is_supported(make_client, make_response, captured_response):
    calls = []

    def transport_fn(url, *, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return make_response(200, captured_response)

    client = JevClient("test-key", transport=transport_fn, sleep=lambda delay: None)
    result = client.decide("state", {"is_bug": noul("is_bug", *GOOD_NOUL)})
    assert result.answer("is_bug").band == BAND_AUTO
    assert calls[0]["url"].endswith("/decisions")
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_transport_without_timeout_kwarg_is_supported(make_client, make_response, captured_response):
    harness = make_client(make_response(200, captured_response), no_timeout=True)
    result = harness.client.decide("state", {"is_bug": noul("is_bug", *GOOD_NOUL)})
    assert result.answer("is_bug").value == 0.96
    assert harness.transport.calls[0].timeout is None


# --------------------------------------------------------------------------- #
# key hygiene and lifecycle
# --------------------------------------------------------------------------- #
def test_missing_api_key_is_rejected():
    with pytest.raises(JevAuthError, match="API key"):
        JevClient("")


def test_api_key_never_appears_in_repr_or_errors(make_client, make_response, triage_questions):
    secret = "dummy-openrouter-key-for-hygiene-test"
    harness = make_client(make_response(401, {"detail": "no auth"}), api_key=secret)
    assert secret not in repr(harness.client)
    with pytest.raises(JevAuthError) as excinfo:
        harness.client.decide("state", triage_questions)
    assert secret not in str(excinfo.value)
    assert secret not in json.dumps(excinfo.value.detail)


def test_close_does_not_close_an_injected_transport(make_client):
    harness = make_client()
    harness.client.close()
    harness.client.close()  # idempotent
    assert harness.transport.closed is False


def test_client_works_as_a_context_manager(make_client, make_response, captured_response):
    harness = make_client(make_response(200, captured_response))
    with harness.client as client:
        result = client.decide("state", {"is_bug": noul("is_bug", *GOOD_NOUL)})
    assert result.answer("is_bug").value == 0.96


def test_score_questions_are_supported_end_to_end(make_client, make_response):
    payload = {"id": "s", "answers": {"urgency": {"type": "score", "score": 0.5, "confidence": 0.4}}}
    harness = make_client(make_response(200, payload))
    questions = {"urgency": score("urgency", "How urgent?", ["Can wait", "Blocking now"])}
    result = harness.client.decide("state", questions)
    assert result.answer("urgency").band == BAND_ABSTAIN


def test_zero_confidence_is_unavailable_not_flat(make_client, make_response, triage_questions):
    """``confidence: 0`` is the fallback's "no confidence" — never a flat spread."""
    payload = {
        "id": "gen-dec-8",
        "answers": {
            "is_bug": {"type": "noul", "noul": 0.99},
            "team": {
                "type": "choice",
                "choice": "payments",
                "confidence": 0.0,
                "probabilities": {"payments": 0.34, "frontend": 0.33, "account": 0.33},
            },
            "urgency": {"type": "score", "score": 1.0, "confidence": 0},
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)

    team = result.answer("team")
    assert team.confidence is None  # 0.0 normalises to "unavailable"
    assert team.band == BAND_HITL  # a flat 0.34 would have been abstain
    assert team.chosen_probability == pytest.approx(0.34)
    assert any("0.0" in issue for issue in team.issues)
    assert result.answer("urgency").band == BAND_HITL


def test_default_transport_is_a_lazy_httpx_client():
    client = JevClient("test-key")
    assert client._transport is None  # nothing is created at construction time
    transport = client._get_transport()
    assert isinstance(transport, httpx.Client)
    assert client._owns_transport is True
    client.close()
    assert client._transport is None


def test_offline_guard_is_active():
    """Proof that the whole suite runs with sockets blocked."""
    import socket

    with pytest.raises(AssertionError, match="network access attempted"):
        socket.create_connection(("openrouter.ai", 443), timeout=0.01)


# --------------------------------------------------------------------------- #
# degraded answers: the type signal and the degradation cap are load-bearing
# --------------------------------------------------------------------------- #
def test_unexpected_answer_type_is_signalled_and_capped(make_client, make_response, triage_questions):
    """Rule (type-mismatch branch): an answer whose ``type`` is not the one that was
    requested must be reported in ``issues`` — never silently normalised — and it
    must not keep an ``auto`` band."""
    bodies = [
        {"type": "essay", "choice": "payments", "confidence": 0.99},  # unknown primitive
        {"type": "noul", "noul": 0.99},  # a valid primitive, but not the one asked for
    ]
    for body in bodies:
        payload = {
            "id": "gen-dec-type",
            "answers": {
                "is_bug": {"type": "noul", "noul": 0.99},
                "team": body,
                "urgency": {"type": "score", "score": 1.0, "confidence": 0.99},
            },
        }
        harness = make_client(make_response(200, payload))
        result = harness.client.decide("state", triage_questions)
        team = result.answer("team")
        reported = body["type"]

        assert team.issues, f"type {reported!r} must be signalled in the answer"
        assert any(
            issue.startswith(f"response type {reported!r} differs from the requested 'choice'")
            for issue in team.issues
        ), team.issues
        assert any(f"team: response type {reported!r}" in issue for issue in result.issues)
        assert team.band == BAND_HITL
        assert team.band != BAND_AUTO


def test_any_degradation_caps_auto_to_hitl(make_client, make_response, triage_questions):
    """Rule (degradation cap): a reported confidence of 0.99 must not rescue a
    degraded answer — any problem caps the band at ``hitl``, while a clean
    high-confidence answer still reaches ``auto``."""
    payload = {
        "id": "gen-dec-cap",
        "answers": {
            "is_bug": {"type": "noul", "noul": 0.99},
            "team": {
                "type": "choice",
                "choice": "payments",
                "confidence": 0.99,
                "probabilities": {"payments": 0.995, "frontend": 0.005, "account": 0.0},
            },
            "urgency": {"type": "prose", "score": 2.0, "confidence": 0.99},  # degraded type
        },
    }
    harness = make_client(make_response(200, payload))
    result = harness.client.decide("state", triage_questions)

    urgency = result.answer("urgency")
    assert urgency.confidence == pytest.approx(0.99)  # the confidence itself is intact
    assert urgency.issues
    assert urgency.band == BAND_HITL  # 0.99 alone would have been auto
    assert urgency.band != BAND_AUTO

    # the cap is not a blanket downgrade: a clean answer still earns `auto`
    assert result.answer("is_bug").band == BAND_AUTO
