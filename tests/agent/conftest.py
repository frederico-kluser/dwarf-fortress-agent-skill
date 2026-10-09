"""Offline test rig for the agent package (narrator + question formatter).

Nothing here touches the network or the real CoALA CLI:

* ``FakeTransport`` is an httpx-like object (``post(url, *, headers, json,
  timeout)``) that pops queued answers and records every request body, so the
  reasoning payload of each call is assertable;
* ``ScriptedLLM`` is a canned :class:`~system_one_dwarf.agent.llm.LLMClient` that
  returns queued texts (and can raise a queued exception) while recording the
  prompts and keywords it received;
* ``FakeMemory`` records the memory calls (``set_narrative``/``add_episode``/…)
  and can be told to fail on a chosen method;
* the autouse ``_no_network`` fixture makes any real socket use fail loudly, so a
  test that accidentally reached OpenRouter would error instead of passing.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
from typing import Any, Mapping

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:  # the package is not installed in the venv
    sys.path.insert(0, str(SRC))

from system_one_dwarf.agent import (  # noqa: E402
    LLMResult,
    LLMUsage,
    reasoning_ladder,
    reasoning_mode_name,
)

#: A key that only ever exists inside the tests.
FAKE_KEY = "sk-or-v1-test-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly on any real socket use (the suite must be 100% offline)."""

    def guard(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tests/agent must never open a network socket")

    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


class FakeResponse:
    """Minimal ``httpx.Response`` look-alike."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        *,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {"content-type": "application/json"})
        if text is None:
            text = json.dumps(payload if payload is not None else {})
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FakeResponse(status_code={self.status_code}, text={self.text[:60]!r})"


class FakeTransport:
    """Queued responses + full request recording (the offline network)."""

    def __init__(self, *responses: Any) -> None:
        self.queue: list[Any] = list(responses)
        #: ``[(url, headers, body, timeout), …]`` for every request made.
        self.calls: list[dict[str, Any]] = []
        #: Answer used when the queue runs dry (default: a generic chat answer).
        self.default: Any = chat_payload("ok")

    def push(self, *responses: Any) -> "FakeTransport":
        self.queue.extend(responses)
        return self

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,  # noqa: A002 - mirrors httpx's keyword
        timeout: float | None = None,
    ) -> Any:
        self.calls.append(
            {"url": url, "headers": dict(headers or {}), "body": json, "timeout": timeout}
        )
        response = self.queue.pop(0) if self.queue else self.default
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, Mapping):
            response = FakeResponse(200, response)
        return response

    # ------------------------------------------------------------------ helpers
    @property
    def bodies(self) -> list[Any]:
        return [call["body"] for call in self.calls]

    @property
    def last_body(self) -> Any:
        return self.calls[-1]["body"]

    def reasons(self) -> list[Any]:
        """The ``reasoning`` parameter of every request (``None`` = omitted)."""
        return [body.get("reasoning") for body in self.bodies]

    def close(self) -> None:  # pragma: no cover - nothing to close
        return None


def chat_payload(
    text: str,
    *,
    model: str = "deepseek/deepseek-v4.1-flash",
    input_tokens: int = 120,
    output_tokens: int = 40,
    cost: float | None = 0.000023,
    include_usage: bool = True,
) -> dict[str, Any]:
    """An OpenAI-compatible chat completion body carrying ``text``."""
    payload: dict[str, Any] = {
        "id": "gen-test-1",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
    }
    if include_usage:
        usage: dict[str, Any] = {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}
        if cost is not None:
            usage["cost"] = cost
        payload["usage"] = usage
    return payload


def narration_payload(narrative: str, record: str = "", *, model: str = "deepseek/deepseek-v4.1-flash") -> dict[str, Any]:
    """A chat body whose text follows the narrator's JSON protocol."""
    return chat_payload(
        json.dumps({"narrative": narrative, "record": record}), model=model
    )


def question_payload(question: str, options: list[str] | None = None, why: str = "") -> dict[str, Any]:
    """A chat body whose text follows the question formatter's JSON protocol."""
    return chat_payload(json.dumps({"question": question, "options": options or [], "why": why}))


class ScriptedLLM:
    """Canned :class:`~system_one_dwarf.agent.llm.LLMClient` for narrator/formatter tests."""

    def __init__(
        self,
        *answers: Any,
        usage: LLMUsage | None = None,
        model: str = "fake-model",
        latency_ms: int = 7,
    ) -> None:
        self.answers = list(answers)
        self.usage = usage if usage is not None else LLMUsage(10, 5, 0.000004)
        self.model = model
        self.latency_ms = latency_ms
        #: Every call as ``{system, user, reasoning, max_tokens, temperature, purpose}``.
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(
        self,
        system: str,
        user: str,
        *,
        reasoning: Any = "auto",
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "reasoning": reasoning,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "purpose": purpose,
            }
        )
        if not self.answers:
            raise AssertionError("ScriptedLLM has no answer left for this call")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        param = reasoning_ladder(reasoning)[0]
        return LLMResult(
            text=str(answer),
            usage=self.usage,
            latency_ms=self.latency_ms,
            model=self.model,
            reasoning=reasoning_mode_name(param),
            reasoning_param=dict(param) if param is not None else None,
            attempts=1,
            raw={},
        )


class FakeMemory:
    """Records memory calls; can be told to fail on a chosen method."""

    def __init__(self, *fail_on: str) -> None:
        #: ``[{"method", "content", "kwargs"}, …]`` in call order.
        self.calls: list[dict[str, Any]] = []
        self.fail_on = set(fail_on)
        self.narrative = ""

    def methods(self) -> list[str]:
        return [call["method"] for call in self.calls]

    def contents(self, method: str) -> list[str]:
        return [call["content"] for call in self.calls if call["method"] == method]

    def kwargs_of(self, method: str) -> dict[str, Any]:
        for call in self.calls:
            if call["method"] == method:
                return call["kwargs"]
        return {}

    def _record(self, method: str, content: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": method, "content": content, "kwargs": dict(kwargs)})
        if method in self.fail_on:
            raise RuntimeError(f"{method} is broken on purpose")
        return {"ok": True, "method": method}

    # -- the MemoryStore surface the narrator uses ------------------------- #
    def add_episode(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("add_episode", content, kwargs)

    def add_fact(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("add_fact", content, kwargs)

    def add_procedure(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("add_procedure", content, kwargs)

    def set_narrative(self, text: str, **kwargs: Any) -> dict[str, Any]:
        result = self._record("set_narrative", text, kwargs)
        self.narrative = text
        return result


class FakeLedger:
    """``SpendLedger``-shaped recorder for :func:`spend_recorder`."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(
        self,
        provider: str,
        model: str,
        *,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reason: str | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "provider": provider,
            "model": model,
            "cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reason": reason,
            "ts": ts,
        }
        self.rows.append(row)
        return row


@pytest.fixture
def memory() -> FakeMemory:
    return FakeMemory()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()