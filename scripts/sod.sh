#!/usr/bin/env bash
# =============================================================================
# sod — ALIAS DEPRECIADO do System One Dwarf
#
# O comando global passou a chamar-se 'df' (substitui 'sod'): usa `df up`.
# Este shim mantém a compatibilidade — avisa (stderr, não quebra nada) e
# delega TODO o comportamento em scripts/df.sh, incluindo a semântica antiga
# de 'sod' sem argumentos (= 'sod up') e 'sod -h|--help' (= a nossa ajuda).
# =============================================================================
set -u

printf "⚠ 'sod' foi substituído por 'df' — usa 'df up'\n" >&2

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# DF_SOD_ALIAS=1: diz ao df.sh que o aviso já foi impresso e que deve manter a
# semântica antiga do 'sod' (sem argumentos = 'up'; nada de despacho de disco).
export DF_SOD_ALIAS=1
exec "$HERE/df.sh" "$@"
