"""Memory wrapper: subprocess argv, JSON parsing, supersede/edit, translator seam."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from fakes import ExplodingTranslator, FakeCoalaCli, MarkerTranslator
from system_one_dwarf.campaign import (
    CoalaCallError,
    CoalaCli,
    CoalaMissing,
    IdentityTranslator,
    MemoryStore,
    Translator,
)
from system_one_dwarf.campaign.memory import safe_translate

RECORDED_AT = "2026-10-01T00:00:05.000Z"


@pytest.fixture
def cli() -> FakeCoalaCli:
    return FakeCoalaCli()


@pytest.fixture
def memory(tmp_path: Path, cli: FakeCoalaCli) -> MemoryStore:
    return MemoryStore(tmp_path / "campanhas" / "urist", "urist", cli=cli)


class TestCliContract:
    def test_add_episode_uses_the_documented_argv(self, memory, cli):
        memory.add_episode("Climbed the hill.", key="ep-001", origin="agent")

        assert cli.args_of("add") == [
            [
                "add",
                "--type",
                "episodic",
                "--content",
                "Climbed the hill.",
                "--origin",
                "agent",
                "--key",
                "ep-001",
                "--tags",
                "episode",
                "--json",
            ]
        ]

    def test_add_fact_and_procedure_defaults(self, memory, cli):
        memory.add_fact("The river is north.", key="fact-river")
        memory.add_procedure("Use 'act goto' for travel.", key="proc-goto")

        assert "--type" in cli.args_of("add")[0] and "semantic" in cli.args_of("add")[0]
        assert "procedural" in cli.args_of("add")[1]
        assert "fact" in cli.args_of("add")[0]
        assert "procedure" in cli.args_of("add")[1]

    def test_add_entry_rejects_unknown_kinds(self, memory):
        with pytest.raises(ValueError):
            memory.add_entry("telepathic", "nope")

    def test_recall_and_search_argv(self, memory, cli):
        memory.recall("what do I know", budget=1500, top=5, kind="episodic", tags="episode")
        memory.search("river", limit=3, kind="semantic", include_superseded=True)

        assert cli.args_of("recall") == [
            [
                "recall",
                "--budget",
                "1500",
                "--top",
                "5",
                "--type",
                "episodic",
                "--tags",
                "episode",
                "what do I know",
                "--json",
            ]
        ]
        assert cli.args_of("search") == [
            [
                "search",
                "river",
                "--limit",
                "3",
                "--type",
                "semantic",
                "--include-superseded",
                "--json",
            ]
        ]

    def test_supersede_argv(self, memory, cli):
        cli.run(["add", "--type", "episodic", "--content", "First.", "--key", "ep-1", "--tags", "episode"])
        memory.supersede(cli.entries[0]["id"], "Second.", origin="owner", tags="episode")

        assert cli.args_of("supersede") == [
            [
                "supersede",
                "1",
                "--content",
                "Second.",
                "--origin",
                "owner",
                "--tags",
                "episode",
                "--json",
            ]
        ]

    def test_stats_and_export(self, memory, cli):
        memory.add_fact("A fact.")
        assert memory.stats()["entries"]["total"] == 1
        assert memory.export()["entries"][0]["content"] == "A fact."


class TestCliSubprocess:
    """The real ``CoalaCli`` (subprocess) — offline, with ``subprocess.run`` faked."""

    def test_missing_script_names_the_path(self, tmp_path):
        cli = CoalaCli(tmp_path / "gone" / "scripts" / "coala.py")
        with pytest.raises(CoalaMissing) as excinfo:
            cli.run(["stats"])
        assert str(tmp_path / "gone" / "scripts" / "coala.py") in str(excinfo.value)

    def test_runs_python3_script_with_json_flag(self, tmp_path, monkeypatch):
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = CoalaCli(script, python="python3").run(["stats"])

        assert result == {"ok": True}
        assert seen["argv"] == ["python3", str(script), "stats", "--json"]

    def test_non_zero_exit_raises_with_stderr(self, tmp_path, monkeypatch):
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 2, stdout="", stderr="Traceback: boom"),
        )
        with pytest.raises(CoalaCallError) as excinfo:
            CoalaCli(script).run(["search", "x"])
        assert "exit 2" in str(excinfo.value) and "boom" in str(excinfo.value)

    def test_garbage_output_raises(self, tmp_path, monkeypatch):
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="not json at all", stderr=""),
        )
        with pytest.raises(CoalaCallError):
            CoalaCli(script).run(["stats"])

    def test_trailing_human_line_is_tolerated(self, tmp_path, monkeypatch):
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0, stdout='{"ok": true}\nUsar: python3 coala.py recall\n', stderr=""
            ),
        )
        assert CoalaCli(script).run(["add"]) == {"ok": True}

    def test_empty_recall_human_sentence_is_an_empty_result(self, tmp_path, monkeypatch):
        """The real CLI prints a sentence (not JSON) when recall matches nothing."""
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0, stdout="(working memory vazia — nada recuperado com estes filtros)\n", stderr=""
            ),
        )
        memory = MemoryStore(tmp_path, "urist", cli=CoalaCli(script))

        assert memory.recall(None, budget=500, tags="narrative") == {
            "budget_tokens": 500,
            "used_tokens": 0,
            "count": 0,
            "items": [],
        }
        assert memory.narrative() == ""
        assert memory.episodes() == []
        assert memory.working_memory() == ""

    def test_empty_recall_still_fails_on_a_real_error(self, tmp_path, monkeypatch):
        script = tmp_path / "coala.py"
        script.write_text("# stub\n")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom"),
        )
        memory = MemoryStore(tmp_path, "urist", cli=CoalaCli(script))
        with pytest.raises(CoalaCallError):
            memory.recall(None)


class TestEditorSemantics:
    def test_edit_supersedes_an_existing_record(self, memory, cli):
        cli.run(["add", "--type", "episodic", "--content", "Old text.", "--key", "ep-1", "--tags", "episode"])
        memory.save_episode("New text.", entry_id="1")

        assert cli.commands().count("supersede") == 1
        active = [e for e in cli.entries if not e["superseded_by"]]
        assert [e["content"] for e in active] == ["New text."]
        assert cli.entries[0]["superseded_by"] == active[0]["id"]

    def test_edit_falls_back_to_add_for_unknown_ids(self, memory, cli):
        cli.fail_on.add("supersede")
        memory.edit("999", "Brand new.")

        assert cli.args_of("add")[-1][1:3] == ["--type", "episodic"]
        assert cli.entries[-1]["content"] == "Brand new."

    def test_save_episode_without_id_appends(self, memory, cli):
        memory.save_episode("A fresh episode.")
        assert cli.args_of("supersede") == []
        assert cli.args_of("add")[0][4] == "A fresh episode."


class TestTranslator:
    def test_protocol_and_identity_fallback(self):
        assert isinstance(IdentityTranslator(), Translator)
        assert IdentityTranslator().to_english("Olá") == "Olá"
        assert IdentityTranslator().to_portuguese("Hello") == "Hello"

    def test_pt_to_en_before_storing_episodes(self, tmp_path, cli):
        memory = MemoryStore(tmp_path, "urist", cli=cli, translator=MarkerTranslator())
        memory.save_episode("O aventureiro acorda.")

        assert cli.entries[-1]["content"] == "EN<O aventureiro acorda.>"

    def test_narrative_round_trip_through_the_translator(self, tmp_path, cli):
        memory = MemoryStore(tmp_path, "urist", cli=cli, translator=MarkerTranslator())
        memory.set_narrative("O aventureiro acorda numa clareira.")

        stored = [e for e in cli.entries if e["key"] == "narrative"][0]
        assert stored["content"] == "EN<O aventureiro acorda numa clareira.>"
        assert memory.narrative() == "EN<O aventureiro acorda numa clareira.>"
        assert memory.narrative_pt() == "PT<EN<O aventureiro acorda numa clareira.>>"

    def test_broken_translator_never_breaks_the_memory(self, tmp_path, cli):
        memory = MemoryStore(tmp_path, "urist", cli=cli, translator=ExplodingTranslator())
        memory.set_narrative("Keep me.")
        assert cli.entries[-1]["content"] == "Keep me."
        assert safe_translate(ExplodingTranslator(), "text", "en") == "text"
        assert safe_translate(None, "text", "pt") == "text"

    def test_pt_to_en_mapping(self, tmp_path, cli):
        from fakes import FakeTranslator

        translator = FakeTranslator({"Há uma caverna.": "There is a cave."})
        memory = MemoryStore(tmp_path, "urist", cli=cli, translator=translator)
        memory.save_episode("Há uma caverna.")
        assert cli.entries[-1]["content"] == "There is a cave."


class TestReadModel:
    def test_narrative_is_empty_when_unset(self, memory):
        assert memory.narrative() == ""
        assert memory.working_memory() == ""
        assert memory.episodes() == []

    def test_narrative_uses_the_supersession_key(self, memory, cli):
        memory.set_narrative("First narrative.")
        memory.set_narrative("Second narrative.")

        assert memory.narrative() == "Second narrative."
        recalled = cli.args_of("recall")[-1]
        assert recalled[recalled.index("--tags") + 1] == "narrative"
        assert recalled[recalled.index("--type") + 1] == "semantic"

    def test_episodes_are_newest_first_with_pt_field(self, memory, cli):
        memory.add_episode("First event.")
        memory.add_episode("Second event.")

        episodes = memory.episodes()

        assert [e["content"] for e in episodes] == ["Second event.", "First event."]
        assert set(episodes[0]) == {"id", "ts", "kind", "content", "content_pt"}
        assert episodes[0]["kind"] == "episodic"
        assert episodes[0]["ts"].endswith("Z")
        assert episodes[0]["id"] == str(cli.entries[-1]["id"])

    def test_working_memory_renders_lines(self, memory):
        memory.add_episode("Climbed the hill.")
        memory.add_fact("The river is north.")

        rendered = memory.working_memory()

        assert "[episodic] Climbed the hill." in rendered
        assert "[semantic] The river is north." in rendered

    def test_updated_at_is_z_normalised(self, memory):
        memory.add_episode("Something happened.")
        updated = memory.updated_at()
        assert updated.endswith("Z") and "+00:00" not in updated

    def test_updated_at_none_when_empty(self, memory):
        assert memory.updated_at() is None

    def test_exists_checks_script_and_db(self, tmp_path, cli):
        skill = tmp_path / "proj" / ".agents" / "urist-agent-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "memory").mkdir(parents=True)
        cli.script = skill / "scripts" / "coala.py"
        memory = MemoryStore(tmp_path / "proj", "urist", cli=cli)

        assert memory.exists() is False
        cli.script.write_text("# x")
        assert memory.exists() is False
        (skill / "memory" / "coala.sqlite").write_bytes(b"x")
        assert memory.exists() is True

    def test_export_parses_the_written_file(self, memory, cli):
        memory.add_fact("Exported fact.")
        data = memory.export()
        assert json.loads(json.dumps(data))["entries"][0]["content"] == "Exported fact."
