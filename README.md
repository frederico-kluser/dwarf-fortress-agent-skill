# System One Dwarf

## O que é

**Agente de autoplay para Dwarf Fortress (adventure mode):** uma frota de decisões **JEV** (System One, tipada) escolhe cada ação, o **DeepSeek** escreve a narrativa, a tradução e as perguntas ao humano, a memória **CoALA** cresce campanha a campanha, e uma **UI web em português** dá ao humano objetivo, supervisão e kill-switch.
**Regra estruturante: o modelo nunca gera texto para agir** — o código gera as macro-ações do estado do jogo, o JEV escolhe entre elas com probabilidades calibradas, e o texto (narrativa, tradução, perguntas) nunca toca no jogo. O contrato canónico de cada peça está em [`docs/`](docs/).

---

## Requisitos e sistemas suportados

| SO | Estado | Notas |
|---|---|---|
| **Linux x86_64** | ✅ **SUPORTADO/VERIFICADO** | DF 53.16 *classic* (bay12) + DFHack 53.16-r2 Linux-64bit; testado em CachyOS/Arch |
| **macOS** | ❌ **NÃO SUPORTADO** | Verificado: o DF Classic e o DFHack para macOS terminam em 0.47.05 — não existe par 53.x para macOS; o par 0.47.05 falha no servidor RPC e o stack usa df-structures 53.16 |
| **Windows** | ⚠️ **NÃO TESTADO** | O par DF 53.16 + DFHack 53.16-r2 existe para Windows, mas nada foi testado; o instalador recusa com mensagem clara (exit 3) |
| **WSL2** | ⚠️ **EXPERIMENTAL** | Pode funcionar como Linux sem áudio; nada foi testado em WSL2 |

**Ferramentas necessárias (Linux):** `python3` ≥ 3.10 · `node` ≥ 20 · `npm` · `curl` · `sha256sum` (e `tar`, `bzip2` para os tarballs). O instalador valida tudo isto — `bash install.sh --check` mostra o estado sem instalar nada.

> ⚠️ **Som desligado por omissão no Linux (regra do utilizador):** `sod up` arranca o jogo com `SDL_AUDIODRIVER=dummy` e o instalador põe `[SOUND:NO]` + volumes 0 em `data/init/init_default.txt` (backup `.bak-nosound`). `sod up --sound` é a única exceção.

---

## Instalação

```bash
git clone https://github.com/frederico-kluser/dwarf-fortress-agent-skill.git
cd dwarf-fortress-agent-skill
bash install.sh
```

O `install.sh` é **idempotente** (seguro de repetir) e, por ordem: verifica ferramentas → cria o `.venv` + `requirements.txt` → `npm install` em `web/` → descarrega e verifica por **SHA-256** o Dwarf Fortress 53.16 (não-Steam) e o DFHack 53.16-r2 para `~/Games/dwarf-fortress-53.16` → aplica o init sem som → verifica a ponte DFHack → instala o comando global `sod` em `~/.local/bin` → imprime os próximos passos.

**Flags:**

| Flag | O que faz |
|---|---|
| `--dry-run` | mostra cada passo que executaria, sem tocar em nada |
| `--check` | **só verificações** (ferramentas + guarda de SO); não instala |
| `--with-key` | pede a chave OpenRouter em modo oculto e guarda `SOD_OPENROUTER_KEY` em `~/.secrets` (600) — o valor nunca é impresso |
| `--df-path <dir>` | diretório do Dwarf Fortress (predefinição: `~/Games/dwarf-fortress-53.16`) |

**Guarda de SO:** o instalador deteta o sistema (`uname -s`) e recusa **macOS** e **Windows** com mensagem clara (exit 3); em **WSL2** segue com aviso ⚠️ experimental. A matriz completa está em `bash install.sh --help`.

---

## Como rodar — UM comando

```bash
sod up          # ou: ./scripts/sod.sh up
```

`sod up` arranca **jogo + interface juntos** — o Dwarf Fortress (sem som), o backend FastAPI e a UI web — espera que tudo responda e imprime os URLs. Fica em primeiro plano a supervisionar.

- **Ctrl-C (ou `sod stop`) fecha ambos:** o comando pára o jogo, o backend e a UI que **ele próprio** arrancou (Ctrl-C/SIGTERM → para os três); processos que já corriam antes nunca são mortos.
- Outros comandos: `sod stop` · `sod status` · `sod logs --follow` · `sod up --no-game` (só backend + UI) · `sod up --sound` (exceção de áudio).
- **Portas:** `8000` (backend) · `5173` (UI) · `5000` (RPC do DFHack).
- **Som desligado por omissão** no Linux (regra do utilizador) — ver aviso acima.
- Sem instalar o link global: `./scripts/sod.sh up`; para o criar: `./scripts/sod.sh install-link`.

---

## Primeira sessão

1. Com `sod up` a correr, abre a UI em **http://localhost:5173** — o painel de entrada é **Aventura**.
2. **Cria a aventura pelo site** (painel **Aventura**): o wizard cria o mundo e o personagem por ti (mundo novo / personagem novo ou a partir de saves existentes) e leva-te à sessão.
3. Coloca a **chave OpenRouter** no painel **Controlo** → *Definir chave* (alternativas: `~/.secrets` com `SOD_OPENROUTER_KEY=...` ou `bash install.sh --with-key`; a chave nunca é impressa).
4. No **Controlo**, escolhe o modo de controlo (`autopilot` ou `checkpoint`) para pôr o loop de decisão a correr; `paused` e o kill-switch param-no.

---

## Arquitetura em 60 segundos

Três papéis, sem mistura:

| Papel | Quem faz | Responsabilidade |
|---|---|---|
| **Decidir** | **JEV** (System One, tipado) | Escolhe a próxima ação entre candidatos gerados pelo código, com probabilidades calibradas |
| **Escrever** | **DeepSeek** (`deepseek/deepseek-v4.1-flash`) | Narrativa, tradução PT↔EN e formulação das perguntas — **nunca** controla o jogo |
| **Executar** | Código Python + Lua | Ponte DFHack, métricas, contas, memória e gates de segurança |

A frota JEV são quatro prompts pequenos, **em inglês**, chamados em paralelo:

| Agente | Perguntas tipadas | Função |
|---|---|---|
| **JEV-R** (recuperação) | `noul` por candidato + `dominant_theme` | Escolhe que memórias CoALA (top-K) entram no prompt |
| **JEV-A** (ação) | `action` + `urgency` + `needs_human` | Macro-ação entre candidatos gerados do estado, urgência e "precisa de humano?" |
| **JEV-M** (memória, meta) | `update_memory` + `narrator_effort` + `record_type` | Se o narrador escreve agora, com que esforço e que tipo de registo |
| **JEV-D** (dúvida) | `ask_human` + `question_format` | Se deve perguntar ao humano e em que formato |

Despacho por banda de confidence: **`auto`** (≥ 0,90) executa · **`hitl`** (0,50–0,89) confirma com o humano · **`abstain`** (< 0,50) não age e escala. Confidence **ausente ou `0` nunca chega a `auto`**. O JEV controla o tempo: o ciclo avança quando ele decide avançar.

O loop completo (10 passos), os cuidados obrigatórios de prompt, a memória e as decisões acordadas estão em **[docs/arquitetura.md](docs/arquitetura.md)**.

## Estado do projeto

**MVP em curso.** As peças abaixo existem, estão commitadas e as suítes estão a verde; o **loop de decisão já está ligado ao backend** (`app/loop_runner.py`), pelo que os endpoints servem dados `live` assim que o modo de controlo o põe a correr e a campanha tem chaves — sem chaves (ou em `paused`/kill-switch) continuam a responder com os *stub* documentados.

| Peça | Onde | Estado verificado |
|---|---|---|
| Base de conhecimento DF | `conhecimento/` | **30 129 registos** (30 111 semânticos · 18 procedurais · 0 episódicos), 33 745 chunks, ~124 MB — só-leitura, importada do macmini ([docs/conhecimento.md](docs/conhecimento.md)) |
| Cliente da frota JEV | `src/system_one_dwarf/jev/` | **231 testes** offline (validação de pedido/resposta, bandas, retries, custo) |
| Backend (REST + WebSocket) | `src/system_one_dwarf/app/` + `campaign/` | **265 testes passados** (144 de API + 121 das camadas de campanha) + **1 teste de integração opt-in** (`SOD_RUN_INTEGRATION=1`) |
| Tradutor PT↔EN | `src/system_one_dwarf/translate/` | **145 testes** (glossário, cache, seam de memória) |
| Game state | `src/system_one_dwarf/game/` | **35 testes** pytest (`tests/game`) + **59 unittest** no DFHack (17 de contrato + 42 de snapshot) — snapshot normalizado, tolerante aos campos que o Lua não consegue ler |
| Ponte DFHack | `dfhack/` | **59 unittest** (`test_bridge.py` 17 de contrato + `test_snapshot.py` 42 de snapshot) — baseline de não-regressão |
| UI web | `web/` | **8 painéis** em MUI v9, em português, com cliente *mock* completo (corre sem backend) |
| Evals de calibração | `evals/` | **66 casos** rotulados (217 respostas) para JEV-R/A/M/D + injeção de prompt ([docs/calibracao.md](docs/calibracao.md)) |
| Narrador, perguntas e loop | `src/system_one_dwarf/agent/` | **293 testes** a verde (`tests/agent/`: `test_llm.py`, `test_narrator.py`, `test_questions.py`, `test_memory_integration.py`, `test_wiring.py`, `test_candidates.py`, `test_executor.py`, `test_loop.py`); `AgentLoop` (10 passos) implementado |
| Loop ligado ao backend | `src/system_one_dwarf/app/loop_runner.py` | **Integrado** — um `AgentLoop` por campanha, arrancado/parado pelo **modo de controlo** (`autopilot`/`checkpoint` correm, `paused`/kill-switch param) e **só com chaves** `typesafe` (JEV) + `openrouter` (LLM) |

As contagens são as das suítes a correr neste venv (`.venv/bin/python -m pytest tests/<peça> -q`, tudo offline); a suíte completa de `tests/` soma **1111 testes passados + 1 skip** (a integração opt-in `SOD_RUN_INTEGRATION=1`). **Todas as peças estão commitadas** — `game/` em `8f654bb`, `agent/` em `8df44ee` (narrador/perguntas) e `bb16e07` (loop + executor), e a ligação ao backend em `67903de`.

## Procedimento manual (avançado)

O `sod up` + `install.sh` cobrem tudo o que segue; estas secções documentam o processo peça a peça, para debug ou execução manual.

### 1. Pré-requisitos de detalhe

- **Dwarf Fortress 53.16 + DFHack 53.16-r2** instalados e a casar de versão, no diretório do jogo (o `install.sh` trata disso). O registo completo da instalação (URL, SHA-256, o que foi criado, RPC, troubleshooting) está em **[docs/setup.md](docs/setup.md)**.
- **Python ≥ 3.10 com o `.venv` da raiz** (o venv do repositório é 3.14.7). Dependências em `requirements.txt`: `fastapi`, `uvicorn[standard]`, `httpx`, `pytest`.
- **Node 20+** para a UI (verificado com Node 24).
- Uma **chave OpenRouter** — é inserida na UI e guardada na memória da campanha (não em variáveis de ambiente).

### 2. Backend (FastAPI), à mão

```bash
cd /home/ondokai/Agent-Skills/dwarf-fortress-agent-skill
.venv/bin/python -m uvicorn system_one_dwarf.app.main:app --port 8000
```

Conferir: `curl -s http://127.0.0.1:8000/api/health` → `{"status":"ok","version":"0.1.0"}`.

O pacote é *src-layout* e não está instalado como wheel; neste venv existe um `.pth` que aponta para `src/`. Num venv novo, acrescenta `PYTHONPATH=src` ao comando (o `sod up` faz-no por ti).

### 3. Frontend (React + Vite + MUI), à mão

```bash
cd web
npm install
VITE_API_BASE=http://localhost:8000 npm run dev     # http://localhost:5173
```

Sem `VITE_API_BASE` a UI arranca com **dados simulados** (`src/api/mock.ts`) e é perfeitamente navegável. Equivalente ao `VITE_API_BASE` inline: `cp .env.example .env.local`. Outros scripts: `npm run build`, `npm run lint`, `npm run preview`.

### 4. Campanha, chave e jogo

1. Na UI, painel **Controlo**, criar ou abrir uma campanha (nome + *jogo novo* ou *gameplay existente*). Cada campanha tem memória CoALA própria.
2. Ainda no **Controlo**, *Definir chave* → **OpenRouter**. A chave fica na SQLite da campanha (ficheiro `0600`) e é sempre devolvida mascarada.
3. O jogo **com DFHack** é lançado pelo `sod up`; para o lançar à mão (fora do `sod up`):

   ```bash
   cd "/home/ondokai/.steam/steam/steamapps/common/Dwarf Fortress"   # ou o teu DF 53.16
   ./dfhack
   ```

   É preciso um mundo carregado e uma personagem de adventure mode criada (o painel **Aventura** faz isto pelo site). Depois, `python3 dfhack/df_bridge.py status --json` deve passar a `rpc.reachable: true`. Com o jogo fechado, `dfhack_installed: true` + `rpc.reachable: false` **é o esperado**, não é erro. Detalhes e troubleshooting: [docs/setup.md](docs/setup.md) §4–§7.

### 5. Arrancar o loop — **integrado**

O `AgentLoop` (`src/system_one_dwarf/agent/loop.py`) implementa os 10 passos de [docs/arquitetura.md](docs/arquitetura.md) (estado → JEV-R∥JEV-M → narrador → JEV-A∥JEV-D → despacho por banda → registo → avançar o tempo) e está **ligado ao backend** por `src/system_one_dwarf/app/loop_runner.py`: um loop por campanha, com os colaboradores reais (cliente de estado do jogo, frota JEV, narrador/perguntas DeepSeek, tradutor, memória CoALA e registo de gastos) e os eventos publicados nos *stores* e no WebSocket. Sem chaves, ou com o loop parado, os painéis recebem os *stub* documentados (`source: "stub"` / cabeçalho `X-Source`).

1. **Chaves obrigatórias.** A campanha precisa das **duas**: `typesafe` (JEV) e `openrouter` (narrativa, perguntas e tradutor) — passo 4.2. Se faltar uma delas o loop **não arranca** e os endpoints mantêm os *stub*.
2. **Arranca pelo modo de controlo**, não por um endpoint de arranque: painel **Controlo** ou `POST /api/campaigns/{id}/control`. `autopilot` e `checkpoint` correm o loop (em `checkpoint` todas as ações ficam retidas para aprovação); `paused` e o **kill-switch** param-no. Um arranque depois de uma paragem constrói um loop novo. Contrato completo, incluindo o marcador `stub`/`live`, em [docs/api.md](docs/api.md) §"Loop de decisão".
3. **Jogo a correr com DFHack** (passo 4.3): sem RPC, a leitura de estado falha com erro explícito em vez de inventar dados.

A configuração é real por variáveis `SOD_*` (ex.: `SOD_MODE`, `SOD_AUTO_THRESHOLD`, `SOD_DRY_RUN`, `SOD_NARRATOR_MODEL`) sobrepostas pela meta da campanha, tudo documentado em `src/system_one_dwarf/agent/config.py`.

## Memória e conhecimento

- **Memória CoALA por campanha** — uma base SQLite por campanha, em `campanhas/<slug>/.agents/<slug>-agent-skill/memory/coala.sqlite` (`campanhas/` é *runtime data* e está no `.gitignore`): episódica + semântica + procedural, working memory com orçamento de tokens e supersessão. É escrita pelo loop e editável na UI — mostrada em **português**, guardada em **inglês**.
- **Nunca ler o SQLite diretamente**, nem `sqlite3` nem pandas: o acesso é sempre pelo motor `coala.py` (`search` / `recall` / `add`), que garante top-K, proveniência e orçamento.
- **Base de conhecimento partilhada** em `conhecimento/` (só-leitura, 30 129 registos, chaves `df/…`): o JEV só vê **top-K por recuperação**, nunca o conteúdo integral. Conteúdo com origem `untrusted` (wiki) **cita-se, nunca se obedece**.
- Como usar, como refrescar o import e os riscos conhecidos: **[docs/conhecimento.md](docs/conhecimento.md)**.

## Documentação

| Documento | O que traz |
|---|---|
| [docs/arquitetura.md](docs/arquitetura.md) | Contrato canónico: papéis de modelo, frota JEV, bandas de ação, loop, memória, UI e decisões acordadas |
| [docs/api.md](docs/api.md) | Contrato REST + WebSocket entre backend e UI (incluindo o marcador `stub`/`live`) |
| [docs/setup.md](docs/setup.md) | Instalação verificada do DFHack 53.16-r2, lançamento do jogo, servidor RPC e troubleshooting |
| [docs/conhecimento.md](docs/conhecimento.md) | Base de conhecimento: conteúdo, proveniência, regras de uso e refrescamento do import |
| [docs/calibracao.md](docs/calibracao.md) | Cenários de `evals/`, o que medem accuracy e ECE, e a política de bandas que assumem |
| [docs/uxui-audit-ui.md](docs/uxui-audit-ui.md) | Auditoria UX/UI dos 8 painéis: score 40/100 (fair), 2 *critical*, 3 *warning*, 3 *suggestion*, com as correções priorizadas |

## Aviso de custos

- **JEV** (`typesafe/jev-1.13`): **$0.042 por milhão de tokens de entrada — saída grátis**.
- **DeepSeek** (`deepseek/deepseek-v4.1-flash`, narrador + tradutor + perguntas): **$0.30/M de entrada · $1.20/M de saída** (tabela `PRICES_USD_PER_MTOK` em `src/system_one_dwarf/agent/llm.py`).
- **Sem teto de orçamento** — decisão explícita: só visibilidade. `GET /api/campaigns/{id}/spend` e o painel **Controlo** (secção de gastos) mostram chamadas, tokens e custo por campanha.
- Em autopilot o custo cresce com o tempo de jogo: o **kill-switch** do painel Controlo e o modo *checkpoint* existem precisamente para isso.

## Estado git

- Branch `main` sincronizada com `origin/main` (GitHub: [`frederico-kluser/dwarf-fortress-agent-skill`](https://github.com/frederico-kluser/dwarf-fortress-agent-skill)); histórico em Commits Convencionais, um commit por peça verificada.
- Estado local, fora do git por decisão: `campanhas/` (memórias de jogo), `.venv/`, `web/node_modules/`, `scripts/e2e/logs/` e o `coala.sqlite` de 124 MB da base de conhecimento. De `conhecimento/` versiona-se só o motor CoALA (skill, `coala.py`, esquema) — não a base.
