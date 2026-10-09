"""Erros tipados da camada de GAME STATE.

Hierarquia::

    GameStateError
    └── BridgeError          # a ponte falhou (exit != 0, JSON inválido, contrato quebrado)
        └── GameNotRunning   # o jogo/DFHack não está acessível (jogo fechado, DF ausente)

Quem chama pode apanhar só :class:`GameNotRunning` para o caso "o jogo não está aberto"
e :class:`BridgeError` para tudo o resto que venha da ponte.
"""
from __future__ import annotations

from typing import Optional, Sequence

__all__ = ["GameStateError", "BridgeError", "GameNotRunning"]


class GameStateError(Exception):
    """Erro base da camada de estado do jogo."""


class BridgeError(GameStateError):
    """A ponte ``df_bridge.py`` falhou ou devolveu algo fora do contrato.

    Atributos
    ---------
    exit_code: código de saída da ponte (0/10/11/12/13) quando conhecido.
    hint: dica devolvida pela ponte em ``--json`` (``{"ok": false, "hint": ...}``).
    command: comando executado (para diagnóstico/log).
    stderr: stderr da ponte (truncado).
    """

    def __init__(self, message: str, *, exit_code: Optional[int] = None,
                 hint: Optional[str] = None, command: Sequence[str] = (),
                 stderr: Optional[str] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.hint = hint
        self.command = tuple(command or ())
        self.stderr = stderr

    def __str__(self) -> str:  # pragma: no cover - trivial
        text = super().__str__()
        if self.exit_code is not None:
            text += " (exit_code=%s)" % self.exit_code
        if self.hint:
            text += " — hint: %s" % self.hint
        return text


class GameNotRunning(BridgeError):
    """O Dwarf Fortress / DFHack não está acessível (jogo fechado, DFHack ausente)."""
