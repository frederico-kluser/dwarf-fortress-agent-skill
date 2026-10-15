"""``agent.narrator``: narrativas em ``none`` quando nada mudou (Passo 19).

A alavanca é o ``narrator_effort`` do JEV-M: quando os eventos recentes não
mudam desde o último episódio, o narrador devolve ``Skip`` com effort ``none`` —
zero chamadas LLM, menos ruído narrativo (``PROBLEMAS-E-SOLUCOES.md`` §H6).
"""

from __future__ import annotations

from system_one_dwarf.agent import Narrator, Skip
from system_one_dwarf.agent.narrator import EFFORT_NONE, NOTHING_CHANGED, change_key

from .conftest import FakeMemory, ScriptedLLM

EVENTS = ["Woke up in a clearing near the river.", "Spotted a wolf pack to the north-east."]
NEW_EVENTS = EVENTS + ["Killed the wolf with the spear."]


def make(memory=None, **kwargs) -> Narrator:
    return Narrator(ScriptedLLM("ROLLING LINE. " + "Word. " * 40 + "End.",
                                "SECOND LINE. " + "Word. " * 40 + "End."),
                    memory if memory is not None else FakeMemory(), **kwargs)


def test_change_key_is_stable_and_event_sensitive() -> None:
    assert change_key(EVENTS) == change_key(list(EVENTS))
    assert change_key(EVENTS) != change_key(NEW_EVENTS)
    assert change_key(EVENTS) != change_key([])
    assert change_key(EVENTS) == change_key(EVENTS + ["  ", ""])  # vazios não contam


def test_the_first_consider_always_narrates() -> None:
    narrator = make()
    result = narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    assert not isinstance(result, Skip)
    assert narrator.calls == 1


def test_nothing_changed_since_the_last_episode_returns_none() -> None:
    memory = FakeMemory()
    narrator = make(memory)

    first = narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")
    again = narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail updated")

    assert not isinstance(first, Skip)
    assert isinstance(again, Skip)
    assert again.reason == NOTHING_CHANGED
    assert again.effort == EFFORT_NONE          # o narrador devolve `none`
    assert again.calls == 0 and again.cost_usd == 0.0
    assert narrator.calls == 1                  # a 2.ª não custou nada
    assert again.as_dict()["effort"] == "none"


def test_new_events_narrate_again() -> None:
    narrator = make()
    narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    result = narrator.consider({"update": True, "effort": "high"}, NEW_EVENTS, "tail")

    assert not isinstance(result, Skip)
    assert narrator.calls == 2


def test_the_tail_changing_alone_is_not_a_change() -> None:
    """O narrative_tail muda quando o próprio narrador escreve — não é progresso."""
    narrator = make()
    narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    again = narrator.consider({"update": True, "effort": "high"}, EVENTS, "outra tail")

    assert isinstance(again, Skip) and again.reason == NOTHING_CHANGED


def test_no_change_can_be_switched_off() -> None:
    narrator = make(skip_on_no_change=False)
    narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    result = narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    assert not isinstance(result, Skip)
    assert narrator.calls == 2


def test_the_effort_none_lever_from_jev_m_still_works_as_before() -> None:
    strict = make(skip_on_effort_none=True)
    result = strict.consider({"update": True, "effort": EFFORT_NONE}, EVENTS, "tail")

    assert isinstance(result, Skip) and result.reason == "effort_none"


def test_a_failed_memory_write_does_not_claim_a_change() -> None:
    """Sem episódio guardado não há "desde o último episódio" — não se salta."""
    memory = FakeMemory("add_episode", "set_narrative")
    narrator = make(memory)

    narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")
    again = narrator.consider({"update": True, "effort": "high"}, EVENTS, "tail")

    assert not isinstance(again, Skip)
    assert narrator.calls == 2
