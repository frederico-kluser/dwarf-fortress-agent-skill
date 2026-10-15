"""Fachada da «memória viva» (Onda 2): diálogo + diário + extrações + episódios.

Porquê: os Passos 7 e 9 partilham os mesmos módulos de memória
(:mod:`.dialogue`, :mod:`.logbook`, :mod:`.extraction`, :mod:`.memory`) — o
``MemoryBundle`` é o ponto único que o loop/narrador consultam, para que o
wiring seja uma linha e não cinco.

O que «conversar com alguém puxa automaticamente o contexto relevante» quer
dizer em código: :meth:`MemoryBundle.observe` transcreve as falas novas e, quando
uma conversa **reabre** com um interlocutor (silêncio ≥ :data:`REOPEN_GAP_S`
desde a última fala dele), devolve em ``Observation.contexts`` o bloco de
contexto já puxado — cartão + factos citados + falas por recência + importância +
similaridade. Nenhuma chamada manual é preciso para a conversa se lembrar.

Escrita: só se acrescenta. O diário é append-only (``logbook.py``, triggers), a
transcrição bruta nunca é apagada, e as extrações são derivadas e citáveis.
Os eventos são projetados para a camada episódica CoALA (``project``) — é lá que
vivem por campanha (``campanhas/<slug>/…/memory/coala.sqlite``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .dialogue import DialogueLine, DialogueMemory
from .extraction import ExtractionStore, rule_extractor
from .logbook import LogEvent, Logbook
from .memory import EpisodicStore
from .recall import RETRIEVAL_WEIGHTS, ScoreWeights, combined_score, epoch_from_iso, fit_lines

log = logging.getLogger("system_one_dwarf.campaign.bundle")

__all__ = [
    "DEFAULT_SOURCES",
    "MemoryBundle",
    "Observation",
    "REOPEN_GAP_S",
]

#: Silêncio que marca «conversa nova» — e puxa o contexto automaticamente.
REOPEN_GAP_S = 1800.0

#: Fontes do bloco de contexto, por ordem (a mais pessoal primeiro).
DEFAULT_SOURCES = ("dialogue", "facts", "logbook", "episodes")


@dataclass(frozen=True)
class Observation:
    """O que :meth:`MemoryBundle.observe` viu num snapshot.

    ``contexts`` é o puxão automático: ``{chave do interlocutor: bloco EN de
    contexto, rotulado como DADOS}`` para as conversas que reabriram neste passo.
    """

    dialogue: tuple[DialogueLine, ...] = ()
    events: tuple[LogEvent, ...] = ()
    contexts: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dialogue": [line.as_dict() for line in self.dialogue],
            "events": [event.as_dict() for event in self.events],
            "contexts": dict(self.contexts),
        }


class MemoryBundle:
    """Diário + transcrição + extrações + episódios CoALA de UMA campanha.

    Nenhum método levanta (``guard`` por cima): a memória nunca derruba o loop.
    Os stores vêm ligados (``CampaignManager.bundle``) ou montados à mão nos
    testes.
    """

    def __init__(
        self,
        dialogue: DialogueMemory,
        logbook: Logbook,
        extraction: ExtractionStore,
        episodes: EpisodicStore | None = None,
        memory: Any = None,
        *,
        clock: Callable[[], float] = time.time,
        weights: ScoreWeights | Mapping[str, Any] | None = None,
        reopen_gap_s: float = REOPEN_GAP_S,
        auto_extract: bool = True,
        auto_reflect: bool = True,
        auto_project: bool = True,
        reflector: Callable[[Sequence[LogEvent]], Any] | None = None,
    ) -> None:
        self.dialogue = dialogue
        self.logbook = logbook
        self.extraction = extraction
        self.episodes = episodes
        #: ``MemoryStore`` da campanha (projeção episódica via CLI); ``None`` = sem projeção.
        self.memory = memory
        self.clock = clock
        self.weights = ScoreWeights.from_any(weights, RETRIEVAL_WEIGHTS)
        self.reopen_gap_s = float(reopen_gap_s)
        self.auto_extract = auto_extract
        self.auto_reflect = auto_reflect
        self.auto_project = auto_project
        self.reflector = reflector
        self.errors = 0
        self._last_state: Any = None

    # --------------------------------------------------------------- escrita

    def observe(
        self,
        state: Any,
        before: Any = None,
        *,
        step: int | None = None,
        action: Any = None,
        executed: Any = None,
    ) -> Observation:
        """Transcreve/dialoga + diária o snapshot e **puxa o contexto** das conversas novas.

        Devolve as falas e eventos novos e, em ``contexts``, o contexto das
        conversas que reabriram (interlocutor silencioso ≥ :data:`REOPEN_GAP_S`).
        A extração dos factos/palavras dadas acontece em background (assíncrona).
        """
        try:
            prior = {card.interlocutor: card.last_seen for card in self.dialogue.interlocutors()}
            lines = tuple(self.dialogue.observe(state, step=step))
            previous = before if before is not None else self._last_state
            events = tuple(self.logbook.observe(previous, state, step=step, action=action, executed=executed))
            self._last_state = state
            contexts = self._open_contexts(prior, lines)
            if self.auto_extract and (lines or events):
                self.extraction.kick(self.dialogue, self.logbook)
            if self.auto_reflect and self.logbook.should_reflect():
                self.logbook.reflect(self.reflector, step=step)
            if self.auto_project and self.memory is not None and events:
                self.logbook.project_episodes(self.memory)
            return Observation(dialogue=lines, events=events, contexts=contexts)
        except Exception:
            self.errors += 1
            log.warning("MemoryBundle.observe failed", exc_info=True)
            return Observation()

    def _open_contexts(self, prior: Mapping[str, str], lines: Sequence[DialogueLine]) -> dict[str, str]:
        """Contexto puxado para cada conversa que reabriu neste passo."""
        contexts: dict[str, str] = {}
        for line in lines:
            key = line.interlocutor
            if key in contexts:
                continue
            before_seen = prior.get(key)
            if before_seen is None:
                continue  # primeiro contacto: não há memória a puxar
            gap = _gap_hours(before_seen, line.ts, float(self.clock()))
            if gap is not None and gap * 3600.0 >= self.reopen_gap_s:
                block = self.context(line.text, interlocutor=key, k=3, char_budget=800)
                if block:
                    contexts[key] = block
        return contexts

    def extract(self, *, extractor: Any = None, background: bool = False, limit: int = 500) -> Any:
        """Corre a extração derivada (assíncrona por omissão: devolve a thread)."""
        if background:
            return self.extraction.kick(self.dialogue, self.logbook, extractor=extractor, limit=limit)
        return self.extraction.run_pending(
            self.dialogue, self.logbook, extractor=extractor or rule_extractor, limit=limit,
        )

    def reflect(self, reflector: Callable[[Sequence[LogEvent]], Any] | None = None,
                step: int | None = None) -> LogEvent | None:
        """Reflexão agregada como evento derivado (só se houver o que refletir)."""
        if not self.logbook.should_reflect():
            return None
        return self.logbook.reflect(reflector or self.reflector, step=step)

    def project(self, *, limit: int = 500) -> int:
        """Projeta os eventos do diário na camada episódica CoALA (append-only)."""
        if self.memory is None:
            return 0
        return self.logbook.project_episodes(self.memory, limit=limit)

    # ---------------------------------------------------------------- leitura

    def context(
        self,
        situation: Any,
        interlocutor: Any = None,
        *,
        k: int = 3,
        char_budget: int = 1200,
        now: Any = None,
        sources: Sequence[str] = DEFAULT_SOURCES,
    ) -> str:
        """Bloco de contexto EN (DADOS) para uma conversa/situação.

        Cartão + falas (``dialogue``), factos/palavras dadas **citados** (``facts``),
        eventos do diário (``logbook``) e episódios CoALA (``episodes``) — cada
        bloco rotulado como dado, nunca como instrução.
        """
        wanted = set(sources or ())
        blocks: list[str] = []
        if "dialogue" in wanted:
            blocks.append(self.dialogue.state_block(situation, interlocutor=interlocutor, k=k, now=now))
        if "facts" in wanted:
            blocks.append(self.extraction.state_block(situation, k=max(2, k), now=now, interlocutor=interlocutor))
        if "logbook" in wanted:
            blocks.append(self.logbook.state_block(situation, k=max(2, k), now=now))
        if "episodes" in wanted and self.episodes is not None:
            blocks.append(self.episodes.state_block(situation, k=max(2, k - 1), now=now))
        return fit_lines([block for block in blocks if block], char_budget)

    def memory_items(
        self,
        situation: Any,
        *,
        k: int = 8,
        interlocutor: Any = None,
        now: Any = None,
        weights: ScoreWeights | Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Top-K composto do **JEV-R**: recência + importância + similaridade.

        Funde as quatro lojas com :func:`.recall.combined_score` (valores brutos,
        comparáveis entre lojas) e devolve itens na forma do loop
        (``{id,key,title,content,tags}``) com ``score`` final — é a lista de
        candidatos ``include_*`` do JEV-R. ``weights`` recalibra os pesos por
        chamada (API: :class:`.recall.ScoreWeights` / :data:`.recall.RETRIEVAL_WEIGHTS`).
        """
        try:
            w = ScoreWeights.from_any(weights, self.weights)
            pool: list[dict[str, Any]] = []
            pool.extend(self.dialogue.memory_items(situation, interlocutor=interlocutor, k=k, now=now))
            pool.extend(self.extraction.memory_items(situation, k=k, now=now))
            pool.extend(self.logbook.memory_items(situation, k=k, now=now))
            if self.episodes is not None:
                pool.extend(self.episodes.memory_items(situation, k=k, now=now))
            for item in pool:
                item["score"] = round(
                    combined_score(item.get("recency"), item.get("importance"), item.get("relevance"), w), 4
                )
            pool.sort(key=lambda it: (-float(it.get("score") or 0.0), str(it.get("id"))))
            return pool[: max(1, int(k))]
        except Exception:
            self.errors += 1
            log.warning("MemoryBundle.memory_items failed", exc_info=True)
            return []

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "dialogue": self.dialogue.stats(),
            "logbook": self.logbook.stats(),
            "extraction": self.extraction.stats(),
            "errors": self.errors,
        }
        if self.episodes is not None:
            out["episodes"] = self.episodes.stats()
        return out


def _gap_hours(before_seen: Any, after_ts: Any, now_wall: float) -> float | None:
    """Horas de silêncio entre a última fala anterior e a nova (``None`` se ilegível)."""
    old = epoch_from_iso(before_seen)
    new = epoch_from_iso(after_ts)
    if new is None:
        new = now_wall
    if old is None:
        return None
    return max(0.0, (new - old) / 3600.0)
