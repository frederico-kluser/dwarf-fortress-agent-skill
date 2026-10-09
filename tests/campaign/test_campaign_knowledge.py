"""Shared knowledge base: path reservation + read-only search helper.

The ``conhecimento/`` base is populated by a **later piece**; this module only
owns the path/config constants and the read-only search helper.
"""

from __future__ import annotations

from pathlib import Path

from fakes import FakeCoalaCli
from system_one_dwarf.campaign import KNOWLEDGE_NAME, knowledge_dir, search_knowledge
from system_one_dwarf.campaign.memory import KNOWLEDGE_LIMIT
from system_one_dwarf.campaign import memory as memory_module
from system_one_dwarf.campaign.paths import knowledge_memory_db, knowledge_script


class TestLayout:
    def test_knowledge_name_constant(self):
        assert KNOWLEDGE_NAME == "conhecimento-dwarf"

    def test_paths_follow_the_coala_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "conhecimento"))
        skill = tmp_path / "conhecimento" / ".agents" / "conhecimento-dwarf-agent-skill"
        assert knowledge_dir() == tmp_path / "conhecimento"
        assert knowledge_script() == skill / "scripts" / "coala.py"
        assert knowledge_memory_db() == skill / "memory" / "coala.sqlite"

    def test_default_knowledge_dir_is_inside_the_repo(self):
        from system_one_dwarf.campaign.paths import DEFAULT_KNOWLEDGE_DIR, REPO_ROOT

        assert DEFAULT_KNOWLEDGE_DIR == REPO_ROOT / "conhecimento"


class TestSearch:
    def test_missing_base_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "absent"))
        assert search_knowledge("how do I build a well?") == []

    def test_read_only_search_over_the_installed_base(self, tmp_path, monkeypatch):
        skill = tmp_path / "conhecimento" / ".agents" / "conhecimento-dwarf-agent-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "memory").mkdir(parents=True)
        (skill / "scripts" / "coala.py").write_text("# fake\n")
        (skill / "memory" / "coala.sqlite").write_bytes(b"fake")
        monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "conhecimento"))

        calls: list[list[str]] = []

        class Cli(FakeCoalaCli):
            def run(self, args, *, json_output=True):
                calls.append(list(args))
                return {"results": [{"id": 1, "content": "Wells need a bucket."}]}

        monkeypatch.setattr(memory_module, "CoalaCli", lambda script, db=None: Cli(script))

        results = search_knowledge("well", limit=3)

        assert results == [{"id": 1, "content": "Wells need a bucket."}]
        assert calls[0][:4] == ["search", "well", "--limit", "3"]
        # the helper never writes: only `search` is ever invoked
        assert {call[0] for call in calls} == {"search"}

    def test_failing_base_is_swallowed(self, tmp_path, monkeypatch):
        skill = tmp_path / "conhecimento" / ".agents" / "conhecimento-dwarf-agent-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "memory").mkdir(parents=True)
        (skill / "scripts" / "coala.py").write_text("# fake\n")
        (skill / "memory" / "coala.sqlite").write_bytes(b"fake")
        monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "conhecimento"))

        class Cli(FakeCoalaCli):
            def run(self, args, *, json_output=True):
                from system_one_dwarf.campaign import CoalaCallError

                raise CoalaCallError("knowledge base is busy", argv=list(args))

        monkeypatch.setattr(memory_module, "CoalaCli", lambda script, db=None: Cli(script))

        assert search_knowledge("anything") == []

    def test_memory_store_delegates_to_the_helper(self, tmp_path, monkeypatch):
        from system_one_dwarf.campaign import MemoryStore

        monkeypatch.setattr(
            memory_module, "search_knowledge", lambda query, limit=10: [{"query": query, "limit": limit}]
        )
        store = MemoryStore(tmp_path, "urist", cli=FakeCoalaCli())
        assert store.search_knowledge("well", limit=2) == [{"query": "well", "limit": 2}]


class TestLargeRecords:
    """Knowledge records are whole wiki articles: the CLI truncates at 48 KB."""

    def _install_fake_base(self, tmp_path, monkeypatch):
        skill = tmp_path / "conhecimento" / ".agents" / "conhecimento-dwarf-agent-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "memory").mkdir(parents=True)
        (skill / "scripts" / "coala.py").write_text("# fake\n")
        (skill / "memory" / "coala.sqlite").write_bytes(b"fake")
        monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "conhecimento"))

    def test_truncated_output_retries_with_a_smaller_top_k(self, tmp_path, monkeypatch):
        from system_one_dwarf.campaign import CoalaCallError

        self._install_fake_base(tmp_path, monkeypatch)
        seen: list[int] = []

        class Cli(FakeCoalaCli):
            def run(self, args, *, json_output=True, tolerate_non_json=False):
                limit = int(args[args.index("--limit") + 1])
                seen.append(limit)
                if limit > 1:
                    raise CoalaCallError("output exceeded 48 KB", argv=list(args))
                return {"results": [{"id": 1, "content": "# Well"}]}

        monkeypatch.setattr(memory_module, "CoalaCli", lambda script, db=None: Cli(script))

        assert search_knowledge("well", limit=4) == [{"id": 1, "content": "# Well"}]
        assert seen == [4, 2, 1]

    def test_default_limit_is_small(self, tmp_path, monkeypatch):
        self._install_fake_base(tmp_path, monkeypatch)
        seen: list[int] = []

        class Cli(FakeCoalaCli):
            def run(self, args, *, json_output=True, tolerate_non_json=False):
                seen.append(int(args[args.index("--limit") + 1]))
                return {"results": []}

        monkeypatch.setattr(memory_module, "CoalaCli", lambda script, db=None: Cli(script))
        search_knowledge("well")
        assert seen == [KNOWLEDGE_LIMIT] and KNOWLEDGE_LIMIT <= 5

    def test_giving_up_is_still_empty(self, tmp_path, monkeypatch):
        from system_one_dwarf.campaign import CoalaCallError

        self._install_fake_base(tmp_path, monkeypatch)

        class Cli(FakeCoalaCli):
            def run(self, args, *, json_output=True, tolerate_non_json=False):
                raise CoalaCallError("still too big", argv=list(args))

        monkeypatch.setattr(memory_module, "CoalaCli", lambda script, db=None: Cli(script))
        assert search_knowledge("well", limit=8) == []


def test_knowledge_dir_is_not_the_campaign_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOD_CAMPANHAS_DIR", str(tmp_path / "campanhas"))
    monkeypatch.setenv("SOD_KNOWLEDGE_DIR", str(tmp_path / "conhecimento"))
    from system_one_dwarf.campaign import campanhas_dir

    assert Path(campanhas_dir()) != Path(knowledge_dir())
