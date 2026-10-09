"""Campaign lifecycle: slugs, meta.json, installer subprocess, control state."""

from __future__ import annotations

import json
import stat
import subprocess

import pytest

from system_one_dwarf.campaign import (
    CampaignError,
    CampaignManager,
    CampaignNotFound,
    CoalaInstaller,
    InstallerMissing,
    InvalidSlug,
    slugify,
    validate_slug,
)
from system_one_dwarf.campaign import manager as manager_module
from system_one_dwarf.campaign.paths import PRIVATE_DIR_MODE


class TestSlugs:
    def test_slugify_normalises_names(self):
        assert slugify("Urist McAdventurer") == "urist-mcadventurer"
        assert slugify("  Boatmurdered!!  ") == "boatmurdered"
        assert slugify("João Ção") == "joao-cao"
        assert slugify("a/b/../c") == "a-b-c"
        assert slugify("---") == ""

    def test_slugify_truncates_to_63_chars(self):
        assert len(slugify("x" * 200)) == 63

    @pytest.mark.parametrize("slug", ["urist", "a1", "x" * 63])
    def test_validate_slug_accepts_safe_values(self, slug):
        assert validate_slug(slug) == slug

    @pytest.mark.parametrize("slug", ["", "Urist", "-lead", "trail-", "a b", "../etc", "x" * 64])
    def test_validate_slug_rejects_everything_else(self, slug):
        with pytest.raises(InvalidSlug):
            validate_slug(slug)


class TestCreate:
    def test_create_new_game_writes_meta_and_runs_installer(self, manager, campaigns_root, fake_installer):
        campaign, created = manager.create("Urist McAdventurer", True)

        assert created is True
        assert campaign.slug == "urist-mcadventurer"
        assert campaign.is_new_game is True
        assert campaign.mode == "autopilot" and campaign.kill_switch is False
        assert campaign.created_at.endswith("Z") and campaign.updated_at.endswith("Z")

        root = campaigns_root / "urist-mcadventurer"
        assert root.is_dir()
        assert stat.S_IMODE(root.stat().st_mode) == PRIVATE_DIR_MODE

        meta = json.loads((root / "meta.json").read_text())
        assert meta["id"] == campaign.id
        assert meta["name"] == "Urist McAdventurer"
        assert meta["is_new_game"] is True
        assert meta["mode"] == "autopilot"

        assert fake_installer.calls == [
            {"project": str(root), "name": "urist-mcadventurer"}
        ]

    def test_create_seeds_the_objective_mirror(self, manager, campaigns_root, fake_cli):
        manager.create("Urist McAdventurer", True)
        mirror = campaigns_root / "urist-mcadventurer" / "objetivo.json"
        assert json.loads(mirror.read_text()) == {"narrative": "", "subgoals": []}
        # ... and one semantic record in the campaign memory
        added = fake_cli.args_of("add")
        assert len(added) == 1
        assert "objective:index" in added[0]

    def test_create_is_idempotent_and_non_destructive(self, manager, fake_installer, fake_cli):
        first, created_first = manager.create("Urist McAdventurer", True)
        fake_cli.run(["add", "--type", "episodic", "--content", "Kept episode.", "--tags", "episode"])

        second, created_second = manager.create("Urist McAdventurer", True)

        assert created_first is True and created_second is False
        assert second.id == first.id
        assert len(fake_installer.calls) == 2  # installer is idempotent, called again
        assert any("Kept episode." == e["content"] for e in fake_cli.entries)

    def test_attach_existing_memory(self, manager):
        first, _ = manager.create("Urist McAdventurer", True)
        attached, created = manager.create("Urist McAdventurer", False)
        assert created is False
        assert attached.id == first.id

    def test_attach_unknown_campaign_is_not_found(self, manager):
        with pytest.raises(CampaignNotFound):
            manager.create("Never Played", False)

    def test_installer_failure_propagates_and_writes_no_meta(self, campaigns_root, fake_cli):
        from fakes import FakeInstaller

        manager = CampaignManager(
            root=campaigns_root,
            installer=FakeInstaller(fail=True),
            cli_factory=lambda _root, _slug: fake_cli,
        )
        with pytest.raises(CampaignError):
            manager.create("Doomed", True)
        assert not (campaigns_root / "doomed" / "meta.json").exists()

    def test_invalid_name_is_rejected(self, manager):
        with pytest.raises(InvalidSlug):
            manager.create("!!!", True)


class TestLoading:
    def test_list_get_and_require(self, manager):
        campaign, _ = manager.create("Urist McAdventurer", True)

        listed = manager.list_campaigns()
        assert [c.id for c in listed] == [campaign.id]
        assert manager.get(campaign.id).slug == "urist-mcadventurer"
        assert manager.get("urist-mcadventurer").id == campaign.id
        assert manager.get("nope") is None
        assert manager.require(campaign.id).name == "Urist McAdventurer"
        with pytest.raises(CampaignNotFound):
            manager.require("nope")

    def test_list_ignores_directories_without_meta(self, manager, campaigns_root):
        manager.create("Urist McAdventurer", True)
        (campaigns_root / "stray-dir").mkdir(parents=True)
        (campaigns_root / "stray-file.txt").write_text("hi")
        assert len(manager.list_campaigns()) == 1

    def test_corrupt_meta_is_skipped(self, manager, campaigns_root):
        manager.create("Urist McAdventurer", True)
        (campaigns_root / "broken").mkdir()
        (campaigns_root / "broken" / "meta.json").write_text("{not json")
        assert len(manager.list_campaigns()) == 1

    def test_missing_root_lists_nothing(self, campaigns_root, fake_cli):
        manager = CampaignManager(root=campaigns_root, cli_factory=lambda _r, _s: fake_cli)
        assert manager.list_campaigns() == []
        assert manager.ensure_root().is_dir()


class TestControl:
    def test_set_control_persists_to_meta(self, manager, campaigns_root):
        campaign, _ = manager.create("Urist McAdventurer", True)

        payload = manager.set_control(campaign, mode="checkpoint", kill_switch=True)

        assert payload["mode"] == "checkpoint"
        assert payload["kill_switch"] is True
        assert payload["ts"].endswith("Z")
        meta = json.loads((campaigns_root / "urist-mcadventurer" / "meta.json").read_text())
        assert meta["mode"] == "checkpoint" and meta["kill_switch"] is True
        reloaded = manager.require(campaign.id)
        assert reloaded.control == {"mode": "checkpoint", "kill_switch": True}

    def test_set_control_partial_update_keeps_the_rest(self, manager):
        campaign, _ = manager.create("Urist McAdventurer", True)
        manager.set_control(campaign, mode="paused")
        assert manager.set_control(campaign, kill_switch=True)["mode"] == "paused"

    def test_invalid_mode_is_rejected(self, manager):
        campaign, _ = manager.create("Urist McAdventurer", True)
        with pytest.raises(CampaignError):
            manager.set_control(campaign, mode="yolo")


class TestInstallerSubprocess:
    def test_install_builds_the_canonical_argv(self, tmp_path, monkeypatch):
        installer_script = tmp_path / "coala-install.py"
        installer_script.write_text("# stub\n")
        project = tmp_path / "campanhas" / "urist"
        project.mkdir(parents=True)
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0, stdout="APLICADO", stderr="")

        monkeypatch.setattr(manager_module.subprocess, "run", fake_run)

        result = CoalaInstaller(installer_script, python="python3").install(project, "urist")

        assert seen["argv"] == [
            "python3",
            str(installer_script),
            "install",
            "--project",
            str(project.resolve()),
            "--name",
            "urist",
        ]
        assert seen["kwargs"]["check"] is False
        assert result["ok"] is True

    def test_install_non_zero_exit_raises(self, tmp_path, monkeypatch):
        installer_script = tmp_path / "coala-install.py"
        installer_script.write_text("# stub\n")
        project = tmp_path / "p"
        project.mkdir()

        monkeypatch.setattr(
            manager_module.subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 3, stdout="", stderr="boom"),
        )
        with pytest.raises(CampaignError) as excinfo:
            CoalaInstaller(installer_script).install(project, "p")
        assert "exit 3" in str(excinfo.value) and "boom" in str(excinfo.value)

    def test_missing_installer_names_the_path(self, tmp_path):
        missing = tmp_path / "nope" / "coala-install.py"
        installer = CoalaInstaller(missing)
        with pytest.raises(InstallerMissing) as excinfo:
            installer.ensure_available()
        assert str(missing) in str(excinfo.value)

    def test_missing_installer_fails_the_create(self, campaigns_root, fake_cli, tmp_path):
        manager = CampaignManager(
            root=campaigns_root,
            installer=CoalaInstaller(tmp_path / "gone" / "coala-install.py"),
            cli_factory=lambda _r, _s: fake_cli,
        )
        with pytest.raises(InstallerMissing):
            manager.create("Urist", True)
