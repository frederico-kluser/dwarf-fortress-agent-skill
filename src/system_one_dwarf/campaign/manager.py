"""Campaign lifecycle: create / load / list, slug validation, CoALA installer.

Canonical layout (authoritative):

* ``campanhas/<slug>/`` is the campaign root **and** a CoALA project root.
* The CoALA skill is installed with the real installer, as a subprocess::

      python3 ~/Agent-Skills/coala-agent-skill/scripts/coala-install.py \\
          install --project <abs campanhas/<slug>> --name <slug>

  The installer is idempotent, so ``create()`` can always call it.  If the
  installer is missing the error message names the missing path.
* ``campanhas/<slug>/meta.json`` holds ``id``, ``name``, ``slug``,
  ``created_at``, ``updated_at``, ``is_new_game``, ``mode`` and ``kill_switch``
  (the last two back the control panel / kill-switch).

``is_new_game`` semantics: ``True`` = a new game, the memory directory is
created fresh and the objective is seeded; ``False`` = attach to the memory of an
existing gameplay — a campaign that does not exist raises ``CampaignNotFound``
(the route renders the contract's ``404 {"detail": "campaign not found"}``).
Attaching is **never destructive**: the installer is idempotent and existing
memory records are kept.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .clock import utc_now_iso
from .keys import KeyStore
from .memory import IdentityTranslator, MemoryStore, Translator
from .objective import ObjectiveStore
from .paths import (
    campanhas_dir,
    coala_installer_path,
    coala_python,
    ensure_private_dir,
    keys_db,
    meta_path,
    skill_script,
    spend_db,
)
from .spend import SpendLedger

log = logging.getLogger("system_one_dwarf.campaign.manager")

MODES: tuple[str, ...] = ("autopilot", "checkpoint", "paused")
DEFAULT_MODE = "autopilot"

SLUG_MAX_LEN = 63
_SLUG_RE = re.compile(rf"^[a-z0-9](?:[a-z0-9-]{{0,{SLUG_MAX_LEN - 2}}}[a-z0-9])?$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

_INSTALL_TIMEOUT = 300.0


class CampaignError(RuntimeError):
    """Base class for campaign-layer failures."""


class InvalidSlug(CampaignError, ValueError):
    """The campaign name cannot be turned into a safe directory slug."""


class CampaignNotFound(CampaignError, LookupError):
    """No campaign with that id/slug."""


class InstallerMissing(CampaignError):
    """The CoALA installer does not exist — the message names the path."""


def slugify(name: str) -> str:
    """``"Urist McAdventurer"`` -> ``"urist-mcadventurer"`` (ASCII, dash-separated)."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP_RE.sub("-", text).strip("-")
    return slug[:SLUG_MAX_LEN].strip("-")


def validate_slug(slug: str) -> str:
    """Validate a directory slug; only ``[a-z0-9-]``, no leading/trailing dash."""
    slug = str(slug or "")
    if not slug or not _SLUG_RE.match(slug) or ".." in slug:
        raise InvalidSlug(
            f"invalid campaign slug {slug!r}: use letters, digits and dashes "
            f"(max {SLUG_MAX_LEN} chars)"
        )
    return slug


@dataclass(frozen=True)
class Campaign:
    """One campaign (mirror of ``meta.json``)."""

    id: str
    slug: str
    name: str
    is_new_game: bool
    created_at: str
    updated_at: str
    mode: str = DEFAULT_MODE
    kill_switch: bool = False
    root: Path = Path(".")

    @property
    def control(self) -> dict[str, Any]:
        return {"mode": self.mode, "kill_switch": self.kill_switch}

    def to_api(self) -> dict[str, Any]:
        """Exactly the ``GET /api/campaigns`` item shape."""
        return {
            "id": self.id,
            "name": self.name,
            "is_new_game": self.is_new_game,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "control": self.control,
        }


class CoalaInstaller:
    """Runs ``coala-install.py install`` (idempotent) for a campaign root."""

    def __init__(
        self,
        installer_path: Path | str | None = None,
        *,
        python: str | None = None,
        timeout: float = _INSTALL_TIMEOUT,
    ) -> None:
        self.path = Path(installer_path) if installer_path else coala_installer_path()
        self.python = python or coala_python()
        self.timeout = timeout

    def ensure_available(self) -> Path:
        if not self.path.exists():
            raise InstallerMissing(
                f"CoALA installer not found at {self.path} — expected "
                f"~/Agent-Skills/coala-agent-skill/scripts/coala-install.py "
                f"(override with SOD_COALA_INSTALLER)"
            )
        return self.path

    def install(self, project: Path | str, name: str) -> dict[str, Any]:
        """Install/refresh the local CoALA skill of ``project`` (idempotent)."""
        self.ensure_available()
        project = Path(project)
        argv = [
            self.python,
            str(self.path),
            "install",
            "--project",
            str(project.resolve()),
            "--name",
            name,
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                cwd=str(project.parent if project.parent.exists() else Path.cwd()),
            )
        except subprocess.TimeoutExpired as exc:
            raise CampaignError(
                f"CoALA installer timed out after {self.timeout}s for {project}"
            ) from exc
        if proc.returncode != 0:
            raise CampaignError(
                f"CoALA installer failed (exit {proc.returncode}) for {project}: "
                f"{(proc.stderr or proc.stdout or '').strip()[-400:]}"
            )
        return {
            "ok": True,
            "argv": argv,
            "stdout": proc.stdout,
            "skill": str(skill_script(project, name)),
        }


class CampaignManager:
    """Registry of campaigns on disk (``campanhas/<slug>/meta.json``)."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        installer: Any | None = None,
        python: str | None = None,
        translator: Translator | None = None,
        cli_factory: Callable[[Path, str], Any] | None = None,
    ) -> None:
        self._root = Path(root) if root is not None else campanhas_dir()
        self.installer = installer if installer is not None else CoalaInstaller(python=python)
        self.python = python or coala_python()
        self.translator: Translator = translator or IdentityTranslator()
        #: Optional seam: ``(campaign_root, slug) -> CoALA CLI object``.  Tests inject
        #: a fake CLI here; production leaves it ``None`` (the real ``coala.py``).
        self.cli_factory = cli_factory

    # ------------------------------------------------------------------- location

    @property
    def root(self) -> Path:
        return self._root

    def ensure_root(self) -> Path:
        """Create the campaigns root (runtime data — gitignored, never committed)."""
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    # -------------------------------------------------------------------- loading

    def _load(self, root: Path) -> Campaign | None:
        meta_file = meta_path(root)
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("unreadable campaign meta at %s — skipping", meta_file)
            return None
        if not isinstance(data, dict) or not data.get("id"):
            return None
        mode = data.get("mode") if data.get("mode") in MODES else DEFAULT_MODE
        return Campaign(
            id=str(data["id"]),
            slug=str(data.get("slug") or root.name),
            name=str(data.get("name") or root.name),
            is_new_game=bool(data.get("is_new_game", False)),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or data.get("created_at") or ""),
            mode=mode,
            kill_switch=bool(data.get("kill_switch", False)),
            root=root,
        )

    def _write_meta(self, campaign: Campaign) -> None:
        payload = {
            "id": campaign.id,
            "slug": campaign.slug,
            "name": campaign.name,
            "is_new_game": campaign.is_new_game,
            "created_at": campaign.created_at,
            "updated_at": campaign.updated_at,
            "mode": campaign.mode,
            "kill_switch": campaign.kill_switch,
        }
        ensure_private_dir(campaign.root)
        target = meta_path(campaign.root)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)

    def list_campaigns(self) -> list[Campaign]:
        if not self._root.exists():
            return []
        found = []
        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            campaign = self._load(entry)
            if campaign is not None:
                found.append(campaign)
        found.sort(key=lambda c: (c.created_at, c.slug), reverse=True)
        return found

    def get(self, campaign_id: str) -> Campaign | None:
        for campaign in self.list_campaigns():
            if campaign.id == campaign_id or campaign.slug == campaign_id:
                return campaign
        return None

    def require(self, campaign_id: str) -> Campaign:
        campaign = self.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"campaign not found: {campaign_id}")
        return campaign

    def get_by_slug(self, slug: str) -> Campaign | None:
        return self._load(self._root / validate_slug(slug))

    # -------------------------------------------------------------------- create

    def create(self, name: str, is_new_game: bool = True) -> tuple[Campaign, bool]:
        """Create or attach a campaign; returns ``(campaign, created)``."""
        slug = validate_slug(slugify(name))
        root = self._root / slug
        existing = self._load(root)
        if existing is None and not is_new_game:
            raise CampaignNotFound(f"campaign not found: {name}")

        created = existing is None
        now = utc_now_iso()
        if created:
            ensure_private_dir(root)

        # Idempotent install: safe on every create/attach.
        self.installer.install(root, slug)

        if created:
            campaign = Campaign(
                id=str(uuid.uuid4()),
                slug=slug,
                name=str(name).strip() or slug,
                is_new_game=bool(is_new_game),
                created_at=now,
                updated_at=now,
                mode=DEFAULT_MODE,
                kill_switch=False,
                root=root,
            )
            self._write_meta(campaign)
        else:
            campaign = Campaign(**{**existing.__dict__, "updated_at": now})
            self._write_meta(campaign)

        if is_new_game:
            # Fresh memory seeded with the objective (never destructive).
            self.objective(campaign).seed(campaign.name)
        return campaign, created

    # ------------------------------------------------------------------- control

    def set_control(
        self,
        campaign: Campaign,
        *,
        mode: str | None = None,
        kill_switch: bool | None = None,
    ) -> dict[str, Any]:
        """Persist the autonomy mode / kill-switch; returns the contract payload.

        The merge base is the *persisted* state (``meta.json``), so a partial
        update from a stale caller can never resurrect an older mode.
        """
        current = self._load(campaign.root) or campaign
        new_mode = current.mode if mode is None else str(mode)
        if new_mode not in MODES:
            raise CampaignError(f"invalid mode {new_mode!r}; expected one of {', '.join(MODES)}")
        updated = Campaign(
            **{
                **current.__dict__,
                "mode": new_mode,
                "kill_switch": current.kill_switch if kill_switch is None else bool(kill_switch),
                "updated_at": utc_now_iso(),
            }
        )
        self._write_meta(updated)
        return {"mode": updated.mode, "kill_switch": updated.kill_switch, "ts": updated.updated_at}

    # ---------------------------------------------------------------- sub-stores

    def memory(self, campaign: Campaign, translator: Translator | None = None) -> MemoryStore:
        cli = self.cli_factory(campaign.root, campaign.slug) if self.cli_factory else None
        return MemoryStore(
            campaign.root,
            campaign.slug,
            cli=cli,
            translator=translator or self.translator,
        )

    def objective(self, campaign: Campaign, translator: Translator | None = None) -> ObjectiveStore:
        return ObjectiveStore(
            campaign.root,
            memory=self.memory(campaign, translator),
            translator=translator or self.translator,
        )

    def keys(self, campaign: Campaign) -> KeyStore:
        return KeyStore(keys_db(campaign.root))

    def spend(self, campaign: Campaign) -> SpendLedger:
        return SpendLedger(spend_db(campaign.root))

    # ------------------------------------------------------------------ lifecycle

    def mode_of(self, campaign_id: str) -> str:
        return self.require(campaign_id).mode


def default_manager() -> CampaignManager:
    """Manager bound to the canonical ``campanhas/`` root (env-overridable)."""
    return CampaignManager()


__all__ = [
    "Campaign",
    "CampaignError",
    "CampaignManager",
    "CampaignNotFound",
    "CoalaInstaller",
    "InstallerMissing",
    "InvalidSlug",
    "MODES",
    "default_manager",
    "slugify",
    "validate_slug",
]