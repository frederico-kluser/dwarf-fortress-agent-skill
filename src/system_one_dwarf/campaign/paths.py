"""Canonical filesystem layout of the campaign layer.

Decisions (authoritative, from the project brief):

* A campaign lives at ``campanhas/<slug>/`` and is itself a CoALA project root:
  its memory is ``campanhas/<slug>/.agents/<slug>-coala-agent-skill/memory/coala.sqlite``
  (canonical ``coala-install.py`` v3+ naming; the legacy ``<slug>-agent-skill`` dir
  kept by older installs is still resolved, see :func:`skill_dir`).
* The shared game-knowledge base lives at ``conhecimento/`` (own CoALA install,
  name ``conhecimento-dwarf``); it is read-only for us.
* Campaign secrets live in ``campanhas/<slug>/chaves.sqlite`` (file 0600, dir 0700).

Every path is derivable from the campaign root + campaign name, so nothing here
holds state.  Tests point ``SOD_CAMPANHAS_DIR`` at a temporary directory and
never touch the real ``campanhas/`` tree.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Repository root (``<repo>/src/system_one_dwarf/campaign/paths.py`` -> ``<repo>``).
REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_CAMPANHAS_DIR = REPO_ROOT / "campanhas"
DEFAULT_KNOWLEDGE_DIR = REPO_ROOT / "conhecimento"

#: CoALA install name of the shared game-knowledge base.
KNOWLEDGE_NAME = "conhecimento-dwarf"

#: Installer of the CoALA skill (installed per campaign as a subprocess).
DEFAULT_COALA_INSTALLER = (
    Path.home() / "Agent-Skills" / "coala-agent-skill" / "scripts" / "coala-install.py"
)

ENV_CAMPANHAS_DIR = "SOD_CAMPANHAS_DIR"
ENV_KNOWLEDGE_DIR = "SOD_KNOWLEDGE_DIR"
ENV_COALA_INSTALLER = "SOD_COALA_INSTALLER"
ENV_PYTHON = "SOD_PYTHON"

KEYS_DB_NAME = "chaves.sqlite"
SPEND_DB_NAME = "gastos.sqlite"
META_NAME = "meta.json"
OBJECTIVE_NAME = "objetivo.json"

#: Private permissions: directory 0700, SQLite files 0600.
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def coala_python() -> str:
    """Interpreter used for the CoALA CLI/installer subprocesses.

    Defaults to the literal ``python3`` (the canonical layout decision), then
    falls back to the running interpreter.
    """
    override = os.environ.get(ENV_PYTHON)
    if override:
        return override
    return shutil.which("python3") or sys.executable


def campanhas_dir() -> Path:
    """Root of the per-campaign data (``<repo>/campanhas`` unless overridden)."""
    override = os.environ.get(ENV_CAMPANHAS_DIR)
    return Path(override).expanduser() if override else DEFAULT_CAMPANHAS_DIR


def knowledge_dir() -> Path:
    """Root of the shared game-knowledge CoALA install (read-only for the agent)."""
    override = os.environ.get(ENV_KNOWLEDGE_DIR)
    return Path(override).expanduser() if override else DEFAULT_KNOWLEDGE_DIR


def coala_installer_path() -> Path:
    override = os.environ.get(ENV_COALA_INSTALLER)
    return Path(override).expanduser() if override else DEFAULT_COALA_INSTALLER


#: Canonical basename of a local CoALA skill (``coala-install.py`` v3+):
#: ``<name>-coala-agent-skill`` — the technique is always part of the name.
SKILL_SUFFIX = "-coala-agent-skill"
#: Legacy basename created by older installers (still resolved for old campaigns
#: and compat symlinks): ``<name>-agent-skill``.
LEGACY_SKILL_SUFFIX = "-agent-skill"


def skill_dirname(name: str) -> str:
    """Canonical skill-dir basename for ``name``: ``<name>-coala-agent-skill``."""
    return f"{name}{SKILL_SUFFIX}"


def legacy_skill_dirname(name: str) -> str:
    """Legacy skill-dir basename for ``name``: ``<name>-agent-skill``."""
    return f"{name}{LEGACY_SKILL_SUFFIX}"


def skill_dir(project: Path, name: str) -> Path:
    """``<project>/.agents/<skill>`` — the local CoALA skill dir.

    Resolves both naming generations: prefers the canonical
    ``<name>-coala-agent-skill`` when it exists on disk, falls back to the legacy
    ``<name>-agent-skill`` (older installs and compat symlinks left by upgrades),
    and reports the canonical path when nothing exists yet (what the installer
    will create).
    """
    base = Path(project) / ".agents"
    for dirname in (skill_dirname(name), legacy_skill_dirname(name)):
        candidate = base / dirname
        if candidate.exists():
            return candidate
    return base / skill_dirname(name)


def skill_script(project: Path, name: str) -> Path:
    """``<project>/.agents/<skill>/scripts/coala.py`` (resolved, see :func:`skill_dir`)."""
    return skill_dir(project, name) / "scripts" / "coala.py"


def memory_db(project: Path, name: str) -> Path:
    """``<project>/.agents/<skill>/memory/coala.sqlite`` (resolved, see :func:`skill_dir`)."""
    return skill_dir(project, name) / "memory" / "coala.sqlite"


def knowledge_memory_db() -> Path:
    """Memory DB of the shared knowledge base (may not exist yet — later piece)."""
    return memory_db(knowledge_dir(), KNOWLEDGE_NAME)


def knowledge_script() -> Path:
    """``coala.py`` of the shared knowledge base."""
    return skill_script(knowledge_dir(), KNOWLEDGE_NAME)


def campaign_dir(slug: str) -> Path:
    """``campanhas/<slug>`` (the campaign's CoALA project root)."""
    return campanhas_dir() / slug


def meta_path(root: Path) -> Path:
    return Path(root) / META_NAME


def keys_db(root: Path) -> Path:
    return Path(root) / KEYS_DB_NAME


def spend_db(root: Path) -> Path:
    return Path(root) / SPEND_DB_NAME


def objective_path(root: Path) -> Path:
    return Path(root) / OBJECTIVE_NAME


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and force mode 0700."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, PRIVATE_DIR_MODE)
    return path


def ensure_private_file(path: Path) -> Path:
    """Force mode 0600 on an existing file."""
    path = Path(path)
    if path.exists():
        os.chmod(path, PRIVATE_FILE_MODE)
    return path
