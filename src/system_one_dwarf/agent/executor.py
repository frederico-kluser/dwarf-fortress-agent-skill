"""Execução de uma macro-ação: da :class:`MacroAction` ao DSL do ``df_bridge``.

O executor é a única peça do loop que escreve no jogo. Três regras não
negociáveis:

1. **Gates de segurança sempre ligados.** Todo ``act batch`` sai com
   ``--stop-on-threat`` **e** ``--stop-on-focus-change`` (e sem
   ``--allow-travel-barrier``): a ponte aborta o lote no primeiro passo em que
   aparece uma ameaça ou muda o *focus* da tela, antes de continuar a carregar
   teclas às cegas.
2. **Guard antes de executar.** Antes de mandar seja o que for, o executor relê
   o snapshot e compara-o com o estado que alimentou a decisão. Se o *focus*
   mudou, se apareceu um hostil, se a ameaça colou ao aventureiro ou se ele
   morreu, o plano é **recusado** (``status="aborted"``) com o motivo — a ação
   foi decidida para outra situação.
3. **Nada de teclas inventadas.** Só passos *verificados* são executados:
   ``move``, ``goto``, ``wait``, ``read``, ``sleep`` (ops do DSL com semântica
   fechada). Abrir inventário, selecionar item, consumir, pegar, atacar e falar
   dependem de sequências de ``df.interface_key`` que ainda **não** foram
   confirmadas ao vivo: esses passos ficam no plano como *manual*, com nota, e
   o resultado sai com ``needs_manual_mapping=True`` executando apenas a
   parte de movimento/espera. (O mapeamento ao vivo é refinado contra o jogo
   real; o plano já está no formato final, só faltam as teclas.)

Comandos gerados (mesma convenção de flags do ``GameStateClient.command()``)::

    python3 dfhack/df_bridge.py act batch --json --stop-on-focus-change \
        --stop-on-threat --max-steps 120 "<dsl>"
    python3 dfhack/df_bridge.py unpause --json     # passo 9 (avançar tempo)
    python3 dfhack/df_bridge.py pause   --json     # …e voltar a pausar

O runner é injetável (``runner(argv, timeout) -> objeto com returncode/stdout/
stderr``), pelo que nada aqui precisa do jogo aberto para ser testado.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..game.models import GameState
from .types import (
    ExecutionResult,
    MacroAction,
    PlanStep,
    TimeAdvanceResult,
    utc_now_iso,
)

__all__ = [
    "DEFAULT_THREAT_RADIUS",
    "MAX_COORD",
    "MAX_PLAN_STEPS",
    "MOVE_DIRECTIONS",
    "BridgeRunner",
    "MacroExecutor",
    "SubprocessBridgeRunner",
    "EXIT_CMD_FAILED",
    "EXIT_NO_DF",
    "EXIT_UNREACHABLE",
]

#: Repositório: ``src/system_one_dwarf/agent/executor.py`` → raiz.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BRIDGE_PATH = REPO_ROOT / "dfhack" / "df_bridge.py"

#: Códigos de saída da ponte (espelham ``dfhack/df_bridge.py``).
EXIT_OK, EXIT_NO_DF, EXIT_NO_GAMELOG, EXIT_UNREACHABLE, EXIT_CMD_FAILED = 0, 10, 11, 12, 13
GAME_DOWN_EXITS = (EXIT_NO_DF, EXIT_UNREACHABLE)

DEFAULT_THREAT_RADIUS = 10
DEFAULT_MAX_STEPS = 120
DEFAULT_DELAY = 0.4


class BridgeRunner(Protocol):
    """Assinatura mínima do runner injetável (o ``GameStateClient`` já tem um)."""

    def __call__(self, argv: Sequence[str], timeout: float) -> Any:  # pragma: no cover
        ...


class SubprocessBridgeRunner:
    """Runner real: corre o ``df_bridge.py`` como subprocesso (mesma rota do snapshot)."""

    def __init__(self, python: str | None = None, cwd: str | Path | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        self.python = python or sys.executable or "python3"
        self.cwd = str(cwd or REPO_ROOT)
        self.env = dict(env) if env else {}

    def __call__(self, argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(self.env)
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(timeout),
            cwd=self.cwd,
            env=env,
        )


def _attr(source: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    return default


def _pick(source: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """Primeiro valor não-``None`` de um mapeamento (``params`` da macro-ação)."""
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return default


class MacroExecutor:
    """Traduz macro-ações em planos DSL e executa-os com gates de segurança.

    Args:
        game: cliente do jogo (``GameStateClient``) — usado para herdar
            ``python``/``bridge_path``/``--df-path``/``--port``/``--via`` e para
            o *guard* (``snapshot()``). ``None`` é permitido em testes puros de
            plano (o guard é desligado).
        config: :class:`~.types.LoopConfig` (``step_timeout_s``, ``dry_run``,
            ``advance_seconds``, …).
        runner: runner injetável ``(argv, timeout) -> resultado``; por omissão
            corre o ``df_bridge.py`` local.
        bridge_path / python / df_path / port / via: sobrepõem o que vem do
            cliente do jogo.
        threat_radius / max_steps / delay: parâmetros do ``act batch``.
        guard: ``True`` (padrão) relê o snapshot antes de executar e recusa o
            plano se a situação mudou.
        dry_run: monta e valida o plano na ponte sem premir nada (``--dry-run``).
        sleeper: injeção do sono (testes não esperam de verdade).
    """

    def __init__(
        self,
        game: Any = None,
        config: Any = None,
        *,
        runner: BridgeRunner | Callable[..., Any] | None = None,
        bridge_path: str | Path | None = None,
        python: str | None = None,
        df_path: str | None = None,
        port: int | None = None,
        via: str | None = None,
        threat_radius: int = DEFAULT_THREAT_RADIUS,
        max_steps: int = DEFAULT_MAX_STEPS,
        delay: float = DEFAULT_DELAY,
        guard: bool = True,
        dry_run: bool | None = None,
        sleeper: Callable[[float], Any] | None = None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.game = game
        self.config = config
        self.bridge_path = Path(
            bridge_path or _attr(game, "bridge_path", default=DEFAULT_BRIDGE_PATH)
        )
        self.python = python or _attr(game, "python", default=None) or sys.executable or "python3"
        self.df_path = df_path if df_path is not None else _attr(game, "df_path")
        self.port = port if port is not None else _attr(game, "port")
        self.via = (via if via is not None else _attr(game, "via", default="auto")) or "auto"
        self.threat_radius = int(threat_radius)
        self.max_steps = int(max_steps)
        self.delay = float(delay)
        self.guard_enabled = bool(guard)
        if dry_run is None:
            dry_run = bool(getattr(config, "dry_run", False))
        self.dry_run = bool(dry_run)
        self._sleeper = sleeper
        self._clock = clock
        self._runner = runner if runner is not None else _attr(game, "runner", default=None)
        #: Comandos emitidos (auditoria/testes) — só o texto, sem segredos.
        self.commands: list[tuple[str, ...]] = []

    # ------------------------------------------------------------------ runner

    @property
    def runner(self) -> BridgeRunner:
        if self._runner is None:
            self._runner = SubprocessBridgeRunner(self.python)
        return self._runner

    def _timeout(self, default: float = 60.0) -> float:
        value = getattr(self.config, "step_timeout_s", None)
        return float(value) if value else float(default)

    def _run(self, argv: Sequence[str], timeout: float | None = None) -> Any:
        argv = tuple(str(part) for part in argv)
        self.commands.append(argv)
        runner = self.runner
        limit = float(timeout if timeout is not None else self._timeout())
        try:
            return runner(argv, limit)
        except TypeError:  # runner mínimo que só aceita argv
            return runner(argv)  # type: ignore[misc]

    def _global_flags(self) -> list[str]:
        flags: list[str] = ["--json"]
        if self.df_path:
            flags += ["--df-path", str(self.df_path)]
        if self.port:
            flags += ["--port", str(self.port)]
        if self.via and self.via != "auto":
            flags += ["--via", str(self.via)]
        return flags

    def bridge_command(self, *subcommand: str, flags: bool = True) -> list[str]:
        """``[python, df_bridge.py, <subcomando…>] + flags globais``.

        As flags globais (``--json``/``--df-path``/``--port``/``--via``) vêm logo
        a seguir ao subcomando, como no ``GameStateClient.command()``.
        """
        argv = [self.python, str(self.bridge_path), *subcommand]
        if flags:
            argv += self._global_flags()
        return argv

    # ------------------------------------------------------------------- plano

    def plan(self, action: MacroAction, state: GameState | None = None) -> tuple[PlanStep, ...]:
        """Plano DSL da macro-ação: passos verificados + passos *manual* (com nota).

        Os argumentos que entram no DSL (direção, nº de passos, coordenadas) são
        validados contra o **vocabulário da própria DSL** antes de serem
        interpolados: uma ``MacroAction`` construída à mão com
        ``direction="n; key A_TALK"`` levanta ``ValueError`` aqui em vez de
        produzir lixo que a ponte recusaria no ``parse`` (nada é injetado: o
        texto nunca chega a ser comando).

        Raises:
            TypeError: ``action`` não é uma :class:`~.types.MacroAction`.
            ValueError: argumento fora do vocabulário/intervalo (``direction``,
                ``steps``, coordenadas).
        """
        if not isinstance(action, MacroAction):
            raise TypeError(f"plan expects a MacroAction, got {type(action).__name__}")
        params = dict(action.params or {})
        direction = _safe_direction(_pick(params, "direction", "dir", default=""))
        steps = _safe_steps(params.get("steps"), action.id)
        verb = action.verb or action.id.split(":", 1)[0]

        plan: list[PlanStep] = []
        if verb == "attack":
            if direction and steps > 0:
                plan.append(PlanStep(f"move {direction} times={steps}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
            plan.append(PlanStep(
                "",
                manual=True,
                note=(
                    "attack key unverified: in adventure mode walking into the hostile's tile is "
                    "the attack; no df.interface_key for an explicit attack was confirmed live"
                ),
            ))
        elif verb == "flee":
            if direction and steps > 0:
                plan.append(PlanStep(f"move {direction} times={steps}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
        elif verb == "rest":
            plan.append(PlanStep("wait long", manual=False))
            plan.append(PlanStep("sleep 1500", manual=False))
            plan.append(PlanStep("read screen", manual=False))
            plan.append(PlanStep(
                "",
                manual=True,
                note=(
                    "true sleeping (multi-hour) needs the travel/sleep verb "
                    "(A_TRAVEL_SLEEP is a travel key and is unverified for resting): this plan "
                    "passes turns instead"
                ),
            ))
        elif verb in ("drink_potion", "eat", "drink"):
            item = str(params.get("item") or action.target or "")
            if params.get("search"):
                plan.append(PlanStep("read screen", manual=False))
                plan.append(PlanStep(
                    "",
                    manual=True,
                    note=(
                        f"nothing to {'drink' if verb == 'drink' else 'eat'} in the pack: reaching "
                        f"{action.target or 'a source'} needs a route/landmark that is not in the "
                        "snapshot (no coordinates to goto)"
                    ),
                ))
            else:
                plan.append(PlanStep("wait short", manual=False))
                plan.append(PlanStep("read screen", manual=False))
                plan.append(PlanStep(
                    "",
                    manual=True,
                    note=(
                        f"consume '{item}' unverified: opening the inventory, selecting the item "
                        "and using it needs live mapping of df.interface_key names"
                    ),
                ))
        elif verb == "pick_up":
            x = _safe_coord(params.get("x"), "x", action.id)
            y = _safe_coord(params.get("y"), "y", action.id)
            if x is not None and y is not None:
                plan.append(PlanStep(f"goto {x},{y}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
            plan.append(PlanStep(
                "",
                manual=True,
                note=(
                    f"pickup key unverified: grabbing '{params.get('item') or action.target}' "
                    "from the ground needs live mapping"
                ),
            ))
        elif verb == "talk":
            if direction and steps > 0:
                plan.append(PlanStep(f"move {direction} times={steps}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
            plan.append(PlanStep(
                "",
                manual=True,
                note=(
                    "talk flow unverified: A_TALK is a real interface key, but the conversation "
                    "menu/focus sequence was not confirmed live"
                ),
            ))
        elif verb == "navigate_to":
            x = _safe_coord(params.get("x"), "x", action.id)
            y = _safe_coord(params.get("y"), "y", action.id)
            if x is not None and y is not None:
                plan.append(PlanStep(f"goto {x},{y}", manual=False))
                plan.append(PlanStep("read screen", manual=False))
            else:
                plan.append(PlanStep("read screen", manual=False))
                plan.append(PlanStep(
                    "",
                    manual=True,
                    note=(
                        f"'{action.target or 'destination'}' has no coordinates in the snapshot: "
                        "mapping a named destination needs the travel landmark flow"
                    ),
                ))
        elif verb == "explore":
            walk = direction or "n"
            plan.append(PlanStep(f"move {walk} times={max(1, steps or 3)}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
        elif verb == "wait":
            kind = str(params.get("kind") or "short")
            plan.append(PlanStep(f"wait {kind}", manual=False))
            plan.append(PlanStep("read screen", manual=False))
        elif verb == "other":
            plan.append(PlanStep("read screen", manual=False))
        else:  # verbo desconhecido: não se inventa um plano
            plan.append(PlanStep(
                "", manual=True,
                note=f"no DSL plan for unknown verb {verb!r}",
            ))
        return tuple(plan)

    def plan_dsl(self, action: MacroAction, state: GameState | None = None) -> str:
        """DSL completo do plano: passos verificados + notas manuais como comentários.

        O texto continua a ser DSL válido (``#`` é comentário na ponte), mas só
        os passos verificados são enviados ao jogo (ver :meth:`executable_dsl`).
        """
        return self._render(self.plan(action, state), include_manual=True)

    def executable_dsl(self, action: MacroAction, state: GameState | None = None) -> str:
        """APENAS os passos verificados (o que é realmente executado)."""
        return self._render(self.plan(action, state), include_manual=False)

    @staticmethod
    def _render(plan: Sequence[PlanStep], *, include_manual: bool) -> str:
        """Junta o plano em DSL.

        O ``parse_batch`` da ponte parte o texto por ``;`` **antes** de tirar os
        comentários ``#``, por isso uma nota manual com ``;`` (ou mudança de
        linha) viraria um "passo desconhecido": a nota é sanitizada aqui e o
        plano completo continua a ser DSL válido.
        """
        parts: list[str] = []
        for step in plan:
            if step.manual:
                if include_manual and step.note:
                    parts.append(f"# MANUAL: {_comment_safe(step.note)}")
                continue
            if step.dsl.strip():
                parts.append(_comment_safe(step.dsl.strip()))
        return "; ".join(parts)

    @staticmethod
    def _manual_notes(plan: Sequence[PlanStep]) -> tuple[str, ...]:
        notes = [step.note for step in plan if step.manual and step.note]
        if any(step.manual and not step.note for step in plan):
            notes.append("unmapped step without a note")
        return tuple(notes)

    # ------------------------------------------------------------------- guard

    @staticmethod
    def _threat_key(unit: Any) -> str:
        """Identidade estável de um hostil: nome (ou direção, se anónimo) + perigo.

        A contagem sozinha não chega: um goblin a 6 tiles substituído por um
        dragão a 7 tiles tem a mesma contagem e uma distância *maior* — sem
        identidade, a guarda deixava passar um plano decidido para outro mundo.
        """
        name = (getattr(unit, "name", None) or "").strip().lower()
        if not name:
            name = f"anon:{str(getattr(unit, 'direction', '') or '?').strip().lower()}"
        return f"{name}{'|great' if getattr(unit, 'great_danger', False) else ''}"

    def _threat_signature(self, state: GameState) -> tuple[int, float, tuple[str, ...], bool]:
        """``(nº de hostis, distância do mais próximo, identidades, great danger?)``."""
        hostiles = list(state.metrics.threats.hostiles)
        nearest = state.metrics.threats.nearest
        distance = float(nearest.distance) if nearest and nearest.distance is not None else 1e9
        identities = tuple(sorted(self._threat_key(unit) for unit in hostiles))
        great = any(bool(getattr(unit, "great_danger", False)) for unit in hostiles)
        return (len(hostiles), distance, identities, great)

    def guard_check(self, state: GameState) -> tuple[bool, str | None, tuple[str, ...]]:
        """A situação ainda é a que foi decidida? ``(ok, reason, warnings)``.

        Compara o snapshot atual com o estado que alimentou a decisão: *focus*,
        vida do aventureiro, **identidade/nível** dos hostis (um goblin trocado
        por um dragão conta como mudança, mesmo com a mesma contagem), ameaça
        nova e ameaça que colou (≤ 5 tiles). Nada disto dispara com um hostil que
        apenas se aproximou de longe (12 → 11 tiles): isso é o dia-a-dia e os
        gates do ``act batch`` continuam a cobri-lo.

        Não usável (sem cliente ou sem snapshot) ⇒ ``ok=True`` com aviso: os
        gates do próprio ``act batch`` continuam a proteger a execução.
        """
        if not self.guard_enabled or self.game is None:
            return True, None, ()
        snapshot = getattr(self.game, "snapshot", None)
        if not callable(snapshot):
            return True, None, ("guard unavailable: game client has no snapshot()",)
        try:
            current = snapshot()
        except Exception as exc:  # jogo caiu/ponte falhou: o guard não decide por nós
            return True, None, (f"guard could not re-read the state: {exc}",)
        if not isinstance(current, GameState):
            return True, None, ("guard ignored a non-GameState snapshot",)
        if state.focus and current.focus and state.focus != current.focus:
            return False, "guard:focus_changed", ()
        if not current.is_alive and state.is_alive:
            return False, "guard:adventurer_dead", ()
        before_count, before_distance, before_ids, before_great = self._threat_signature(state)
        after_count, after_distance, after_ids, after_great = self._threat_signature(current)
        if after_count > before_count:
            return False, "guard:threat_appeared", ()
        if after_ids != before_ids:
            return False, "guard:threat_set_changed", ()
        if after_great and not before_great:
            return False, "guard:great_danger_appeared", ()
        if after_distance <= 5 < before_distance:
            return False, "guard:threat_closed_in", ()
        return True, None, ()

    # --------------------------------------------------------------- execução

    def execute(self, action: MacroAction, state: GameState) -> ExecutionResult:
        """Executa a macro-ação (plano seguro) com gates de segurança ligados.

        Um plano inválido (argumento fora do vocabulário da DSL) **não** derruba o
        passo nem envia nada ao jogo: devolve ``refused`` com o motivo claro.
        """
        if not isinstance(action, MacroAction):
            raise TypeError(f"execute expects a MacroAction, got {type(action).__name__}")
        started = time.monotonic()
        try:
            steps = self.plan(action, state)
        except ValueError as exc:
            return _with_duration(ExecutionResult(
                action_id=action.id, status="refused", ok=False, reason="invalid_plan",
                errors=(str(exc),),
            ), started)
        full_dsl = self._render(steps, include_manual=True)
        safe_dsl = self._render(steps, include_manual=False)
        manual_notes = self._manual_notes(steps)
        needs_manual = any(step.manual for step in steps) or bool(action.needs_manual_mapping)

        def finish(result: ExecutionResult) -> ExecutionResult:
            return result if result.duration_ms else _with_duration(result, started)

        ok, reason, warnings = self.guard_check(state)
        if not ok:
            return finish(ExecutionResult(
                action_id=action.id, status="aborted", ok=False, reason=reason,
                dsl=full_dsl, steps_planned=len(steps), steps_executed=0,
                needs_manual_mapping=needs_manual, manual_notes=manual_notes,
                errors=tuple(warnings),
            ))
        if not safe_dsl.strip():
            return finish(ExecutionResult(
                action_id=action.id, status="refused", ok=False,
                reason="no_verified_steps",
                dsl=full_dsl, steps_planned=len(steps), steps_executed=0,
                needs_manual_mapping=needs_manual, manual_notes=manual_notes,
                errors=tuple(warnings) + (
                    "every step of this action still needs live key mapping",
                ),
            ))

        argv = self.bridge_command(
            "act", "batch",
            "--stop-on-focus-change", "--stop-on-threat",
            "--threat-radius", str(self.threat_radius),
            "--max-steps", str(self.max_steps),
            "--delay", str(self.delay),
        )
        if self.dry_run:
            argv.append("--dry-run")
        argv.append(safe_dsl)

        try:
            proc = self._run(argv)
        except Exception as exc:  # ponte ausente, timeout, OSError…
            return finish(ExecutionResult(
                action_id=action.id, status="error", ok=False, reason=f"bridge_failed: {exc}",
                dsl=full_dsl, executed_dsl=safe_dsl, steps_planned=len(steps),
                needs_manual_mapping=needs_manual, manual_notes=manual_notes,
                commands=(tuple(argv),), errors=tuple(warnings) + (str(exc),),
            ))

        code = getattr(proc, "returncode", None)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        report = _parse_report(stdout)
        status, ok_flag, failure = self._classify(code, report, dry_run=self.dry_run)
        executed = 0
        if isinstance(report, Mapping):
            summary = report.get("summary")
            if isinstance(summary, Mapping):
                executed = int(summary.get("executed") or 0)
            elif report.get("dry_run"):
                executed = 0
        errors = tuple(warnings)
        if failure:
            errors = errors + (failure,)
        if code not in (EXIT_OK, EXIT_CMD_FAILED) and stderr:
            errors = errors + (str(stderr)[:300],)
        return finish(ExecutionResult(
            action_id=action.id, status=status, ok=ok_flag,
            reason=report.get("reason") if isinstance(report, Mapping) and report.get("reason") else failure,
            dsl=full_dsl, executed_dsl=safe_dsl, steps_planned=len(steps),
            steps_executed=executed, needs_manual_mapping=needs_manual,
            manual_notes=manual_notes, commands=(tuple(argv),),
            report=report if isinstance(report, Mapping) else None, errors=errors,
        ))

    @staticmethod
    def _classify(code: Any, report: Any, *, dry_run: bool) -> tuple[str, bool, str | None]:
        """``(status, ok, failure_reason)`` a partir do código de saída + relatório."""
        if code in GAME_DOWN_EXITS:
            return "error", False, f"the game is not reachable (bridge exit {code})"
        if dry_run and code == EXIT_OK:
            return "dry_run", True, None
        if isinstance(report, Mapping):
            if report.get("aborted_at") is not None:
                return "aborted", False, None
            if report.get("dry_run"):
                return "dry_run", True, None
            if report.get("ok") is False:
                return "error", False, "the bridge reported a failed plan"
            if code in (EXIT_OK, None):
                return "executed", True, None
        if code == EXIT_CMD_FAILED:
            return "aborted", False, "the bridge aborted the batch (exit 13)"
        if code == EXIT_OK:
            return "executed", True, None
        return "error", False, f"unexpected bridge exit code {code!r}"

    # ------------------------------------------------------------ tempo (passo 9)

    def advance_time(self, seconds: float | None = None) -> TimeAdvanceResult:
        """Despausa ``seconds`` e volta a pausar (o JEV controla o tempo)."""
        started = time.monotonic()
        wait = float(seconds if seconds is not None else getattr(self.config, "advance_seconds", 0.0) or 0.0)
        if self.dry_run or wait <= 0:
            return TimeAdvanceResult(seconds=wait, ok=True, unpaused=False, paused=False,
                                     duration_ms=_ms(started))
        commands: list[tuple[str, ...]] = []
        errors: list[str] = []
        unpaused = paused = False
        try:
            argv = tuple(self.bridge_command("unpause", *self._global_flags()))
            commands.append(argv)
            proc = self._run(argv)
            code = getattr(proc, "returncode", None)
            unpaused = code == EXIT_OK
            if not unpaused:
                errors.append(f"unpause failed (bridge exit {code})")
            else:
                self._sleep(wait)
        except Exception as exc:
            errors.append(f"unpause failed: {exc}")
        finally:
            try:
                argv = tuple(self.bridge_command("pause", *self._global_flags()))
                commands.append(argv)
                proc = self._run(argv)
                code = getattr(proc, "returncode", None)
                paused = code == EXIT_OK
                if not paused:
                    errors.append(f"pause failed (bridge exit {code})")
            except Exception as exc:
                errors.append(f"pause failed: {exc}")
        return TimeAdvanceResult(
            seconds=wait, ok=unpaused and paused, unpaused=unpaused, paused=paused,
            commands=tuple(commands), errors=tuple(errors), duration_ms=_ms(started),
        )

    def _sleep(self, seconds: float) -> None:
        if self._sleeper is not None:
            self._sleeper(seconds)
        else:  # pragma: no cover - o sono real não corre nos testes
            time.sleep(float(seconds))


#: Direções aceites pelo op ``move`` da ponte (``df_bridge.MOVE_DIRS``).
MOVE_DIRECTIONS: frozenset[str] = frozenset(
    "n s e w ne nw se sw up down wait".split()
)

#: Teto de passos de um único passo do plano (um valor maior é bug de quem gerou).
MAX_PLAN_STEPS = 1000

#: Coordenadas plausíveis para ``goto`` (o mundo do DF não chega perto disto).
MAX_COORD = 1_000_000


def _safe_direction(value: Any) -> str:
    """Direção do vocabulário da DSL (``n``/``ne``/``up``…) ou ``""``.

    Raises:
        ValueError: valor não vazio fora do vocabulário (ex.: ``"n; key A_TALK"``).
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text not in MOVE_DIRECTIONS:
        raise ValueError(
            f"direction {value!r} is not in the DSL vocabulary "
            f"({', '.join(sorted(MOVE_DIRECTIONS))})"
        )
    return text


def _safe_steps(value: Any, action_id: str = "") -> int:
    """Nº de passos **inteiro** em ``0..MAX_PLAN_STEPS`` (``None``/vazio ⇒ 0).

    Um ``float`` com parte fracionária é recusado (truncar em silêncio um valor
    que ninguém quis dar seria inventar um plano).
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or (isinstance(value, float) and not float(value).is_integer()):
        raise ValueError(f"action {action_id!r}: steps {value!r} is not an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"action {action_id!r}: steps {value!r} is not an integer") from None
    if not 0 <= number <= MAX_PLAN_STEPS:
        raise ValueError(
            f"action {action_id!r}: steps {number} outside 0..{MAX_PLAN_STEPS}"
        )
    return number


def _safe_coord(value: Any, label: str, action_id: str = "") -> int | None:
    """Coordenada **inteira** dentro de ``±MAX_COORD`` (``None`` ⇒ ``None``)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or (isinstance(value, float) and not float(value).is_integer()):
        raise ValueError(f"action {action_id!r}: {label}={value!r} is not an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"action {action_id!r}: {label}={value!r} is not an integer") from None
    if not -MAX_COORD <= number <= MAX_COORD:
        raise ValueError(f"action {action_id!r}: {label}={number} outside ±{MAX_COORD}")
    return number


def _comment_safe(text: str) -> str:
    """Uma linha de DSL nunca pode conter ``;``/``#``/newline (senão vira passo)."""
    return " ".join(str(text).replace(";", ",").replace("#", "-").split())


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _with_duration(result: ExecutionResult, started: float) -> ExecutionResult:
    return replace(result, duration_ms=_ms(started))


def _parse_report(stdout: str) -> Mapping[str, Any] | None:
    """Extrai o relatório JSON do ``act batch`` (a ponte imprime-o inteiro)."""
    text = (stdout or "").strip()
    if not text:
        return None
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if starts:
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            text = text[start:end + 1]
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, Mapping) else None
