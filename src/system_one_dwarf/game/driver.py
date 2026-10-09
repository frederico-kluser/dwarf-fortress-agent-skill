"""Driver de aventura DF 53.16: motor de passos + listagem de saves + processo do jogo.

Este módulo é a peça que transforma o fluxo **provado** do E2E
(``scripts/e2e/steps.json`` + ``create_adventurer.py``) em serviço reutilizável:

* :class:`AdventureDriver` — corre os passos dos menus (cliques sintéticos
  derivados do ``screen_text``; os menus do DF 53.16 são *mouse-first*: ``key
  SELECT`` não responde no título) com as regras da casa: **≤ 5 tentativas por
  passo** e, na falha final, **relatório PT** (:class:`StepAbort.pt_report`).
* :class:`SubprocessBridge` — a superfície de ponte usada pelo driver
  (``screen``/``click``/``keys``/``command``/``snapshot``) sobre o
  ``dfhack/df_bridge.py``; o ``runner`` é injetável (testes offline).
* :func:`load_steps` — carrega o ``steps.json`` canónico (fonte única: fica em
  ``scripts/e2e/steps.json``; o CLI continua a usá-lo).
* :class:`GameProcessManager` — arranca/pára **o processo do jogo** no caminho
  configurado (mesmo lançamento do ``scripts/sod.sh``: ``SDL_AUDIODRIVER=dummy
  SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1 ./dfhack`` destacado com log; a
  paragem usa o escalonamento provado TERM→KILL por grupo, porque o ``dwarfort``
  ignora SIGTERM). Só gere o jogo no caminho configurado.

Marcas de terreno (best-effort, ver ``docs/api.md``):

* **GUESS (parcial)** — o parsing da pasta de saves: o formato documentado da
  wiki é ``data/save/<pasta>/`` com ``world.dat`` (mundo sem jogo ativo) ou
  ``world.sav`` (jogo ativo, carregável com ``load-save``); nomes de pasta são
  ``region#`` ou ``region#-AAAA-MM-DD`` (cópias sazonais) mas podem ser
  renomeados. ``id`` = nome da pasta (é o argumento do ``load-save``).
* **GUESS** — passos *soft* opcionais (nome do mundo/personagem, ``randomize``,
  parâmetros "quick"): labels derivados do que os ecrãs mostram; são best-effort
  e nunca abortam o fluxo (``soft: true``).
* **GROUND** — ``load-save <pasta>`` só carrega saves *ativos* (documentação
  DFHack); saves inativos seguem o fluxo de menus até ao ecrã de tipo de jogo.

Nada aqui toca no jogo por si: o driver só fala com a ponte que lhe derem.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "AdventureDriver",
    "AdventureError",
    "BridgeCommandError",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_POLL_TIMEOUT",
    "GameProcessError",
    "GameProcessManager",
    "OsProcessOps",
    "StepAbort",
    "StepCancelled",
    "SubprocessBridge",
    "UnknownAdventure",
    "check_expect",
    "default_df_path",
    "label_centre",
    "load_steps",
    "typed_keys",
]

# src/system_one_dwarf/game/driver.py → raiz do repositório
REPO_ROOT = Path(__file__).resolve().parents[3]
#: Fonte única do fluxo provado (o CLI scripts/e2e/create_adventurer.py usa o mesmo).
DEFAULT_STEPS_PATH = REPO_ROOT / "scripts" / "e2e" / "steps.json"
DEFAULT_BRIDGE_PATH = REPO_ROOT / "dfhack" / "df_bridge.py"

#: Regra do utilizador: ≤ 5 tentativas por passo antes do aborto com relatório PT.
DEFAULT_MAX_ATTEMPTS = 5
#: Timeout dos passos ``poll`` (worldgen/export podem demorar minutos).
DEFAULT_POLL_TIMEOUT = 240.0

#: Nome do executável do jogo (o que ``pgrep -x`` vê).
GAME_PROCESS_NAME = "dwarfort"
#: Pasta do jogo (regra do projeto: DF 53.16 NÃO-Steam).
DEFAULT_DF_SUBDIR = "Games/dwarf-fortress-53.16"

# Nomes dos passos do steps.json que separam os dois fluxos provados.
WORLD_FLOW_END = "poll_export"
CHARACTER_FLOW_START = "choose_adventurer"


def default_df_path() -> str:
    """Caminho configurado do DF (``DF_PATH`` > ``SOD_DF_PATH`` > ``~/Games/...``)."""
    for name in ("DF_PATH", "SOD_DF_PATH"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return str(Path.home() / DEFAULT_DF_SUBDIR)


# --------------------------------------------------------------------------- #
# Motor de passos (portado de scripts/e2e/create_adventurer.py)                #
# --------------------------------------------------------------------------- #


class AdventureError(RuntimeError):
    """Base dos erros do fluxo de aventura."""


class StepAbort(AdventureError):
    """Passo falhou nas 5 tentativas — carrega o relatório PT do aborto."""

    def __init__(
        self,
        step: dict[str, Any],
        screens: list[tuple[str, str]],
        actions: list[str],
        attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.step = dict(step)
        self.screens = list(screens)
        self.actions = list(actions)
        self.attempts = attempts
        super().__init__(self.pt_summary())

    def pt_summary(self) -> str:
        return (
            f"passo {self.step.get('name')!r} abortado apos {self.attempts} tentativas"
        )

    def to_dict(self) -> dict[str, Any]:
        """Relatório estruturado (para logs/eventos) — nunca inclui chaves."""
        return {
            "step": self.step.get("name"),
            "attempts": self.attempts,
            "actions": list(self.actions),
            "screens": [
                {
                    "focus": focus,
                    "text_head": " / ".join(ln.strip() for ln in text.splitlines() if ln.strip())[:180],
                }
                for focus, text in self.screens
            ],
        }

    def pt_report(self, title: str = "adventure flow") -> str:
        """Relatório PT completo (mesma forma do CLI E2E)."""
        lines = [
            "",
            f"=== RELATORIO DE ABORTO ({title}) ===",
            f"Passo: {self.step.get('name')}",
            f"Tentativas: {self.attempts}/{self.attempts}",
            f"Acoes enviadas: {' | '.join(self.actions) or '(nenhuma)'}",
            f"Telas vistas ({len(self.screens)}):",
        ]
        for i, (focus, text) in enumerate(self.screens, 1):
            head = " / ".join(ln.strip() for ln in text.splitlines() if ln.strip())[:180]
            lines.append(f"  [{i}] focus={focus} :: {head or '(vazia)'}")
        lines.append("ABORTADO: fluxo de criacao nao concluido.")
        return "\n".join(lines)


class StepCancelled(AdventureError):
    """O fluxo foi cancelado (``cancel()`` devolveu True) — nada mais é enviado."""


class UnknownAdventure(AdventureError):
    """``adventure_id`` não existe na pasta de saves."""


class BridgeCommandError(AdventureError):
    """A ponte devolveu saída não-zero (jogo em baixo, comando inválido, …)."""


def load_steps(path: Any = None) -> list[dict[str, Any]]:
    """Carrega o ``steps.json`` canónico (ou ``path``) — lista não vazia de passos."""
    steps = json.loads(Path(path or DEFAULT_STEPS_PATH).read_text(encoding="utf-8"))
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"steps invalido em {path or DEFAULT_STEPS_PATH}: esperava lista nao vazia")
    return steps


def label_centre(text: str, needle: str, regex: bool = False) -> tuple[int, int] | None:
    """(x, y) do centro de ``needle`` no texto do ecrã, ou ``None``.

    As coordenadas derivam de ``screen_text``: cada linha do texto é uma linha do
    ecrã (é assim que os cliques sintéticos funcionam nos menus do DF 53.16).
    """
    for y, line in enumerate(text.splitlines()):
        if regex:
            m = re.search(needle, line)
            if m:
                return (m.start() + (m.end() - m.start()) // 2, y)
        else:
            idx = line.find(needle)
            if idx >= 0:
                return (idx + len(needle) // 2, y)
    return None


def check_expect(expect: Any, focus: str, text: str) -> bool:
    """True quando o ``expect` do passo é satisfeito pelo focus/texto do ecrã.

    ``expect`` pode ser uma string ou uma lista de alternativas (qualquer uma
    serve).  ``focus:``/``screen:`` limitam a procura ao focus/ao texto; o resto
    procura em ambos.  Vazio é sempre verdade (usado pelos passos ``soft``).
    """
    if isinstance(expect, (list, tuple)):
        return any(check_expect(item, focus, text) for item in expect)
    expect = str(expect)
    if expect.startswith("focus:"):
        needle = expect[len("focus:"):].split(" (")[0].strip()
        return needle.lower() in focus.lower()
    if expect.startswith("screen:"):
        needle = expect[len("screen:"):].split(" (")[0].strip()
        return needle.lower() in text.lower()
    return expect.lower() in (focus + "\n" + text).lower()


def typed_keys(text: str) -> list[str]:
    """Teclas ``STRING_Axxx`` (ASCII) para escrever ``text`` num campo de texto.

    GUESS: ``df.interface_key.STRING_Axxx`` é o código ASCII (xxx) — é o nome
    listado pelo harness E2E para escrita de texto.  Só ASCII imprimível.
    """
    keys = []
    for ch in str(text)[:64]:
        code = ord(ch)
        if 32 <= code <= 126:
            keys.append("STRING_A%03d" % code)
    return keys


# --------------------------------------------------------------------------- #
# Ponte (superfície usada pelo driver)                                          #
# --------------------------------------------------------------------------- #


class SubprocessBridge:
    """Corre o ``dfhack/df_bridge.py`` e expõe a superfície mínima do driver.

    Superfície: ``screen() -> (focus, text)``, ``click(x, y)``, ``keys(names)``,
    ``command(argv) -> stdout``, ``snapshot() -> dict``, ``save_root() -> Path``.

    ``runner`` é a injeção para testes: ``callable(cmd) -> objeto com
    returncode/stdout/stderr`` (a mesma forma do ``GameStateClient``).
    """

    def __init__(
        self,
        df_path: str | None = None,
        port: int | None = None,
        *,
        bridge_path: Any = None,
        python: str | None = None,
        runner: Callable[[Sequence[str]], Any] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.df_path = str(df_path or default_df_path())
        self.port = port
        self.bridge_path = Path(bridge_path or DEFAULT_BRIDGE_PATH)
        self.python = python or "python3"
        self.runner = runner
        self.timeout = timeout

    # ------------------------------------------------------------------ baixo
    def command_argv(self, args: Iterable[str]) -> list[str]:
        # mesma ordem do CLI E2E: flags da ponte antes do subcomando
        cmd = [self.python, str(self.bridge_path)]
        if self.df_path:
            cmd += ["--df-path", str(self.df_path)]
        if self.port:
            cmd += ["--port", str(self.port)]
        cmd += [str(a) for a in args]
        return cmd

    def _run(self, args: Iterable[str], timeout: float | None = None) -> Any:
        cmd = self.command_argv(args)
        if self.runner is not None:
            return self.runner(cmd)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            timeout=timeout if timeout is not None else self.timeout,
        )

    def command(self, args: Iterable[str], timeout: float | None = None) -> str:
        """Corre um comando da ponte e devolve o stdout (``BridgeCommandError``)."""
        proc = self._run(args, timeout)
        code = getattr(proc, "returncode", None)
        stdout = getattr(proc, "stdout", "") or ""
        if code:
            stderr = (getattr(proc, "stderr", "") or "").strip()
            raise BridgeCommandError(
                f"ponte falhou (rc={code}): {(stderr or stdout).strip()[:300] or 'sem saida'}"
            )
        return stdout

    # -------------------------------------------------------------- superfície
    def screen(self) -> tuple[str, str]:
        """-> (focus:str, text:str) de ``act screen --json``."""
        proc = self._run(["act", "screen", "--json"])
        try:
            data = json.loads(getattr(proc, "stdout", "") or "{}").get("data", {})
        except ValueError:
            return "", getattr(proc, "stdout", "") or ""
        focus = "/".join(data.get("focus") or [])
        text = "\n".join(data.get("lines") or [])
        return focus, text

    def click(self, x: int, y: int) -> None:
        self.command(["act", "batch", f"click {x},{y}; sleep 500"])

    def keys(self, names: Sequence[str]) -> None:
        self.command(["act", "batch", "key " + " ".join(names) + "; sleep 500"])

    def snapshot(self) -> dict[str, Any]:
        """Snapshot cru do contrato (``snapshot --json``) — levanta se falhar."""
        payload = json.loads(self.command(["snapshot", "--json"]) or "{}")
        return payload if isinstance(payload, dict) else {}

    def save_root(self) -> Path:
        """``<df>/data/save`` — onde vivem as pastas de saves (``region*``)."""
        return Path(self.df_path) / "data" / "save"


# --------------------------------------------------------------------------- #
# Driver de aventura                                                            #
# --------------------------------------------------------------------------- #


class AdventureDriver:
    """Corre os fluxos de menus provados por cima de uma ponte.

    ``bridge_runner`` implementa a superfície :class:`SubprocessBridge`
    (``screen``/``click``/``keys``/``command``/``snapshot``/``save_root``);
    nos testes é um bridge falso.  ``translator`` é o seam de tradução PT (só é
    usado para textos de apoio; o ``display_pt`` da app é quem produz o
    ``last_screen_pt`` do estado das tarefas).

    Regras do motor (as do E2E, fixas):

    * cada passo tem **≤ 5 tentativas** (``max_attempts``);
    * cada tentativa: ler ecrã → agir → esperar ``wait`` → verificar ``expect``;
    * passos ``poll`` só observam (worldgen/export) até ``poll_timeout``;
    * passos ``soft`` (aditivos, GUESS) nunca abortam o fluxo;
    * falha final → :class:`StepAbort` com relatório PT;
    * ``cancel()`` devolvendo True → :class:`StepCancelled` (não envia mais teclas).
    """

    def __init__(
        self,
        bridge_runner: Any,
        translator: Any | None = None,
        *,
        steps: Sequence[dict[str, Any]] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.bridge = bridge_runner
        self.translator = translator
        self.steps = list(steps) if steps is not None else load_steps()
        self.max_attempts = int(max_attempts)
        self.poll_timeout = float(poll_timeout)
        self.sleep = sleep or time.sleep

    # --------------------------------------------------------------- utilitários
    def save_root(self) -> Path:
        getter = getattr(self.bridge, "save_root", None)
        if callable(getter):
            return Path(getter())
        return Path(default_df_path()) / "data" / "save"

    def _check_cancel(self, cancel: Callable[[], bool] | None) -> None:
        if cancel is not None and cancel():
            raise StepCancelled("fluxo de aventura cancelado")

    @staticmethod
    def _emit(on_event: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # pragma: no cover - observabilidade nunca parte o fluxo
            pass

    # ---------------------------------------------------------- motor de passos
    def run_step(
        self,
        step: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        index: int = 0,
        total: int = 0,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Corre um passo; devolve (telas vistas, ações enviadas) ou aborta.

        ``StepAbort`` na falha final; passos ``soft`` falham em silêncio
        (devolvem as telas/ações e o fluxo segue).
        """
        keys = step.get("keys") or []
        directive = keys[0] if keys else ""
        expect = step.get("expect", "")
        wait = step.get("wait", 1.0)
        soft = bool(step.get("soft"))
        name = step.get("name")

        self._emit(on_event, {
            "event": "step_start", "index": index, "total": total,
            "name": name, "directive": directive or "(poll/none)", "guessed": step.get("guessed"),
        })

        if directive.startswith("poll"):
            return self._run_poll(step, expect, wait, on_event=on_event, cancel=cancel,
                                  index=index, total=total)

        screens: list[tuple[str, str]] = []
        actions: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            self._check_cancel(cancel)
            focus, text = self.bridge.screen()
            screens.append((focus, text))
            self._emit(on_event, {
                "event": "screen", "index": index, "total": total, "name": name,
                "focus": focus, "screen_text": text, "attempt": attempt,
            })

            if directive.startswith("click_re "):
                pat = directive[len("click_re "):]
                centre = label_centre(text, pat, regex=True)
                if centre:
                    self.bridge.click(*centre)
                    actions.append(f"click@{centre} /{pat}/")
                else:
                    actions.append(f"click_re {pat} -> NOT FOUND")
            elif directive.startswith("click "):
                label = directive[len("click "):].strip().strip('"')
                centre = label_centre(text, label)
                if centre:
                    self.bridge.click(*centre)
                    actions.append(f"click@{centre} '{label}'")
                else:
                    actions.append(f"click '{label}' -> NOT FOUND")
            elif directive.startswith("key "):
                names = directive[len("key "):].split()
                self.bridge.keys(names)
                actions.append("key " + " ".join(names))
            elif directive.startswith("type "):
                names = typed_keys(directive[len("type "):])
                if names:
                    self.bridge.keys(names)
                    actions.append(f"type {len(names)} keys")
                else:
                    actions.append("type -> EMPTY")
            else:
                actions.append(f"unhandled: {directive}")

            self._emit(on_event, {"event": "action", "index": index, "total": total,
                                  "name": name, "action": actions[-1]})
            self.sleep(wait)
            self._check_cancel(cancel)
            focus, text = self.bridge.screen()
            screens.append((focus, text))
            self._emit(on_event, {
                "event": "screen", "index": index, "total": total, "name": name,
                "focus": focus, "screen_text": text, "attempt": attempt, "after_action": True,
            })
            if check_expect(expect, focus, text):
                self._emit(on_event, {"event": "step_ok", "index": index, "total": total,
                                      "name": name, "expect": expect})
                return screens, actions
            self._emit(on_event, {"event": "attempt_failed", "index": index, "total": total,
                                  "name": name, "attempt": attempt})

        if soft:
            self._emit(on_event, {"event": "step_skipped", "index": index, "total": total,
                                  "name": name})
            return screens, actions
        raise StepAbort(step, screens, actions, attempts=self.max_attempts)

    def _run_poll(
        self,
        step: dict[str, Any],
        expect: Any,
        wait: Any,
        *,
        on_event: Callable[[dict[str, Any]], None] | None,
        cancel: Callable[[], bool] | None,
        index: int,
        total: int,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Passo ``poll``: observa até o ``expect`` aparecer (ou ``poll_timeout``)."""
        wait_s = max(0.05, float(wait if wait is not None else 8.0))
        rounds = max(1, int(self.poll_timeout / wait_s))
        for _ in range(rounds):
            self._check_cancel(cancel)
            focus, text = self.bridge.screen()
            self._emit(on_event, {
                "event": "screen", "index": index, "total": total,
                "name": step.get("name"), "focus": focus, "screen_text": text, "poll": True,
            })
            if check_expect(expect, focus, text):
                self._emit(on_event, {"event": "step_ok", "index": index, "total": total,
                                      "name": step.get("name"), "expect": expect})
                return [(focus, text)], ["poll"]
            self.sleep(wait_s)
        focus, text = self.bridge.screen()
        raise StepAbort(step, [(focus, text)], ["poll"], attempts=self.max_attempts)

    def run_flow(
        self,
        steps: Sequence[dict[str, Any]],
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Corre uma lista de passos (o fluxo provado, com ≤ 5 tentativas cada)."""
        steps = list(steps)
        for i, step in enumerate(steps, 1):
            self.run_step(step, on_event=on_event, cancel=cancel, index=i, total=len(steps))
        return {"ok": True, "steps": len(steps)}

    # ------------------------------------------------------------- construção
    def _slice_world(self) -> list[dict[str, Any]]:
        world: list[dict[str, Any]] = []
        for step in self.steps:
            world.append(dict(step))
            if step.get("name") == WORLD_FLOW_END:
                break
        return world

    def _slice_character(self) -> list[dict[str, Any]]:
        character: list[dict[str, Any]] = []
        started = False
        for step in self.steps:
            if step.get("name") == CHARACTER_FLOW_START:
                started = True
            if started:
                character.append(dict(step))
        return character

    @staticmethod
    def _soft_step(name: str, directive: str) -> dict[str, Any]:
        """Passo aditivo best-effort (GUESS): nunca aborta o fluxo."""
        return {"name": name, "expect": [""], "keys": [directive],
                "wait": 0.8, "guessed": True, "soft": True}

    def build_world_steps(
        self, *, world_name: str | None = None, params: str = "quick"
    ) -> list[dict[str, Any]]:
        """Passos do fluxo de criação de mundo (título → ``choose_game_type``).

        ``params="quick"`` (GUESS) tenta encurtar o worldgen clicando labels de
        tamanho/história curtos quando visíveis; ``params="default"`` aceita os
        parâmetros do ecrã tal como estão (o fluxo provado).  ``world_name`` é
        best-effort: clica o campo de nome e escreve-o (soft).
        """
        steps = self._slice_world()
        if params == "quick":
            tweak = self._soft_step("quick_world_params", "click_re ^(Pocket|Small|Short)")
        else:
            tweak = None
        name_steps: list[dict[str, Any]] = []
        if world_name:
            name_steps = [
                self._soft_step("world_name_field", "click_re ^Name"),
                self._soft_step("world_name_type", f"type {world_name}"),
            ]
        out: list[dict[str, Any]] = []
        inserted = False
        for step in steps:
            if step.get("name") == "create_world_defaults":
                if tweak is not None:
                    out.append(tweak)
                out.extend(name_steps)
                inserted = True
            out.append(step)
        if not inserted:  # steps customizados sem esse passo: extras no início
            out = ([tweak] if tweak is not None else []) + name_steps + out
        return out

    def build_character_steps(
        self,
        *,
        name: str | None = None,
        race: str | None = None,
        origin: str | None = None,
        randomize: bool = False,
    ) -> list[dict[str, Any]]:
        """Passos da criação de personagem (``choose_adventurer`` → in-game).

        Os parâmetros **guiam a correspondência de labels** (o que o contrato
        chama "params guide label-matching"): ``race`` vira ``Select <race>``,
        ``origin`` vira ``Select <origin>``; sem parâmetros o fluxo aceita tudo
        (labels ``Accept``/``Ready`` do fluxo provado).  ``name``/``randomize``
        são passos soft (GUESS) e nunca abortam o fluxo.
        """
        steps = self._slice_character()
        name_steps: list[dict[str, Any]] = []
        if name:
            name_steps = [
                self._soft_step("character_name_field", "click_re ^Name"),
                self._soft_step("character_name_type", f"type {name}"),
            ]
        randomize_looks = self._soft_step("randomize_looks", "click_re ^Randomize")
        randomize_personality = self._soft_step("randomize_personality", "click_re ^Randomize")
        out: list[dict[str, Any]] = []
        for step in steps:
            step = dict(step)
            step_name = step.get("name")
            if step_name == "select_human":
                step["keys"] = [f'click "Select {race or "human"}"']
            elif step_name == "select_origin" and origin:
                step["keys"] = [f'click "Select {origin}"']
            if step_name == "accept_appearance" and randomize:
                out.append(randomize_looks)
            if step_name == "accept_personality" and randomize:
                out.append(randomize_personality)
            out.append(step)
            if step_name == "select_origin":
                out.extend(name_steps)
        return out

    # ------------------------------------------------------------------ fluxos
    def create_world(
        self,
        *,
        world_name: str | None = None,
        params: str = "quick",
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Título → mundo criado → exportado (termina no ecrã de tipo de jogo)."""
        if params not in ("quick", "default"):
            raise ValueError(f"params invalido: {params!r} (esperava 'quick' ou 'default')")
        steps = self.build_world_steps(world_name=world_name, params=params)
        return self.run_flow(steps, on_event=on_event, cancel=cancel)

    def create_character(
        self,
        *,
        name: str | None = None,
        race: str | None = None,
        origin: str | None = None,
        randomize: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Tipo de jogo ``Adventurer`` → personagem criada → in-game (``dungeonmode``)."""
        steps = self.build_character_steps(
            name=name, race=race, origin=origin, randomize=randomize
        )
        return self.run_flow(steps, on_event=on_event, cancel=cancel)

    def load_adventure(
        self,
        adventure_id: str,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Carrega um save existente.

        Saves **ativos** (``world.sav``) usam o ``load-save`` do DFHack (não
        interativo, GROUND na documentação DFHack); saves inativos (só mundo
        gerado) seguem o fluxo de menus até ao ecrã de tipo de jogo — a partir
        daí a criação de personagem continua com ``create_character``.
        """
        adventures = self.list_adventures()
        adv = next((a for a in adventures if a.get("id") == adventure_id), None)
        if adv is None:
            raise UnknownAdventure(f"aventura desconhecida: {adventure_id!r}")

        if adv.get("kind") == "active":
            self.bridge.command(["run", "load-save", adv["id"]])
            self._emit(on_event, {
                "event": "action", "index": 1, "total": 1, "name": "load_save",
                "action": f"load-save {adv['id']}",
            })
            self.run_step(
                {"name": "poll_loaded", "keys": ["poll"], "wait": 3.0,
                 "expect": ["focus:dungeonmode"]},
                on_event=on_event, cancel=cancel, index=1, total=1,
            )
            return {"ok": True, "via": "load-save", "adventure": adv}

        steps = self._load_menu_steps(adv)
        self.run_flow(steps, on_event=on_event, cancel=cancel)
        return {"ok": True, "via": "menu", "adventure": adv}

    @staticmethod
    def _load_menu_steps(adv: dict[str, Any]) -> list[dict[str, Any]]:
        """Fluxo de menus para carregar um save (GUESS — labels derivados do save).

        Termina num ``poll`` verificável (``choose_game_type`` ou ``dungeonmode``)
        para que a falha final tenha o relatório PT das 5 tentativas.
        """
        label = str(adv.get("name") or adv.get("id") or "")
        folder = str(adv.get("id") or "")
        return [
            {"name": "title_to_start", "keys": ['click "Start Game"'],
             "expect": [""], "wait": 1.0, "guessed": True, "soft": True},
            {"name": "select_save", "keys": [f'click "{label}"'],
             "expect": [""], "wait": 1.0, "guessed": True, "soft": True},
            {"name": "select_save_folder", "keys": [f"click_re {re.escape(folder)}"],
             "expect": [""], "wait": 1.0, "guessed": True, "soft": True},
            {"name": "poll_game_type", "keys": ["poll"], "wait": 3.0,
             "expect": ["focus:choose_game_type", "focus:dungeonmode"]},
        ]

    # ------------------------------------------------------------- saves/estado
    def list_adventures(self, save_root: Any = None) -> list[dict[str, Any]]:
        """Saves da pasta ``data/save`` (best-effort, ver nota GUESS no módulo).

        Entrada: ``{"id", "name", "kind": "world"|"active", "folder", "saved_at"}``.
        ``kind: "active"`` = tem ``world.sav`` (jogo ativo, carregável com
        ``load-save``); ``kind: "world"`` = só ``world.dat`` (mundo gerado,
        nenhum jogo iniciado).  Ordenado por ``saved_at`` descendente.
        """
        root = Path(save_root) if save_root is not None else self.save_root()
        if not root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            out.append(self._describe_save(folder))
        out.sort(key=lambda a: a.get("saved_at") or "", reverse=True)
        return out

    @staticmethod
    def _describe_save(folder: Path) -> dict[str, Any]:
        name = re.sub(r"-\d{4,5}-\d{2}-\d{2}$", "", folder.name) or folder.name
        world_dat = folder / "world.dat"
        world_sav = folder / "world.sav"
        kind = "active" if world_sav.is_file() else "world"
        newest = max(
            (p.stat().st_mtime for p in (world_sav, world_dat) if p.is_file()),
            default=folder.stat().st_mtime,
        )
        saved_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(newest))
        return {
            "id": folder.name,
            "name": name,
            "kind": kind,
            "folder": str(folder),
            "saved_at": saved_at,
            # GUESS: nomes de personagens vivem em binários dentro do save —
            # não são parseáveis offline; omitido (contract: `characters?[]`).
            "characters": None,
        }

    def current_state(self, *, adventure_id: str | None = None) -> dict[str, Any] | None:
        """``current`` do contrato: o aventureiro do último snapshot, se existir.

        ``None`` quando não há snapshot utilizável ou não há aventureiro (é
        exatamente o caso "sem aventureiro" da ponte).  ``alive`` é best-effort:
        ``False`` quando o gamelog marca a morte do aventureiro.
        """
        try:
            snap = self.bridge.snapshot()
        except Exception:
            return None
        if not isinstance(snap, dict):
            return None
        adv = snap.get("adventurer")
        if not isinstance(adv, dict) or not str(adv.get("name") or "").strip():
            return None
        name = str(adv["name"])
        location = (snap.get("metrics") or {}).get("location") or {}
        world = location.get("world") or None
        aid = adventure_id or self._match_adventure(str(world or ""))
        return {
            "adventure_id": aid,
            "adventurer_name": name,
            "world_name": world,
            "alive": self._alive(snap, name),
        }

    def _match_adventure(self, world_name: str) -> str | None:
        """GUESS: a pasta pode ter sido renomeada para o nome do mundo."""
        if not world_name:
            return None
        for adv in self.list_adventures():
            if str(adv.get("name") or "").lower() == world_name.lower() \
                    or str(adv.get("id") or "").lower() == world_name.lower():
                return str(adv.get("id"))
        return None

    @staticmethod
    def _alive(snap: dict[str, Any], name: str) -> bool:
        health = (snap.get("adventurer") or {}).get("health") or {}
        if health.get("blood") == 0:
            return False
        low = name.lower()
        for event in snap.get("gamelog_events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("category") == "death" and low in str(event.get("text") or "").lower():
                return False
        return True


# --------------------------------------------------------------------------- #
# Processo do jogo (system control)                                             #
# --------------------------------------------------------------------------- #


class GameProcessError(RuntimeError):
    """O processo do jogo não pode ser gerido (caminho inválido, spawn falhou)."""


class OsProcessOps:
    """Operações de processo reais (Linux) — injetáveis para testes.

    Contrato: :meth:`find_game` devolve **apenas** processos do jogo que
    pertencem a ``df_path`` (guarda: só se gere o jogo no caminho configurado).
    """

    #: raiz do /proc (injectável para testes do scan)
    def __init__(self, proc_root: Any = "/proc", sleep: Callable[[float], None] | None = None) -> None:
        self.proc_root = Path(proc_root)
        self.sleep = sleep or time.sleep

    def find_game(self, df_path: Any) -> list[dict[str, int]]:
        """``[{"pid", "pgid"}]`` dos ``dwarfort`` instalados em ``df_path``."""
        base = str(Path(df_path).resolve())
        found: list[dict[str, int]] = []
        try:
            entries = list(self.proc_root.iterdir())
        except OSError:  # pragma: no cover - sem /proc não há scan
            return []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                comm = (entry / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if comm != GAME_PROCESS_NAME:
                continue
            try:
                exe = os.readlink(str(entry / "exe"))
                cwd = os.readlink(str(entry / "cwd"))
            except OSError:
                exe = cwd = ""
            if not (exe.startswith(base) or cwd.startswith(base)):
                continue  # outro Dwarf Fortress: não é nosso
            try:
                pgid = os.getpgid(int(entry.name))
            except OSError:  # pragma: no cover - processo morreu no meio
                pgid = int(entry.name)
            found.append({"pid": int(entry.name), "pgid": pgid})
        return found

    def spawn(self, cmd: Sequence[str], *, cwd: str, env: dict[str, str], log_path: Any) -> int:
        """Lança destacado (``start_new_session`` = próprio grupo) com log."""
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                list(cmd), cwd=cwd, env=env, stdout=log, stderr=log,
                start_new_session=True, close_fds=True,
            )
        finally:
            log.close()  # o filho ficou com a sua cópia do fd
        return proc.pid

    def signal_group(self, pgid: int, sig: int) -> bool:
        """``kill -<sig> -- -<pgid>`` com fallback para o pid (como o sod.sh)."""
        try:
            os.killpg(pgid, sig)
            return True
        except OSError:
            try:
                os.kill(pgid, sig)
                return True
            except OSError:
                return False

    def group_alive(self, pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
            return True
        except OSError:
            return False

    def probe_rpc(self, port: int) -> bool:
        """Sonda TCP do RPC DFHack (best-effort — o snapshot é quem confirma)."""
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except OSError:
            return False


def resolve_rpc_port(df_path: Any) -> int:
    """Porta RPC DFHack (``DFHACK_PORT`` > ``remote-server.json`` > 5000)."""
    env = os.environ.get("DFHACK_PORT", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    try:
        with open(Path(df_path) / "dfhack-config" / "remote-server.json", encoding="utf-8") as fh:
            port = json.load(fh).get("port")
        if isinstance(port, int) and port > 0:
            return port
    except (OSError, ValueError):
        pass
    return 5000  # default real do DFHack


class GameProcessManager:
    """Arranca/pára o jogo no **caminho configurado** (o mesmo lançamento do sod.sh).

    * ``start``: ``env SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=x11
      LIBGL_ALWAYS_SOFTWARE=1 ./dfhack`` destacado (grupo próprio) com log;
    * ``stop``: escalonamento provado — ``kill -TERM -- -<pgid>``, espera do
      grupo até 10 s e ``kill -KILL -- -<pgid>`` (o dwarfort ignora SIGTERM);
    * guarda: só gere processos do jogo pertencentes a ``df_path``
      (:meth:`OsProcessOps.find_game`) e só lança pelo lançador desse caminho.

    ``ops`` é o seam injetável — os testes nunca lançam nem matam processos.
    """

    def __init__(
        self,
        df_path: str | None = None,
        *,
        ops: Any | None = None,
        log_path: Any = None,
        runtime_dir: Any = None,
    ) -> None:
        self.df_path = Path(df_path or default_df_path()).expanduser()
        self.ops = ops or OsProcessOps()
        self.runtime_dir = Path(
            runtime_dir or os.environ.get("SOD_RUNTIME_DIR") or (REPO_ROOT / ".runtime")
        )
        self.log_path = Path(log_path) if log_path else self.runtime_dir / "game.log"

    # ------------------------------------------------------------------ estado
    def status(self) -> dict[str, Any]:
        """``{"running", "pid", "rpc", "path"}`` — a forma do ``GET /api/system/status``."""
        procs = self.ops.find_game(self.df_path)
        running = bool(procs)
        pid = procs[0]["pid"] if procs else None
        rpc = False
        if running:
            try:
                rpc = bool(self.ops.probe_rpc(resolve_rpc_port(self.df_path)))
            except Exception:  # pragma: no cover - a sonda nunca parte o estado
                rpc = False
        return {"running": running, "pid": pid, "rpc": rpc, "path": str(self.df_path)}

    # ------------------------------------------------------------------ controlo
    def start(self) -> dict[str, Any]:
        """Lança o jogo (idempotente: já a correr → ``already_running``)."""
        launcher = self.df_path / "dfhack"
        if not launcher.is_file() or not os.access(str(launcher), os.X_OK):
            raise GameProcessError(f"lancador nao encontrado: {launcher}")
        state = self.status()
        if state["running"]:
            return {"action": "start", "already_running": True, **state}
        env = dict(os.environ)
        env.update({
            "SDL_AUDIODRIVER": "dummy",   # regra do utilizador: sem som
            "SDL_VIDEODRIVER": "x11",
            "LIBGL_ALWAYS_SOFTWARE": "1",
        })
        try:
            pid = self.ops.spawn([str(launcher)], cwd=str(self.df_path),
                                 env=env, log_path=self.log_path)
        except OSError as exc:
            raise GameProcessError(f"falhei a lancar o jogo: {exc}") from exc
        self._write_pidfile(pid)
        return {"action": "start", "already_running": False, "running": True,
                "pid": pid, "rpc": False, "path": str(self.df_path)}

    def stop(self, *, grace: float = 10.0) -> dict[str, Any]:
        """Pára o jogo com o escalonamento provado TERM→KILL por grupo."""
        procs = self.ops.find_game(self.df_path)
        if not procs:
            return {"action": "stop", "stopped": False, **self.status()}
        groups = [int(p["pgid"]) for p in procs]
        for pgid in groups:
            self.ops.signal_group(pgid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while any(self.ops.group_alive(pgid) for pgid in groups) and time.monotonic() < deadline:
            self.ops.sleep(1.0)
        forced = []
        for pgid in groups:
            if self.ops.group_alive(pgid):
                self.ops.signal_group(pgid, signal.SIGKILL)
                forced.append(pgid)
        if forced:
            self.ops.sleep(0.5)
        self._clear_pidfile()
        state = self.status()
        return {"action": "stop", "stopped": True, "forced_groups": forced, **state}

    # ------------------------------------------------------------------ pidfile
    def _write_pidfile(self, pid: int) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            (self.runtime_dir / "game.pid").write_text(f"{pid}\n", encoding="utf-8")
        except OSError:  # pragma: no cover - best-effort, como o sod.sh
            pass

    def _clear_pidfile(self) -> None:
        try:
            (self.runtime_dir / "game.pid").unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
