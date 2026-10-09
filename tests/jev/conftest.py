"""Offline test rig for the Jev fleet client.

Everything here is deterministic and network-free:

* ``FakeTransport`` is an httpx-like object (``post(url, *, headers, json,
  timeout)``) that pops queued responses/exceptions and records every call;
* the autouse ``_no_network`` fixture makes any real socket use fail loudly, so
  a test that accidentally reached the API would error instead of passing;
* ``CAPTURED_*`` are captured response shapes (the real one and a degraded one
  with ``confidence`` missing), used to pin the parser.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:  # the package is not installed in the venv
    sys.path.insert(0, str(SRC))

from system_one_dwarf.jev import JevClient  # noqa: E402

#: Real captured response (three question types, full optional fields).
CAPTURED_RESPONSE: dict[str, Any] = {
    "id": "gen-dec-1",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "answers": {
        "is_bug": {"type": "noul", "noul": 0.96},
        "team": {
            "type": "choice",
            "choice": "payments",
            "confidence": 0.67,
            "probabilities": {"payments": 0.78, "frontend": 0.22, "account": 0},
        },
        "urgency": {
            "type": "score",
            "score": 1.99,
            "confidence": 0.99,
            "probabilities": {"0": 0, "1": 0, "2": 1},
            "legend": {"0": "Can wait", "1": "This week", "2": "Blocking now"},
        },
    },
    "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992},
}

#: Degraded response: the optional fields are missing (legal on the alpha schema).
CAPTURED_DEGRADED: dict[str, Any] = {
    "id": "gen-dec-2",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "answers": {
        "is_bug": {"type": "noul", "noul": 0.51},
        "team": {"type": "choice", "choice": "frontend"},
        "urgency": {"type": "score", "score": 0.4},
    },
}


class FakeResponse:
    """Minimal httpx.Response look-alike."""

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

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"FakeResponse(status_code={self.status_code})"


@dataclass
class FakeCall:
    url: str
    headers: dict[str, str] | None
    body: dict[str, Any] | None
    timeout: float | None


class FakeTransport:
    """Queues responses (or exceptions) and records the calls it received."""

    def __init__(self, *responses: Any, repeat_last: bool = False) -> None:
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.calls: list[FakeCall] = []
        self.closed = False

    def _handle(self, url: str, headers: Any, body: Any, timeout: Any) -> Any:
        self.calls.append(FakeCall(url=url, headers=headers, body=body, timeout=timeout))
        if not self.responses:
            raise AssertionError("FakeTransport received more calls than queued responses")
        item = self.responses[0] if self.repeat_last else self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url: str, headers: Any = None, json: Any = None, timeout: Any = None) -> Any:
        return self._handle(url, headers, json, timeout)

    def close(self) -> None:
        self.closed = True

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_body(self) -> dict[str, Any] | None:
        return self.calls[-1].body if self.calls else None


class FakeTransportNoTimeout(FakeTransport):
    """httpx-like transport whose ``post`` does not accept a ``timeout`` kwarg."""

    def post(self, url: str, headers: Any = None, json: Any = None) -> Any:
        return self._handle(url, headers, json, None)


class SleepRecorder:
    """Records the delays the client would have slept."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@dataclass
class Harness:
    """A client wired to a fake transport, plus its recorded calls/sleeps."""

    client: JevClient
    transport: FakeTransport
    sleeps: list[float] = field(default_factory=list)


_DEFAULT_SLEEP = object()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test tries to open a real socket."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted during an offline test")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@pytest.fixture
def make_response():
    """Factory for :class:`FakeResponse`."""

    def _make(
        status_code: int = 200,
        payload: Any = None,
        *,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        return FakeResponse(status_code, payload, text=text, headers=headers)

    return _make


@pytest.fixture
def make_transport():
    """Factory for :class:`FakeTransport` (optionally without ``timeout``)."""

    def _make(*responses: Any, no_timeout: bool = False, repeat_last: bool = False) -> FakeTransport:
        cls = FakeTransportNoTimeout if no_timeout else FakeTransport
        return cls(*responses, repeat_last=repeat_last)

    return _make


@pytest.fixture
def make_client(make_transport):
    """Factory for a :class:`Harness`: client + fake transport + sleep recorder."""

    def _make(
        *responses: Any,
        transport: FakeTransport | None = None,
        sleep: Any = _DEFAULT_SLEEP,
        api_key: str = "test-key",
        no_timeout: bool = False,
        repeat_last: bool = False,
        **client_kwargs: Any,
    ) -> Harness:
        fake = transport if transport is not None else make_transport(
            *responses, no_timeout=no_timeout, repeat_last=repeat_last
        )
        recorder = SleepRecorder()
        if sleep is _DEFAULT_SLEEP:
            sleep_arg: Any = recorder
        else:
            sleep_arg = sleep
        client = JevClient(api_key, transport=fake, sleep=sleep_arg, **client_kwargs)
        return Harness(client=client, transport=fake, sleeps=recorder.delays)

    return _make


@pytest.fixture
def captured_response() -> dict[str, Any]:
    return json.loads(json.dumps(CAPTURED_RESPONSE))


@pytest.fixture
def captured_degraded() -> dict[str, Any]:
    return json.loads(json.dumps(CAPTURED_DEGRADED))


@pytest.fixture
def triage_questions():
    """The three questions of the captured triage request."""
    from system_one_dwarf.jev import choice, noul, score

    return {
        "is_bug": noul(
            "is_bug",
            "Is this report a defect?",
            "It describes behaviour that is broken.",
            "It is a question or a request for help.",
        ),
        "team": choice(
            "team",
            "Which team should handle it?",
            {
                "payments": "Checkout and billing.",
                "frontend": "Rendering and layout.",
                "account": "Login and profiles.",
                "other": "None of these teams.",
            },
        ),
        "urgency": score(
            "urgency",
            "How urgent is it?",
            ["Can wait", "This week", "Blocking now"],
        ),
    }
