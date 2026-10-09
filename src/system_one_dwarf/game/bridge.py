"""Cliente da ponte DFHack: ``snapshot()`` → :class:`~.models.GameState` tipado.

Duas rotas para o mesmo contrato JSON:

* ``backend="subprocess"`` (padrão) — corre ``python3 dfhack/df_bridge.py snapshot --json``
  e lê o stdout; é a rota "oficial" (respeita ``--df-path``/``--port``/``--via``).
* ``backend="tcp"`` — importa o ``df_bridge.py`` no próprio processo e chama
  :func:`collect_snapshot` diretamente (fala RPC com o DFHack sem spawnar processo).

Ambas devolvem o mesmo dicionário; nada aqui depende do jogo estar aberto para importar.
Só stdlib (o ``dfhack/df_bridge.py`` é stdlib-only por contrato).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .errors import BridgeError, GameNotRunning
from .models import GameState

__all__ = ["DEFAULT_BRIDGE_PATH", "GameStateClient", "REPO_ROOT", "import_bridge_module"]

# src/system_one_dwarf/game/bridge.py → raiz do repositório
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BRIDGE_PATH = REPO_ROOT / "dfhack" / "df_bridge.py"

# códigos de saída da ponte (espelham dfhack/df_bridge.py)
EXIT_OK, EXIT_NO_DF, EXIT_NO_GAMELOG, EXIT_UNREACHABLE, EXIT_CMD_FAILED = 0, 10, 11, 12, 13
GAME_DOWN_EXITS = (EXIT_NO_DF, EXIT_UNREACHABLE)

Runner = Callable[[Sequence[str]], Any]


def import_bridge_module(bridge_path: Optional[Path] = None):
    """Importa ``dfhack/df_bridge.py`` como módulo (para a rota TCP, sem subprocesso)."""
    path = Path(bridge_path or DEFAULT_BRIDGE_PATH)
    if not path.is_file():
        raise BridgeError("bridge not found at %s" % path)
    name = "system_one_dwarf._df_bridge"
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None) == str(path):
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BridgeError("cannot load the bridge module from %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - falha de instalação, não de jogo
        sys.modules.pop(name, None)
        raise BridgeError("bridge at %s failed to import: %s" % (path, exc)) from exc
    return module


class GameStateClient:
    """Lê o snapshot do jogo (adventure mode) através do ``df_bridge.py``.

    Parâmetros
    ----------
    bridge_path: caminho do ``df_bridge.py`` (padrão: o do repositório).
    python: interpretador usado no backend ``subprocess`` (padrão: ``sys.executable``).
    df_path / port / via: repassados como ``--df-path`` / ``--port`` / ``--via``.
    radius: raio da varredura de unidades do snapshot (tiles).
    events: quantos eventos do ``gamelog.txt`` incluir.
    timeout: segundos de espera do subprocesso.
    backend: ``"subprocess"`` (padrão) ou ``"tcp"``.
    runner: injeção para testes — ``callable(cmd) -> objeto com returncode/stdout/stderr``.
    bridge_module: injeção para testes — módulo no lugar do ``df_bridge.py`` importado.
    env: variáveis de ambiente extra para o subprocesso.
    """

    def __init__(self, bridge_path: Optional[Any] = None, python: Optional[str] = None,
                 df_path: Optional[str] = None, port: Optional[int] = None,
                 via: str = "auto", radius: int = 25, events: int = 20,
                 timeout: float = 30.0, backend: str = "subprocess",
                 runner: Optional[Runner] = None, bridge_module: Optional[Any] = None,
                 env: Optional[Mapping[str, str]] = None) -> None:
        if backend not in ("subprocess", "tcp"):
            raise ValueError('backend must be "subprocess" or "tcp", got %r' % (backend,))
        self.bridge_path = Path(bridge_path).expanduser() if bridge_path else DEFAULT_BRIDGE_PATH
        self.python = python or sys.executable or "python3"
        self.df_path = df_path or os.environ.get("DF_PATH") or None
        self.port = port
        self.via = via
        self.radius = radius
        self.events = events
        self.timeout = timeout
        self.backend = backend
        self.runner = runner
        self.bridge_module = bridge_module
        self.env = dict(env) if env else {}

    # ---------- API pública ----------
    def snapshot(self) -> GameState:
        """Snapshot tipado do jogo. Levanta ``GameNotRunning``/``BridgeError``."""
        return GameState.from_dict(self.snapshot_payload())

    def snapshot_payload(self) -> Dict[str, Any]:
        """O dicionário cru do contrato (útil para logs/inspeção antes do parse)."""
        payload = self._payload_tcp() if self.backend == "tcp" else self._payload_subprocess()
        if not isinstance(payload, dict):
            raise BridgeError("bridge returned %s instead of a JSON object"
                              % type(payload).__name__, command=self.command())
        version = payload.get("schema_version")
        if version is not None and version != GameState.SCHEMA_VERSION:
            raise BridgeError("unsupported snapshot schema_version %r (expected %d)"
                              % (version, GameState.SCHEMA_VERSION), command=self.command())
        return payload

    def command(self) -> List[str]:
        """Linha de comando do snapshot (backend ``subprocess``)."""
        cmd = [self.python, str(self.bridge_path), "snapshot", "--json",
               "--radius", str(self.radius), "--events", str(self.events)]
        if self.df_path:
            cmd += ["--df-path", str(self.df_path)]
        if self.port:
            cmd += ["--port", str(self.port)]
        if self.via and self.via != "auto":
            cmd += ["--via", str(self.via)]
        return cmd

    # ---------- backend subprocess ----------
    def _payload_subprocess(self) -> Dict[str, Any]:
        cmd = self.command()
        runner = self.runner or self._run_subprocess
        try:
            proc = runner(cmd)
        except FileNotFoundError as exc:
            raise GameNotRunning("cannot execute the bridge (%s): %s" % (cmd[0], exc),
                                 exit_code=EXIT_UNREACHABLE, command=cmd) from exc
        except subprocess.TimeoutExpired as exc:
            raise GameNotRunning("bridge timed out after %.1fs" % self.timeout,
                                 exit_code=EXIT_UNREACHABLE, command=cmd) from exc
        except OSError as exc:
            raise BridgeError("failed to run the bridge: %s" % exc, command=cmd) from exc

        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        code = getattr(proc, "returncode", None)
        if code != EXIT_OK:
            raise self._error_from_exit(code, stdout, stderr, cmd)
        try:
            return json.loads(stdout)
        except ValueError as exc:
            raise BridgeError("bridge returned invalid JSON: %s" % exc, exit_code=code,
                              stderr=str(stderr)[:500], command=cmd) from exc

    def _run_subprocess(self, cmd: Sequence[str]):
        env = dict(os.environ)
        env.update(self.env)
        return subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=self.timeout, env=env,
                              cwd=str(REPO_ROOT))

    def _error_from_exit(self, code: Optional[int], stdout: str, stderr: str,
                         cmd: Sequence[str]) -> BridgeError:
        """Traduz a saída de erro da ponte (``--json``: {ok,error,hint,exit_code})."""
        message, hint, exit_code = None, None, code
        try:
            body = json.loads(stdout)
            if isinstance(body, dict) and body.get("error"):
                message = str(body["error"])
                hint = body.get("hint")
                exit_code = body.get("exit_code", code)
        except ValueError:
            pass
        if message is None:
            message = (stderr or "").strip() or "bridge exited with code %s" % code
        cls = GameNotRunning if exit_code in GAME_DOWN_EXITS else BridgeError
        return cls(message, exit_code=exit_code, hint=hint, command=cmd,
                   stderr=str(stderr)[:500])

    # ---------- backend tcp (sem subprocesso) ----------
    def _bridge_args(self) -> SimpleNamespace:
        via = self.via
        if via == "auto":
            via = "tcp"                      # rota TCP: fala RPC direto, sem dfhack-run
        return SimpleNamespace(df_path=self.df_path, port=self.port, via=via, radius=self.radius,
                               events=self.events, json=True, lines=20, journal=False)

    def _payload_tcp(self) -> Dict[str, Any]:
        bridge = self.bridge_module or import_bridge_module(self.bridge_path)
        df, _how = bridge.find_df_path(self.df_path)
        if df is None and self._bridge_args().via != "tcp":
            raise GameNotRunning("Dwarf Fortress not found (set df_path or $DF_PATH)",
                                 exit_code=EXIT_NO_DF)
        try:
            return bridge.collect_snapshot(self._bridge_args(), df)
        except bridge.BridgeError as exc:
            code = getattr(exc, "exit_code", None)
            cls = GameNotRunning if code in GAME_DOWN_EXITS else BridgeError
            raise cls(str(exc), exit_code=code, hint=getattr(exc, "hint", None),
                      command=["tcp", str(self.bridge_path)]) from exc


def snapshot(client: Optional[GameStateClient] = None) -> GameState:
    """Atalho: ``snapshot()`` com um cliente padrão."""
    return (client or GameStateClient()).snapshot()
