# Setup local — Dwarf Fortress 53.16 (NON-Steam) + DFHack 53.16-r2

> Peça ENVIRONMENT SETUP do "System One Dwarf". Máquina: Acer (Linux), sessão
> Wayland com XWayland (`DISPLAY=:0`). **Regras duras**: jogo NON-Steam (o
> binário livre do bay12games), **sem som garantido**, sem emuladores/VMs, e a
> instalação Steam em `~/.steam/...` **não é usada nem tocada**.

## 1. Proveniência (download e instalação)

### Dwarf Fortress 53.16 (classic, Linux, NON-Steam)

| Campo | Valor |
|---|---|
| URL | <https://www.bay12games.com/dwarves/df_53_16_linux.tar.bz2> |
| Tamanho | 18 745 340 bytes (~18,7 MB) |
| sha256 | `2f9c0134b2465cccb705b8d3e322cdff07df7374ffbfafffe8f982f2ef7e7e7d` |
| Destino | `~/Games/dwarf-fortress-53.16/` |
| Versão | confirmada com `strings ~/Games/dwarf-fortress-53.16/dwarfort \| grep -x '53\.16'` → `53.16` |

Nota: o tarball do bay12 **extrai plano** (sem diretório de topo): os 13
elementos (`dwarfort`, `data/`, `g_src/`, `licenses/`, `run_df`, `readme.txt`,
`release notes.txt`, `file changes.txt`, `command line.txt`, `libfmod.so.13`,
`libfmod_plugin.so`, `libg_src_lib.so`, `libsdl_mixer_plugin.so`) foram movidos
para dentro de `~/Games/dwarf-fortress-53.16/` após a extração. O conteúdo
pré-existente de `~/Games` (`.agents/`, `.claude/`, `MovingPlatformPOC/`) não foi
tocado.

### DFHack 53.16-r2 (Linux-64bit)

| Campo | Valor |
|---|---|
| URL | <https://github.com/DFHack/dfhack/releases/download/53.16-r2/dfhack-53.16-r2-Linux-64bit.tar.bz2> |
| sha256 esperado | `3a3998c73473db2dc35d12f6db159f09495133eaec5837e34b58313687bf1352` |
| Verificação | `sha256sum -c` → **SUCESSO** antes de extrair |
| Instalação | extraído **por cima** do diretório do DF (`tar -xf … -C ~/Games/dwarf-fortress-53.16/`) |

Adicionou na raiz do DF: `hack/`, `dfhack`, `dfhack-run`, `libdfhooks.so`,
`dfhooks_dfhack.ini`. Alternativa equivalente (re-instalação futura):
`bash dfhack/install_dfhack_linux.sh --manual --yes --force --tag 53.16-r2 --df-path ~/Games/dwarf-fortress-53.16`
(o instalador aceita `--df-path`; por omissão procura nas bibliotecas Steam).

## 2. Sem som — TODAS as camadas (belt and braces)

1. **Env de lançamento (garantia dura)**: `SDL_AUDIODRIVER=dummy` — o SDL fica
   **sem backend de áudio nenhum**; não há dispositivo onde tocar som.
2. **Init do jogo**: em `data/init/init_default.txt` (o ficheiro que este build
   lê — confirmado por `strings dwarfort`; overrides do jogador seriam
   `prefs/init.txt`, que **não existe** nesta instalação):
   `[SOUND:NO]` e todas as 7 chaves de volume a `0`
   (`MASTER_VOLUME`, `MUSIC_VOLUME`, `AMBIENCE_VOLUME`, `SFX_VOLUME`,
   `MUSIC_VOLUME_ADV`, `AMBIENCE_VOLUME_ADV`, `SFX_VOLUME_ADV`).
   Backup: `data/init/init_default.txt.bak-nosound`.
3. **`pasuspender --`**: **não existe nesta máquina** (não é PulseAudio; há
   `pactl`/PipeWire mas é desnecessário com a camada 1). Camada registada como
   indisponível.

Comando de lançamento (é SEMPRE este, env incluído):

```bash
cd ~/Games/dwarf-fortress-53.16 && \
SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1 ./dfhack
```

- `SDL_VIDEODRIVER=x11` + `LIBGL_ALWAYS_SOFTWARE=1`: contornam o problema do
  ecrã preto (renderização software/llvmpipe via XWayland).

## 3. Ponte (`dfhack/df_bridge.py`) → diretório NON-Steam

**Não foi preciso editar a ponte.** Ela suporta override oficial
(precedência no código: flag > env > auto-detecção Steam):

- flag: `--df-path /home/ondokai/Games/dwarf-fortress-53.16`
- env: `export DF_PATH=/home/ondokai/Games/dwarf-fortress-53.16`

Sem override, a ponte auto-detecta `~/.steam/steam/steamapps/common/Dwarf Fortress`
(`found_via: "auto (Steam)"`) — para os testes do agente usar **sempre** o
override. A porta segue `--port` > env `DFHACK_PORT` > `dfhack-config/remote-server.json` > 5000.

## 4. Comandos de verificação

```bash
DF=/home/ondokai/Games/dwarf-fortress-53.16
# versão do DF
strings "$DF/dwarfort" | grep -x '53\.16'
# processo vivo + env no processo real
pgrep -af dwarfort
tr '\0' '\n' < /proc/$(pgrep -x dwarfort)/environ | grep -E 'SDL_AUDIODRIVER|SDL_VIDEODRIVER|LIBGL_ALWAYS_SOFTWARE'
# porta 5000 (RPC DFHack)
ss -ltnp | grep ':5000'
# ponte apontada ao non-Steam
python3 dfhack/df_bridge.py status --json --df-path "$DF"
#   esperado: df_path=/home/ondokai/Games/dwarf-fortress-53.16, found_via="--df-path",
#             dfhack_installed:true, rpc.reachable:true
# snapshot (ANTES de existir aventureiro)
python3 dfhack/df_bridge.py snapshot --json --df-path "$DF"
#   esperado: exit 13, "dfb-state: sem aventureiro (o jogo esta em adventure mode?)"
#             — erro DOCUMENTADO e esperado enquanto não há personagem
```

## 5. Troubleshooting

| Sintoma | Causa / solução |
|---|---|
| **Porta 5000 ocupada** | Outro DF/DFHack já escuta. O servidor RPC do DFHack sobe em silêncio. Resolver: matar o processo antigo (`pkill dwarfort`) ou usar outra porta: `DFHACK_PORT=5001` (env) / `--port 5001` (ponte) — ambos os lados têm de concordar. |
| **Jogo morto / rpc.reachable:false** | Relançar com o comando de §2 (env-wrapped). Confirmar com `pgrep -af dwarfort` e `ss -ltnp \| grep :5000`. `df_bridge.py status` com jogo fechado → `rpc.reachable:false` é esperado. |
| **Som a tocar** | Não deve acontecer. Confirmar que o lançamento levou `SDL_AUDIODRIVER=dummy` (ver `/proc/$(pgrep -x dwarfort)/environ`) e que `data/init/init_default.txt` tem `[SOUND:NO]` e volumes a `0`. Sem `prefs/init.txt` a fazer override. |
| **Ecrã preto** | Renderização GPU. Já aplicado: `LIBGL_ALWAYS_SOFTWARE=1` + `SDL_VIDEODRIVER=x11`. Aceitável se o RPC funcionar — o agente lê estado via Lua, não por pixels. Comando DFHack para diagnóstico no console: `die`. |
| **`snapshot` exit 13 "sem aventureiro"** | **Esperado** antes de criar o aventureiro. Só cria mundo + personagem desbloqueia snapshots com estado. |
| **Steam a tentar atualizar o DF** | Irrelevante aqui: esta instalação é a clássica, fora da Steam. Nunca usar o binário de `~/.steam/...` para os testes. |

## 6. Estado verificado nesta máquina (2026-10-08)

- DF 53.16 non-Steam instalado em `~/Games/dwarf-fortress-53.16/`.
- DFHack 53.16-r2 (sha256 verificado) instalado por cima.
- Jogo lançado env-wrapped, **deixado a correr**: `./dwarfort` (via `./dfhack`),
  RPC `127.0.0.1:5000` LISTEN, `dfhack_installed:true`, `rpc.reachable:true`,
  `ok_read:true`, `ok_write:true`.
- `snapshot --json` → exit 13 `"dfb-state: sem aventureiro"` (esperado).
- Log de consola do arranque: `~/Games/dwarf-fortress-53.16/dfhack-console.log`
  (e `stderr.log` criado pelo próprio DFHack).
