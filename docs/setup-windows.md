# Setup — Windows + Dwarf Fortress da Steam (instalação verificada)

Registo do que foi instalado nesta máquina Windows para o stack do System One Dwarf
correr contra o **Dwarf Fortress da Steam** (o `dfhack/df_bridge.py` a falar com o jogo,
o backend/UI a subir e o JEV a responder via OpenRouter). Espelha `docs/setup.md`
(Linux): descreve o **estado real verificado**, não um plano.

**Data da instalação:** 2026-11-08/09
**Máquina:** Windows 11 · Python 3.12 + uv · Node 24 · Git Bash

---

## 1. O que foi instalado

| Item | Valor |
|---|---|
| Dwarf Fortress | **53.16** (Steam appid `975370`, buildid `24557528` — o mesmo build verificado no Linux) |
| Diretório do DF | `C:\Program Files (x86)\Steam\steamapps\common\Dwarf Fortress` |
| DFHack | **53.16-r3** (Windows-64bit; escolhido sobre a r2 porque corrige a pendura do terminal Windows no exit e um bug do RemoteServer que fechava o socket cedo demais) |
| URL do artefacto | `https://github.com/DFHack/dfhack/releases/download/53.16-r3/dfhack-53.16-r3-Windows-64bit.zip` |
| SHA-256 | `8f391b5512bf42f887bb6cb7d55c8a4f1633f81a006dc7cfe9abde1e141886c3` (confere com o `digest` do GitHub release) |
| Método | zip oficial descarregado, **desbloqueado** (`Unblock-File`) e extraído para dentro do diretório do DF |
| Python | venv `.venv` via `uv sync --extra dev` (CPython 3.13.17; fastapi/uvicorn/httpx/pytest) |
| UI web | `npm install` em `web/` (416 pacotes) |
| Launcher | `scripts/df.ps1` (`up`/`stop`/`status`/`logs`/`install-link`/`help`) |

### Ficheiros criados no diretório do DF (verificado: 0 colisões)

O zip do Windows acrescenta exatamente **3 entradas novas** na raiz do DF — nada do jogo
foi substituído:

| Caminho | Notas |
|---|---|
| `hack/` | plugins, scripts Lua, libs — inclui **`hack/dfhack-run.exe`** (cliente RPC) e `hack/launchdf.exe` |
| `dfhooks.dll` | carregada pelo **próprio** `Dwarf Fortress.exe` no arranque |
| `dfhooks_dfhack.ini` | conteúdo: `hack/dfhooks_dfhack.dll` |

**Lançamento:** `Dwarf Fortress.exe` (direto ou pelo botão Play da Steam) já arranca com
DFHack — ao contrário do Linux, não há lançador `./dfhack` separado. O console do DFHack
abre numa janela própria.

---

## 2. Variáveis e chaves (nunca impressas)

| Onde | O quê |
|---|---|
| `~/.secrets` | `OPENROUTER_API_KEY` e `SOD_OPENROUTER_KEY` (mesmo valor) — ⚠️ **estavam guardadas entre aspas** (`"sk-or-v1-…"`), o que dava `401 Missing Authentication header`; as aspas foram removidas e os loaders toleram-as |
| Ambiente (User) | `DF_PATH` → `C:\Program Files (x86)\Steam\steamapps\common\Dwarf Fortress` (a auto-detecção Steam no Windows também funciona: registo HKCU + `libraryfolders.vdf`) |
| Ambiente (User) | `MOTION_TOKEN` — **necessária para o `npm install`** de `web/` (o `web/.npmrc` expande `${MOTION_TOKEN}` para o registry privado do `@motionplus/core`; processos filhos do agente não herdam o User-scope, há que carregá-la explicitamente) |

---

## 3. Comandos exatos usados

```powershell
# 1) dependências
uv sync --extra dev                       # .venv (CPython 3.13.17)
$env:MOTION_TOKEN = [Environment]::GetEnvironmentVariable('MOTION_TOKEN','User')
Push-Location web; npm install; Pop-Location

# 2) DFHack (download + verificação + unblock + extração)
curl.exe -sSL --fail --retry 3 -o $env:TEMP\sod-setup\dfhack-53.16-r3-Windows-64bit.zip `
  https://github.com/DFHack/dfhack/releases/download/53.16-r3/dfhack-53.16-r3-Windows-64bit.zip
Get-FileHash $env:TEMP\sod-setup\dfhack-53.16-r3-Windows-64bit.zip -Algorithm SHA256  # conferir digest
Unblock-File $env:TEMP\sod-setup\dfhack-53.16-r3-Windows-64bit.zip
tar -xf $env:TEMP\sod-setup\dfhack-53.16-r3-Windows-64bit.zip `
  -C 'C:\Program Files (x86)\Steam\steamapps\common\Dwarf Fortress'

# 3) verificação da ponte
python dfhack\df_bridge.py status --json
```

Estado verificado **com o jogo fechado**:

```json
{ "kind": "windows", "dfhack_installed": true, "dfhack_run": true,
  "rpc": { "port": 5000, "reachable": false }, "ok_read": true, "ok_write": false }
```

`dfhack_installed:true` + `rpc.reachable:false` **é o esperado** com o jogo fechado.

---

## 4. Como correr — Windows

```powershell
pwsh scripts/df.ps1 up          # jogo + backend + UI (Ctrl-C pára só o que arrancou)
pwsh scripts/df.ps1 status      # estado de cada componente
pwsh scripts/df.ps1 stop        # pára só os PID registados em .runtime/
pwsh scripts/df.ps1 logs -Follow
pwsh scripts/df.ps1 install-link  # cria df.cmd (+ sod.cmd) numa pasta do PATH
```

- Portas: `8000` (backend) · `5173` (UI) · `5000` (RPC DFHack).
- **Som ligado** no Windows (a regra de silêncio é do Linux).
- Smoke do JEV (gasta ~$0.00002): `python scripts\jev_smoke.py` — respostas tipadas
  (`noul`/`choice`) de `typesafe/jev-1.13` via `openrouter.ai/api/alpha/decisions`.
- `install.sh` continua a **recusar Windows (exit 3)** — cobre só o fluxo Linux — e
  aponta para este documento e para o `scripts/df.ps1`.

---

## 5. Adaptações de código feitas para Windows

| Peça | O quê |
|---|---|
| `dfhack/df_bridge.py` | auto-detecção Steam no Windows (registo HKCU + `WINDOWS_STEAM_CANDIDATES` + VDF com `\\` escapado); `classify` → `windows` (vs `proton` no Linux); `find_dfhack_run` encontra `hack/dfhack-run.exe`; `dfhack_run_ok` não depende de `X_OK` no Windows |
| `src/system_one_dwarf/game/driver.py` | `SIG_KILL = getattr(signal, "SIGKILL", 9)`; `signal_group`/`group_alive` sem `os.killpg` (Windows: `os.kill` + sonda `OpenProcess` — **`os.kill(pid, 0)` MATA no Windows**) |
| `scripts/df.ps1` | launcher Windows (novo) |
| `install.sh` | guarda de SO Windows agora aponta para o fluxo Windows |
| Testes | `dfhack/test_bridge.py` +8 (WindowsSupportTests) e defaults host-agnósticos; `tests/game/test_driver.py`, `tests/game/test_bridge_client.py`, `tests/agent/test_executor.py` sem POSIX-isms; novo `tests/e2e/test_windows_scripts.py` (4 smoke) |

## 6. Baseline de testes em Windows (conhecido, por iterar)

Suite completa: **3742 passaram** no primeiro arranque em Windows. As falhas restantes
agrupam-se em dívida de portabilidade (nada a ver com o jogo):

1. **Permissões POSIX** (~9): `stat.S_IMODE == 0o600/0700` não se verifica no Windows
   (`st_mode` vem `0o666`) — `tests/campaign/*`, `tests/app/test_runtime_persistence.py`.
2. **Rig live/bash** (~20): `tests/e2e/test_smoke_scripts.py` e `test_session_live.py`
   usam bash, `ss`, `pgrep`, sinais — precisam de rig equivalente via `df.ps1`.
3. **Golden set** (1): `tests/jev/test_golden_regression.py` — a regeneração difere do
   hold-out congelado (determinismo por host; investigar ordenação/paths).
4. **`hunt_plan_cli`** (2): o plano fallback difere num campo — investigar.
