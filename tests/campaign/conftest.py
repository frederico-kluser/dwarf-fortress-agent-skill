"""Shared fixtures for the campaign-layer tests (offline: fake installer + CLI)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP_TESTS = REPO / "tests" / "app"
for _path in (str(REPO / "src"), str(APP_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from fakes import FakeCoalaCli, FakeInstaller, FakeTranslator  # noqa: E402
from system_one_dwarf.campaign import CampaignManager  # noqa: E402


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "integration: needs the real CoALA installer/CLI (opt-in offline run)"
    )


@pytest.fixture
def campaigns_root(tmp_path: Path) -> Path:
    return tmp_path / "campanhas"


@pytest.fixture
def fake_cli() -> FakeCoalaCli:
    return FakeCoalaCli()


@pytest.fixture
def fake_installer() -> FakeInstaller:
    return FakeInstaller()


@pytest.fixture
def manager(campaigns_root: Path, fake_cli: FakeCoalaCli, fake_installer: FakeInstaller, translator) -> CampaignManager:
    return CampaignManager(
        root=campaigns_root,
        installer=fake_installer,
        cli_factory=lambda _root, _slug: fake_cli,
        translator=translator,
    )


@pytest.fixture
def translator() -> FakeTranslator:
    return FakeTranslator()


@pytest.fixture
def campaign(manager: CampaignManager):
    created, _is_new = manager.create("Urist McAdventurer", True)
    return created
