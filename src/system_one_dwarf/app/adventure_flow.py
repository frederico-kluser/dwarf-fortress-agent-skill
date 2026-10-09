"""Tarefas assíncronas de aventura — uma por campanha de cada vez.

É a peça de app que embrulha o :class:`~system_one_dwarf.game.driver.AdventureDriver`
para a API: cada pedido ``POST .../adventures/{new-world,new-character,load}``
arranca uma **tarefa** num thread à parte (``runner`` injetável — nos testes o
runner é síncrono ou diferido) e devolve ``202 {"task_id", "kind"}``.

Regras (as do contrato ``docs/api.md``):

* **uma tarefa de cada vez por campanha** — começar com uma em curso →
  :class:`TaskBusyError` (a rota traduz em ``409``);
* cada passo do motor corre com **≤ 5 tentativas**; na falha final a tarefa
  termina em ``state: "error"`` com o **relatório PT** (:meth:`StepAbort.pt_report`)
  em ``error``;
* ``cancel`` deixa de enviar teclas (o motor verifica o cancelamento entre
  ações/tentativas) e a tarefa termina com ``error: "cancelled"``;
* cada transição publica o evento WS aditivo ``adventure_task`` com
  ``{task_id, kind, state, step, steps_total, last_screen_pt?, error?}``;
* ``last_screen_pt`` sai do seam do tradutor (:func:`display_pt` da app),
  calculado **no worker** (uma tradução por passo) — o endpoint de estado nunca
  bloqueia em tradução nem em I/O de jogo.

``current`` (o aventureiro vivo do último snapshot) é resolvido pelo driver; o
store só memoriza o ``adventure_id`` da última carga bem-sucedida para o
completar.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..game.driver import StepAbort, StepCancelled, UnknownAdventure

log = logging.getLogger("system_one_dwarf.app.adventure_flow")

#: Tipos de tarefa (o ``kind`` do contrato).
TASK_NEW_WORLD = "new_world"
TASK_NEW_CHARACTER = "new_character"
TASK_LOAD = "load"
TASK_KINDS: tuple[str, ...] = (TASK_NEW_WORLD, TASK_NEW_CHARACTER, TASK_LOAD)

#: Estados de tarefa (o contrato fixa estes três).
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

#: O contrato fixa ``error: "cancelled"`` para o cancelamento (não há 4.º estado).
CANCELLED_ERROR = "cancelled"

#: Evento WS aditivo publicado em cada transição de tarefa.
ADVENTURE_TASK_EVENT = "adventure_task"


class TaskBusyError(RuntimeError):
    """Já existe uma tarefa em curso nesta campanha (→ ``409``)."""


def _default_runner(target: Callable[[], None]) -> None:
    """Runner por omissão: thread daemon (o worker nunca bloqueia a API)."""
    threading.Thread(target=target, daemon=True).start()


def _default_display_pt(translator: Any, canonical: str) -> str | None:
    """Seam do tradutor (``views.display_pt``) — importado tarde para não ciclar."""
    from .views import display_pt

    return display_pt(translator, canonical)


@dataclass
class AdventureTask:
    """Registo de uma tarefa (o objeto ``task`` do contrato)."""

    id: str
    kind: str
    campaign_id: str = ""
    state: str = STATE_RUNNING
    step: int = 0
    steps_total: int = 0
    last_screen_pt: str | None = None
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    #: tradução já feita para este passo (throttle: 1 tradução por passo)
    _pt_step: int | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_payload(self) -> dict[str, Any]:
        """A forma do contrato: ``{id, kind, state, step, steps_total, ...}``."""
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "step": self.step,
                "steps_total": self.steps_total,
                "last_screen_pt": self.last_screen_pt,
                "error": self.error,
            }

    def to_event(self) -> dict[str, Any]:
        """A forma do evento WS: ``{task_id, kind, state, step, ...}``."""
        payload = self.to_payload()
        payload["task_id"] = payload.pop("id")
        return payload


class AdventureTasks:
    """Store das tarefas de aventura por campanha (com thread runner injetável).

    ``driver_factory(campaign_id)`` devolve o driver (real ou falso nos testes);
    ``publish(campaign_id, type, payload)`` é o bus thread-safe da app;
    ``translator_for(campaign_id)`` devolve o tradutor da campanha para o
    ``last_screen_pt``; ``runner(target)`` arranca o worker (por omissão uma
    thread daemon).
    """

    def __init__(
        self,
        driver_factory: Callable[[str], Any],
        *,
        publish: Callable[[str, str, dict[str, Any]], None] | None = None,
        translator_for: Callable[[str], Any] | None = None,
        runner: Callable[[Callable[[], None]], Any] | None = None,
        display_pt: Callable[[Any, str], str | None] | None = None,
    ) -> None:
        self.driver_factory = driver_factory
        self.publish = publish
        self.translator_for = translator_for
        self.runner = runner or _default_runner
        self.display_pt = display_pt or _default_display_pt
        self._tasks: dict[str, AdventureTask] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._adventure_ids: dict[str, str] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- leitura
    def last(self, campaign_id: str) -> AdventureTask | None:
        with self._lock:
            return self._tasks.get(campaign_id)

    def status(self, campaign_id: str) -> dict[str, Any]:
        """``{"task": {...} | null}`` — o resto do payload é montado pela rota."""
        task = self.last(campaign_id)
        return {"task": task.to_payload() if task is not None else None}

    def last_adventure_id(self, campaign_id: str) -> str | None:
        """``adventure_id`` da última carga bem-sucedida (para o ``current``)."""
        with self._lock:
            return self._adventure_ids.get(campaign_id)

    # ------------------------------------------------------------------ escrita
    def start(self, campaign_id: str, kind: str, params: dict[str, Any] | None = None) -> dict[str, str]:
        """Arranca uma tarefa (``TaskBusyError`` quando já há uma em curso)."""
        if kind not in TASK_KINDS:
            raise ValueError(f"kind invalido: {kind!r} (esperava um de {TASK_KINDS})")
        params = dict(params or {})
        with self._lock:
            current = self._tasks.get(campaign_id)
            if current is not None and current.state == STATE_RUNNING:
                raise TaskBusyError(
                    f"ja ha uma tarefa em curso nesta campanha: {current.kind} ({current.id})"
                )
            task = AdventureTask(
                id=f"t-{uuid.uuid4().hex[:12]}",
                kind=kind,
                campaign_id=campaign_id,
                params=params,
            )
            self._tasks[campaign_id] = task
            self._cancels[campaign_id] = threading.Event()
            cancel_event = self._cancels[campaign_id]
        self._publish(campaign_id, task)
        try:
            self.runner(lambda: self._run_task(campaign_id, task, cancel_event))
        except Exception as exc:  # o runner falhou: a tarefa não pode ficar "running"
            self._finish(campaign_id, task, cancel_event, error=f"runner falhou: {exc}")
            raise
        return {"task_id": task.id, "kind": kind}

    def cancel(self, campaign_id: str) -> dict[str, Any]:
        """Cancela a tarefa em curso (deixa de enviar teclas) → ``error: "cancelled"``."""
        with self._lock:
            task = self._tasks.get(campaign_id)
            if task is None:
                return {"cancelled": False, "task": None}
            if task.state != STATE_RUNNING:
                return {"cancelled": False, "task": task.to_payload()}
            cancel_event = self._cancels.get(campaign_id)
            if cancel_event is not None:
                cancel_event.set()
            task.state = STATE_ERROR
            task.error = CANCELLED_ERROR
        self._publish(campaign_id, task)
        return {"cancelled": True, "task": task.to_payload()}

    # ------------------------------------------------------------------ worker
    def _run_task(self, campaign_id: str, task: AdventureTask, cancel_event: threading.Event) -> None:
        cancel = cancel_event.is_set
        error: str | None = None
        try:
            driver = self.driver_factory(campaign_id)
            on_event = self._event_handler(campaign_id, task, cancel_event)
            if task.kind == TASK_NEW_WORLD:
                driver.create_world(
                    world_name=task.params.get("world_name"),
                    params=str(task.params.get("params") or "quick"),
                    on_event=on_event,
                    cancel=cancel,
                )
                self._set_adventure_id(campaign_id, None)
            elif task.kind == TASK_NEW_CHARACTER:
                driver.create_character(
                    name=task.params.get("name"),
                    race=task.params.get("race"),
                    origin=task.params.get("origin"),
                    randomize=bool(task.params.get("randomize")),
                    on_event=on_event,
                    cancel=cancel,
                )
                self._set_adventure_id(campaign_id, None)
            elif task.kind == TASK_LOAD:
                result = driver.load_adventure(
                    str(task.params.get("adventure_id") or ""),
                    on_event=on_event,
                    cancel=cancel,
                )
                adventure = (result or {}).get("adventure") or {}
                self._set_adventure_id(campaign_id, adventure.get("id"))
        except StepAbort as exc:
            error = exc.pt_report()
        except StepCancelled:
            error = CANCELLED_ERROR
        except UnknownAdventure as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a tarefa nunca pode ficar pendurada
            log.warning("adventure task %s failed", task.id, exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
        self._finish(campaign_id, task, cancel_event, error=error)

    def _finish(
        self,
        campaign_id: str,
        task: AdventureTask,
        cancel_event: threading.Event,
        *,
        error: str | None,
    ) -> None:
        with task._lock:
            if cancel_event.is_set():
                task.state, task.error = STATE_ERROR, CANCELLED_ERROR
            elif error is not None:
                task.state, task.error = STATE_ERROR, error
            else:
                task.state = STATE_DONE
        self._publish(campaign_id, task)

    def _set_adventure_id(self, campaign_id: str, adventure_id: Any) -> None:
        with self._lock:
            if adventure_id:
                self._adventure_ids[campaign_id] = str(adventure_id)
            else:
                self._adventure_ids.pop(campaign_id, None)

    def _event_handler(
        self, campaign_id: str, task: AdventureTask, cancel_event: threading.Event
    ) -> Callable[[dict[str, Any]], None]:
        """Atualiza o registo com os eventos do motor (step, ecrã traduzido)."""

        def on_event(event: dict[str, Any]) -> None:
            if cancel_event.is_set():
                raise StepCancelled("cancelado")
            step = int(event.get("index") or 0)
            total = int(event.get("total") or 0)
            with task._lock:
                changed = (step and step != task.step) or (total and total != task.steps_total)
                if step:
                    task.step = step
                if total:
                    task.steps_total = total
                needs_pt = event.get("event") == "screen" and task._pt_step != step
            if needs_pt and step:
                self._translate_screen(campaign_id, task, step, str(event.get("screen_text") or ""))
                changed = True
            if changed:
                self._publish(campaign_id, task)

        return on_event

    def _translate_screen(
        self, campaign_id: str, task: AdventureTask, step: int, screen_text: str
    ) -> None:
        """``last_screen_pt`` via o seam ``display_pt`` — no worker, nunca na API."""
        translator = None
        if self.translator_for is not None:
            try:
                translator = self.translator_for(campaign_id)
            except Exception:  # pragma: no cover - tradutor é best-effort
                translator = None
        try:
            translated = self.display_pt(translator, screen_text)
        except Exception:  # pragma: no cover - tradução nunca parte a tarefa
            translated = None
        with task._lock:
            task._pt_step = step
            if translated:
                task.last_screen_pt = translated

    def _publish(self, campaign_id: str, task: AdventureTask) -> None:
        if self.publish is None:
            return
        try:
            self.publish(campaign_id, ADVENTURE_TASK_EVENT, task.to_event())
        except Exception:  # pragma: no cover - observabilidade nunca parte a tarefa
            log.warning("could not publish %s for %s", ADVENTURE_TASK_EVENT, campaign_id,
                        exc_info=True)


__all__ = [
    "ADVENTURE_TASK_EVENT",
    "AdventureTask",
    "AdventureTasks",
    "CANCELLED_ERROR",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_RUNNING",
    "TASK_KINDS",
    "TASK_LOAD",
    "TASK_NEW_CHARACTER",
    "TASK_NEW_WORLD",
    "TaskBusyError",
]
