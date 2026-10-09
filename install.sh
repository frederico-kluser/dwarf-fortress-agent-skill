#!/usr/bin/env bash
# =============================================================================
# install.sh — instalador/configurador do System One Dwarf (idempotente)
#
#   bash install.sh              instala/configura tudo (seguro de repetir)
#   bash install.sh --dry-run    mostra o que faria, sem tocar em nada
#   bash install.sh --check      SÓ verifica ferramentas/estado (não instala)
#   bash install.sh --with-key   (opcional) guarda SOD_OPENROUTER_KEY em
#                                ~/.secrets (entrada oculta; nunca ecoa o valor)
#
# Guarda de SO (uname -s): Linux x86_64 instala; WSL2 segue com aviso
# EXPERIMENTAL; macOS e Windows são RECUSADOS (exit 3) — vê --help para a
# matriz completa de suporte.
#
# O que faz, por ordem:
#   1. ferramentas: python3 ≥ 3.10, node ≥ 20, npm, curl, sha256sum, tar, bzip2
#   2. .venv + pip install -r requirements.txt
#   3. web/ → npm install
#   4. download + verificação SHA-256 do Dwarf Fortress 53.16 (não-Steam) e do
#      DFHack 53.16-r2 para ~/Games/dwarf-fortress-53.16
#      (o tarball do DF extrai FLAT — as entradas são movidas para o diretório)
#   5. init sem som: data/init/init_default.txt → [SOUND:NO] + volumes 0
#      (backup: init_default.txt.bak-nosound)   ⚠️ SOM DESATIVADO POR PREDEFINIÇÃO
#   6. verificação: python3 dfhack/df_bridge.py status --json
#   7. comando global: scripts/sod.sh install-link → ~/.local/bin/sod
#   8. próximos passos
# =============================================================================
set -u
set -o pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

DF_PATH="${SOD_DF_PATH:-$HOME/Games/dwarf-fortress-53.16}"
CACHE_DIR="${SOD_CACHE_DIR:-$HOME/Games/.sod-cache}"
DRY_RUN=0
CHECK_ONLY=0
WITH_KEY=0

DF_URL="https://www.bay12games.com/dwarves/df_53_16_linux.tar.bz2"
DF_SHA256="2f9c0134b2465cccb705b8d3e322cdff07df7374ffbfafffe8f982f2ef7e7e7d"
DF_TB="df_53_16_linux.tar.bz2"

DFHACK_URL="https://github.com/DFHack/dfhack/releases/download/53.16-r2/dfhack-53.16-r2-Linux-64bit.tar.bz2"
DFHACK_SHA256="3a3998c73473db2dc35d12f6db159f09495133eaec5837e34b58313687bf1352"
DFHACK_TB="dfhack-53.16-r2-Linux-64bit.tar.bz2"

FAILURES=0
STEPS_OK=0

# ------------------------------------------------------------------- output
say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; STEPS_OK=$((STEPS_OK + 1)); }
warn() { printf '⚠️  %s\n' "$*"; }
err()  { printf '❌ %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }
step() { printf '\n— %s\n' "$*"; }
would() { printf '   [dry-run] faria: %s\n' "$*"; }

# ------------------------------------------- matriz de suporte (verificada)
support_matrix() {
  cat <<'EOF'
SISTEMAS SUPORTADOS (matriz verificada nesta sessão):
  SO            Estado                   Notas
  Linux x86_64  ✅ SUPORTADO/VERIFICADO  DF 53.16 classic (bay12) + DFHack
                                         53.16-r2 Linux-64bit; testado em
                                         CachyOS/Arch
  macOS         ❌ NÃO SUPORTADO         DF Classic e DFHack para macOS terminam
                                         em 0.47.05 — não existe par 53.x; o par
                                         0.47.05 falha no servidor RPC e o stack
                                         usa df-structures 53.16
  Windows       ⚠️ NÃO TESTADO           o par DF 53.16 + DFHack 53.16-r2 existe
                                         para Windows, mas nada foi testado; este
                                         instalador recusa (exit 3)
  WSL2          ⚠️ EXPERIMENTAL          pode funcionar como Linux sem áudio;
                                         nada foi testado em WSL2

FERRAMENTAS NECESSÁRIAS (Linux): python3 ≥ 3.10, node ≥ 20, npm, curl,
sha256sum (e tar, bzip2 para os tarballs).
EOF
}

# ------------------------------------------------------------- guarda de SO
detect_os() {
  OS_KERNEL="$(uname -s 2>/dev/null || echo unknown)"
  OS_WSL=0
  case "$OS_KERNEL" in
    Linux) grep -qi microsoft /proc/version 2>/dev/null && OS_WSL=1 ;;
  esac
}

os_guard() { # Linux segue; WSL2 segue com aviso; macOS/Windows recusam (exit 3)
  detect_os
  case "$OS_KERNEL" in
    Linux)
      if [ "$OS_WSL" = 1 ]; then
        warn "WSL2 detetado — suporte EXPERIMENTAL: pode funcionar como Linux"
        warn "sem áudio (SDL_AUDIODRIVER=dummy), mas nada foi testado em WSL2."
      fi
      return 0
      ;;
    Darwin)
      err "macOS NÃO SUPORTADO (exit 3): não existe par Dwarf Fortress 53.16 + DFHack 53.16-r2 para macOS."
      say "   Verificado nesta sessão: o DF Classic e o DFHack para macOS terminam em 0.47.05;"
      say "   o par 0.47.05 falha no servidor RPC e o stack usa df-structures 53.16."
      say "   Usa Linux x86_64 (nativo). Matriz completa: bash install.sh --help"
      exit 3
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      warn "Windows NÃO TESTADO — este instalador recusa (exit 3)."
      say "   O par DF 53.16 + DFHack 53.16-r2 existe para Windows, mas nada foi"
      say "   testado; o suporte oficial é Linux x86_64. Usa WSL2 (experimental)"
      say "   ou Linux nativo. Matriz completa: bash install.sh --help"
      exit 3
      ;;
    *)
      err "SO não reconhecido: $OS_KERNEL — suportado apenas Linux x86_64 (exit 3)"
      exit 3
      ;;
  esac
}

usage() {
  cat <<EOF
install.sh — instala/configura o System One Dwarf (idempotente)

USO:
  bash install.sh [--dry-run] [--check] [--with-key] [--df-path <dir>]

OPÇÕES:
  --dry-run        não altera nada; mostra cada passo que executaria
  --check          só verificações (ferramentas + guarda de SO); não instala
  --with-key       pede a chave OpenRouter em modo oculto (só com terminal
                   interativo) e guarda SOD_OPENROUTER_KEY em ~/.secrets (600)
  --df-path <dir>  diretório do Dwarf Fortress
                   (predefinição: ${DF_PATH})
  -h, --help       esta ajuda (com a matriz de sistemas suportados)

$(support_matrix)

⚠️  SOM DESATIVADO POR PREDEFINIÇÃO NO LINUX (regra do utilizador): o passo 6
    põe [SOUND:NO] + volumes 0 em data/init/init_default.txt (backup
    .bak-nosound). O comando 'sod up' também arranca o jogo sem som
    (SDL_AUDIODRIVER=dummy); '--sound' é a única exceção.

A chave OpenRouter NUNCA é impressa. Coloca-a: na UI (painel Controlo →
Definir chave), em ~/.secrets a linha  SOD_OPENROUTER_KEY=sk-or-...  (chmod 600)
ou com: bash install.sh --with-key.
EOF
}

# ------------------------------------------------------------- utilitários
run() { # executa (ou simula, em dry-run) e trata o erro com a regra das 5 tentativas
  local desc="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then
    would "$*"
    return 0
  fi
  local attempt rc
  for attempt in 1 2 3 4 5; do
    if "$@"; then
      return 0
    fi
    rc=$?
    [ "$attempt" = 5 ] && break
    warn "$desc: falha (tentativa $attempt/5, exit $rc) — a repetir em ${attempt}s ..."
    sleep "$attempt"
  done
  err "$desc: falhou 5 vezes — comando: $*"
  return 1
}

python_minor_ok() { # ≥ 3.10
  python3 - "$1" <<'PY'
import sys
need = tuple(int(x) for x in sys.argv[1].split(".")[:2])
sys.exit(0 if sys.version_info[:2] >= need else 1)
PY
}

node_major_ok() { # ≥ 20
  local major="${1#v}"; major="${major%%.*}"
  [ -n "$major" ] && [ "$major" -ge 20 ] 2>/dev/null
}

check_hash() { # $1 ficheiro · $2 sha256 esperado
  printf '%s  %s\n' "$2" "$1" | sha256sum -c --quiet - >/dev/null 2>&1
}

archive_safe() { # sem caminhos absolutos nem '..'
  if tar -tjf "$1" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
    return 1
  fi
  return 0
}

df_installed()   { [ -x "$DF_PATH/dwarfort" ] && [ -f "$DF_PATH/data/init/init_default.txt" ]; }
dfhack_installed() { [ -x "$DF_PATH/dfhack" ] && [ -d "$DF_PATH/hack" ] && [ -f "$DF_PATH/libdfhooks.so" ]; }

download() { # $1 url · $2 destino final · $3 sha256
  local url="$1" dest="$2" sha="$3"
  if [ -f "$dest" ]; then
    if check_hash "$dest" "$sha"; then
      ok "tarball já em cache e íntegro: $dest"
      return 0
    fi
    warn "tarball em cache com hash errado — a descartar e a baixar de novo"
    rm -f "$dest"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    would "curl -L --fail --retry 3 -o $dest $url && verificar sha256"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  local attempt
  for attempt in 1 2 3 4 5; do
    say "   download (tentativa $attempt/5): $url"
    if curl -L --fail --retry 3 --connect-timeout 20 -o "$dest.part" "$url"; then
      if check_hash "$dest.part" "$sha"; then
        mv "$dest.part" "$dest"
        ok "descarregado e verificado (sha256 OK): $(basename "$dest")"
        return 0
      fi
      err "sha256 NÃO confere para $(basename "$dest") — ficheiro descartado"
      rm -f "$dest.part"
    else
      warn "download falhou (tentativa $attempt/5)"
      rm -f "$dest.part"
    fi
    [ "$attempt" = 5 ] || sleep "$attempt"
  done
  err "não foi possível obter $(basename "$dest") após 5 tentativas"
  return 1
}

detect_key() { # presença sem nunca mostrar o valor → "encontrada"/"ausente"
  local name v rel f line
  for name in SOD_OPENROUTER_KEY OPENROUTER_API_KEY; do
    v="${!name-}"
    [ -n "$v" ] && { say "   chave: encontrada (env \$$name)"; return 0; }
  done
  for rel in ".dsh/.credentials.yaml" ".secrets"; do
    f="$HOME/$rel"
    [ -f "$f" ] || continue
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?[A-Za-z0-9_]*OPENROUTER[A-Za-z0-9_]*[[:space:]]*[:=]' "$f" 2>/dev/null | head -n1)"
    [ -n "$line" ] && { say "   chave: encontrada (~/$rel)"; return 0; }
  done
  say "   chave: ausente (define na UI ou com --with-key / ~/.secrets)"
  return 1
}

# --------------------------------------------------------------- passos
step_tools() {
  step "1/8 · Ferramentas"
  local missing=0 v
  if command -v python3 >/dev/null 2>&1; then
    v="$(python3 --version 2>&1 | awk '{print $2}')"
    if python_minor_ok 3.10; then ok "python3 $v (≥ 3.10)"; else err "python3 $v é antigo (precisa ≥ 3.10)"; missing=1; fi
  else
    err "python3 não encontrado"; missing=1
  fi
  if command -v node >/dev/null 2>&1; then
    v="$(node --version)"
    if node_major_ok "$v"; then ok "node $v (≥ 20)"; else err "node $v é antigo (precisa ≥ 20)"; missing=1; fi
  else
    err "node não encontrado (precisa ≥ 20)"; missing=1
  fi
  if command -v npm >/dev/null 2>&1; then ok "npm $(npm --version)"; else err "npm não encontrado"; missing=1; fi
  if command -v curl >/dev/null 2>&1; then ok "curl presente"; else err "curl não encontrado"; missing=1; fi
  if command -v sha256sum >/dev/null 2>&1; then ok "sha256sum presente"; else err "sha256sum não encontrado"; missing=1; fi
  if command -v tar >/dev/null 2>&1; then ok "tar presente"; else err "tar não encontrado"; missing=1; fi
  if command -v bzip2 >/dev/null 2>&1; then ok "bzip2 presente"; else err "bzip2 não encontrado (necessário para os .tar.bz2)"; missing=1; fi
  return "$missing"
}

step_venv() {
  step "2/8 · Ambiente Python (.venv + requirements.txt)"
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    ok ".venv já existe ($("$REPO_ROOT/.venv/bin/python" --version 2>&1))"
  elif [ "$DRY_RUN" = 1 ]; then
    would "python3 -m venv $REPO_ROOT/.venv"
  else
    run "criar .venv" python3 -m venv "$REPO_ROOT/.venv" || return 1
    ok ".venv criado"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    would ".venv/bin/python -m pip install -r requirements.txt"
    return 0
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    run "pip install -r requirements.txt" \
      "$REPO_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -q \
      -r "$REPO_ROOT/requirements.txt" \
      && ok "dependências Python instaladas (requirements.txt)"
  fi
}

step_web() {
  step "3/8 · UI web (npm install)"
  if [ "$DRY_RUN" = 1 ]; then
    would "cd web && npm install"
    return 0
  fi
  if [ -d "$REPO_ROOT/web/node_modules" ] \
     && [ "$REPO_ROOT/web/node_modules" -nt "$REPO_ROOT/web/package.json" ] \
     && [ "$REPO_ROOT/web/node_modules" -nt "$REPO_ROOT/web/package-lock.json" ]; then
    ok "node_modules já instalado e mais novo que package.json/package-lock.json (skip)"
    return 0
  fi
  ( cd "$REPO_ROOT/web" && run "npm install" npm install ) \
    && ok "dependências npm instaladas (web/)"
}

step_df() {
  step "4/8 · Dwarf Fortress 53.16 (não-Steam) em $DF_PATH"
  [ "$DRY_RUN" = 1 ] || mkdir -p "$DF_PATH"
  if df_installed; then
    ok "Dwarf Fortress já instalado ($DF_PATH/dwarfort existe) — download/extração ignorados"
    return 0
  fi
  download "$DF_URL" "$CACHE_DIR/$DF_TB" "$DF_SHA256" || return 1
  [ "$DRY_RUN" = 1 ] && { would "extrair $DF_TB (flat: mover as entradas) para $DF_PATH"; return 0; }
  local tb="$CACHE_DIR/$DF_TB" staging e entries
  archive_safe "$tb" || { err "tarball do DF contém caminhos suspeitos — abortado"; return 1; }
  entries="$(tar -tjf "$tb" | cut -d/ -f1 | sort -u | wc -l)"
  say "   entradas de topo no tarball: $entries (extração FLAT → movidas para o diretório)"
  staging="$(mktemp -d)"
  if ! tar -xjf "$tb" -C "$staging"; then
    err "falha ao extrair $tb"; rm -rf "$staging"; return 1
  fi
  mkdir -p "$DF_PATH"
  for e in "$staging"/* "$staging"/.[!.]*; do
    [ -e "$e" ] || continue
    mv "$e" "$DF_PATH"/
  done
  rm -rf "$staging"
  if df_installed; then
    ok "Dwarf Fortress extraído para $DF_PATH ($entries entradas movidas)"
  else
    err "extração concluída mas $DF_PATH/dwarfort não apareceu"
    return 1
  fi
}

step_dfhack() {
  step "5/8 · DFHack 53.16-r2 (extraído POR CIMA do diretório do DF)"
  if dfhack_installed; then
    ok "DFHack já instalado ($DF_PATH/dfhack + hack/ + libdfhooks.so) — ignorado"
    return 0
  fi
  download "$DFHACK_URL" "$CACHE_DIR/$DFHACK_TB" "$DFHACK_SHA256" || return 1
  [ "$DRY_RUN" = 1 ] && { would "extrair $DFHACK_TB por cima de $DF_PATH"; return 0; }
  local tb="$CACHE_DIR/$DFHACK_TB"
  archive_safe "$tb" || { err "tarball do DFHack contém caminhos suspeitos — abortado"; return 1; }
  if ! tar -xjf "$tb" -C "$DF_PATH"; then
    err "falha ao extrair $tb por cima de $DF_PATH"
    return 1
  fi
  if dfhack_installed; then
    ok "DFHack instalado em $DF_PATH"
  else
    err "extração do DFHack concluída mas falta dfhack/hack//libdfhooks.so"
    return 1
  fi
}

step_nosound() {
  step "6/8 · Init sem som ([SOUND:NO] + volumes 0)  ⚠️ som desativado por predefinição"
  local init="$DF_PATH/data/init/init_default.txt"
  if [ ! -f "$init" ]; then
    err "não encontro $init (instala o DF primeiro — passo 4)"
    return 1
  fi
  # Nota: o init oficial do DF tem fins de linha CRLF; as verificações leem uma
  # cópia sem \r (tr) e o sed não toca nos bytes do fim de linha.
  if tr -d '\r' <"$init" | grep -qx '\[SOUND:NO\]' \
     && ! tr -d '\r' <"$init" | grep -qE '^\[(MASTER_VOLUME|MUSIC_VOLUME|AMBIENCE_VOLUME|SFX_VOLUME|MUSIC_VOLUME_ADV|AMBIENCE_VOLUME_ADV|SFX_VOLUME_ADV):[1-9]' ; then
    ok "init já sem som ([SOUND:NO] + volumes 0) — nada a fazer"
    [ -f "$init.bak-nosound" ] && say "   backup existente: $init.bak-nosound"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    would "cp -n $init $init.bak-nosound && sed (SOUND:NO + volumes 0)"
    return 0
  fi
  if [ ! -f "$init.bak-nosound" ]; then
    cp "$init" "$init.bak-nosound"
    ok "backup criado: $init.bak-nosound"
  else
    say "   backup já existia: $init.bak-nosound (preservado)"
  fi
  sed -i -E \
    -e 's/^\[SOUND:[A-Za-z]+\]/[SOUND:NO]/' \
    -e 's/^\[(MASTER_VOLUME|MUSIC_VOLUME|AMBIENCE_VOLUME|SFX_VOLUME|MUSIC_VOLUME_ADV|AMBIENCE_VOLUME_ADV|SFX_VOLUME_ADV):[0-9]+\]/[\1:0]/' \
    "$init"
  if tr -d '\r' <"$init" | grep -qx '\[SOUND:NO\]'; then
    ok "init atualizado: [SOUND:NO] + volumes 0"
  else
    err "não consegui pôr [SOUND:NO] em $init"
    return 1
  fi
}

step_bridge() {
  step "7/8 · Verificação da ponte DFHack (df_bridge.py status)"
  local out
  if [ "$DRY_RUN" = 1 ]; then
    would "python3 dfhack/df_bridge.py status --json --df-path $DF_PATH"
    return 0
  fi
  if ! out="$(python3 "$REPO_ROOT/dfhack/df_bridge.py" status --json --df-path "$DF_PATH" 2>&1)"; then
    err "df_bridge.py status falhou: $(printf '%s' "$out" | tail -n1)"
    return 1
  fi
  if printf '%s' "$out" | grep -q '"dfhack_installed": true'; then
    ok "dfhack_installed: true"
  else
    err "dfhack_installed não é true — vê docs/setup.md §4–§7"
    return 1
  fi
  if printf '%s' "$out" | grep -q '"reachable": true'; then
    say "   rpc.reachable: true (jogo aberto)"
  else
    say "   rpc.reachable: false (esperado com o jogo fechado)"
  fi
}

step_link() {
  step "8/8 · Comando global (sod em ~/.local/bin)"
  if [ "$DRY_RUN" = 1 ]; then
    would "bash scripts/sod.sh install-link"
    return 0
  fi
  bash "$REPO_ROOT/scripts/sod.sh" install-link && ok "comando global instalado"
}

step_next() {
  say ""
  say "==================================================================="
  say " INSTALAÇÃO CONCLUÍDA — próximos passos"
  say "==================================================================="
  detect_key
  say ""
  say "  1. UM comando para tudo (jogo + interface):"
  say "       sod up                 # ou: ./scripts/sod.sh up"
  say "     Arranca o Dwarf Fortress + backend (8000) + UI (5173) juntos;"
  say "     Ctrl-C (ou 'sod stop') fecha AMBOS — só o que ele iniciou."
  say "     ⚠️  SOM DESATIVADO por predefinição no Linux (regra do utilizador);"
  say "         'sod up --sound' é a única exceção (e o init tem backup .bak-nosound)."
  say ""
  say "  2. Chave OpenRouter (uma destas formas, nunca impressa):"
  say "       • na UI: painel Controlo → Definir chave   (recomendado)"
  say "       • em ~/.secrets:  SOD_OPENROUTER_KEY=sk-or-...   (chmod 600 ~/.secrets)"
  say "       • reinstala com:  bash install.sh --with-key"
  say ""
  say "  3. Campanha: na UI → Controlo → criar campanha → modo de controlo"
  say "     (autopilot ou checkpoint)."
  say ""
  say "  Comandos: sod up | sod stop | sod status | sod logs --follow"
  say "  Docs:     README.md · docs/setup.md · docs/api.md · docs/arquitetura.md"
  say "==================================================================="
}

# -------------------------------------------------------------- argumentos
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY_RUN=1 ;;
    --check)    CHECK_ONLY=1 ;;
    --with-key) WITH_KEY=1 ;;
    --df-path)  DF_PATH="${2:-}"; [ $# -ge 2 ] && shift ;;
    -h|--help)  usage; exit 0 ;;
    *) printf '❌ opção desconhecida: %s (vê: bash install.sh --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

os_guard   # Linux/WSL2 seguem; macOS/Windows saem aqui com exit 3

say "=== System One Dwarf · install.sh ==="
say "repo:  $REPO_ROOT"
say "df:    $DF_PATH"
say "cache: $CACHE_DIR"
[ "$DRY_RUN" = 1 ] && warn "MODO DRY-RUN: nada será alterado"

if [ "$CHECK_ONLY" = 1 ]; then
  say ""
  say "— modo --check: só verificações (nada é instalado) —"
  step_tools
  rc=$?
  say ""
  if [ "$rc" = 0 ]; then ok "check: todas as ferramentas presentes (usa 'bash install.sh' para instalar)"
  else err "check: há ferramentas em falta (vê as linhas ❌ acima)"; fi
  exit "$rc"
fi

step_tools    || { err "ferramentas em falta — instala-as e volta a correr"; exit 1; }
step_venv
step_web
step_df
step_dfhack
step_nosound
step_bridge
step_link

# --with-key: SÓ com opt-in explícito e terminal interativo (sem prompts por
# predefinição — a alternativa documentada é a UI ou ~/.secrets à mão).
if [ "$WITH_KEY" = 1 ]; then
  say ""
  say "— guardar chave OpenRouter em ~/.secrets —"
  if [ "$DRY_RUN" = 1 ]; then
    would "pedir chave (entrada oculta) e acrescentar SOD_OPENROUTER_KEY a ~/.secrets (chmod 600)"
  elif [ ! -t 0 ]; then
    warn "stdin não é um terminal — a ignorar --with-key; escreve em ~/.secrets: SOD_OPENROUTER_KEY=... (chmod 600)"
  else
    key=""
    printf 'Chave OpenRouter (não será mostrada; Enter vazio cancela): '
    IFS= read -rs key
    printf '\n'
    if [ -z "$key" ]; then
      warn "vazio — nada guardado"
    elif grep -qE '^[[:space:]]*(export[[:space:]]+)?SOD_OPENROUTER_KEY[[:space:]]*[:=]' "$HOME/.secrets" 2>/dev/null; then
      warn "~/.secrets já tem SOD_OPENROUTER_KEY — nada alterado (edita à mão se quiseres trocar)"
    else
      touch "$HOME/.secrets"; chmod 600 "$HOME/.secrets"
      printf 'SOD_OPENROUTER_KEY=%s\n' "$key" >>"$HOME/.secrets"
      key=""
      ok "SOD_OPENROUTER_KEY guardada em ~/.secrets (chmod 600) — valor não impresso"
    fi
  fi
fi

step_next

say ""
if [ "$FAILURES" -gt 0 ]; then
  err "instalação terminou com $FAILURES erro(s) — vê as linhas ❌ acima"
  exit 1
fi
ok "instalação concluída sem erros ($STEPS_OK passos OK)"
exit 0
