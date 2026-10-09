"""DeepSeekTranslator: happy paths, batching, reasoning degradation, errors, cost."""

from __future__ import annotations

import json

import pytest

from system_one_dwarf.translate import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DeepSeekTranslator,
    TranslationAuthError,
    TranslationQuotaError,
    TranslationRequestError,
    TranslationResponseError,
    TranslationTransportError,
    Usage,
    estimate_cost,
)
from system_one_dwarf.translate.fallback import IdentityTranslator

from dwarf_translate_fakes import (
    FAKE_KEY,
    FakeTransport,
    SleepRecorder,
    chat_response,
    error_body_response,
    error_response,
    lines_payload,
    make_translator,
    prefixed_reply,
)


# --------------------------------------------------------------------- happy path


def test_to_pt_happy_path_request_and_result() -> None:
    transport = FakeTransport([chat_response("Olá, aventureiro.")])
    translator = make_translator(transport)

    result = translator.to_pt("Hello, adventurer.")

    assert transport.count == 1
    call = transport.calls[0]
    assert call["url"] == DEFAULT_ENDPOINT
    assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    body = call["json"]
    assert body["model"] == DEFAULT_MODEL
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == DEFAULT_MAX_TOKENS <= 2000
    assert body["messages"][0]["role"] == "system"
    assert "only the translation" in body["messages"][0]["content"]
    assert "into Brazilian Portuguese" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "Hello, adventurer."}

    assert result.text == "Olá, aventureiro."
    assert result.source_lang == "en"
    assert result.target_lang == "pt"
    assert result.model == DEFAULT_MODEL
    assert result.via == "llm"
    assert result.latency_ms >= 0
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 500
    assert result.usage.cost_usd == estimate_cost(1000, 500) == 0.0009
    assert translator.calls == 1
    assert translator.usage_total == Usage(1000, 500, 0.0009)


def test_to_en_happy_path_uses_the_other_direction() -> None:
    transport = FakeTransport([chat_response("Look for water to the east.")])
    translator = make_translator(transport)

    result = translator.to_en("Procure água a leste.")

    system = transport.bodies()[0]["messages"][0]["content"]
    assert "into English" in system
    assert transport.user_contents() == ["Procure água a leste."]
    assert result.text == "Look for water to the east."
    assert result.source_lang == "pt"
    assert result.target_lang == "en"


def test_empty_input_makes_no_call() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    result = translator.to_pt("")

    assert transport.count == 0
    assert result.text == ""
    assert result.via == "skipped"
    assert result.usage == Usage()


def test_clean_translation_strips_model_chatter() -> None:
    transport = FakeTransport(
        [
            chat_response('```\nOlá\n```'),
            chat_response('Translation: "Bom dia"'),
        ]
    )
    translator = make_translator(transport, use_cache=False)

    assert translator.to_pt("Hello").text == "Olá"
    assert translator.to_pt("Good morning").text == "Bom dia"


# ----------------------------------------------------------------- usage / cost


def test_provider_reported_cost_wins() -> None:
    transport = FakeTransport([chat_response("olá", cost=0.042318)])
    translator = make_translator(transport)

    result = translator.to_pt("hello")

    assert result.usage.cost_usd == 0.042318
    assert result.cost_usd == 0.042318


def test_usage_accepts_input_output_token_names() -> None:
    transport = FakeTransport(
        [chat_response("olá", usage={"input_tokens": 20, "output_tokens": 10})]
    )
    translator = make_translator(transport)

    result = translator.to_pt("hello")

    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10
    assert result.usage.cost_usd == estimate_cost(20, 10)


def test_missing_usage_is_zero_not_an_error() -> None:
    transport = FakeTransport([chat_response("olá", usage=None)])
    translator = make_translator(transport)

    result = translator.to_pt("hello")

    assert result.usage == Usage()
    assert result.usage.cost_usd == 0.0
    assert translator.usage_total == Usage()


def test_response_model_is_echoed() -> None:
    transport = FakeTransport([chat_response("olá", model="deepseek/deepseek-v4.1-flash:free")])
    translator = make_translator(transport)

    assert translator.to_pt("hello").model == "deepseek/deepseek-v4.1-flash:free"


# ----------------------------------------------------------------------- batching


def test_to_pt_batch_is_one_call_and_keeps_order() -> None:
    lines = ["You are standing in a clearing.", "A deer grazes nearby.", "The sun sets."]
    transport = FakeTransport([chat_response(lines_payload([f"[pt] {line}" for line in lines]))])
    translator = make_translator(transport)

    results = translator.to_pt_batch(lines)

    assert transport.count == 1
    body = transport.bodies()[0]
    assert json.loads(body["messages"][-1]["content"]) == lines
    assert "JSON array" in body["messages"][0]["content"]
    assert [item.text for item in results] == [f"[pt] {line}" for line in lines]
    assert all(item.via == "llm" for item in results)
    total = Usage()
    for item in results:
        total = total + item.usage
    assert total == Usage(1000, 500, 0.0009)


def test_to_pt_batch_accepts_fenced_json() -> None:
    transport = FakeTransport([chat_response('```json\n["[pt] a", "[pt] b"]\n```')])
    translator = make_translator(transport)

    results = translator.to_pt_batch(["a", "b"])

    assert transport.count == 1
    assert [item.text for item in results] == ["[pt] a", "[pt] b"]


def test_to_pt_batch_accepts_object_wrapper_and_indexed_items() -> None:
    wrapped = chat_response(json.dumps({"translations": ["[pt] a", "[pt] b"]}))
    indexed = chat_response(
        json.dumps([{"index": 1, "text": "[pt] b"}, {"index": 0, "text": "[pt] a"}])
    )
    transport = FakeTransport([wrapped, indexed])
    translator = make_translator(transport, use_cache=False)

    assert [item.text for item in translator.to_pt_batch(["a", "b"])] == ["[pt] a", "[pt] b"]
    assert [item.text for item in translator.to_pt_batch(["a", "b"])] == ["[pt] a", "[pt] b"]
    assert transport.count == 2


def test_to_pt_batch_accepts_numbered_plain_text() -> None:
    transport = FakeTransport([chat_response("1. [pt] a\n2. [pt] b")])
    translator = make_translator(transport)

    results = translator.to_pt_batch(["a", "b"])

    assert transport.count == 1
    assert [item.text for item in results] == ["[pt] a", "[pt] b"]


def test_to_pt_batch_malformed_output_falls_back_to_per_line_calls() -> None:
    lines = ["one", "two", "three"]
    transport = FakeTransport(
        [
            chat_response("Sure! Here are the translations you asked for."),
            chat_response("[pt] one"),
            chat_response("[pt] two"),
            chat_response("[pt] three"),
        ]
    )
    translator = make_translator(transport)

    results = translator.to_pt_batch(lines)

    assert transport.count == 4  # 1 failed batch + 3 single calls
    assert translator.batch_parse_failures == 1
    assert [item.text for item in results] == ["[pt] one", "[pt] two", "[pt] three"]
    assert [transport.user_contents()[index] for index in (1, 2, 3)] == lines


def test_to_pt_batch_wrong_length_falls_back() -> None:
    transport = FakeTransport(
        [
            chat_response(lines_payload(["[pt] one", "[pt] two"])),
            chat_response("[pt] one"),
            chat_response("[pt] two"),
            chat_response("[pt] three"),
        ]
    )
    translator = make_translator(transport)

    results = translator.to_pt_batch(["one", "two", "three"])

    assert transport.count == 4
    assert translator.batch_parse_failures == 1
    assert [item.text for item in results] == ["[pt] one", "[pt] two", "[pt] three"]


def test_to_pt_batch_skips_lines_without_letters() -> None:
    transport = FakeTransport([chat_response("[pt] hello")])
    translator = make_translator(transport)

    results = translator.to_pt_batch(["", "hello", "42", "---", "   "])

    assert transport.count == 1  # only "hello" needed a call
    assert [item.text for item in results] == ["", "[pt] hello", "42", "---", "   "]
    assert [item.via for item in results] == ["skipped", "llm", "skipped", "skipped", "skipped"]
    assert all(item.usage == Usage() for item in results[::2])


def test_to_pt_batch_all_static_makes_no_call() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    results = translator.to_pt_batch(["", "42", "----+----"])

    assert transport.count == 0
    assert [item.text for item in results] == ["", "42", "----+----"]


def test_batch_usage_is_summed_over_the_single_call() -> None:
    transport = FakeTransport([chat_response(lines_payload(["[pt] a", "[pt] b"]), input_tokens=800, output_tokens=200)])
    translator = make_translator(transport)

    results = translator.to_pt_batch(["a", "b"])

    assert sum(item.usage.cost_usd for item in results) == pytest.approx(estimate_cost(800, 200))
    assert translator.usage_total == Usage(800, 200, estimate_cost(800, 200))


# ------------------------------------------------------------- reasoning ladder


def test_reasoning_starts_off_and_is_disabled() -> None:
    transport = FakeTransport([chat_response("olá")])
    translator = make_translator(transport)

    translator.to_pt("hello")

    assert transport.reasoning_params() == [{"enabled": False}]
    assert translator.reasoning_mode == "off"
    assert translator.degradations == 0


def test_reasoning_degradation_chain_off_low_then_absent() -> None:
    transport = FakeTransport(
        [
            error_response(400, "reasoning.enabled is not supported"),
            error_response(422, "unknown reasoning effort"),
            chat_response("Olá"),
        ]
    )
    translator = make_translator(transport)

    result = translator.to_pt("hello")

    assert result.text == "Olá"
    assert transport.count == 3
    assert transport.reasoning_params() == [
        {"enabled": False},
        {"effort": "low"},
        "<absent>",
    ]
    assert translator.reasoning_mode == "default"
    assert translator.degradations == 2


def test_reasoning_choice_is_sticky_across_calls() -> None:
    transport = FakeTransport(
        [
            error_response(400, "no reasoning please"),
            chat_response("Olá"),
            chat_response("Adeus"),
        ]
    )
    translator = make_translator(transport)

    assert translator.to_pt("hello").text == "Olá"
    assert translator.to_pt("bye").text == "Adeus"

    assert transport.count == 3
    assert transport.reasoning_params() == [{"enabled": False}, {"effort": "low"}, {"effort": "low"}]
    assert translator.reasoning_mode == "low"


def test_reasoning_param_never_kills_a_translation() -> None:
    """Both cheaper modes rejected, no-param accepted: the translation still lands."""
    transport = FakeTransport(
        [error_response(400, "bad"), error_response(400, "bad"), chat_response("Olá")]
    )
    translator = make_translator(transport)

    assert translator.to_pt("hello").text == "Olá"
    assert translator.degradations == 2


def test_reasoning_true_low_start_skips_the_off_step() -> None:
    transport = FakeTransport([chat_response("olá")])
    translator = make_translator(transport, reasoning="low")

    translator.to_pt("hello")

    assert transport.reasoning_params() == [{"effort": "low"}]


def test_reasoning_disabled_never_sends_the_parameter() -> None:
    transport = FakeTransport([chat_response("olá")])
    translator = make_translator(transport, reasoning=False)

    translator.to_pt("hello")

    assert transport.reasoning_params() == ["<absent>"]


def test_genuine_bad_request_after_the_ladder_is_terminal() -> None:
    transport = FakeTransport([error_response(400, "bad"), error_response(400, "bad"), error_response(400, "still bad")])
    translator = make_translator(transport)

    with pytest.raises(TranslationRequestError) as excinfo:
        translator.to_pt("hello")

    assert transport.count == 3  # the whole ladder was tried
    assert excinfo.value.status_code == 400
    assert translator.degradations == 2


# ------------------------------------------------------------------- error paths


def test_401_is_terminal() -> None:
    transport = FakeTransport([error_response(401, "invalid api key")])
    translator = make_translator(transport)

    with pytest.raises(TranslationAuthError) as excinfo:
        translator.to_pt("hello")

    assert transport.count == 1
    assert excinfo.value.status_code == 401
    assert "invalid api key" in str(excinfo.value)


def test_402_is_terminal() -> None:
    transport = FakeTransport([error_response(402, "insufficient credits")])
    translator = make_translator(transport)

    with pytest.raises(TranslationQuotaError) as excinfo:
        translator.to_pt("hello")

    assert transport.count == 1
    assert excinfo.value.status_code == 402


def test_429_honours_retry_after() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport(
        [error_response(429, "slow down", headers={"Retry-After": "2"}), chat_response("olá")]
    )
    translator = make_translator(transport, sleep=sleeper)

    assert translator.to_pt("hello").text == "olá"
    assert transport.count == 2
    assert sleeper.delays == [2.0]


def test_429_without_retry_after_uses_backoff() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport([error_response(429), chat_response("olá")])
    translator = make_translator(transport, sleep=sleeper, backoff_base=0.5, backoff_factor=2.0)

    translator.to_pt("hello")

    assert sleeper.delays == [0.5]


def test_429_exhausts_attempts_and_fails_typed() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport([error_response(429), error_response(429)])
    translator = make_translator(transport, sleep=sleeper, max_attempts=2)

    with pytest.raises(TranslationTransportError) as excinfo:
        translator.to_pt("hello")

    assert transport.count == 2
    assert excinfo.value.status_code == 429
    assert excinfo.value.attempts == 2
    assert sleeper.delays == [0.5]


def test_retry_after_is_capped() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport(
        [error_response(429, headers={"Retry-After": "600"}), chat_response("olá")]
    )
    translator = make_translator(transport, sleep=sleeper, max_retry_after=5.0)

    translator.to_pt("hello")

    assert sleeper.delays == [5.0]


def test_network_error_is_retried_then_reported() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport([RuntimeError("connection reset"), chat_response("olá")])
    translator = make_translator(transport, sleep=sleeper)

    assert translator.to_pt("hello").text == "olá"
    assert sleeper.delays == [0.5]

    dead = FakeTransport([RuntimeError("boom"), RuntimeError("boom")])
    failing = make_translator(dead, sleep=SleepRecorder(), max_attempts=2)
    with pytest.raises(TranslationTransportError):
        failing.to_pt("hello")
    assert dead.count == 2


def test_500_is_retried() -> None:
    transport = FakeTransport([error_response(503, "upstream"), chat_response("olá")])
    translator = make_translator(transport, sleep=SleepRecorder())

    assert translator.to_pt("hello").text == "olá"
    assert transport.count == 2


def test_200_with_error_body_is_not_a_success() -> None:
    transport = FakeTransport([error_body_response(402, "Insufficient credits")])
    translator = make_translator(transport)

    with pytest.raises(TranslationQuotaError):
        translator.to_pt("hello")

    assert transport.count == 1  # terminal: no retry


def test_200_with_rate_limit_error_body_is_retried() -> None:
    transport = FakeTransport([error_body_response(429, "rate limited"), chat_response("olá")])
    translator = make_translator(transport, sleep=SleepRecorder())

    assert translator.to_pt("hello").text == "olá"
    assert transport.count == 2


def test_200_with_rate_limit_and_retry_after_header() -> None:
    sleeper = SleepRecorder()
    transport = FakeTransport(
        [
            error_body_response(429, "rate limited", headers={"Retry-After": "3"}),
            chat_response("olá"),
        ]
    )
    translator = make_translator(transport, sleep=sleeper)

    translator.to_pt("hello")

    assert sleeper.delays == [3.0]


def test_empty_completion_raises_response_error() -> None:
    transport = FakeTransport([chat_response("   ")])
    translator = make_translator(transport)

    with pytest.raises(TranslationResponseError):
        translator.to_pt("hello")


def test_missing_choices_raises_response_error() -> None:
    from dwarf_translate_fakes import FakeResponse

    transport = FakeTransport([FakeResponse(200, {"id": "x", "usage": {}})])
    translator = make_translator(transport)

    with pytest.raises(TranslationResponseError):
        translator.to_pt("hello")


def test_response_without_status_code_is_a_transport_error() -> None:
    from dwarf_translate_fakes import FakeResponse

    transport = FakeTransport([FakeResponse(status_code=None, payload={})])  # type: ignore[arg-type]
    translator = make_translator(transport)

    with pytest.raises(TranslationTransportError):
        translator.to_pt("hello")


# ------------------------------------------------------------------------- keys


def test_missing_key_is_terminal_and_makes_no_call() -> None:
    transport = FakeTransport()
    translator = DeepSeekTranslator(lambda: "   ", transport=transport)

    with pytest.raises(TranslationAuthError):
        translator.to_pt("hello")

    assert transport.count == 0
    assert translator.is_configured() is False


def test_raising_get_key_is_terminal() -> None:
    def broken() -> str:
        raise RuntimeError("key store unavailable")

    transport = FakeTransport()
    translator = DeepSeekTranslator(broken, transport=transport)

    with pytest.raises(TranslationAuthError):
        translator.to_pt("hello")

    assert transport.count == 0


def test_key_never_appears_in_repr_or_errors() -> None:
    secret = "sk-or-v1-super-secret-value"
    transport = FakeTransport([error_response(401, "invalid key")])
    translator = DeepSeekTranslator(lambda: secret, transport=transport)

    with pytest.raises(TranslationAuthError) as excinfo:
        translator.to_pt("hello")

    assert secret not in repr(translator)
    assert secret not in str(excinfo.value)
    assert secret in transport.calls[0]["headers"]["Authorization"]


def test_is_configured_true_with_a_key() -> None:
    assert make_translator(FakeTransport()).is_configured() is True


# ------------------------------------------------------------ lifecycle / plumbing


def test_transport_is_lazy_when_not_injected() -> None:
    translator = DeepSeekTranslator(lambda: FAKE_KEY)

    assert translator._transport is None
    translator.close()  # must not raise


def test_close_closes_the_injected_transport_and_is_idempotent() -> None:
    transport = FakeTransport()
    translator = make_translator(transport)

    with translator as entered:
        assert entered is translator
    assert transport.closed is True

    translator.close()  # second close is a no-op


def test_callable_transport_is_accepted() -> None:
    seen: list[dict] = []

    def my_post(url: str, *, headers=None, json=None, timeout=None):
        seen.append({"url": url, "json": json, "timeout": timeout})
        return chat_response("olá")

    translator = DeepSeekTranslator(lambda: FAKE_KEY, transport=my_post)

    assert translator.to_pt("hello").text == "olá"
    assert seen[0]["url"] == DEFAULT_ENDPOINT
    assert seen[0]["json"]["model"] == DEFAULT_MODEL


def test_unknown_direction_is_rejected() -> None:
    translator = make_translator(FakeTransport())

    with pytest.raises(ValueError):
        translator.translate("hello", "en->es")


def test_unknown_reasoning_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_translator(FakeTransport(), reasoning="ultra")


def test_identity_translator_satisfies_the_same_protocol() -> None:
    identity = IdentityTranslator()

    assert identity.to_pt("hello").text == "hello"
    assert identity.to_pt_batch(["a", "b"])[1].text == "b"
    assert identity.translate_screen("a\nb").lines_pt == ("a", "b")


def test_offline_guard_blocks_real_network_access() -> None:
    """Self-check: the suite could not reach the network even by accident."""
    import httpx

    with pytest.raises(AssertionError):
        httpx.Client()
    with pytest.raises(AssertionError):
        httpx.post(DEFAULT_ENDPOINT, json={})


def test_deterministic_reply_helper_matches_the_batch_protocol() -> None:
    transport = FakeTransport(reply=prefixed_reply())
    translator = make_translator(transport)

    assert translator.to_pt_batch(["a", "b"])[0].text == "[pt] a"
    assert translator.to_en("olá").text == "[en] olá"


# ------------------------------------------- OpenRouter integration alignment
# (skill gotchas 11, 13, 17, 18, 19, 20)


def test_attribution_headers_are_sent_on_every_request() -> None:
    """Gotcha 11: attribution is ``HTTP-Referer`` + ``X-OpenRouter-Title``."""
    transport = FakeTransport([chat_response("olá")])
    make_translator(transport).to_pt("hello")

    headers = transport.calls[0]["headers"]
    assert headers["HTTP-Referer"] == "https://github.com/frederico-kluser/dwarf-fortress-agent-skill"
    assert headers["X-OpenRouter-Title"] == "System One Dwarf"
    assert "X-OpenRouter-App" not in headers  # the wrong header (gotcha 11)


def test_model_and_endpoint_defaults_are_pinned() -> None:
    assert DEFAULT_MODEL == "deepseek/deepseek-v4.1-flash"
    assert DEFAULT_ENDPOINT == "https://openrouter.ai/api/v1/chat/completions"

    transport = FakeTransport([chat_response("olá")])
    make_translator(transport).to_pt("hello")
    assert transport.calls[0]["url"] == DEFAULT_ENDPOINT
    assert transport.bodies()[0]["model"] == DEFAULT_MODEL


def test_reasoning_ladder_is_nested_object_only() -> None:
    """Gotcha 13: only ``{"effort": …}`` / ``{"enabled": …}``, enum has no "min"."""
    from system_one_dwarf.translate.translator import REASONING_LADDER

    enum = {"max", "xhigh", "high", "medium", "low", "minimal", "none"}
    for _name, param in REASONING_LADDER:
        if param is None:
            continue
        assert set(param) <= {"effort", "enabled"}
        if "effort" in param:
            assert param["effort"] in enum
            assert param["effort"] != "min"

    transport = FakeTransport([chat_response("olá")])
    make_translator(transport).to_pt("hello")
    body = transport.bodies()[0]
    assert "reasoning_effort" not in body  # nested-only: gotcha 13
    assert body["reasoning"] == {"enabled": False}


def test_retry_defaults_are_three_attempts_and_a_thirty_second_cap() -> None:
    """Gotcha 18: bounded retries honouring ``Retry-After``, capped at 30 s."""
    from system_one_dwarf.translate.translator import DEFAULT_MAX_ATTEMPTS

    translator = DeepSeekTranslator(lambda: FAKE_KEY, transport=FakeTransport())
    assert DEFAULT_MAX_ATTEMPTS == 3
    assert translator.max_attempts == 3
    assert translator.max_retry_after == 30.0

    # and the bound is real: three failures, no fourth attempt
    transport = FakeTransport([error_response(429, "slow down")] * 3)
    failing = make_translator(transport, sleep=SleepRecorder())
    with pytest.raises(TranslationTransportError) as excinfo:
        failing.to_pt("hello")
    assert transport.count == 3
    assert excinfo.value.attempts == 3


def test_max_tokens_is_clamped_to_the_context_window() -> None:
    """Gotcha 17: ``max_tokens`` never exceeds ``context_length - prompt``."""
    from system_one_dwarf.translate.translator import clamp_max_tokens, estimate_prompt_tokens

    transport = FakeTransport([chat_response("olá")])
    translator = make_translator(transport, max_tokens=2_000_000)

    translator.to_pt("hello")

    body = transport.bodies()[0]
    prompt_tokens = estimate_prompt_tokens(
        body["messages"][0]["content"], body["messages"][1]["content"]
    )
    assert body["max_tokens"] == clamp_max_tokens(2_000_000, prompt_tokens)
    assert body["max_tokens"] < 1_000_000

    # an ordinary request is left untouched
    small = FakeTransport([chat_response("olá")])
    make_translator(small).to_pt("hello")
    assert small.bodies()[0]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_cost_keeps_the_six_decimal_money_contract() -> None:
    """Gotcha 19: provider ``cost`` wins, otherwise token pricing, 6 decimals."""
    from system_one_dwarf.translate.translator import money

    transport = FakeTransport(
        [chat_response("olá", usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})]
    )
    result = make_translator(transport).to_pt("hello")
    assert result.usage.cost_usd == 1.50  # 0.30 + 1.20 USD per Mtok
    assert result.usage.cost_usd == money(result.usage.cost_usd)

    priced = FakeTransport([chat_response("olá", cost=0.0423184)])
    assert make_translator(priced).to_pt("hello").usage.cost_usd == 0.042318


def test_200_with_error_body_auth_is_terminal() -> None:
    """Gotcha 20: a 200 carrying an ``error`` body is the error it is."""
    transport = FakeTransport([error_body_response(401, "invalid api key")])
    translator = make_translator(transport)

    with pytest.raises(TranslationAuthError):
        translator.to_pt("hello")

    assert transport.count == 1  # terminal: no retry


def test_200_with_error_body_classifies_by_message_without_a_code() -> None:
    from dwarf_translate_fakes import FakeResponse

    quota = FakeTransport([FakeResponse(200, {"error": {"message": "Insufficient credits"}})])
    with pytest.raises(TranslationQuotaError):
        make_translator(quota).to_pt("hello")

    limited = FakeTransport(
        [FakeResponse(200, {"error": {"message": "Rate limit exceeded"}}), chat_response("olá")]
    )
    assert make_translator(limited, sleep=SleepRecorder()).to_pt("hello").text == "olá"
    assert limited.count == 2  # rate limits are retried


def test_503_is_retried() -> None:
    transport = FakeTransport([error_response(503, "no provider available"), chat_response("olá")])
    translator = make_translator(transport, sleep=SleepRecorder())

    assert translator.to_pt("hello").text == "olá"
    assert transport.count == 2
