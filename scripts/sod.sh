#!/usr/bin/env bash
# =============================================================================
# sod — comando global do System One Dwarf
#
#   ./scripts/sod.sh [up]        arranca JOGO + BACKEND + UI e fica em primeiro
#                                plano; Ctrl-C / SIGTERM (ou `sod stop`) pára
#                                jogo+backend+UI que ESTE comando arrancou
#   ./scripts/sod.sh stop        pára tudo o que `sod up` iniciou
#   ./scripts/sod.sh status      estado por componente (jogo/backend/frontend)
#   ./scripts/sod.sh logs [-f]   segue os logs do runtime
#   ./scripts/sod.sh install-link  liga `sod` em ~/.local/bin
#
# ⚠️  SOM DESATIVADO POR PREDEFINIÇÃO NO LINUX (regra do utilizador): o jogo é
#     lançado com SDL_AUDIODRIVER=dummy. Usa `--sound` para o som (e restaura
#     <df>/data/init/init_default.txt a partir do .bak-nosound se necessário).
#
# Segurança: processos que NÃO foram iniciados por este comando nunca são
# mortos — só se avisa. Os PID dos processos nossos ficam em .runtime/ (ou
# $SOD_RUNTIME_DIR) com um marcador de posse (<nome>.owned); stop/status
# distinguem "nosso" de "externo".
# =============================================================================
set -u
set -o pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
RUNTIME_DIR="${SOD_RUNTIME_DIR:-$REPO_ROOT/.runtime}"
DEFAULT_DF_PATH="${SOD_DF_PATH:-$HOME/Games/dwarf-fortress-53.16}"

CMD=""
PORT=8000
WEB_PORT=5173
DF_PATH="$DEFAULT_DF_PATH"
WITH_GAME=1
WITH_WEB=1
WITH_BACKEND=1
WITH_SOUND=0
FOLLOW=0

STARTED_ANY=0   # algo foi iniciado por NÓS nesta execução?
CLEANED=0       # cleanup já correu?

# ------------------------------------------------------------------- output
say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
err()  { printf '❌ %s\n' "$*" >&2; }

usage() {
  cat <<EOF
sod — comando global do System One Dwarf (jogo + backend + UI web)

USO:
  sod [up] [opções]      arranca tudo e fica em primeiro plano (Ctrl-C para parar)
  sod stop               pára tudo o que 'sod up' iniciou (e SÓ isso)
  sod status             estado de cada componente
  sod logs [--follow]    últimos 200 linhas dos logs (--follow segue)
  sod install-link       cria a ligação ~/.local/bin/sod

OPÇÕES:
  --no-game              não arranca o Dwarf Fortress
  --no-backend           não arranca o backend FastAPI
  --no-web               não arranca a UI web (Vite)
  --port <n>             porta do backend (predefinição: 8000)
  --web-port <n>         porta da UI web (predefinição: 5173)
  --df-path <dir>        diretório do Dwarf Fortress
                         (predefinição: ${DEFAULT_DF_PATH})
  --sound                LIGA o som (omite SDL_AUDIODRIVER=dummy)
  -h, --help             esta ajuda

SISTEMAS SUPORTADOS (matriz verificada):
  Linux x86_64  ✅ SUPORTADO/VERIFICADO (DF 53.16 classic + DFHack 53.16-r2)
  macOS         ❌ NÃO SUPORTADO (não há par DF/DFHack 53.x para macOS)
  Windows       ⚠️ NÃO TESTADO (o par existe; o instalador recusa)
  WSL2          ⚠️ EXPERIMENTAL (pode funcionar como Linux sem áudio)

⚠️  SOM DESLIGADO POR OMISSÃO NO LINUX (REGRA DO UTILIZADOR): o jogo arranca
    sempre sem som (SDL_AUDIODRIVER=dummy; --sound é a única exceção) e o
    instalador põe [SOUND:NO] + volumes 0 em data/init/init_default.txt.

PARAGEM: Ctrl-C / SIGTERM (ou 'sod stop') → pára jogo+backend+UI que ESTE
    comando arrancou — processos que já corriam antes nunca são mortos.

A chave OpenRouter é lida sem nunca ser mostrada (env SOD_OPENROUTER_KEY /
OPENROUTER_API_KEY ou ~/.dsh/.credentials.yaml / ~/.secrets) e só é passada
ao processo do backend; em caso algum é impressa.

EXEMPLOS:
  sod                          # o mesmo que 'sod up'
  sod up --no-game             # só backend + UI
  sod up --port 8010 --web-port 5183
  sod logs --follow
EOF
}

# ------------------------------------------------------------- utilitários
port_open() {
  local port="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket, sys
p = int(sys.argv[1])
for fam, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(0.4)
        s.connect((addr, p))
        s.close()
        sys.exit(0)
    except OSError:
        pass
sys.exit(1)
PY
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

alive() { [ -n "${1:-}" ] && [ "$1" -gt 0 ] 2>/dev/null && kill -0 "$1" 2>/dev/null; }

rt_pid() {
  local f="$RUNTIME_DIR/$1.pid" p
  [ -f "$f" ] || return 1
  p="$(cat "$f" 2>/dev/null)"
  [ -n "$p" ] || return 1
  printf '%s\n' "$p"
}

svc_alive() { local p; p="$(rt_pid "$1" 2>/dev/null)" || return 1; alive "$p"; }

pid_of_port() { # melhor esforço: PID a escutar na porta (ss)
  local port="$1"
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnpH "sport = :$port" 2>/dev/null \
    | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n1
}

game_running() { pgrep -x dwarfort >/dev/null 2>&1; }

health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1
  else
    port_open "$PORT"
  fi
}

# ------------------------------------------------------- chave (nunca mostrar)
KEY_VALUE=""
KEY_SOURCE=""
clean_value() {
  local v="$1"
  v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
  case "$v" in
    \"*\") v="${v#\"}"; v="${v%\"}" ;;
    \'*\') v="${v#\'}"; v="${v%\'}" ;;
  esac
  v="${v%% \#*}"
  printf '%s' "$v"
}
detect_key() {
  KEY_VALUE=""; KEY_SOURCE=""
  local name v rel f line value
  for name in SOD_OPENROUTER_KEY OPENROUTER_API_KEY; do
    v="$(clean_value "${!name-}")"
    if [ -n "$v" ]; then
      KEY_VALUE="$v"; KEY_SOURCE="env \$$name"; return 0
    fi
  done
  for rel in ".dsh/.credentials.yaml" ".secrets"; do
    f="$HOME/$rel"
    [ -f "$f" ] || continue
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?[A-Za-z0-9_]*OPENROUTER[A-Za-z0-9_]*[[:space:]]*[:=]' "$f" 2>/dev/null | head -n1)"
    [ -n "$line" ] || continue
    value="$(printf '%s\n' "$line" | sed -E 's/^[^:=]*[:=][[:space:]]*//')"
    value="$(clean_value "$value")"
    if [ -n "$value" ] && [ "${value#\#}" = "$value" ]; then
      KEY_VALUE="$value"; KEY_SOURCE="~/$rel"; return 0
    fi
  done
  return 1
}

# --------------------------------------------------------------- processos
start_service() { # $1 nome · $2 workdir · $3 log · resto: comando
  local name="$1" cwd="$2" log="$3"; shift 3
  mkdir -p "$RUNTIME_DIR"
  : >"$log"
  # setsid: sessão/grupo de processos próprios → 'stop' mata a árvore inteira
  # (npm → vite). O processo filho escreve o PRÓPRIO PID no pidfile (robusto
  # mesmo que o setsid faça fork).
  setsid bash -c 'cd "$1" || exit 1; echo $$ > "$2"; shift 2; exec "$@"' \
    sod-spawn "$cwd" "$RUNTIME_DIR/$name.pid" "$@" >>"$log" 2>&1 &
  sleep 0.4
  local p
  p="$(rt_pid "$name" 2>/dev/null)" || p=""
  if [ -z "$p" ] || ! alive "$p"; then
    err "não foi possível arrancar '$name' (vê $log)"
    return 1
  fi
  : >"$RUNTIME_DIR/$name.owned"
  STARTED_ANY=1
  ok "$name arrancado (pid $p) → log: $log"
  return 0
}

stop_tree() { # $1 nome · $2 pid (líder de sessão/grupo criado por start_service)
  local name="$1" pid="$2" i
  # O jogo ignora SIGTERM e o líder pode morrer primeiro: a sobrevivência é
  # medida pelo GRUPO (pgrep -g), não pelo líder, e o escalonamento é KILL.
  if ! alive "$pid" && ! pgrep -g "$pid" >/dev/null 2>&1; then
    rm -f "$RUNTIME_DIR/$name.pid"
    return 0
  fi
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for i in $(seq 1 10); do
    pgrep -g "$pid" >/dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -g "$pid" >/dev/null 2>&1; then
    warn "$name (grupo $pid) não respondeu a TERM — a forçar (KILL)"
    kill -KILL -- "-$pid" 2>/dev/null
    sleep 0.5
  fi
  rm -f "$RUNTIME_DIR/$name.pid"
  if pgrep -g "$pid" >/dev/null 2>&1; then
    err "$name: sobreviveu ao KILL (grupo $pid; pids $(pgrep -g "$pid" | tr '\n' ' '))"
    return 1
  fi
  ok "$name parado (grupo $pid)"
  return 0
}

stragglers() { # $1 nome · $2 padrão pkill -f (com classe de carateres anti-self-match)
  local name="$1" pattern="$2" hits
  hits="$(pgrep -u "$USER" -f "$pattern" 2>/dev/null | tr '\n' ' ')"
  [ -n "${hits// /}" ] || return 0
  warn "$name: sobras detetadas (pid $hits) — a matar (padrão: $pattern)"
  pkill -u "$USER" -f "$pattern" 2>/dev/null
  sleep 0.5
}

stragglers_game() { # o binário do jogo gere o próprio argv; casa-se é pelo NOME
  local hits
  hits="$(pgrep -u "$USER" -x dwarfort 2>/dev/null | tr '\n' ' ')"
  [ -n "${hits// /}" ] || return 0
  warn "jogo: sobras detetadas (pid $hits) — a matar (nome: dwarfort)"
  pkill -u "$USER" -x dwarfort 2>/dev/null
  pkill -u "$USER" -f '[d]warfort' 2>/dev/null # fallback por argv (anti-self-match)
  sleep 0.5
}

stop_owned_all() {
  local name pid
  for name in frontend backend game; do
    pid="$(rt_pid "$name" 2>/dev/null)" || continue
    stop_tree "$name" "$pid"
  done
  # Fallback de padrão/nome (sobras/órfãos) SÓ para o que eramos donos; os
  # marcadores .owned só são removidos DEPOIS do relatório final.
  for name in frontend backend game; do
    [ -f "$RUNTIME_DIR/$name.owned" ] || continue
    case "$name" in
      frontend) stragglers "frontend" "$REPO_ROOT/web/node_modules/.*vi[t]e" ;;
      backend)  stragglers "backend"  "uvicorn system_one_dwarf[.]app[.]main" ;;
      game)     stragglers_game ;;
    esac
  done
}

report_leftovers() {
  if game_running; then
    if [ -f "$RUNTIME_DIR/game.owned" ]; then
      err "jogo (pid $(pgrep -x dwarfort | tr '\n' ' ')) AINDA corre apesar do stop — repete 'sod stop'"
    else
      warn "jogo ainda em execução (pid $(pgrep -x dwarfort | tr '\n' ' ')) — NÃO iniciado por 'sod up' (deixado a correr)"
    fi
  fi
  if port_open "$PORT"; then
    warn "porta $PORT ainda ocupada (pid $(pid_of_port "$PORT" || echo '?')) — processo externo, não tocado"
  fi
  if port_open "$WEB_PORT"; then
    warn "porta $WEB_PORT ainda ocupada (pid $(pid_of_port "$WEB_PORT" || echo '?')) — processo externo, não tocado"
  fi
}

forget_owned() {
  rm -f "$RUNTIME_DIR/game.owned" "$RUNTIME_DIR/backend.owned" "$RUNTIME_DIR/frontend.owned"
  rm -f "$RUNTIME_DIR/game.pid" "$RUNTIME_DIR/backend.pid" "$RUNTIME_DIR/frontend.pid"
}

confirm_ports() {
  local p
  for p in "$PORT" "$WEB_PORT"; do
    if port_open "$p"; then
      warn "porta $p ainda a aceitar ligações (pid $(pid_of_port "$p" || echo '?'))"
    else
      ok "porta $p livre"
    fi
  done
  if game_running; then
    warn "jogo ainda em execução (não é do 'sod up' — mantido)"
  else
    ok "jogo não está em execução"
  fi
}

# ------------------------------------------------------------------- up
cmd_up() {
  local backend_started=0 web_started=0 deadline key_txt

  mkdir -p "$RUNTIME_DIR"
  rm -f "$RUNTIME_DIR/.stopping"   # marcador de um 'sod stop' antigo

  say "=== System One Dwarf · sod up ==="
  say "repo:      $REPO_ROOT"
  say "runtime:   $RUNTIME_DIR"
  say "df:        $DF_PATH"
  if [ "$WITH_SOUND" = 1 ]; then
    warn "SOM LIGADO (--sound): SDL_AUDIODRIVER não é dummy; se o init tiver [SOUND:NO], restaura o .bak-nosound"
  else
    say "som:       DESATIVADO (predefinição no Linux, regra do utilizador; --sound liga)"
  fi

  # ---- chave (valor nunca impresso)
  if detect_key; then
    key_txt="encontrada ($KEY_SOURCE)"
  else
    key_txt="ausente — define-a na UI (painel Controlo) ou em ~/.secrets"
  fi
  say "chave:     $key_txt"

  # ---- 1. jogo
  if [ "$WITH_GAME" = 1 ]; then
    if game_running; then
      warn "jogo já em execução (pid $(pgrep -x dwarfort | tr '\n' ' ')) — não iniciado por nós; a reutilizar e a deixar correr"
    elif [ ! -x "$DF_PATH/dfhack" ]; then
      err "não encontro o lançador: $DF_PATH/dfhack (usa --df-path ou corre ./install.sh)"
      say "a continuar sem o jogo ..."
    else
      game_env=(env SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1)
      [ "$WITH_SOUND" = 1 ] || game_env+=(SDL_AUDIODRIVER=dummy)
      start_service game "$DF_PATH" "$RUNTIME_DIR/game.log" \
        "${game_env[@]}" ./dfhack || true
    fi
  else
    say "— jogo: ignorado (--no-game)"
  fi

  # ---- 2. backend
  if [ "$WITH_BACKEND" = 1 ]; then
    if port_open "$PORT"; then
      warn "já há algo na porta $PORT (pid $(pid_of_port "$PORT" || echo '?')) — NÃO é nosso; não o tocamos e não arrancamos outro"
    else
      py="$REPO_ROOT/.venv/bin/python"
      [ -x "$py" ] || py="$(command -v python3 || true)"
      if [ -z "$py" ]; then
        err "sem python3 para o backend (instala o projeto: ./install.sh)"
      else
        benv=(env "DF_PATH=$DF_PATH" "PYTHONPATH=$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}")
        [ -n "$KEY_VALUE" ] && benv+=("SOD_OPENROUTER_KEY=$KEY_VALUE")
        if start_service backend "$REPO_ROOT" "$RUNTIME_DIR/backend.log" \
            "${benv[@]}" "$py" -m uvicorn system_one_dwarf.app.main:app \
            --host 127.0.0.1 --port "$PORT"; then
          backend_started=1
        fi
      fi
    fi
  else
    say "— backend: ignorado (--no-backend)"
  fi

  # ---- 3. frontend
  if [ "$WITH_WEB" = 1 ]; then
    if port_open "$WEB_PORT"; then
      warn "já há algo na porta $WEB_PORT (pid $(pid_of_port "$WEB_PORT" || echo '?')) — NÃO é nosso; não o tocamos e não arrancamos outro"
    elif [ ! -d "$REPO_ROOT/web/node_modules" ]; then
      err "web/node_modules não existe — corre './install.sh' (ou 'cd web && npm install') primeiro"
    else
      if start_service frontend "$REPO_ROOT/web" "$RUNTIME_DIR/frontend.log" \
          env "VITE_API_BASE=http://127.0.0.1:$PORT" \
          npm run dev -- --port "$WEB_PORT" --strictPort; then
        web_started=1
      fi
    fi
  else
    say "— frontend: ignorado (--no-web)"
  fi

  # ---- 4. espera de saúde (≤ 60 s) com progresso
  if [ "$WITH_BACKEND" = 1 ] && [ "$backend_started" = 1 ]; then
    say "-> a aguardar /api/health (≤ 60 s) ..."
    deadline=$((SECONDS + 60))
    while [ $SECONDS -lt $deadline ]; do
      if health_ok; then ok "backend saudável: http://127.0.0.1:$PORT/api/health"; break; fi
      if ! svc_alive backend; then
        err "backend morreu ao arrancar (vê $RUNTIME_DIR/backend.log)"
        cleanup 1
      fi
      [ $((SECONDS % 5)) -eq 0 ] && say "   ... ainda a arrancar (porta $PORT fechada)"
      sleep 1
    done
    health_ok || { err "backend não respondeu em 60 s (vê $RUNTIME_DIR/backend.log)"; cleanup 1; }
  fi
  if [ "$WITH_WEB" = 1 ] && [ "$web_started" = 1 ]; then
    say "-> a aguardar a UI na porta $WEB_PORT (≤ 60 s) ..."
    deadline=$((SECONDS + 60))
    while [ $SECONDS -lt $deadline ]; do
      if port_open "$WEB_PORT"; then ok "UI a servir: http://localhost:$WEB_PORT"; break; fi
      if ! svc_alive frontend; then
        err "frontend morreu ao arrancar (vê $RUNTIME_DIR/frontend.log)"
        cleanup 1
      fi
      [ $((SECONDS % 5)) -eq 0 ] && say "   ... ainda a arrancar (porta $WEB_PORT fechada)"
      sleep 1
    done
    port_open "$WEB_PORT" || { err "UI não abriu a porta $WEB_PORT em 60 s (vê $RUNTIME_DIR/frontend.log)"; cleanup 1; }
  fi

  # ---- 5. URLs + pista de campanha
  say ""
  say "=== pronto ==="
  say "UI web:      http://localhost:$WEB_PORT"
  say "API:         http://127.0.0.1:$PORT/api/health"
  say "Logs:        $RUNTIME_DIR/{game,backend,frontend}.log  (ou: sod logs --follow)"
  say ""
  say "Campanha:    na UI → painel Controlo → criar campanha → Definir chave"
  say "             (OpenRouter) → modo de controlo (autopilot/checkpoint)."
  say "             A chave também pode vir de env ou ~/.secrets (SOD_OPENROUTER_KEY)."
  [ "$WITH_SOUND" = 1 ] || say "Som:         DESATIVADO por predefinição (regra do utilizador)."
  say ""
  say "Em primeiro plano: Ctrl-C para TUDO o que 'sod up' iniciou (ou 'sod stop')."

  # ---- 6. bloquear em primeiro plano + paragem limpa em INT/TERM/EXIT
  trap 'cleanup 0' INT TERM
  trap 'cleanup 0' EXIT
  while :; do
    sleep 2
    if [ -f "$RUNTIME_DIR/.stopping" ]; then
      say ""
      say "'sod stop' detetado — supervisor a encerrar."
      cleanup 0
    fi
    for name in game backend frontend; do
      [ -f "$RUNTIME_DIR/$name.pid" ] || continue
      pid="$(rt_pid "$name" 2>/dev/null)" || continue
      if ! alive "$pid"; then
        err "'$name' (pid $pid) terminou inesperadamente"
        cleanup 1
      fi
    done
  done
}

cleanup() {
  local rc="${1:-0}"
  [ "$CLEANED" = 1 ] && exit "$rc"
  CLEANED=1
  trap - INT TERM EXIT
  if [ "$STARTED_ANY" = 1 ]; then
    say ""
    say "— a parar tudo o que 'sod up' iniciou —"
    stop_owned_all
    report_leftovers
    forget_owned
    say "— limpo —"
  fi
  exit "$rc"
}

# ----------------------------------------------------------------- stop
cmd_stop() {
  say "=== System One Dwarf · sod stop ==="
  : >"$RUNTIME_DIR/.stopping"   # sinaliza o supervisor de um 'sod up' ativo
  local name pid owned=0
  for name in frontend backend game; do
    pid="$(rt_pid "$name" 2>/dev/null)" || continue
    owned=1
    stop_tree "$name" "$pid"
  done
  # Fallback de padrão/nome (sobras/órfãos) — só do que era nosso (marcador .owned).
  for name in frontend backend game; do
    [ -f "$RUNTIME_DIR/$name.owned" ] || continue
    owned=1
    case "$name" in
      frontend) stragglers "frontend" "$REPO_ROOT/web/node_modules/.*vi[t]e" ;;
      backend)  stragglers "backend"  "uvicorn system_one_dwarf[.]app[.]main" ;;
      game)     stragglers_game ;;
    esac
  done
  report_leftovers
  forget_owned
  say "— verificação das portas —"
  confirm_ports
  [ "$owned" = 0 ] && say "(não havia PID registado por 'sod up' neste runtime)"
  say "— pronto —"
}

# --------------------------------------------------------------- status
component_line() { printf '%-22s %-12s %s\n' "$1" "$2" "$3"; }

cmd_status() {
  say "=== System One Dwarf · status ==="
  say "repo:    $REPO_ROOT"
  say "runtime: $RUNTIME_DIR"
  say ""
  local pid detail state allp

  if game_running; then
    allp="$(pgrep -x dwarfort | tr '\n' ' ')"
    if [ -f "$RUNTIME_DIR/game.owned" ]; then
      state="✅ nosso"; detail="pid $allp· df: $DF_PATH"
    else
      state="✅ externo"; detail="pid $allp· df: $DF_PATH (não é do 'sod up')"
    fi
  else
    state="❌ parado"; detail="df: $DF_PATH (lançador: $DF_PATH/dfhack)"
  fi
  component_line "Jogo (dwarfort)" "$state" "$detail"

  if port_open "$PORT"; then
    pid="$(rt_pid backend 2>/dev/null)" || pid=""
    if [ -n "$pid" ] && alive "$pid"; then
      state="✅ nosso"; detail="pid $pid"
    else
      state="⚠️ externo"; detail="pid $(pid_of_port "$PORT" || echo '?') (não é do 'sod up')"
    fi
    if health_ok; then
      detail="$detail · http://127.0.0.1:$PORT/api/health OK"
    else
      detail="$detail · /api/health NÃO responde"
    fi
  else
    state="❌ parado"; detail="porta $PORT fechada"
  fi
  component_line "Backend (FastAPI)" "$state" "$detail"

  if port_open "$WEB_PORT"; then
    pid="$(rt_pid frontend 2>/dev/null)" || pid=""
    if [ -n "$pid" ] && alive "$pid"; then
      state="✅ nosso"; detail="pid $pid · http://localhost:$WEB_PORT"
    else
      state="⚠️ externo"; detail="pid $(pid_of_port "$WEB_PORT" || echo '?') · http://localhost:$WEB_PORT (não é do 'sod up')"
    fi
  else
    state="❌ parado"; detail="porta $WEB_PORT fechada"
  fi
  component_line "Frontend (Vite)" "$state" "$detail"

  say ""
  say "logs: $RUNTIME_DIR/{game,backend,frontend}.log"
  if detect_key; then say "chave OpenRouter: encontrada ($KEY_SOURCE)"; else say "chave OpenRouter: ausente"; fi
}

# ----------------------------------------------------------------- logs
cmd_logs() {
  local files=() f
  for f in game backend frontend; do
    [ -f "$RUNTIME_DIR/$f.log" ] && files+=("$RUNTIME_DIR/$f.log")
  done
  if [ ${#files[@]} -eq 0 ]; then
    say "sem logs em $RUNTIME_DIR (ainda não houve 'sod up')"
    return 0
  fi
  if [ "$FOLLOW" = 1 ]; then
    tail -n 200 -f "${files[@]}"
  else
    tail -n 200 "${files[@]}"
  fi
}

# ------------------------------------------------------------ install-link
cmd_install_link() {
  local target="$HOME/.local/bin/sod"
  mkdir -p "$HOME/.local/bin"
  ln -sfn "$SCRIPT_PATH" "$target"
  ok "ligação criada: $target -> $SCRIPT_PATH"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ok "~/.local/bin já está no PATH — 'sod' disponível" ;;
    *) warn "~/.local/bin NÃO está no PATH; acrescenta: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
  esac
}

# -------------------------------------------------------------- argumentos
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      up|stop|status|logs|install-link|help) CMD="$1" ;;
      --no-game)    WITH_GAME=0 ;;
      --no-backend) WITH_BACKEND=0 ;;
      --no-web)     WITH_WEB=0 ;;
      --sound)      WITH_SOUND=1 ;;
      --follow|-f)  FOLLOW=1 ;;
      --port)       PORT="${2:-}"; [ $# -ge 2 ] && shift ;;
      --web-port)   WEB_PORT="${2:-}"; [ $# -ge 2 ] && shift ;;
      --df-path)    DF_PATH="${2:-}"; [ $# -ge 2 ] && shift ;;
      -h|--help)    CMD="help" ;;
      *) err "opção desconhecida: $1 (vê: sod --help)"; exit 2 ;;
    esac
    shift
  done
  case "$PORT$WEB_PORT" in
    *[!0-9]*) err "--port/--web-port precisam de ser números"; exit 2 ;;
  esac
  [ -n "$CMD" ] || CMD="up"
}

main() {
  parse_args "$@"
  case "$CMD" in
    up)           cmd_up ;;
    stop)         cmd_stop ;;
    status)       cmd_status ;;
    logs)         cmd_logs ;;
    install-link) cmd_install_link ;;
    help)         usage ;;
    *)            usage; exit 2 ;;
  esac
}

main "$@"
