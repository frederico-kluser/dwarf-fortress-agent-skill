"""``agent.llm``: transport, reasoning ladder, error mapping, usage seam.

Everything runs against ``FakeTransport`` — no socket is ever opened (the autouse
fixture in ``conftest.py`` enforces it).
"""

from __future__ import annotations

import json

import pytest

from system_one_dwarf.agent import (
    DeepSeekLLM,
    LLMAuthError,
    LLMQuotaError,
    LLMRequestError,
    LLMResponseError,
    LLMTransportError,
    estimate_cost,
    money,
    reasoning_ladder,
    spend_recorder,
)
from system_one_dwarf.agent.llm import DEFAULT_ENDPOINT

from .conftest import FAKE_KEY, FakeLedger, FakeResponse, FakeTransport, chat_payload


def make_llm(transport: FakeTransport, **kwargs) -> DeepSeekLLM:
    kwargs.setdefault("sleep", lambda _delay: None)
    return DeepSeekLLM(lambda: FAKE_KEY, transport=transport, **kwargs)


# --------------------------------------------------------------------- happy path


def test_complete_sends_the_prompts_and_reads_the_text():
    transport = FakeTransport(chat_payload("A chronicle line."))
    llm = make_llm(transport, max_tokens=321, temperature=0.25)

    result = llm.complete("You narrate.", "RECENT EVENTS: 1. woke up", purpose="narrator")

    assert result.text == "A chronicle line."
    assert result.model == "deepseek/deepseek-v4.1-flash"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 40)
    assert result.usage.cost_usd == 0.000023
    assert result.reasoning == "default"
    assert result.reasoning_param is None
    assert result.latency_ms >= 0

    call = transport.calls[0]
    assert call["url"] == DEFAULT_ENDPOINT
    assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    body = call["body"]
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "You narrate."
    assert body["messages"][1]["content"] == "RECENT EVENTS: 1. woke up"
    assert body["max_tokens"] == 321
    assert body["temperature"] == 0.25
    assert "reasoning" not in body  # "auto" leaves the API default alone
    assert llm.calls == 1


def test_key_never_appears_in_repr_or_in_an_error():
    transport = FakeTransport(FakeResponse(401, {"error": {"message": "bad key"}}))
    llm = make_llm(transport)

    assert FAKE_KEY not in repr(llm)
    assert "set" in repr(llm)
    with pytest.raises(LLMAuthError) as excinfo:
        llm.complete("s", "u")
    assert FAKE_KEY not in str(excinfo.value)


def test_missing_key_is_a_terminal_auth_error_without_a_request():
    transport = FakeTransport()
    llm = DeepSeekLLM(lambda: "", transport=transport)

    with pytest.raises(LLMAuthError) as excinfo:
        llm.complete("s", "u")

    assert transport.calls == []
    assert excinfo.value.terminal is True


# ---------------------------------------------------------------- effort mapping


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("max", {"effort": "max"}),
        ("xhigh", {"effort": "high"}),
        ("high", {"effort": "high"}),
        ("medium", {"effort": "low"}),
        ("low", {"effort": "low"}),
        ("minimal", {"enabled": False}),
        ("none", {"enabled": False}),
    ],
)
def test_reasoning_payload_per_effort(effort: str, expected: dict):
    transport = FakeTransport(chat_payload("ok"))
    llm = make_llm(transport)

    result = llm.complete("s", "u", reasoning=effort)

    assert transport.last_body["reasoning"] == expected
    assert result.reasoning_param == expected
    assert result.degraded == 0
    assert result.reasoning == (
        "off" if expected.get("enabled") is False else f"effort:{expected['effort']}"
    )


def test_reasoning_ladder_shape():
    assert reasoning_ladder("max") == (
        {"effort": "max"},
        {"effort": "high"},
        {"effort": "low"},
        None,
    )
    assert reasoning_ladder("none") == ({"enabled": False}, None)
    assert reasoning_ladder("auto") == (None,)
    assert reasoning_ladder(None) == (None,)
    assert reasoning_ladder({"effort": "xhigh"})[0] == {"effort": "high"}
    assert reasoning_ladder({"enabled": True}) == ({"enabled": True}, None)


def test_unsupported_effort_falls_back_one_level_down_and_still_succeeds():
    transport = FakeTransport(
        FakeResponse(400, {"error": {"message": "unsupported reasoning effort"}}),
        chat_payload("ok"),
    )
    llm = make_llm(transport)

    result = llm.complete("s", "u", reasoning="max")

    assert transport.reasons() == [{"effort": "max"}, {"effort": "high"}]
    assert result.text == "ok"
    assert result.reasoning_param == {"effort": "high"}
    assert result.degraded == 1
    assert llm.degradations == 1


def test_ladder_walks_all_the_way_to_no_reasoning_parameter():
    transport = FakeTransport(
        FakeResponse(400, {"error": {"message": "bad effort"}}),
        FakeResponse(400, {"error": {"message": "bad effort"}}),
        chat_payload("ok"),
    )
    llm = make_llm(transport)

    result = llm.complete("s", "u", reasoning="high")

    assert transport.reasons() == [{"effort": "high"}, {"effort": "low"}, None]
    assert result.reasoning_param is None
    assert result.reasoning == "default"
    assert result.text == "ok"


def test_rejection_of_every_rung_is_a_terminal_request_error():
    transport = FakeTransport(
        *[FakeResponse(422, {"error": {"message": "unsupported reasoning effort"}}) for _ in range(4)]
    )
    llm = make_llm(transport)

    with pytest.raises(LLMRequestError) as excinfo:
        llm.complete("s", "u", reasoning="max")

    assert transport.reasons() == [
        {"effort": "max"},
        {"effort": "high"},
        {"effort": "low"},
        None,
    ]
    assert excinfo.value.terminal is True


def test_reasoning_none_never_degrades_and_fails_fast():
    transport = FakeTransport(FakeResponse(400, {"error": {"message": "nope"}}))
    llm = make_llm(transport)

    with pytest.raises(LLMRequestError):
        llm.complete("s", "u", reasoning=None)

    assert len(transport.calls) == 1
    assert transport.last_body.get("reasoning") is None


def test_unknown_effort_warns_and_sends_no_reasoning_parameter():
    transport = FakeTransport(chat_payload("ok"))
    llm = make_llm(transport)

    result = llm.complete("s", "u", reasoning="banana")

    assert "reasoning" not in transport.last_body
    assert result.reasoning == "default"


# ----------------------------------------------------------------- error mapping


def test_401_is_terminal():
    transport = FakeTransport(FakeResponse(401, {"error": {"message": "invalid api key"}}))
    llm = make_llm(transport)

    with pytest.raises(LLMAuthError) as excinfo:
        llm.complete("s", "u")

    assert excinfo.value.status_code == 401
    assert excinfo.value.terminal is True
    assert len(transport.calls) == 1  # never retried


def test_402_is_terminal():
    transport = FakeTransport(FakeResponse(402, {"error": {"message": "insufficient credits"}}))
    llm = make_llm(transport)

    with pytest.raises(LLMQuotaError) as excinfo:
        llm.complete("s", "u")

    assert excinfo.value.status_code == 402
    assert excinfo.value.terminal is True
    assert len(transport.calls) == 1


def test_429_honours_retry_after_with_monkeypatched_sleep(monkeypatch: pytest.MonkeyPatch):
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    transport = FakeTransport(
        FakeResponse(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "2"}),
        FakeResponse(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
        chat_payload("ok after waiting"),
    )
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport)  # real time.sleep, patched

    result = llm.complete("s", "u")

    assert slept == [2.0, 1.0]
    assert result.text == "ok after waiting"
    assert len(transport.calls) == 3
    assert result.attempts == 3


def test_retry_cap_is_three_attempts():
    transport = FakeTransport(*[FakeResponse(429, {"error": {"message": "slow down"}}) for _ in range(3)])
    llm = make_llm(transport, max_attempts=3)

    with pytest.raises(LLMTransportError) as excinfo:
        llm.complete("s", "u")

    assert len(transport.calls) == 3
    assert excinfo.value.status_code == 429
    assert excinfo.value.terminal is False


def test_5xx_is_retried_then_succeeds():
    transport = FakeTransport(FakeResponse(503, text="upstream down"), chat_payload("ok"))
    llm = make_llm(transport)

    assert llm.complete("s", "u").text == "ok"
    assert len(transport.calls) == 2


def test_network_error_is_retried_with_backoff():
    slept: list[float] = []
    transport = FakeTransport(OSError("connection reset"), chat_payload("ok"))
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport, sleep=slept.append)

    assert llm.complete("s", "u").text == "ok"
    assert slept == [0.5]  # backoff_base * factor ** 0


def test_200_with_error_body_is_treated_as_the_error_it_is():
    transport = FakeTransport(
        FakeResponse(200, {"error": {"code": 401, "message": "no auth"}}),
    )
    llm = make_llm(transport)

    with pytest.raises(LLMAuthError):
        llm.complete("s", "u")


def test_200_with_rate_limit_error_body_is_retried():
    transport = FakeTransport(
        FakeResponse(200, {"error": {"message": "Rate limit exceeded"}}),
        chat_payload("ok"),
    )
    llm = make_llm(transport)

    assert llm.complete("s", "u").text == "ok"
    assert len(transport.calls) == 2


def test_200_with_reasoning_error_body_degrades_the_ladder():
    transport = FakeTransport(
        FakeResponse(200, {"error": {"message": "reasoning effort not supported"}}),
        chat_payload("ok"),
    )
    llm = make_llm(transport)

    result = llm.complete("s", "u", reasoning="max")

    assert transport.reasons() == [{"effort": "max"}, {"effort": "high"}]
    assert result.text == "ok"


def test_200_without_choices_is_a_response_error():
    transport = FakeTransport(FakeResponse(200, {"id": "x", "choices": []}))
    llm = make_llm(transport)

    with pytest.raises(LLMResponseError) as excinfo:
        llm.complete("s", "u")

    assert excinfo.value.terminal is True


def test_200_with_blank_text_is_a_response_error():
    transport = FakeTransport(chat_payload("   "))
    llm = make_llm(transport)

    with pytest.raises(LLMResponseError):
        llm.complete("s", "u")


def test_empty_prompts_are_rejected_without_a_call():
    transport = FakeTransport()
    llm = make_llm(transport)

    with pytest.raises(LLMRequestError):
        llm.complete("", "u")
    with pytest.raises(LLMRequestError):
        llm.complete("s", "   ")

    assert transport.calls == []


# -------------------------------------------------------------------- usage seam


def test_usage_callback_receives_the_usage_of_every_call():
    events = []
    transport = FakeTransport(chat_payload("a"), chat_payload("b"))
    llm = make_llm(transport, usage_callback=events.append)

    llm.complete("s", "u", purpose="narrator")
    llm.complete("s", "u", purpose="questions")

    assert len(events) == 2
    first = events[0]
    assert first.model == "deepseek/deepseek-v4.1-flash"
    assert (first.input_tokens, first.output_tokens) == (120, 40)
    assert first.cost_usd == 0.000023
    assert first.purpose == "narrator"
    assert first.provider == "openrouter"
    assert first.reasoning == "default"
    assert first.ts.endswith("Z")
    assert events[1].purpose == "questions"
    assert llm.usage_callback_errors == 0


def test_usage_callback_failure_never_breaks_the_call():
    def boom(_event):
        raise RuntimeError("ledger is down")

    transport = FakeTransport(chat_payload("still fine"))
    llm = make_llm(transport, usage_callback=boom)

    assert llm.complete("s", "u").text == "still fine"
    assert llm.usage_callback_errors == 1


def test_spend_recorder_adapts_the_campaign_ledger():
    ledger = FakeLedger()
    transport = FakeTransport(chat_payload("ok"))
    llm = make_llm(transport, usage_callback=spend_recorder(ledger))

    llm.complete("s", "u", purpose="narrator")

    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row["provider"] == "openrouter"
    assert row["model"] == "deepseek/deepseek-v4.1-flash"
    assert row["cost_usd"] == 0.000023
    assert (row["input_tokens"], row["output_tokens"]) == (120, 40)
    assert row["reason"] == "narrator"
    assert row["ts"].endswith("Z")


def test_usage_totals_accumulate_and_provider_cost_wins_over_estimate():
    transport = FakeTransport(chat_payload("a", cost=0.5), chat_payload("b", cost=None))
    llm = make_llm(transport)

    first = llm.complete("s", "u")
    second = llm.complete("s", "u")

    assert first.usage.cost_usd == 0.5
    # No provider cost -> estimated from the model card (0.30 / 1.20 per Mtok).
    assert second.usage.cost_usd == estimate_cost(120, 40)
    assert llm.usage_total.cost_usd == money(0.5 + estimate_cost(120, 40))
    assert llm.usage_total.input_tokens == 240
    assert llm.calls == 2


def test_input_payload_and_usage_parsing_tolerate_shapes():
    payload = chat_payload("parts", include_usage=False)
    payload["choices"][0]["message"]["content"] = [{"type": "text", "text": "joined "}, "text"]
    payload["usage"] = {"input_tokens": "10", "output_tokens": 3, "cost": "0.000002"}
    transport = FakeTransport(payload)
    llm = make_llm(transport)

    result = llm.complete("s", "u")

    assert result.text == "joined text"
    assert result.usage.input_tokens == 10
    assert result.usage.cost_usd == 0.000002


def test_callable_transport_is_supported():
    seen: list[dict] = []

    def transport_fn(url, *, headers=None, json=None):  # noqa: A002 - httpx keyword
        seen.append({"url": url, "headers": headers, "body": json})
        return FakeResponse(200, chat_payload("via callable"))

    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport_fn)

    assert llm.complete("s", "u").text == "via callable"
    assert seen[0]["url"] == DEFAULT_ENDPOINT
    assert json.loads(json.dumps(seen[0]["body"]))["model"] == "deepseek/deepseek-v4.1-flash"

def test_the_suite_is_offline_by_construction():
    """The autouse guard must stay active: this suite makes zero network calls."""
    import socket

    with pytest.raises(AssertionError):
        socket.socket()
    with pytest.raises(AssertionError):
        socket.create_connection(("openrouter.ai", 443))


# ------------------------------------------------- helper/parsing branch coverage
# (the value objects and the low-level response readers: every branch here has
#  logic that the happy-path tests above do not reach)


def test_usage_value_object_branches():
    from system_one_dwarf.agent import LLMUsage, LLMUsageEvent, LLMResult, money

    assert money(None) == 0.0
    assert money(0.0000004) == 0.0
    assert LLMUsage(3, 4).total_tokens == 7
    assert LLMUsage(1, 2, 0.5) + LLMUsage(3, 4, 0.25) == LLMUsage(4, 6, 0.75)
    assert LLMUsage(1, 1).__add__(object()) is NotImplemented
    assert LLMUsage(1, 2, 0.5).as_dict() == {
        "input_tokens": 1,
        "output_tokens": 2,
        "cost_usd": 0.5,
    }

    event = LLMUsageEvent(
        model="m",
        input_tokens=10,
        output_tokens=4,
        cost_usd=0.25,
        latency_ms=12,
        purpose="narrator",
        reasoning="effort:high",
        attempts=2,
        degraded=1,
        ts="2026-10-09T00:00:00.000Z",
    )
    assert event.usage == LLMUsage(10, 4, 0.25)
    assert event.spend_record_kwargs() == {
        "cost_usd": 0.25,
        "input_tokens": 10,
        "output_tokens": 4,
        "reason": "narrator",
        "ts": "2026-10-09T00:00:00.000Z",
    }
    payload = event.as_dict()
    assert payload["provider"] == "openrouter"
    assert payload["reasoning"] == "effort:high"
    assert payload["degraded"] == 1
    assert json.loads(json.dumps(payload))["cost_usd"] == 0.25

    result = LLMResult(text="t", usage=LLMUsage(1, 2, 0.5), model="m", reasoning="off")
    assert result.cost_usd == 0.5
    dumped = result.as_dict()
    assert dumped["text"] == "t" and dumped["usage"]["output_tokens"] == 2
    assert LLMResult(text="t").as_dict()["reasoning_param"] is None


def test_reasoning_ladder_and_mode_name_branches():
    from system_one_dwarf.agent import reasoning_mode_name

    with pytest.raises(LLMRequestError):
        reasoning_ladder(42)  # not a string, not a mapping, not None
    assert reasoning_mode_name({"enabled": True}) == "on"
    assert reasoning_mode_name({"effort": "max"}) == "effort:max"
    assert reasoning_mode_name({"enabled": False}) == "off"
    assert reasoning_mode_name(None) == "default"
    assert reasoning_mode_name({"mystery": 1}) == "custom"
    # a mapping that asks for an unsupported effort climbs the known ladder
    assert reasoning_ladder({"effort": "xhigh"}) == ({"effort": "high"}, {"effort": "low"}, None)


def test_numeric_coercion_helpers():
    from system_one_dwarf.agent.llm import _as_float, _as_int

    assert [_as_int(value) for value in (None, True, 5, 5.9, "7", "x", object(), [1])] == [
        None,
        None,
        5,
        5,
        7,
        None,
        None,
        None,
    ]
    assert [_as_float(value) for value in (None, False, 2, "2.5", "x", object())] == [
        None,
        None,
        2.0,
        2.5,
        None,
        None,
    ]


def test_header_reader_tolerates_missing_and_exotic_headers():
    from system_one_dwarf.agent.llm import _header

    class _Headers:
        def __init__(self, pairs):
            self._pairs = pairs

    class _Exploding:
        def get(self, key, default=None):  # a hostile header object
            return None

        def __iter__(self):
            raise RuntimeError("cannot iterate headers")

    class _RaisingGet(dict):
        def get(self, key, default=None):
            raise RuntimeError("boom")

    assert _header(object(), "Retry-After") is None  # no headers at all
    assert _header(_ResponseWithHeaders(None), "Retry-After") is None
    assert _header(_ResponseWithHeaders([("retry-after", "4")]), "Retry-After") == "4"
    assert _header(_ResponseWithHeaders(_Headers([])), "Retry-After") is None
    assert _header(_ResponseWithHeaders(_RaisingGet()), "Retry-After") is None
    assert _header(_ResponseWithHeaders(_Exploding()), "Retry-After") is None


class _ResponseWithHeaders:
    """Minimal response carrying only headers (for the ``_header`` unit tests)."""

    def __init__(self, headers):
        self.headers = headers
        self.status_code = 200
        self.text = "{}"


def test_response_text_and_json_payload_fallbacks():
    from system_one_dwarf.agent.llm import _json_payload, _response_text

    class _Raw:
        def __init__(self, *, text=None, content=None, explode=False):
            self.text = text
            self.content = content
            self._explode = explode
            self.status_code = 200

        def json(self):
            if self._explode:
                raise ValueError("not json")
            return json.loads(self.text)

    assert _response_text(_Raw(content=b'{"a": 1}')).startswith("{")
    assert _response_text(_Raw()) == ""
    assert _json_payload(_Raw(text='{"a": 1}')) == {"a": 1}
    assert _json_payload(_Raw(text=None)) is None
    assert _json_payload(_Raw(text="<html>", explode=True)) is None
    assert _json_payload(_Raw(text="not json at all", explode=True)) is None


def test_a_response_with_bytes_content_is_still_read():
    payload = chat_payload("bytes body")

    class _Raw:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = None

        def __init__(self, body):
            self.content = body

        def json(self):
            raise ValueError("no json() on this fake")

    llm = make_llm(FakeTransport(_Raw(json.dumps(payload).encode("utf-8"))))

    assert llm.complete("s", "u").text == "bytes body"


def test_a_non_json_200_body_is_a_response_error():
    class _Raw:
        status_code = 200
        headers = {}
        text = "<html>bad gateway</html>"

        def json(self):
            raise ValueError("not json")

    llm = make_llm(FakeTransport(_Raw()))

    with pytest.raises(LLMResponseError):
        llm.complete("s", "u")


def test_error_body_classification_branches():
    from system_one_dwarf.agent.llm import _error_status_from_body

    assert _error_status_from_body(None) is None
    assert _error_status_from_body({"choices": []}) is None
    assert _error_status_from_body({"error": None}) is None
    assert _error_status_from_body({"error": {"code": 429, "message": "slow"}}) == 429
    assert _error_status_from_body({"error": {"message": "Rate limit exceeded"}}) == 429
    assert _error_status_from_body({"error": {"message": "insufficient credits"}}) == 402
    assert _error_status_from_body({"error": {"message": "quota exhausted"}}) == 402
    assert _error_status_from_body({"error": {"message": "invalid api key"}}) == 401
    assert _error_status_from_body({"error": {"message": "unauthorized"}}) == 401
    assert _error_status_from_body({"error": {"message": "unsupported reasoning effort"}}) == 400
    assert _error_status_from_body({"error": {"message": "something odd"}}) == 400
    assert _error_status_from_body({"error": "flat string error"}) is None


def test_retry_after_parsing_branches():
    from email.utils import formatdate
    import time as _time

    from system_one_dwarf.agent.llm import _parse_retry_after

    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("abc") is None
    assert _parse_retry_after("-3") == 0.0
    assert _parse_retry_after("2.5") == 2.5
    future = _parse_retry_after(formatdate(_time.time() + 40, usegmt=True))
    assert future is not None and 30 <= future <= 45
    assert _parse_retry_after("Wed, 21 Oct 2020 07:28:00 GMT") == 0.0  # already past


def test_retry_after_http_date_is_capped_and_honoured(monkeypatch: pytest.MonkeyPatch):
    from email.utils import formatdate
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    transport = FakeTransport(
        FakeResponse(
            429,
            {"error": {"message": "slow down"}},
            headers={"Retry-After": formatdate(_time.time() + 900, usegmt=True)},
        ),
        chat_payload("ok"),
    )
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport)

    assert llm.complete("s", "u").text == "ok"
    assert slept == [30.0]  # max_retry_after caps a hostile Retry-After


def test_retry_without_any_retry_after_header_uses_the_backoff():
    slept: list[float] = []
    transport = FakeTransport(_RawStatus(429, "busy"), chat_payload("ok"))
    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=transport, sleep=slept.append)

    assert llm.complete("s", "u").text == "ok"
    assert slept == [0.5]


class _RawStatus:
    """Response with a status code and no usable headers/json()."""

    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers = None

    def json(self):
        raise ValueError("not json")


def test_callable_transport_close_is_a_noop():
    from system_one_dwarf.agent.llm import _CallableTransport

    transport = _CallableTransport(lambda url, **kwargs: FakeResponse(200, chat_payload("ok")))
    assert transport.close() is None


def test_completion_content_rejects_malformed_choices():
    from system_one_dwarf.agent.llm import _completion_content

    assert _completion_content("not a mapping") is None
    assert _completion_content({}) is None
    assert _completion_content({"choices": []}) is None
    assert _completion_content({"choices": ["plain string"]}) is None
    assert _completion_content({"choices": [{"message": {"content": 42}}]}) is None
    assert _completion_content({"choices": [{"message": {"content": []}}]}) is None
    assert _completion_content({"choices": [{"text": "legacy shape"}]}) == "legacy shape"
    assert _completion_content({"choices": [{"message": {"content": [{"text": "a"}, "b"]}}]}) == "ab"


def test_parse_usage_tolerates_missing_shapes():
    from system_one_dwarf.agent import LLMUsage, parse_usage

    assert parse_usage("not a mapping") == LLMUsage()
    assert parse_usage({}) == LLMUsage()
    assert parse_usage({"usage": "nope"}) == LLMUsage()
    assert parse_usage({"usage": {"total_cost": 0.5}}) == LLMUsage(0, 0, 0.5)
    assert parse_usage({"usage": {"prompt_tokens": "x", "completion_tokens": None}}) == LLMUsage()


def test_client_construction_guards():
    with pytest.raises(LLMRequestError):
        DeepSeekLLM("not-callable")  # type: ignore[arg-type]
    with pytest.raises(LLMRequestError):
        DeepSeekLLM(lambda: FAKE_KEY, model="   ")
    with pytest.raises(LLMRequestError):
        DeepSeekLLM(lambda: FAKE_KEY, max_attempts=0)
    with pytest.raises(LLMRequestError):
        DeepSeekLLM(lambda: FAKE_KEY, transport=42)  # neither callable nor httpx-like


def test_client_is_a_context_manager_and_owns_its_transport():
    import httpx

    llm = DeepSeekLLM(lambda: FAKE_KEY)  # no transport injected: lazily created
    with llm as entered:
        assert entered is llm
        assert isinstance(llm._get_transport(), httpx.Client)  # created, not used
    assert llm._transport is None  # closed on exit


def test_is_configured_false_when_get_key_raises():
    def boom() -> str:
        raise RuntimeError("no key store here")

    llm = DeepSeekLLM(boom, transport=FakeTransport())

    assert llm.is_configured() is False
    with pytest.raises(LLMAuthError):
        llm.complete("s", "u")


def test_transport_exception_exhausting_attempts_is_a_transport_error():
    transport = FakeTransport(OSError("down"), OSError("still down"))
    llm = make_llm(transport, max_attempts=2)

    with pytest.raises(LLMTransportError) as excinfo:
        llm.complete("s", "u")

    assert len(transport.calls) == 2
    assert excinfo.value.attempts == 2
    assert excinfo.value.terminal is False


def test_response_without_a_status_code_is_a_transport_error():
    llm = make_llm(FakeTransport(object()))

    with pytest.raises(LLMTransportError):
        llm.complete("s", "u")


def test_transport_without_a_callable_post_is_rejected():
    class _NoPost:
        post = None

    llm = DeepSeekLLM(lambda: FAKE_KEY, transport=_NoPost())

    with pytest.raises(LLMTransportError):
        llm.complete("s", "u")


def test_naive_http_date_retry_after_is_honoured():
    from system_one_dwarf.agent.llm import _parse_retry_after

    # a date without a timezone (parsedate_to_datetime returns a naive datetime)
    value = _parse_retry_after("Wed, 21 Oct 2099 07:28:00")
    assert value is not None and value > 0


def test_signature_introspection_failure_falls_back_to_passing_everything(monkeypatch):
    """Builtins without an introspectable signature must still work as transports."""
    import inspect as _inspect

    from system_one_dwarf.agent.llm import _accepted_kwargs

    def real_signature(obj):
        raise ValueError("no signature for this callable")

    monkeypatch.setattr(_inspect, "signature", real_signature)
    assert _accepted_kwargs(lambda url: None) is None


def test_retry_after_returns_none_when_the_date_parser_yields_nothing(monkeypatch):
    """Defensive branch: an (old-stdlib) parser that answers ``None``."""
    from system_one_dwarf.agent import llm as llm_mod

    monkeypatch.setattr(llm_mod, "parsedate_to_datetime", lambda text: None)

    assert llm_mod._parse_retry_after("not-a-number-not-a-date") is None
