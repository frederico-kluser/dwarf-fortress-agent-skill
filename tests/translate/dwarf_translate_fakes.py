"""Offline test doubles for ``system_one_dwarf.translate`` (imported by tests).

The module name is deliberately project-unique so that ``import
dwarf_translate_fakes`` can never collide with another piece's test helpers.
The autouse network guard itself lives in ``conftest.py``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # the package is not installed in the venv
    sys.path.insert(0, str(SRC))

from system_one_dwarf.translate import DEFAULT_MODEL, DeepSeekTranslator  # noqa: E402

#: Obviously fake key — never a real credential.
FAKE_KEY = "sk-or-v1-offline-test-key"
FAKE_KEY_ALT = "sk-or-v1-second-offline-key"


# ------------------------------------------------------------------- transport


@dataclass
class FakeResponse:
    """Minimal httpx.Response stand-in."""

    status_code: int = 200
    payload: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body_text: str | None = None

    @property
    def text(self) -> str:
        if self.body_text is not None:
            return self.body_text
        if self.payload is None:
            return ""
        return json.dumps(self.payload, ensure_ascii=False)

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("response carries no JSON body")
        return self.payload


class FakeTransport:
    """Scripted transport: ``post`` pops the next step and records the request.

    A step may be a :class:`FakeResponse`, an exception instance (raised) or a
    callable receiving the recorded request.  When the script runs out, ``reply``
    (callable) or ``default`` (response) is used.
    """

    def __init__(
        self,
        script: Iterable[Any] | None = None,
        *,
        reply: Callable[[dict[str, Any]], Any] | None = None,
        default: Any = None,
    ) -> None:
        self.script = list(script or [])
        self.reply = reply
        self.default = default
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        timeout: Any = None,
    ) -> Any:
        record = {
            "url": url,
            "headers": dict(headers or {}),
            "json": json,
            "timeout": timeout,
        }
        self.calls.append(record)
        step: Any
        if self.script:
            step = self.script.pop(0)
        elif self.reply is not None:
            step = self.reply(record)
        else:
            step = self.default
        if callable(step) and not isinstance(step, FakeResponse):
            step = step(record)
        if isinstance(step, BaseException):
            raise step
        if step is None:
            raise AssertionError("FakeTransport: no scripted response left")
        return step

    def close(self) -> None:
        self.closed = True

    # -- assertions helpers -------------------------------------------------- #

    @property
    def count(self) -> int:
        return len(self.calls)

    def bodies(self) -> list[dict[str, Any]]:
        return [call["json"] for call in self.calls]

    def reasoning_params(self) -> list[Any]:
        return [body.get("reasoning", "<absent>") for body in self.bodies()]

    def user_contents(self) -> list[str]:
        return [body["messages"][-1]["content"] for body in self.bodies()]


class SleepRecorder:
    """Injectable ``sleep`` that records delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


# -------------------------------------------------------------------- builders


def chat_response(
    content: str,
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost: float | None = None,
    model: str = DEFAULT_MODEL,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
    usage: Any = ...,
) -> FakeResponse:
    """OpenAI-compatible chat completion payload."""
    if usage is ...:
        usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        if cost is not None:
            usage["cost"] = cost
    payload = {
        "id": "gen-offline-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
    return FakeResponse(status_code=status_code, payload=payload, headers=dict(headers or {}))


def error_response(
    status_code: int,
    message: str = "offline error",
    *,
    headers: Mapping[str, str] | None = None,
) -> FakeResponse:
    """Error response carrying an OpenAI-style ``error`` object."""
    payload = {"error": {"message": message, "code": status_code, "type": "offline_error"}}
    return FakeResponse(status_code=status_code, payload=payload, headers=dict(headers or {}))


def error_body_response(
    code: int,
    message: str = "offline error in a 200 body",
    *,
    headers: Mapping[str, str] | None = None,
) -> FakeResponse:
    """HTTP 200 whose body still carries an ``error`` (seen in the wild)."""
    payload = {"error": {"message": message, "code": code, "type": "offline_error"}}
    return FakeResponse(status_code=200, payload=payload, headers=dict(headers or {}))


def prefixed_reply(prefix_pt: str = "[pt] ", prefix_en: str = "[en] ") -> Callable[[dict[str, Any]], Any]:
    """Deterministic "translation": a direction-aware prefix per input line.

    Understands both protocols of the client: a single user message (one line) and
    a JSON array of lines (batched screen call).
    """

    def reply(call: dict[str, Any]) -> FakeResponse:
        body = call["json"]
        messages = body["messages"]
        system = messages[0]["content"]
        user = messages[-1]["content"]
        prefix = prefix_pt if "into Brazilian Portuguese" in system else prefix_en
        try:
            parsed = json.loads(user)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            content = json.dumps([prefix + str(item) for item in parsed], ensure_ascii=False)
        else:
            content = prefix + user
        return chat_response(content)

    return reply


def make_translator(
    transport: Any,
    *,
    key: str = FAKE_KEY,
    sleep: Callable[[float], None] | None = None,
    **kwargs: Any,
) -> DeepSeekTranslator:
    """Translator wired to a fake transport, with sleeping neutralised."""
    kwargs.setdefault("sleep", sleep if sleep is not None else lambda _delay: None)
    return DeepSeekTranslator(lambda: key, transport=transport, **kwargs)


def lines_payload(lines: Sequence[str]) -> str:
    return json.dumps(list(lines), ensure_ascii=False)
