"""Campaign layer: CoALA memory per campaign, API keys, spend ledger, objective.

Import surface used by the app layer (and by the other pieces)::

    from system_one_dwarf.campaign import (
        CampaignManager, Campaign, CampaignNotFound, InvalidSlug, InstallerMissing,
        KeyStore, SpendLedger, MemoryStore, ObjectiveStore,
        Translator, IdentityTranslator, search_knowledge,
    )

Nothing in this package talks to a model or to the game: it is storage plus the
CoALA CLI subprocess wrapper.
"""

from __future__ import annotations

from .clock import to_z, utc_now_iso
from .keys import PROVIDERS, InvalidKey, KeyStore, UnknownProvider, mask_key, validate_provider
from .manager import (
    MODES,
    Campaign,
    CampaignError,
    CampaignManager,
    CampaignNotFound,
    CoalaInstaller,
    InstallerMissing,
    InvalidSlug,
    default_manager,
    slugify,
    validate_slug,
)
from .bundle import MemoryBundle, Observation
from .dialogue import DialogueLine, DialogueMemory, ProfileCard
from .extraction import ExtractedDraft, ExtractedFact, ExtractionStore, rule_extractor
from .logbook import LogEvent, Logbook
from .memory import (
    KIND_EPISODIC,
    KIND_PROCEDURAL,
    KIND_SEMANTIC,
    CoalaCallError,
    CoalaCli,
    CoalaError,
    CoalaMissing,
    Episode,
    EpisodicStore,
    IdentityTranslator,
    MemoryStore,
    Translator,
    episode_importance,
    search_knowledge,
)
from .objective import ObjectiveStore, normalize_subgoals
from .paths import (
    KNOWLEDGE_NAME,
    campanhas_dir,
    campaign_dir,
    coala_installer_path,
    knowledge_dir,
)
from .recall import RETRIEVAL_WEIGHTS, ScoreWeights, combined_score
from .spend import SpendLedger, money

__all__ = [
    "Campaign",
    "CampaignError",
    "CampaignManager",
    "CampaignNotFound",
    "CoalaCallError",
    "CoalaCli",
    "CoalaError",
    "CoalaInstaller",
    "CoalaMissing",
    "DialogueLine",
    "DialogueMemory",
    "Episode",
    "EpisodicStore",
    "ExtractedDraft",
    "ExtractedFact",
    "ExtractionStore",
    "IdentityTranslator",
    "InstallerMissing",
    "InvalidKey",
    "InvalidSlug",
    "KIND_EPISODIC",
    "KIND_PROCEDURAL",
    "KIND_SEMANTIC",
    "KNOWLEDGE_NAME",
    "KeyStore",
    "LogEvent",
    "Logbook",
    "MODES",
    "MemoryBundle",
    "MemoryStore",
    "ObjectiveStore",
    "Observation",
    "PROVIDERS",
    "ProfileCard",
    "RETRIEVAL_WEIGHTS",
    "ScoreWeights",
    "SpendLedger",
    "Translator",
    "UnknownProvider",
    "campanhas_dir",
    "campaign_dir",
    "coala_installer_path",
    "combined_score",
    "default_manager",
    "episode_importance",
    "knowledge_dir",
    "mask_key",
    "money",
    "normalize_subgoals",
    "rule_extractor",
    "search_knowledge",
    "slugify",
    "to_z",
    "utc_now_iso",
    "validate_provider",
    "validate_slug",
]
