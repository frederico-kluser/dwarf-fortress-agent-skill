#!/usr/bin/env bash
# install_dfhack_linux.sh — instala (ou guia a instalação d)o DFHack para o
# Dwarf Fortress da Steam no Linux.
#
# Duas rotas:
#   Steam  (recomendada) — app oficial "DFHack" (appid 2346660); a Steam instala
#                          e mantém DF e DFHack em versões compatíveis sozinha.
#   Manual (tarball)     — baixa a release do GitHub (DFHack/dfhack) e extrai no
#                          diretório do DF, deixando hack/ ao lado de data/.
#
# Uso:
#   bash install_dfhack_linux.sh                 # detecta tudo e pergunta
#   bash install_dfhack_linux.sh --dry-run       # só mostra o que faria
#   bash install_dfhack_linux.sh --manual --yes  # tarball, sem perguntas
#   bash install_dfhack_linux.sh --df-path "/caminho/Dwarf Fortress" --tag 53.14-r2
#
# Dependências: bash, tar, bzip2 e curl OU wget (python3 só como fallback de JSON).
# Códigos de saída: 0 ok · 10 Dwarf Fortress não encontrado · 1 demais erros.
set -euo pipefail

REPO="DFHack/dfhack"
RELEASES_URL="https://github.com/${REPO}/releases"
DFHACK_STEAM_APPID=2346660

# ---------- saída ----------
if [[ -t 1 ]]; then
  C_BOLD=$'\e[1m'; C_YELLOW=$'\e[33m'; C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_OFF=$'\e[0m'
else
  C_BOLD=""; C_YELLOW=""; C_RED=""; C_GREEN=""; C_OFF=""
fi
log()  { printf '%s\n' "${C_GREEN}▸${C_OFF} $*"; }
warn() { printf '%s\n' "${C_YELLOW}aviso:${C_OFF} $*" >&2; }
die()  { printf '%s\n' "${C_RED}erro:${C_OFF} $1" >&2; exit "${2:-1}"; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<EOF
${C_BOLD}install_dfhack_linux.sh${C_OFF} — DFHack para o Dwarf Fortress (Steam) no Linux

Uso: bash install_dfhack_linux.sh [opções]

Opções:
  --df-path PATH   diretório do Dwarf Fortress (senão: \$DF_PATH ou auto-detecção Steam)
  --tag TAG        release do DFHack (ex.: 53.14-r2; padrão: a última estável do GitHub)
  --steam          só a rota Steam (app oficial — recomendada, atualiza sozinha)
  --manual         só a rota manual (tarball do GitHub extraído no diretório do DF)
  --yes, -y        não pergunta nada (assume "sim")
  --force, -f      reinstala por cima de um hack/ existente
  --dry-run        mostra o plano (detecção, versão, URL) sem baixar nem escrever
  -h, --help       esta ajuda

Códigos de saída: 0 ok · 10 DF não encontrado · 1 demais erros
EOF
}

# ---------- argumentos ----------
DF_DIR_ARG="${DF_PATH:-}"
TAG=""
ROUTE="auto"          # auto | steam | manual
ASSUME_YES=0
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --df-path)   DF_DIR_ARG="${2:?--df-path exige um caminho}"; shift 2 ;;
    --df-path=*) DF_DIR_ARG="${1#*=}"; shift ;;
    --tag)       TAG="${2:?--tag exige uma tag}"; shift 2 ;;
    --tag=*)     TAG="${1#*=}"; shift ;;
    --steam)     ROUTE="steam"; shift ;;
    --manual)    ROUTE="manual"; shift ;;
    --yes|-y)    ASSUME_YES=1; shift ;;
    --force|-f)  FORCE=1; shift ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           usage >&2; die "opção desconhecida: $1" ;;
  esac
done

confirm() {
  # confirm "pergunta" — vira "sim" automático com --yes; "não" quando sem TTY.
  [[ $ASSUME_YES -eq 1 ]] && return 0
  [[ -t 0 ]] || return 1
  local resp
  printf '%s' "$1 [s/N] "
  read -r resp || resp=""
  [[ $resp == [sSyY]* ]]
}

# ---------- detecção do Dwarf Fortress ----------
find_steam_libraries() {
  # Emite cada steamapps/ plausível: instalação clássica, XDG, flatpak e as
  # bibliotecas extras registradas em libraryfolders.vdf (parse de linha "path").
  local roots=(
    "$HOME/.steam/steam/steamapps"
    "$HOME/.local/share/Steam/steamapps"
    "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps"
  )
  local seen="|" lib real vdf p
  emit() {
    real=$(readlink -f "$1" 2>/dev/null || printf '%s' "$1")
    [[ -d $1 && $seen != *"|$real|"* ]] || return 0
    seen="${seen}${real}|"
    printf '%s\n' "$1"
  }
  for lib in "${roots[@]}"; do
    emit "$lib"
    vdf="$lib/libraryfolders.vdf"
    [[ -r $vdf ]] || continue
    while IFS= read -r p; do
      emit "$p/steamapps"
    done < <(sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$vdf")
  done
}

classify_df_dir() {
  # nativo | proton | invalido — o tarball Linux do DFHack só serve no nativo.
  local dir="$1"
  [[ -d "$dir/data" ]] || { echo "invalido"; return; }
  [[ -e "$dir/dwarfort" ]] && { echo "nativo"; return; }
  [[ -e "$dir/Dwarf Fortress.exe" ]] && { echo "proton"; return; }
  echo "invalido"
}

find_df_dir() {
  if [[ -n $DF_DIR_ARG ]]; then
    printf '%s\n' "$DF_DIR_ARG"
    return 0
  fi
  local lib cand
  while IFS= read -r lib; do
    cand="$lib/common/Dwarf Fortress"
    if [[ -d $cand && $(classify_df_dir "$cand") != "invalido" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done < <(find_steam_libraries)
  return 1
}

# ---------- release do GitHub ----------
resolve_tag() {
  # Primeiro sem a API (sem rate-limit): /releases/latest redireciona para
  # /releases/tag/<tag>; a tag é o último segmento da URL final.
  local url tag
  if have curl; then
    url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$RELEASES_URL/latest" 2>/dev/null || true)
    tag="${url##*/}"
    if [[ -n $tag && $tag != "latest" ]]; then printf '%s\n' "$tag"; return 0; fi
  elif have wget; then
    url=$(wget -q --max-redirect=10 --spider --server-response "$RELEASES_URL/latest" 2>&1 \
          | sed -n 's/^[[:space:]]*[Ll]ocation: //p' | tail -1 | tr -d '\r' || true)
    tag="${url##*/}"
    if [[ -n $tag && $tag != "latest" ]]; then printf '%s\n' "$tag"; return 0; fi
  fi
  # Fallback: API JSON (python3 já é pré-requisito das skills).
  if have python3; then
    local api="https://api.github.com/repos/${REPO}/releases/latest"
    local json=""
    if have curl; then json=$(curl -fsSL "$api" 2>/dev/null || true)
    elif have wget; then json=$(wget -qO- "$api" 2>/dev/null || true); fi
    tag=$(printf '%s' "$json" \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)
    if [[ -n $tag ]]; then printf '%s\n' "$tag"; return 0; fi
  fi
  return 1
}

# ---------- rota manual (tarball) ----------
download_and_extract() {
  local dir="$1" tag="$2"
  local asset="dfhack-${tag}-Linux-64bit.tar.bz2"
  local url="$RELEASES_URL/download/${tag}/${asset}"

  have tar   || die "tar não encontrado — instale-o pela sua distro"
  have bzip2 || die "bzip2 não encontrado (o tar precisa dele p/ .tar.bz2) — instale-o pela sua distro"
  { have curl || have wget; } || die "nem curl nem wget encontrados — instale um deles"
  [[ -w $dir ]] || die "sem permissão de escrita em: $dir"

  tmp=$(mktemp -d)                 # global de propósito: o trap EXIT roda fora da função
  trap 'rm -rf "${tmp:-}"' EXIT

  log "Baixando ${asset} …"
  if have curl; then
    curl -fL --progress-bar -o "$tmp/$asset" "$url" || die "download falhou (tag existe?): $url"
  else
    wget -qO "$tmp/$asset" "$url" || die "download falhou (tag existe?): $url"
  fi

  log "Validando o arquivo …"
  tar -tf "$tmp/$asset" >/dev/null 2>&1 || die "tarball corrompido ou incompleto: $asset"

  confirm "Extrair o DFHack ${tag} em \"${dir}\"?" || die "cancelado pelo usuário"

  log "Extraindo em ${dir} …"
  tar -xf "$tmp/$asset" -C "$dir"

  [[ -d "$dir/hack" && -e "$dir/dfhack" ]] \
    || die "a extração terminou, mas hack/ ou ./dfhack não apareceram em $dir"
  log "DFHack ${tag} instalado em ${dir}."
}

# ---------- rota Steam ----------
print_steam_route() {
  cat <<EOF
${C_BOLD}Rota Steam (recomendada)${C_OFF} — o app oficial é grátis e atualiza junto com o jogo:
  1. Na Steam, busque ${C_BOLD}DFHack - Dwarf Fortress Modding Engine${C_OFF} e instale
     (ou rode:  steam steam://install/${DFHACK_STEAM_APPID} );
  2. Na biblioteca, dê Play no ${C_BOLD}DFHack${C_OFF} (não no Dwarf Fortress) —
     ele lança o jogo já com o DFHack carregado;
  3. A Steam mantém DF ↔ DFHack em versões compatíveis automaticamente.
EOF
}

steam_route() {
  print_steam_route
  if have steam && [[ $DRY_RUN -eq 0 ]]; then
    if confirm "Abrir a Steam agora para instalar o DFHack (appid ${DFHACK_STEAM_APPID})?"; then
      log "Chamando a Steam …"
      nohup steam "steam://install/${DFHACK_STEAM_APPID}" >/dev/null 2>&1 &
    fi
  fi
}

# ---------- pós-instalação ----------
print_next_steps() {
  local dir="$1" tag="${2:-}"
  cat <<EOF

${C_BOLD}Próximos passos${C_OFF}
  • Lançar com DFHack:     cd "${dir}" && ./dfhack
                           (o terminal vira o console do DFHack)
  • Comandos no boot:      ./dfhack +unpause +"prospect all"   (separados por +)
  • Desativar por sessão:  ./dfhack --disable-dfhack
  • Controle remoto:       o servidor RPC já sobe em localhost:5000
                           (config: dfhack-config/remote-server.json · env DFHACK_PORT)
  • Ponte para agentes:    python3 df_bridge.py status   (neste mesmo scripts/)
EOF
  if [[ -n $tag ]]; then
    warn "a versão do DFHack precisa casar com a do DF: DFHack ${tag} ↔ DF ${tag%%-*}.
       Se o jogo atualizar pela Steam e o DFHack manual ficar para trás, rode este script de novo (--force)."
  fi
}

# ---------- main ----------
main() {
  log "Procurando o Dwarf Fortress da Steam …"
  local df kind
  if ! df=$(find_df_dir); then
    die "Dwarf Fortress não encontrado nas bibliotecas Steam.
  Dica: passe --df-path \"/caminho/para/Dwarf Fortress\" ou exporte DF_PATH." 10
  fi
  kind=$(classify_df_dir "$df")
  log "Encontrado: ${df} (${kind})"

  case "$kind" in
    invalido)
      die "o diretório não parece ser o do Dwarf Fortress (sem data/): $df" 10 ;;
    proton)
      die "essa instalação é a versão Windows rodando via Proton (só \"Dwarf Fortress.exe\").
  O tarball Linux do DFHack não funciona nela. Opções:
    • troque para o build nativo Linux do DF (Steam → DF → Propriedades → Compatibilidade,
      desmarque o Proton) e rode este script de novo; ou
    • use o app DFHack da Steam, que cuida do build Windows sozinho.
  Detalhes: references/live-bridge-setup.md (skill df-live-bridge)." ;;
  esac

  # Já instalado?
  if [[ -d "$df/hack" && $FORCE -eq 0 ]]; then
    log "O DFHack já está instalado aqui (hack/ presente). Use --force para reinstalar."
    print_next_steps "$df"
    exit 0
  fi

  # Rota só-Steam: imprime/dispara e termina.
  if [[ $ROUTE == "steam" ]]; then
    steam_route
    exit 0
  fi

  # Resolve a tag cedo: o --dry-run também quer mostrá-la.
  local tag="$TAG"
  if [[ -z $tag ]]; then
    log "Consultando a última release em github.com/${REPO} …"
    if ! tag=$(resolve_tag); then
      [[ $DRY_RUN -eq 1 ]] && { warn "sem rede? não deu para resolver a última tag"; tag="<tag>"; } \
        || die "não consegui descobrir a última release (sem rede?). Use --tag, ex.: --tag 53.14-r2"
    fi
  fi

  local asset="dfhack-${tag}-Linux-64bit.tar.bz2"

  if [[ $DRY_RUN -eq 1 ]]; then
    cat <<EOF

${C_BOLD}Plano (--dry-run — nada foi baixado nem escrito)${C_OFF}
  Dwarf Fortress:  ${df} (${kind})
  DFHack:          $( [[ -d "$df/hack" ]] && echo "já instalado (hack/ presente) — reinstalaria por causa do --force" || echo "não instalado" )
  Release:         ${tag}
  Download:        ${RELEASES_URL}/download/${tag}/${asset}
  Ação:            extrair o tarball em "${df}" (hack/ fica ao lado de data/)

Alternativa sem este script: app oficial do DFHack na Steam (appid ${DFHACK_STEAM_APPID}).
EOF
    exit 0
  fi

  # Rota auto: sugere a Steam primeiro; segue para o tarball se o usuário quiser.
  if [[ $ROUTE == "auto" ]]; then
    print_steam_route
    echo
    if ! confirm "Prosseguir com a instalação MANUAL (tarball ${tag}) agora?"; then
      log "Ok — use a rota Steam acima, ou rode de novo com --manual --yes."
      exit 0
    fi
  fi

  download_and_extract "$df" "$tag"
  print_next_steps "$df" "$tag"
}

main
