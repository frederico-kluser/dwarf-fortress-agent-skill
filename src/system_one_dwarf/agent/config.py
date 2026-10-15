"""Defaults e carregamento de configuração do loop (env + meta da campanha).

Três origens, por ordem de precedência (**a última ganha**):

1. os defaults de :class:`~.types.LoopConfig` (``docs/arquitetura.md``);
2. **variáveis de ambiente** ``SOD_*`` (o operador da máquina);
3. **meta da campanha** (``meta.json`` / ``Campaign.to_api()``) — a configuração
   daquela gameplay em particular.

Chaves desconhecidas e valores inválidos **nunca** derrubam o loop: são
ignorados e reportados em :func:`load_config_with_issues` (o loop prefere
arrancar com os defaults a morrer por causa de um ``meta.json`` com lixo).
O ``mode``/``kill_switch`` da meta são apenas o **valor de arranque** — a
verdade viva vem do callback ``control`` (painel de autonomia da UI).

Env (todas opcionais)::

    SOD_MODE=autopilot|checkpoint|paused     SOD_KILL_SWITCH=0|1
    SOD_AUTO_THRESHOLD=0.90                  SOD_HITL_THRESHOLD=0.50
    SOD_RETRIEVAL_K=8                        SOD_SEARCH_LIMIT=16
    SOD_RETRIEVAL_THRESHOLD=0.5              SOD_NEEDS_HUMAN_THRESHOLD=0.5
    SOD_MAX_CANDIDATES=12                    SOD_MAX_ITEMS=6
    SOD_STEP_TIMEOUT_S=120                   SOD_STEP_INTERVAL_S=0
    SOD_ADVANCE_SECONDS=5                    SOD_MAX_STATE_CHARS=8000
    SOD_FLEE_STEPS=5                         SOD_TALK_RADIUS=8
    SOD_PARALLEL_DECISIONS=0|1               SOD_TRANSLATE_SCREEN=0|1
    SOD_TRANSLATE_ASYNC=0|1                  SOD_SHUFFLE_OPTIONS=0|1
    SOD_ADVANCE_ON_EXECUTE=0|1               SOD_DRY_RUN=0|1
    SOD_OBJECTIVE="Reach the fortress alive."
    SOD_JEV_PROVIDER=typesafe                SOD_JEV_MODEL=typesafe/jev-1.13
    SOD_LLM_PROVIDER=openrouter              SOD_NARRATOR_MODEL=deepseek/deepseek-v4.1-flash
    SOD_SCREEN_MODEL=…                       SOD_QUESTION_MODEL=…
    SOD_SESSION_ID=system-one-dwarf          SOD_MAX_EVENTS=20

Watchdog global de sessão (Passo 18 · ``agent.session_watchdog``)::

    SOD_WATCHDOG_WINDOW_MIN=15               SOD_WATCHDOG_GRACE_MIN=2
    SOD_WATCHDOG_STAGE_STEP_MIN=7.5          SOD_WATCHDOG_ENABLED=1
    SOD_WATCHDOG_STAGES="replan,ask_human,kill_switch"

Meta da campanha (aceita chaves no topo **ou** dentro de ``config``/``loop``)::

    {"mode": "checkpoint", "kill_switch": false,
     "control": {"mode": "checkpoint", "kill_switch": false},
     "config": {"max_candidates": 8, "advance_seconds": 3}}
"""
from __future__ import annotations

import os
from dataclasses import fields, replace
from typing import Any, Mapping, MutableMapping, Sequence

from .types import ControlState, LoopConfig, MODES

__all__ = [
    "ENV_PREFIX",
    "ENV_KEYS",
    "META_SECTIONS",
    "WATCHDOG_ENV_KEYS",
    "control_from_any",
    "default_config",
    "env_overrides",
    "load_config",
    "load_config_with_issues",
    "merge_meta_sources",
    "meta_overrides",
    "parse_bool",
    "watchdog_env_overrides",
    "watchdog_meta_overrides",
    "watchdog_settings",
]

ENV_PREFIX = "SOD_"

#: ``SOD_<NOME>`` → campo do :class:`LoopConfig`.
ENV_KEYS: dict[str, str] = {
    "MODE": "mode",
    "KILL_SWITCH": "kill_switch",
    "AUTO_THRESHOLD": "auto_threshold",
    "HITL_THRESHOLD": "hitl_threshold",
    "RETRIEVAL_K": "retrieval_k",
    "RETRIEVAL_THRESHOLD": "retrieval_threshold",
    "NEEDS_HUMAN_THRESHOLD": "needs_human_threshold",
    "MEMORY_UPDATE_THRESHOLD": "memory_update_threshold",
    "SEARCH_LIMIT": "search_limit",
    "MAX_CANDIDATES": "max_candidates",
    "MAX_ITEMS": "max_items",
    "STEP_TIMEOUT_S": "step_timeout_s",
    "STEP_INTERVAL_S": "step_interval_s",
    "ADVANCE_SECONDS": "advance_seconds",
    "MAX_STATE_CHARS": "max_state_chars",
    "MAX_EVENTS": "max_events",
    "FLEE_STEPS": "flee_steps",
    "TALK_RADIUS": "talk_radius",
    "PARALLEL_DECISIONS": "parallel_decisions",
    "TRANSLATE_SCREEN": "translate_screen",
    "TRANSLATE_ASYNC": "screen_translation_async",
    "SHUFFLE_OPTIONS": "shuffle_options",
    "ADVANCE_ON_EXECUTE": "advance_on_execute",
    "DRY_RUN": "dry_run",
    "OBJECTIVE": "objective",
    "JEV_PROVIDER": "jev_provider",
    "JEV_MODEL": "jev_model",
    "LLM_PROVIDER": "llm_provider",
    "NARRATOR_MODEL": "narrator_model",
    "SCREEN_MODEL": "screen_model",
    "QUESTION_MODEL": "question_model",
    "SESSION_ID": "session_id",
}

#: Secções da meta onde as chaves de configuração podem aparecer.
META_SECTIONS: tuple[str, ...] = ("config", "loop", "settings")

_TRUE = ("1", "true", "yes", "on", "y", "sim")
_FALSE = ("0", "false", "no", "off", "n", "nao", "não", "")


def parse_bool(value: Any, default: bool | None = None) -> bool:
    """Booleano tolerante (``"1"``/``"true"``/``"on"``/``"sim"`` → True)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    if default is None:
        raise ValueError(f"cannot read a boolean from {value!r}")
    return default


def _field_types() -> dict[str, type]:
    return {field.name: type(field.default) for field in fields(LoopConfig)}


def _coerce(name: str, value: Any) -> Any:
    """Converte ``value`` para o tipo do campo (``ValueError`` se não der)."""
    if value is None:
        raise ValueError("value is None")
    kind = _field_types().get(name)
    if kind is bool:
        return parse_bool(value)
    if kind is int:
        if isinstance(value, bool):
            return int(value)
        return int(float(str(value).strip())) if isinstance(value, str) else int(value)
    if kind is float:
        if isinstance(value, bool):
            return float(value)
        return float(str(value).strip()) if isinstance(value, str) else float(value)
    return str(value)


def default_config(**overrides: Any) -> LoopConfig:
    """Defaults do sistema (com ``overrides`` validados pelo dataclass)."""
    return LoopConfig(**overrides)


def env_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Campos presentes no ambiente (``{}`` quando nenhuma variável ``SOD_*``)."""
    source = os.environ if env is None else env
    out: dict[str, Any] = {}
    for suffix, field in ENV_KEYS.items():
        raw = source.get(ENV_PREFIX + suffix)
        if raw is None:
            continue
        try:
            out[field] = _coerce(field, raw)
        except (TypeError, ValueError):
            continue
    return out


def _first_section(meta: Mapping[str, Any], names: Sequence[str]) -> Mapping[str, Any]:
    for name in names:
        section = meta.get(name)
        if isinstance(section, Mapping):
            return section
    return {}


def meta_overrides(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Campos da meta da campanha, a partir de ``{mode, kill_switch, config, …}``."""
    if not isinstance(meta, Mapping):
        return {}
    known = {field.name for field in fields(LoopConfig)}
    out: dict[str, Any] = {}

    control = meta.get("control")
    if isinstance(control, Mapping):
        for key in ("mode", "kill_switch"):
            if control.get(key) is not None:
                out[key] = control[key]

    sections: list[Mapping[str, Any]] = []
    if any(meta.get(name) is not None for name in META_SECTIONS):
        sections.append(_first_section(meta, META_SECTIONS))
    sections.append(meta)
    for section in sections:
        for key, value in section.items():
            if key in known and value is not None:
                out[key] = value
    return out


def load_config_with_issues(
    meta: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    base: LoopConfig | None = None,
) -> tuple[LoopConfig, list[str]]:
    """Config final + lista de problemas encontrados (chaves inválidas ignoradas)."""
    config = base or LoopConfig()
    issues: list[str] = []
    for origin, values in (("env", env_overrides(env)), ("meta", meta_overrides(meta))):
        cleaned: dict[str, Any] = {}
        for key, value in values.items():
            try:
                cleaned[key] = _coerce(key, value)
            except (TypeError, ValueError) as exc:
                issues.append(f"ignored {origin} setting {key!r}: {exc}")
        if cleaned:
            try:
                config = replace(config, **cleaned)
            except (TypeError, ValueError) as exc:
                issues.append(f"ignored {origin} settings {sorted(cleaned)}: {exc}")
    # Validação final explícita (o dataclass já valida, isto dá mensagem legível).
    mode = config.mode if config.mode in MODES else LoopConfig().mode
    if mode != config.mode:
        issues.append(f"ignored unknown mode {config.mode!r}")
        config = replace(config, mode=mode)
    return config, issues


def load_config(
    meta: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    base: LoopConfig | None = None,
) -> LoopConfig:
    """Config final do loop (``defaults < env < meta``); problemas são silenciosos."""
    config, _issues = load_config_with_issues(meta=meta, env=env, base=base)
    return config


def control_from_any(value: Any, fallback: LoopConfig | None = None) -> ControlState:
    """Modo + kill-switch vivos (ver :meth:`ControlState.from_any`)."""
    base = fallback or LoopConfig()
    return ControlState.from_any(value, ControlState(mode=base.mode, kill_switch=base.kill_switch))


def merge_meta_sources(*sources: Mapping[str, Any] | None) -> dict[str, Any]:
    """Junta várias metas (a última ganha) sem rebentar com ``None``/tipos estranhos."""
    out: MutableMapping[str, Any] = {}
    for source in sources:
        if isinstance(source, Mapping):
            out.update(source)
    return dict(out)


# ------------------------------------------- watchdog global de sessão (Passo 18)

#: ``SOD_WATCHDOG_<NOME>`` → campo de :class:`~.session_watchdog.WatchdogConfig`.
WATCHDOG_ENV_KEYS: dict[str, str] = {
    "WATCHDOG_WINDOW_MIN": "window_min",
    "WATCHDOG_GRACE_MIN": "grace_min",
    "WATCHDOG_STAGE_STEP_MIN": "stage_step_min",
    "WATCHDOG_STAGES": "stages",
    "WATCHDOG_ENABLED": "enabled",
}

_WATCHDOG_META_KEY = "watchdog"


def watchdog_env_overrides(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Overrides do watchdog vindos de ``SOD_WATCHDOG_*`` (``{}`` quando nenhuma).

    Só junta o que existe — a coerção de tipos é de
    :meth:`~.session_watchdog.WatchdogConfig.from_settings`.
    """
    source = os.environ if env is None else env
    out: dict[str, Any] = {}
    for suffix, name in WATCHDOG_ENV_KEYS.items():
        raw = source.get(ENV_PREFIX + suffix)
        if raw is not None:
            out[name] = raw
    return out


def watchdog_meta_overrides(meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Overrides do watchdog vindos da meta da campanha.

    Aceita uma secção ``"watchdog"`` (no topo ou dentro de ``config``/``loop``)
    **ou** chaves achatadas ``watchdog_*`` — a configuração é por campanha.
    """
    if not isinstance(meta, Mapping):
        return {}
    out: dict[str, Any] = {}
    sections: list[Mapping[str, Any]] = []
    if any(meta.get(name) is not None for name in META_SECTIONS):
        sections.append(_first_section(meta, META_SECTIONS))
    sections.append(meta)
    for section in sections:
        block = section.get(_WATCHDOG_META_KEY)
        if isinstance(block, Mapping):
            for key, value in block.items():
                if key in WATCHDOG_ENV_KEYS.values() and value is not None:
                    out[str(key)] = value
        for key, value in section.items():
            if isinstance(key, str) and key.startswith("watchdog_") and value is not None:
                name = key[len("watchdog_"):]
                if name in WATCHDOG_ENV_KEYS.values():
                    out[name] = value
    return out


def watchdog_settings(
    meta: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Overrides do watchdog global de sessão (``defaults < env < meta``).

    Devolve um mapping pronto para
    :meth:`~.session_watchdog.WatchdogConfig.from_settings` — o módulo do
    watchdog continua sem dependências de I/O, só recebe o resultado.
    """
    merged = dict(watchdog_env_overrides(env))
    merged.update(watchdog_meta_overrides(meta))
    return merged
