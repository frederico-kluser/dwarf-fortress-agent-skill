# Setup — DFHack no Dwarf Fortress da Steam (Linux nativo)

Registo do que foi instalado nesta máquina para a ponte (`dfhack/df_bridge.py`) conseguir
falar com o jogo. É um documento de instalação: descreve o estado real verificado, não um plano.

**Data da instalação:** 2026-10-08
**Máquina:** CachyOS/Arch · DF Steam **nativo Linux** (não-Proton)

---

## 1. O que foi instalado

| Item | Valor |
|---|---|
| Dwarf Fortress | **53.16** (Steam appid `975370`, buildid `24557528`, `StateFlags=4`) |
| Diretório do DF | `/home/ondokai/.steam/steam/steamapps/common/Dwarf Fortress` |
| DFHack | **53.16-r2** (release estável, publicada 2026-09-29) |
| URL do artefacto | `https://github.com/DFHack/dfhack/releases/download/53.16-r2/dfhack-53.16-r2-Linux-64bit.tar.bz2` |
| Tamanho | 20 997 782 bytes (20.0 MiB) |
| SHA-256 | `3a3998c73473db2dc35d12f6db159f09495133eaec5837e34b58313687bf1352` |
| Método | tarball oficial extraído **dentro** do diretório do DF |
| Instalado por | `dfhack/install_dfhack_linux.sh --manual --yes` (ver §3) |

A versão do DFHack **tem de casar com a do DF**: `53.16-r2` ↔ DF `53.16`. Se a Steam
atualizar o jogo, o DFHack manual fica para trás e é preciso reinstalar (ver §6).

### Ficheiros criados no diretório do DF

O tarball adiciona exatamente **5 entradas novas** na raiz do diretório do DF — e **nenhuma
delas colide** com ficheiros já existentes do jogo (verificado antes de extrair, membro a membro):

| Caminho | Notas |
|---|---|
| `hack/` | 1 718 ficheiros, ~67 MB — plugins, scripts Lua, libs, docs offline |
| `dfhack` | lançador (`-rwxr-xr-x`) — arranca o jogo já com o DFHack |
| `dfhack-run` | wrapper para o cliente RPC (`hack/dfhack-run`) |
| `libdfhooks.so` | 34 KB (35 008 bytes) — carregada pelo **próprio DF**, não por `LD_PRELOAD` |
| `dfhooks_dfhack.ini` | 25 bytes — conteúdo: `hack/libdfhooks_dfhack.so` |

**Nada foi apagado nem substituído** no diretório do DF: a comparação do `ls` antes/depois
mostra as 5 entradas acima como novas e zero entradas removidas.

`dfhack-config/` **não existe ainda** — o DFHack cria-o (com os defaults, incluindo
`remote-server.json`) no primeiro arranque do jogo. Ver §5.

---

## 2. Porque a rota tarball (e não a app da Steam)

O instalador conhece duas rotas:

- **Steam** — app oficial *DFHack - Dwarf Fortress Modding Engine* (`appid 2346660`, que é o
  appid do **DFHack**, não do DF). Exige a UI do cliente Steam e instala num diretório
  **separado** (`steamapps/common/DFHack`). Como a ponte lê `hack/` **dentro** do diretório do
  DF, esta rota não satisfazia o `dfhack_installed`.
- **Manual (tarball)** — extrai a release do GitHub diretamente para dentro do diretório do
  DF, deixando `hack/` ao lado de `data/`. É a rota que a ponte espera.

A rota `auto` (sem flags) imprime a sugestão da Steam e pede confirmação interativa num TTY;
sem TTY ela termina sem instalar. Por isso a instalação usou `--manual --yes`, que é a
combinação **documentada no próprio script** para instalação não-interativa.

Sem `sudo`, e nada foi escrito fora do diretório do DF (o download usa `mktemp -d`, que
respeita `$TMPDIR`, e é apagado no fim pelo `trap` do script).

---

## 3. Comandos exatos usados

```bash
cd /home/ondokai/Agent-Skills/dwarf-fortress-agent-skill

# 0) Estado inicial (antes de tocar em nada)
python3 dfhack/df_bridge.py status --json      # dfhack_installed:false, rpc.reachable:false

# 1) Plano, sem descarregar nem escrever nada
bash dfhack/install_dfhack_linux.sh --dry-run

# 2) Instalação real (não-interativa, tarball para dentro do diretório do DF)
bash dfhack/install_dfhack_linux.sh --manual --yes

# 3) Verificação
python3 dfhack/df_bridge.py status --json      # dfhack_installed:true, dfhack_run:true
```

Plano impresso pelo `--dry-run` (idêntico ao executado):

```
Dwarf Fortress:  /home/ondokai/.steam/steam/steamapps/common/Dwarf Fortress (nativo)
DFHack:          não instalado
Release:         53.16-r2
Download:        https://github.com/DFHack/dfhack/releases/download/53.16-r2/dfhack-53.16-r2-Linux-64bit.tar.bz2
Ação:            extrair o tarball em "…/Dwarf Fortress" (hack/ fica ao lado de data/)
```

Para reinstalar por cima (ex.: depois de a Steam atualizar o DF):

```bash
bash dfhack/install_dfhack_linux.sh --manual --yes --force
```

---

## 4. Como lançar o jogo com DFHack (sessão ao vivo)

**O agente não lança o jogo.** Isto é procedimento do humano.

```bash
cd "/home/ondokai/.steam/steam/steamapps/common/Dwarf Fortress"
./dfhack
```

O terminal passa a ser o **console do DFHack**. Convenções úteis:

| Comando | Efeito |
|---|---|
| `./dfhack` | arranca o DF com DFHack (é o lançador; é o próprio DF que carrega `libdfhooks.so`) |
| `./dfhack +unpause +"prospect all"` | comandos no boot, separados por `+` |
| `./dfhack --disable-dfhack` | desativa o DFHack **só nesta sessão** (equivalente a `DFHACK_DISABLE=1`) |

> Lançar pelo **Steam** (`dwarfort` diretamente) **não** carrega o DFHack. Tem de ser `./dfhack`.
> Exceção: se o `SteamAppId` for `2346660` (app DFHack da Steam), o próprio lançador redireciona
> para `hack/launchdf` — mas nessa rota o DFHack vive noutro diretório e a ponte não o vê.

No Linux nativo o **terminal de onde se lança o jogo passa a ser o console do DFHack** — os
comandos escrevem-se diretamente nele (com histórico entre execuções nas setas ↑/↓). Em
alternativa, `dfhack-run <comando>` a partir de outro terminal é equivalente a escrever esse
comando no console — é por aqui que a ponte fala com o processo, por TCP, sem precisar do console.

---

## 5. O que o agente precisa em runtime

Ordem obrigatória — cada passo depende do anterior:

1. **Jogo a correr** — lançado com `./dfhack` (§4). Sem processo, não há servidor RPC.
2. **Mundo carregado** — o DF tem de estar num mundo (não no menu principal).
3. **Personagem de adventure mode criado** — **pelo humano**. Isto está fora do âmbito do
   agente: o agente não cria mundos nem personagens.
4. **Scripts da ponte implantados** — ver abaixo.

Só depois disto `rpc.reachable` passa a `true` e os comandos de leitura/escrita do estado
funcionam.

### Implantação dos scripts `dfb-*.lua`

`df_bridge.py:ensure_game_scripts()` copia os scripts do repositório para o **primeiro**
caminho de procura de scripts do DFHack:

```
<DF>/dfhack-config/scripts/          # Core.txt: "1. dfhack-config/scripts"
```

- **Origem:** `dfhack/dfb-*.lua` ao lado do `df_bridge.py` — hoje 4 ficheiros:
  `dfb-common.lua`, `dfb-state.lua`, `dfb-nav.lua`, `dfb-act.lua`.
- **Destino:** `<DF>/dfhack-config/scripts/` — o diretório é criado com
  `os.makedirs(..., exist_ok=True)`, portanto **não precisa de existir antes**.
- **Quando corre:** apenas a partir de `run_game_script()`, ou seja nos subcomandos que falam
  com o jogo (`run`, `state`, `snapshot`, `act`, …). **Não há subcomando que o faça offline** —
  por isso `dfhack-config/` continua ausente após a instalação, e isso é o esperado.
- **Idempotente:** compara bytes e só reescreve o que mudou; `DF_BRIDGE_SKIP_INSTALL=1` desliga.

Consequência prática: na primeira sessão ao vivo, o primeiro comando do agente é que cria
`dfhack-config/scripts/` e lá deposita os `dfb-*.lua`. Não é preciso passo manual.

---

## 6. Flags do `df_bridge.py status`

`python3 dfhack/df_bridge.py status --json` (implementação em `df_bridge.py`, ~linha 513).

| Campo | Como é calculado | Significado |
|---|---|---|
| `df_path` / `found_via` / `kind` | autodetecção das bibliotecas Steam | onde está o DF; `kind:nativo` = build Linux (o tarball do DFHack só serve nesta) |
| `dfhack_installed` | `isdir(<DF>/hack)` | **DFHack está no disco.** Não diz nada sobre o jogo estar a correr |
| `dfhack_run` | `access(<DF>/dfhack-run, X_OK)` | o cliente RPC existe **e é executável** |
| `rpc.port` | `--port` › `$DFHACK_PORT` › `dfhack-config/remote-server.json` › `5000` | porta do servidor remoto do DFHack |
| `rpc.reachable` | conexão TCP a `localhost:<porta>` | **o jogo está a correr com DFHack.** Falso é normal com o jogo fechado |
| `gamelog.path/exists/size/mtime` | `<DF>/gamelog.txt` | o DF escreve este log ao arrancar — é a prova de que o jogo já correu alguma vez |
| `ok_read` / `ok_write` | depende de RPC vivo | se dá para ler/escrever estado do jogo |

Estado esperado **com o jogo fechado** (o atual):

```json
{ "dfhack_installed": true, "dfhack_run": true,
  "rpc": { "port": 5000, "reachable": false },
  "gamelog": { "exists": false } }
```

`dfhack_installed:true` + `rpc.reachable:false` **não é erro** — é exatamente o que se espera
antes de o humano lançar o jogo.

### Servidor remoto (RPC)

- Porta predefinida **5000**, só existe **enquanto o jogo corre com DFHack**.
- Config: `<DF>/dfhack-config/remote-server.json` (criado no primeiro arranque) — chave `port`.
- `DFHACK_PORT` no ambiente **tem precedência** sobre o JSON; `--port` tem precedência sobre tudo.
- Se a porta 5000 estiver ocupada (outro DF, outro serviço), o servidor **não arranca** e a
  ponte fica `reachable:false` para sempre — usar `--port` ou `DFHACK_PORT`.

---

## 7. Troubleshooting

| Sintoma | Causa provável | Verificação / correção |
|---|---|---|
| `dfhack_installed:false` | DFHack não está no diretório do DF | `ls "<DF>/hack"` — se faltar, correr `install_dfhack_linux.sh --manual --yes` |
| `dfhack_installed:true` mas o jogo não tem DFHack | jogo **reiniciado sem** o DFHack, ou lançado pelo Steam em vez do lançador | confirmar que o arranque foi `./dfhack` (não `dwarfort`, não o botão Play do DF); `ls "<DF>/dfhack"` e reiniciar pelo lançador |
| `rpc.reachable:false` com o jogo aberto | jogo não foi lançado com DFHack; porta 5000 ocupada; DFHack desativado na sessão | verificar `--disable-dfhack`/`DFHACK_DISABLE`; `ss -ltnp \| grep 5000`; conferir `dfhack-config/remote-server.json` e `DFHACK_PORT` |
| `rpc.reachable:false` com o jogo fechado | **normal** — não há processo servidor | nenhuma ação; lançar o jogo com `./dfhack` |
| `gamelog.txt` ausente | o jogo **nunca foi lançado** neste diretório | lançar uma vez com `./dfhack`; o ficheiro aparece em `<DF>/gamelog.txt` |
| DFHack carrega mas os `dfb-*` não são encontrados | scripts ainda não implantados (nenhum comando ao vivo correu) | correr qualquer subcomando ao vivo (`state`, `snapshot`); confirmar `ls "<DF>/dfhack-config/scripts/"` |
| Erro de versão / crash ao arrancar após update da Steam | DF atualizado e DFHack ficou para trás | confirmar a versão do DF e reinstalar a release correspondente: `install_dfhack_linux.sh --manual --yes --force` |
| DFHack instalado mas `kind:proton` | o DF está a correr via Proton | o tarball Linux não serve; usar o build nativo (Steam → Propriedades → Compatibilidade) |

---

## 8. Recibos (para auditoria)

```bash
# versão do DFHack instalada
head -2 "<DF>/hack/news.rst"                     # → DFHack 53.16-r2

# versão do DF
strings -a "<DF>/dwarfort" | grep -x '53\.16'    # → 53.16
grep -E '"(appid|buildid|LastUpdated)"' \
  ~/.steam/steam/steamapps/appmanifest_975370.acf

# hash do tarball oficial
sha256sum dfhack-53.16-r2-Linux-64bit.tar.bz2
# 3a3998c73473db2dc35d12f6db159f09495133eaec5837e34b58313687bf1352
```

Instalação de **2026-10-08**, sem `sudo`, sem escrita fora do diretório do DF, sem remoções.
