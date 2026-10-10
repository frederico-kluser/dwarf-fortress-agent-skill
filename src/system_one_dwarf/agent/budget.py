"""``BudgetGuard`` — teto de orçamento OPT-IN por campanha (ARQUITETURA §9 · Custo).

Porquê
------
Até aqui o custo era só *visibilidade* (``gastos.sqlite``, ``GET .../spend``): uma
sessão presa a gastar tokens do DeepSeek não tinha travão.  O :class:`BudgetGuard`
é o travão — **desligado por omissão** (``limit_usd=None``/``0`` mantém o
comportamento documentado: "sem teto de orçamento").

Contrato (lógica pura, sem I/O)
-------------------------------
``check(total_usd) -> BudgetStatus`` com três estados:

=============  =====================================  ===========================
``state``      quando                                 o que o loop faz
=============  =====================================  ===========================
``ok``         ``total < limit × warn_fraction``      nada (ou guard desligado)
``warn``       ``limit × warn_fraction ≤ total < limit``  publica o evento ``budget``
``exceeded``   ``total ≥ limit``                      publica ``budget`` e **retém**
                                                      as ações (como o checkpoint)
=============  =====================================  ===========================

* o dinheiro é arredondado a **6 casas** (contrato de ``campaign/spend.py``) e as
  comparações fazem-se em micro-USD *inteiros* — nenhum ``0.8 × 0.3`` ou
  ``0.1 + 0.2`` cai do lado errado do limiar por erro de vírgula flutuante;
* ``poll()`` guarda o último estado e marca ``changed`` — o loop publica o evento
  só nas transições (não a cada passo);
* total ``None``/``NaN``/``±inf``/negativo ⇒ 0 (um custo desconhecido ou
  corrompido nunca trava a sessão — e o payload do evento fica JSON válido).

O que *não* faz: ler o ledger, publicar eventos ou parar o loop — isso é do
chamador (``AgentLoop``), que passa ``spend.total_usd()`` e age sobre o estado.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "BUDGET_EXCEEDED",
    "BUDGET_OK",
    "BUDGET_STATES",
    "BUDGET_WARN",
    "DEFAULT_WARN_FRACTION",
    "ENV_LIMIT",
    "ENV_WARN_FRACTION",
    "BudgetGuard",
    "BudgetStatus",
    "money",
    "parse_limit",
]

BUDGET_OK = "ok"
BUDGET_WARN = "warn"
BUDGET_EXCEEDED = "exceeded"
BUDGET_STATES: tuple[str, str, str] = (BUDGET_OK, BUDGET_WARN, BUDGET_EXCEEDED)

#: A partir de que fração do teto o estado passa a ``warn``.
DEFAULT_WARN_FRACTION = 0.8
#: Casas decimais do dinheiro (contrato de ``campaign/spend.py``).
MONEY_DECIMALS = 6
_MICRO = 10 ** MONEY_DECIMALS

#: Chaves de ambiente sugeridas (campos ``budget_limit_usd`` / ``budget_warn_fraction``
#: do ``LoopConfig``; o carregamento por env é do ``agent/config.py``).
ENV_LIMIT = "SOD_BUDGET_USD"
ENV_WARN_FRACTION = "SOD_BUDGET_WARN_FRACTION"

#: Textos de "desligado" aceites em env/meta (``""`` e ``0`` também desligam).
_OFF_WORDS = frozenset({"", "off", "none", "null", "no", "false", "disabled", "0", "0.0"})


def money(value: Any) -> float:
    """Dinheiro no formato do contrato (``float`` com 6 casas); lixo/``None``/``NaN`` → 0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(number, MONEY_DECIMALS)


def _micro(value: float) -> int:
    return int(round(value * _MICRO))


def _usd(value: float) -> str:
    """``8`` → ``8.00``; ``0.000133`` → ``0.000133`` (até 6 casas, mínimo 2)."""
    whole, _, fraction = f"{value:.{MONEY_DECIMALS}f}".partition(".")
    return f"{whole}.{fraction.rstrip('0').ljust(2, '0')}"


def parse_limit(value: Any) -> float | None:
    """Teto em USD a partir de env/meta: ``None`` = desligado.

    Aceita números e textos numéricos (``"5"``, ``"5.50"``, ``"2,5"``);
    ``""``/``0``/``off``/negativo/``NaN``/``inf``/lixo desligam o guard em vez de
    rebentar: um ``meta.json`` estragado não pode impedir o loop de arrancar.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _OFF_WORDS:
            return None
        value = text.replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    limit = money(number)
    return limit if limit > 0 else None


def _fraction(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number <= 0:
        return default
    return min(1.0, number)


@dataclass(frozen=True)
class BudgetStatus:
    """Resultado de :meth:`BudgetGuard.check`/:meth:`~BudgetGuard.poll`."""

    #: ``ok`` | ``warn`` | ``exceeded``.
    state: str
    #: ``total / limit`` (6 casas; pode passar de 1.0); ``0.0`` com o guard desligado.
    fraction: float
    total_usd: float
    #: ``None`` com o guard desligado.
    limit_usd: float | None
    remaining_usd: float | None
    #: Texto para o humano (PT).
    message_pt: str
    enabled: bool = True
    #: ``poll()``: o estado mudou desde a chamada anterior (publicar o evento).
    changed: bool = False

    @property
    def level(self) -> str:
        """Alias de :attr:`state` (``ok``/``warn``/``exceeded``)."""
        return self.state

    @property
    def ok(self) -> bool:
        return self.state == BUDGET_OK

    @property
    def warn(self) -> bool:
        return self.state == BUDGET_WARN

    @property
    def exceeded(self) -> bool:
        return self.state == BUDGET_EXCEEDED

    @property
    def holds_actions(self) -> bool:
        """``True`` quando o loop deve **reter** as ações (só em ``exceeded``)."""
        return self.exceeded

    def as_dict(self) -> dict[str, Any]:
        """Payload do evento ``budget`` (aditivo; dinheiro a 6 casas)."""
        return {
            "state": self.state,
            "enabled": self.enabled,
            "fraction": self.fraction,
            "total_usd": self.total_usd,
            "limit_usd": self.limit_usd,
            "remaining_usd": self.remaining_usd,
            "message_pt": self.message_pt,
        }


class BudgetGuard:
    """Teto de orçamento com aviso (opt-in; pura, thread-safe).

    Args:
        limit_usd: teto em USD; ``None``/``0``/negativo/inválido = **desligado**
            (o comportamento de sempre: só visibilidade).
        warn_fraction: fração do teto a partir da qual o estado é ``warn``
            (``0 < f ≤ 1``; ``1`` = sem aviso, só ``exceeded``).  Inválido ⇒
            :data:`DEFAULT_WARN_FRACTION`.
    """

    def __init__(
        self, limit_usd: float | None = None, warn_fraction: float = DEFAULT_WARN_FRACTION
    ) -> None:
        self.limit_usd: float | None = parse_limit(limit_usd)
        self.warn_fraction = _fraction(warn_fraction, DEFAULT_WARN_FRACTION)
        self._lock = threading.Lock()
        self._last_state: str | None = None

    @classmethod
    def from_settings(cls, settings: Any = None) -> "BudgetGuard":
        """Constrói a partir de um mapping/objeto com ``budget_limit_usd`` /
        ``budget_warn_fraction`` (um ``LoopConfig`` ou ``meta.json``).  Campos em
        falta ⇒ guard desligado."""

        def read(name: str) -> Any:
            if isinstance(settings, Mapping):
                return settings.get(name)
            return getattr(settings, name, None)

        warn = read("budget_warn_fraction")
        return cls(read("budget_limit_usd"), DEFAULT_WARN_FRACTION if warn is None else warn)

    @property
    def enabled(self) -> bool:
        return self.limit_usd is not None

    # ------------------------------------------------------------------ check

    def check(self, total_usd: Any = 0.0) -> BudgetStatus:
        """Estado do gasto ``total_usd`` face ao teto (puro: não guarda nada)."""
        total = max(0.0, money(total_usd))
        limit = self.limit_usd
        if limit is None:
            return BudgetStatus(
                state=BUDGET_OK, fraction=0.0, total_usd=total, limit_usd=None,
                remaining_usd=None, enabled=False,
                message_pt=f"Sem teto de orçamento (só visibilidade): US$ {_usd(total)} gastos.",
            )
        limit_u, total_u = _micro(limit), _micro(total)
        warn_u = _micro(limit * self.warn_fraction)
        if total_u >= limit_u:
            state = BUDGET_EXCEEDED
        elif total_u >= warn_u:
            state = BUDGET_WARN
        else:
            state = BUDGET_OK
        # fração por **baixo** (6 casas): só chega a 1.0 quando esgotou de facto.
        fraction = (total_u * _MICRO // limit_u) / _MICRO
        remaining = money(max(0.0, limit - total))
        percent = f"{math.floor(fraction * 1000) / 10:g}%"
        shown = f"US$ {_usd(total)} de US$ {_usd(limit)} ({percent})"
        if state == BUDGET_EXCEEDED:
            message = (
                f"Orçamento esgotado: {shown}. As ações ficam retidas até o humano "
                "aumentar ou remover o teto."
            )
        elif state == BUDGET_WARN:
            message = f"Atenção: orçamento a {percent} — {shown}; restam US$ {_usd(remaining)}."
        else:
            message = f"Orçamento dentro do teto: {shown}."
        return BudgetStatus(
            state=state, fraction=fraction, total_usd=total, limit_usd=limit,
            remaining_usd=remaining, message_pt=message, enabled=True,
        )

    def poll(self, total_usd: Any = 0.0) -> BudgetStatus:
        """:meth:`check` + deteção de transição (``changed``) para publicar o evento.

        A 1.ª chamada só marca ``changed`` se o estado já não for ``ok`` — um
        arranque saudável não precisa de evento.  Desligado nunca muda.
        """
        status = self.check(total_usd)
        with self._lock:
            previous, self._last_state = self._last_state, status.state
        if not status.enabled:
            return status
        changed = (previous != status.state) if previous is not None else status.state != BUDGET_OK
        if changed:
            return BudgetStatus(
                state=status.state, fraction=status.fraction, total_usd=status.total_usd,
                limit_usd=status.limit_usd, remaining_usd=status.remaining_usd,
                message_pt=status.message_pt, enabled=True, changed=True,
            )
        return status

    def reset(self) -> None:
        """Esquece o último estado (o humano mexeu no teto: volta a anunciar)."""
        with self._lock:
            self._last_state = None

    def __repr__(self) -> str:
        return (f"BudgetGuard(limit_usd={self.limit_usd!r}, "
                f"warn_fraction={self.warn_fraction!r})")
