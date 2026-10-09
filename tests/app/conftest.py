"""Shared fixtures for the API tests (offline: fake installer + fake CoALA CLI)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"
for _path in (str(SRC), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from fakes import FakeCoalaCli, FakeInstaller, FakeTranslator  # noqa: E402
from system_one_dwarf.app.main import create_app  # noqa: E402


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "integration: needs the real CoALA installer/CLI (opt-in offline run)"
    )


@pytest.fixture
def campaigns_root(tmp_path: Path) -> Path:
    """A throwaway ``campanhas/`` root — the real one is never touched by tests."""
    return tmp_path / "campanhas"


@pytest.fixture
def fake_cli() -> FakeCoalaCli:
    return FakeCoalaCli()


@pytest.fixture
def fake_installer() -> FakeInstaller:
    return FakeInstaller()


@pytest.fixture
def make_app(campaigns_root: Path, fake_cli: FakeCoalaCli, fake_installer: FakeInstaller):
    """Factory: ``make_app(translator=..., cli=..., loop_builder=...)`` -> FastAPI app."""

    def _make(*, translator=None, cli=None, installer=None, translator_factory=None,
              loop_builder=None, loop_stop_timeout=5.0):
        return create_app(
            campaigns_root=campaigns_root,
            installer=installer if installer is not None else fake_installer,
            cli_factory=lambda _root, _slug: cli if cli is not None else fake_cli,
            translator=translator,
            translator_factory=translator_factory,
            loop_builder=loop_builder,
            loop_stop_timeout=loop_stop_timeout,
        )

    return _make


@pytest.fixture
def make_client(make_app):
    """Factory yielding an entered ``TestClient`` (lifespan runs, root created)."""

    def _make(**kwargs) -> TestClient:
        client = TestClient(make_app(**kwargs))
        client.__enter__()
        return client

    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def campaign(client: TestClient) -> dict:
    """A freshly created campaign object (API shape)."""
    response = client.post(
        "/api/campaigns", json={"name": "Urist McAdventurer", "is_new_game": True}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def translator() -> FakeTranslator:
    return FakeTranslator()
