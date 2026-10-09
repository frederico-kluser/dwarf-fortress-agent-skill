"""Per-campaign key storage: masking, permissions, and never leaking the key."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from system_one_dwarf.campaign import KeyStore, mask_key
from system_one_dwarf.campaign.keys import (
    PROVIDERS,
    InvalidKey,
    UnknownProvider,
    validate_provider,
)
from system_one_dwarf.campaign.paths import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE

SECRET = "sk-or-v1-0123456789abcdef3f9a"


class TestMasking:
    def test_contract_example_mask(self):
        assert mask_key(SECRET) == "sk-or-v1-••••••••3f9a"

    def test_short_keys_keep_only_the_tail_hidden(self):
        assert mask_key("abc") == "••••••••"
        assert mask_key("abcd") == "••••••••"
        assert mask_key("abcdefghijkl") == "••••••••ijkl"

    def test_empty_key_masks_to_bullets(self):
        assert mask_key("") == "••••••••"

    def test_mask_never_contains_the_whole_key(self):
        masked = mask_key(SECRET)
        assert SECRET not in masked
        assert SECRET[:15] not in masked


class TestProviderValidation:
    def test_canonical_providers(self):
        assert PROVIDERS == ("openrouter", "typesafe", "deepseek")
        assert validate_provider("OpenRouter") == "openrouter"

    def test_unknown_provider_raises(self):
        with pytest.raises(UnknownProvider):
            validate_provider("anthropic")


@pytest.fixture
def store(tmp_path: Path) -> KeyStore:
    return KeyStore(tmp_path / "chaves.sqlite")


class TestKeyStore:
    def test_set_key_returns_only_the_masked_form(self, store):
        result = store.set_key("openrouter", SECRET)
        assert result == {"provider": "openrouter", "masked_key": "sk-or-v1-••••••••3f9a", "set": True}
        assert SECRET not in str(result)

    def test_get_key_is_the_only_full_accessor(self, store):
        store.set_key("openrouter", SECRET)
        assert store.get_key("openrouter") == SECRET
        assert store.masked_key("openrouter") == "sk-or-v1-••••••••3f9a"
        assert store.has_key("openrouter") is True

    def test_unset_provider_reads_are_empty(self, store):
        assert store.get_key("deepseek") is None
        assert store.masked_key("deepseek") is None
        assert store.has_key("deepseek") is False

    def test_list_masked_always_lists_the_three_providers(self, store):
        store.set_key("typesafe", "ts-live-abcdefgh1234")
        listing = store.list_masked()
        assert [item["provider"] for item in listing] == list(PROVIDERS)
        assert listing[0] == {"provider": "openrouter", "masked_key": None, "set": False}
        assert listing[1]["set"] is True and listing[1]["masked_key"].endswith("1234")
        assert listing[2]["set"] is False
        assert SECRET not in str(listing)

    def test_overwrite_keeps_a_single_row(self, store):
        store.set_key("openrouter", SECRET)
        store.set_key("openrouter", "sk-or-v1-ffffffffffff9999")
        conn = sqlite3.connect(store.db_path)
        rows = conn.execute("SELECT provider, masked FROM keys").fetchall()
        conn.close()
        assert rows == [("openrouter", "sk-or-v1-••••••••9999")]

    def test_delete_key(self, store):
        store.set_key("deepseek", "sk-deep-000011112222")
        assert store.delete_key("deepseek") is True
        assert store.has_key("deepseek") is False
        assert store.delete_key("deepseek") is False

    def test_empty_key_is_rejected(self, store):
        with pytest.raises(InvalidKey):
            store.set_key("openrouter", "   ")

    def test_unknown_provider_is_rejected(self, store):
        with pytest.raises(UnknownProvider):
            store.set_key("anthropic", SECRET)

    def test_file_is_0600_and_directory_0700(self, tmp_path: Path):
        db_path = tmp_path / "nested" / "chaves.sqlite"
        store = KeyStore(db_path)
        store.set_key("openrouter", SECRET)

        assert stat.S_IMODE(db_path.stat().st_mode) == PRIVATE_FILE_MODE
        assert stat.S_IMODE(db_path.parent.stat().st_mode) == PRIVATE_DIR_MODE

    def test_loose_permissions_are_tightened_on_open(self, tmp_path: Path):
        db_path = tmp_path / "chaves.sqlite"
        store = KeyStore(db_path)
        store.set_key("openrouter", SECRET)
        os.chmod(db_path, 0o644)

        store.has_key("openrouter")

        assert stat.S_IMODE(db_path.stat().st_mode) == PRIVATE_FILE_MODE

    def test_repr_leaks_nothing(self, store):
        store.set_key("openrouter", SECRET)
        text = repr(store)
        assert SECRET not in text and "3f9a" not in text
        assert "chaves.sqlite" in text

    def test_masked_column_never_holds_the_full_key(self, store):
        store.set_key("openrouter", SECRET)
        conn = sqlite3.connect(store.db_path)
        masked = conn.execute("SELECT masked FROM keys").fetchone()[0]
        conn.close()
        assert masked != SECRET and SECRET not in masked
