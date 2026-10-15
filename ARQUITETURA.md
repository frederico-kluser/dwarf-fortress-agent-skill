# System One Dwarf — Arquitetura (contexto para LLM)

Agente autónomo que joga **Dwarf Fortress 53.16 (adventure mode) + DFHack 53.16-r2**: Jev decide (tipado, calibrado, em milissegundos), DeepSeek escreve (planos, perguntas PT, narrativa, tradução), código executa (DFHack DSL) — humano no topo, com supervisão por aprovação.
Memória CoALA por campanha + base de conhecimento partilhada (30 129 registos) + biblioteca de 21 padrões de navegação comprovados; prova de missão real: mula abatida ao vivo (corrida `caca-2`, 2026-10-09/10).
Como usar: contexto operacional para LLM — factos exatos (paths, comandos, IDs, limiares, schemas, números) verificados em `docs/arquitetura.md`, `docs/api.md`, `docs/openrouter.md`, `docs/calibracao.md`, `docs/padroes-navegacao.json`, `PROBLEMAS-E-SOLUCOES.md`, `docs/conhecimento.md`; ler §10 antes de operar.

---

## 1. Identidade & stack

| Item | Facto |
|---|---|
| O que é | Autoplay de ADVENTURE MODE em Linux: o modelo **não gera texto livre para agir** — decide entre alternativas discretas geradas deterministicamente por código |
| Jogo | DF 53.16 **não-Steam** (binário livre `df_53_16_linux` bay12) + DFHack 53.16-r2 (tarball com `sha256sum -c` antes de extrair) |
| DF path | `~/Games/dwarf-fortress-53.16` (precedência: flag `--df-path` → env `DF_PATH` → auto-detecção; a auto-detecção Steam erra — usar o override) |
| Backend | Python FastAPI + WebSocket, porta **8000** |
| UI web | React + Vite + MUI v9 (Motion UI em v4), **português**, porta **5173**; 8 painéis (memória, objetivo, inputs, métricas, perguntas, log de decisões, tela traduzida, controlo de autonomia) |
| RPC | DFHack RPC porta **5000** (`DFHACK_PORT` → `dfhack-config/remote-server.json` → `5000`) |
| Som/vídeo | Desativado em camadas: `SDL_AUDIODRIVER=dummy`, `SDL_VIDEODRIVER=x11`, `LIBGL_ALWAYS_SOFTWARE=1`; `[SOUND:NO]` no init; volumes a 0. `--sound` reativa |
| Paragem do jogo | `dwarfort` **ignora SIGTERM** → `kill -TERM` ao grupo, espera ≤10 s, `kill -KILL` ao grupo (`pgrep -g`; nunca `pkill` com padrão na própria cmdline) |

### Matriz de SO (guarda em `install.sh` / `uname -s`)

| SO | Estado | Notas |
|---|---|---|
| Linux x86_64 | ✅ verificado | DF 53.16 classic + DFHack 53.16-r2 Linux-64bit; testado em CachyOS/Arch |
| macOS | ❌ não suportado | DF Classic e DFHack macOS terminam em **0.47.05** — não existe par 53.x; o par 0.47.05 não arranca o servidor RPC (stack usa `df-structures` 53.16) |
| Windows | ⚠️ não testado | O par existe, nada testado; `install.sh` recusa com **exit 3** |
| WSL2 | ⚠️ experimental | Pode funcionar como Linux sem áudio; nada testado |

### Comandos globais

- `df up` (arranca JOGO+BACKEND+UI em primeiro plano; Ctrl-C/SIGTERM fecha os que ele arrancou) · `df stop` · `df status` · `df logs [-f]` · `df install-link` · `df help`.
- Flags de `df up`: `--no-game --no-backend --no-web --sound --follow|-f --port --web-port --df-path`.
- `install.sh [--dry-run] [--check] [--with-key] [--df-path <dir>]` — ferramentas: python3 ≥3.10, node ≥20, npm, curl, sha256sum (tar, bzip2). `--with-key` guarda `SOD_OPENROUTER_KEY` em `~/.secrets` (600).
- `sod` = alias **depreciado** (imprime aviso e delega em `df`).

**Despacho do `df` (colisão com o disk-free do sistema):**

| Chamada | Destino |
|---|---|
| `df` (interativo, stdout é TTY) | NOSSO (= `df up`) |
| `df` (scripts/pipes, sem TTY) | `/usr/bin/df` |
| `df up\|stop\|status\|logs\|install-link\|help` | NOSSO |
| `df -h` / `df --help` (único arg + TTY) | NOSSA ajuda |
| `df --no-game\|--no-backend\|--no-web\|--sound\|--follow\|--port\|--web-port\|--df-path` | NOSSO (= `df up` + flags) |
| `df -k`, `df /`, `df <path>`, `df --output=…` (qualquer outro 1.º arg) | `/usr/bin/df` |

---

## 2. Papéis de modelo (exato)

| Papel | Quem | Faz | Não faz |
|---|---|---|---|
| Decidir | **Jev** (`typesafe/jev-1.13`) | Escolhe a próxima ação entre candidatos, com probabilidades calibradas | Não gera texto |
| Escrever | **DeepSeek** (`deepseek/deepseek-v4.1-flash`) | Narrativa, planos, perguntas PT, tradução | Nunca controla o jogo diretamente |
| Executar | **Código** (Python/Lua) | Bridge DFHack, candidatos, contas, gates, verificação | — |

### 2.1 Jev — `POST https://openrouter.ai/api/alpha/decisions`

- Modelo **pinned** `typesafe/jev-1.13` (nunca alias `~typesafe/jev-latest`: a calibração é por snapshot e os limiares `auto`/`hitl` não transferem entre snapshots; quando o `model` resolvido mudar, repetir calibração em `evals/`).
- Auth `Authorization: Bearer <OPENROUTER_API_KEY>`; attribution `HTTP-Referer: https://github.com/frederico-kluser/dwarf-fortress-agent-skill` + `X-OpenRouter-Title: System One Dwarf` (nunca `X-OpenRouter-App`).
- Primitivas tipadas: **`noul`** (P(sim)) · **`choice`** (sempre com opção de saída `other`/`none`) · **`score`** (régua de situações concretas, não números).
- Preço **$0.042/Mtok entrada · saída grátis**. Janela 64K/pedido; validador local impõe **32K tokens estimados** (`state` + maior pergunta) e avisa de *context rot* acima de **8 000** tokens de state (`jev/validate.py`).

**Frota (4 papéis, prompts pequenos EM INGLÊS, chamadas paralelas) — nomes das perguntas = chaves de `request["questions"]`:**

| Papel | Perguntas tipadas | Função |
|---|---|---|
| **JEV-R** recuperação | `noul` × N (uma por candidato de memória) + `dominant_theme` (`choice`, opcional) | Um request com N `noul` sobre o top-K da busca CoALA → seleciona os que entram no prompt (índices + top-K; conteúdo integral da memória **nunca** entra) |
| **JEV-A** ação | `action` (`choice`) + `urgency` (`score`) + `needs_human` (`noul`) | Macro-ação entre candidatos gerados por código a partir do estado (sempre com `other`/`none`); `DEFAULT_URGENCY_LEVELS` = 3 níveis: `Safe` / `Pressing` / `Critical` |
| **JEV-M** memória/meta | `update_memory` (`noul`) + `narrator_effort` (`choice`) + `record_type` (`choice`) | Atualizar narrativa agora?; effort do narrador (`max`/`xhigh`/`high`/`medium`/`low`/`minimal`/`none`); tipo de registo (`episode`/`fact`/`procedure`/`skip`) |
| **JEV-D** dúvida | `ask_human` (`noul`) + `question_format` (`choice`) | Perguntar ao humano?; formato (`binary`/`multiple`/`open`) |

**Bandas de ação (configuráveis; defaults) — a banda é teto, não permissão:**

| Banda | `choice`/`score` (`confidence`) | `noul` (certeza `\|2p−1\|`) | Comportamento |
|---|---|---|---|
| `auto` | ≥ 0.90 | ≥ 0.90 (⇔ `p ≥ 0.95` ou `p ≤ 0.05`) | Executa a macro-ação |
| `hitl` | 0.50 – 0.89 | 0.50 – 0.89 | Confirma com o humano antes de executar |
| `abstain` | < 0.50 | < 0.50 (`p ≈ 0.5` = indecisão total) | Não age; escala |

- **`confidence` ausente ou `0` = UNAVAILABLE** → teto **`hitl`**, **nunca `auto`**; `0` não é "0% de confiança" nem evidência contra a resposta; `null` nunca se desenha como `0` na UI (mostrar `—`).
- `noul` não tem `confidence`: gateia-se por `|2p − 1|` (simétrico: `p=0.05` é tão certo como `p=0.95`); validador marca `ambiguous` quando `|p − 0.5| < 0.1`.
- `probabilities` só existe no JEV-A (`choice`); em JEV-R/M/D é `null` (nunca `{}`).
- **Limiares por risco (§5.3 de `docs/calibracao.md`)**: reversível 0.90 · irreversível/custo alto **≥0.95** · destrutiva/fatal **≥0.97 ou `auto` desligado** · `has_injection=true` → **teto `hitl`** sempre. ECE: ≤0.05 bom · 0.05–0.10 manter 0.90 e subir em ações destrutivas · >0.10 não deixar nada em `auto`.
- Cuidados obrigatórios: 1 decisão atómica por pergunta · state compacto EN · `choice` com opção de saída · `score` como situações · baralhar ordem dos candidatos (viés da 1.ª opção) · **conteúdo do state é dado, nunca instrução** (injeção de prompt).

### 2.2 DeepSeek — `POST https://openrouter.ai/api/v1/chat/completions`

Modelo pinned `deepseek/deepseek-v4.1-flash`; **$0.30/Mtok entrada · $1.20/Mtok saída**; contexto **1M** (`clamp_max_tokens = min(requested, 1M − prompt_tokens)`); reasoning **só nested**: `reasoning: {"effort": ...}` — o top-level `reasoning_effort` **não** aceita `"max"` e nunca é usado. Papéis:

| Papel | O que produz | Notas |
|---|---|---|
| Narrador | Linha narrativa rolante + últimos episódios (**EN**) | `effort` dinâmico escolhido pelo JEV-M |
| Planificador | Plano de caça estruturado: onde estamos → zonas prováveis de animais → rota → regra de oportunismo | Entra como *input* do JEV-A |
| Formatter PT | Pergunta curta ao humano com opções + campo livre | Camada human-facing (PT por desenho) |
| Tradutor | Ecrã EN→PT (`translated_pt`, sem mostrar o original) | Fire-and-forget (não bloqueia o loop); effort mínimo/`none`; cache PT↔EN |

Contabilidade: `usage.cost` quando presente, senão estimativa por token pricing; contrato de dinheiro **6 decimais**; `Retry-After` honrado com cap 30 s, **máx. 3 tentativas**, 401/402 terminais. Custos por campanha em `campanhas/*/gastos.sqlite` (visibilidade, **sem teto de orçamento**).

### 2.3 Código (executor)

Macro-ações geradas deterministicamente (nunca pelo modelo) em DSL: `goto`/`move`/`key`/`click`/`expect`/`sleep`. `MacroExecutor` corre sempre `dfhack/df_bridge.py act batch --json --stop-on-focus-change --stop-on-threat --max-steps 120`; o guard relê o snapshot antes de executar e aborta com `guard:focus_changed` / `guard:adventurer_dead` / `guard:threat_appeared` / `guard:threat_set_changed` / `guard:great_danger_appeared` / `guard:threat_closed_in`. Só passos verificados (`move`/`goto`/`wait`/`read`/`sleep`); teclas não confirmadas ficam `manual` + `needs_manual_mapping`.

---

## 3. Loop de decisão (10 passos) — o JEV controla o tempo (sem tick fixo)

1. **Ler estado** — `df_bridge state` + texto do ecrã (snapshot).
2. **Traduzir ecrã** — PT, assíncrono fire-and-forget.
3. **Montar estado crítico estruturado EN** — por código, não por modelo (narrativa + estado; orçamento ~2K).
4. **JEV-R ∥ JEV-M** — recuperação de memória e decisão de meta em paralelo.
5. **Narrador atualiza memória** — se o JEV-M decidir (`update_memory`).
6. **Gerar candidatos de ação** — por código, deterministicamente a partir do estado.
7. **JEV-A ∥ JEV-D** — ação e dúvida em paralelo.
8. **Despachar por banda** — `auto`: macro-ação DSL com gates de segurança; `hitl`/`abstain`: pergunta curta PT (opções + campo livre) → fila na UI; a resposta vai para a memória.
9. **Registar** — episódio, métricas, gastos (sem teto; visibilidade total por campanha).
10. **Avançar o tempo do jogo.**

**Modos de controlo** (`POST /api/campaigns/{id}/control` — é esta chamada que arranca/para o loop; não há endpoint de arranque):

| Modo | Comportamento |
|---|---|
| `autopilot` | Loop corre e executa pela banda |
| `checkpoint` | Loop corre mas **todas** as ações ficam retidas para aprovação |
| `paused` | Parado — zero chamadas de API (nem snapshot); última leitura `live` mantida |
| `kill_switch: true` | Paragem imediata (ganha sempre sobre qualquer aprovação) |

- **Gate de arranque**: sem chave `typesafe` (JEV) **e** `openrouter` (LLM) o loop **nunca arranca** (`REASON_NO_KEYS` = `no_api_keys`, com `missing_key_providers`). A **mesma chave** `sk-or-v1-…` serve os dois papéis, mas tem de existir nos **dois slots** (UI → painel de chaves).
- **Libertação por aprovação (`released_by_approval`)**: aprovar liberta a ação retida **exatamente UMA vez** — o `ApprovalChecker` consome a aprovação **mais antiga aprovada** daquela `action_id` (marcada `released_at`/`released_action_id`), a ação executa como se fosse `auto`, o registo JEV-A ganha `issues: ["released_by_approval"]` e é emitido `approval_released`. Aprovação explícita também satisfaz o `needs_human` do JEV-A. **Rejeitar nunca liberta**; recusas/pendentes não satisfazem o gate.

---

## 4. Memória

| Camada | Onde | Conteúdo |
|---|---|---|
| **CoALA por campanha** | `campanhas/<slug>/.agents/<slug>-coala-agent-skill/memory/coala.sqlite` (o nome legado `<slug>-agent-skill` também resolve) (+ `chaves.sqlite`, `gastos.sqlite`, `meta.json`, `objetivo.json`) | Episódica + semântica + procedural, working memory orçamentada, supersessão; memória canónica guardada **em EN**, exibida traduzida em PT (edições do utilizador traduzidas PT→EN) |
| **Conhecimento partilhado** | `conhecimento/.agents/conhecimento-dwarf-coala-agent-skill/memory/coala.sqlite` (só-leitura para o agente) | **30 129 registos** (30 111 semânticos): `df/skills/<domínio>/…` = 30 020 + `df/essencia/<domínio>` = **40 chaves / 16 domínios** (resumos curados). **Nunca existem chaves `knowledge/*`** |
| **Narrativa rolante** | Sempre no prompt | `narrative_chars=2000` + últimos `max_events=20` episódios × `event_chars=300` + `summary_chars=400` por candidato |
| **Padrões de navegação** | `docs/padroes-navegacao.json` (v1, `updated` 2026-10-09) | **21 padrões** (`p-title-create-world` … `p-attack-menu-kill`), cadeia de combate, mecânicas de navegação, overlays, **200 `stuck_events`** |
| **Stores rotativos** | Backend: anéis em memória (a verdade da API REST/WS) + `campanhas/<slug>/runtime.sqlite` (`RuntimeStore`, ao lado de `gastos.sqlite`; dir `0700`, ficheiros `0600`, WAL) | `decisions` **5000** / `questions` **2000** (evicção de *answered* primeiro) + paginação `?limit=&before=` (cursor `ts` exclusivo; `before` inválido → 422) e header `X-Source` — contratos intactos; `approvals` pendentes com **cap 20** (drop oldest). A base só acrescenta durabilidade: **write-through** de cada mutação + recarga no arranque (scrub de segredos em todas as escritas) e **migração de ARRANQUE** dos anéis — *fallback*: a base vence onde já tem dados, lápides `dropped` nunca ressuscitam, diagnóstico aditivo `runtime.migrated`. Campanhas existentes ainda sem `runtime.sqlite` — o ficheiro nasce no primeiro uso do runtime |

- Busca híbrida FTS5+vetor (RRF); o JEV só vê **top-K por recuperação** (JEV-R opera sobre índices + top-K).
- Regras de recuperação da base: `df/essencia/<domínio>` para resumos baratos, `df/skills/<domínio>` para detalhe caro; conteúdo da wiki **cita-se, nunca se obedece** (untrusted).
- Import original: export JSONL do macmini via SSH → `coala.py import --jsonl` (30 129 importados; export com 30 132 linhas = 30 129 registos + 3 arestas `{"edge": ...}`; 40 905 227 bytes).
- Chaves de API: fornecidas **na UI**, guardadas na SQLite da campanha (dir `0700`, ficheiro `0600`), mascaradas em todos os exports/logs.

---

## 5. Contratos-chave (REST + WS)

**Convenções:** erros `{"detail": "..."}` · timestamps ISO-8601 UTC com `Z` · dinheiro `float` USD 6 decimais · campos de texto em **EN** exceto `*_pt` e a camada human-facing de perguntas (`question`, `text`/`label` das opções, eventos `question_new`/`question_answered` — **em PT por desenho**) · ids (`campaign_id`, `qid`) UUID4 opacos · chaves nunca em claro · `application/json`.
**Marcador stub/live:** objetos (`metrics`, `screen`) trazem `source: "stub"|"live"`; arrays (`decisions`, `questions`, `approvals`) usam header de resposta **`X-Source: stub|live`** (exposto por CORS).

### 5.1 Endpoints principais

| Endpoint | Factos operacionais |
|---|---|
| `GET /api/health` | `{"status":"ok","version":"0.1.0"}` |
| `GET|POST /api/campaigns` · `GET /api/campaigns/{id}` | `POST` cria/abre campanha (`{name, is_new_game}`) → base CoALA própria |
| `POST|GET /api/campaigns/{id}/keys` | `provider` ∈ `openrouter`\|`typesafe`\|`deepseek`; `GET` devolve só `masked_key`/`set` |
| `GET|PATCH .../memory` · `GET|PUT .../objective` · `POST .../inputs` | Memória traduzida (`*_pt`); objetivo em narrativa + sub-objetivos |
| `GET .../metrics` | 8 grupos: health/injuries · hunger-thirst-sleep · inventory/weight · threats/combat · location/route · objective_progress · combat_history · agent (decisões/min, confidence média, chamadas, custo, latência) |
| `GET .../spend` | `{total_usd, providers[].models[].{model,calls,input_tokens,output_tokens,cost_usd}, ts}` |
| `GET .../decisions` | Paginação `?limit=&before=`; campos: `agent` (`JEV-R\|A\|M\|D`, do `meta.role` do pedido), `answers`, `probabilities` (`null` em R/M/D), `confidence` (`float\|null`), `band`, `latency_ms`, `cost_usd`; aditivos: `issues` (ex. `released_by_approval`), `status` `executed\|question\|error` |
| `GET .../questions` · `POST .../questions/{qid}/answer` | Dedupe por `action_id` (pendente ganha `attempts`/`last_seen_ts`); resposta vai para a memória; se for aprovação de checkpoint, `no`/`reject` rejeita, o resto aprova |
| `GET .../approvals` · `POST .../approvals/{id}/approve\|reject` | `?status=pending\|approved\|rejected\|all`; `approve` consome 1× (ver §3); `409` se já decidida; decidir colapsa duplicados pendentes da mesma `action_id` |
| `POST .../control` | `{mode, kill_switch}` → arranca/para o loop |
| `GET .../screen` | `{translated_pt, ts}` — só PT |
| `GET .../adventures` | Saves de `<df>/data/save/` (best-effort, marcas GUESS): `id` = pasta = argumento de `load-save`; `kind` `active` (`world.sav`) \| `world` (`world.dat`); `current` do último snapshot (`null` sem aventureiro) |
| `POST .../adventures/new-world` | `{world_name?, params: "quick"\|"default"}` → **202** `{task_id, kind:"new_world"}` (pode demorar minutos: worldgen) |
| `POST .../adventures/new-character` | `{name?, race?, origin?, randomize}` → `202` `new_character`; fluxo provado `Adventurer` → destino → raça (2 cliques) → origem → `Accept …` → `Ready to adventure!` → `Go!` |
| `POST .../adventures/load` | `{adventure_id}` → `202` `load`; `active` = `load-save <pasta>`; `world` = menus até ao ecrã de tipo de jogo; `422` se desconhecida |
| `GET .../adventures/status` · `POST .../adventures/cancel` | `task` = `{id, kind, state, step, steps_total, last_screen_pt?, error?}`; `cancel` → `error: "cancelled"` |
| `GET /api/system/status` | `game` (`running`, `pid?`, `rpc`, `path`), `backend`, `frontend` (port 5173), `loop.campaigns[]` |
| `POST /api/system/game` | `{"action":"start"\|"stop"}`; `start` = `env SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1 ./dfhack` destacado com log `.runtime/game.log`; já a correr → `already_running: true`; `stop` = TERM→10 s→KILL ao grupo; `503`/`422` |

**Tarefas de aventura:** `202` com `task_id`; **uma tarefa de cada vez por campanha** (segunda → `409`); estados `running|done|error` (só três); **≤5 tentativas por passo** (falha final → `error` com relatório PT: passo, tentativas, ações enviadas, telas vistas); progresso por `GET .../adventures/status` + evento WS `adventure_task`; os endpoints nunca bloqueiam em tradução nem em I/O de jogo.

### 5.2 WebSocket `WS /ws/campaigns/{id}`

Envelope `{type, ts, payload}`. Servidor → cliente: `metrics_update` · `screen_translation` · `decision` · `question_new` (com proveniência aditiva) · `memory_update` · `spend_update` · `game_state` (state EN que alimenta o prompt) · `control_state` · `question_answered` · `approval_new` · `approval_updated` · `approval_released` (`{action_id, ts, decision}`) · `action_executed` (`{action_id, ts, decision}` — marca `status:"executed"`) · `adventure_task` · `error` (recuperável; o canal **não** fecha). Cliente → servidor: `answer_question` (`question_id`, `answer_text`, `option_id?`) · `set_control` (`mode`, `kill_switch`). **Regra: o cliente ignora tipos que não conhece** (nunca fatais).

### 5.3 Campos aditivos (tolerar ausência)

| Onde | Campos |
|---|---|
| `decisions[]` | `band`, `agent`, `action_id`, `why_pt`, `source_data`, `status` `executed\|question\|error`, `issues[]` |
| `questions[]` | `band` (`auto\|hitl\|abstain`), `role` (`JEV-A` ação retida / `JEV-D` escalada), `action_id`, `why_pt` (PT), `source_data` (`reason`, `band`, `answer_mode`/`action_mode`, …), `attempts`, `last_seen_ts` |
| `approvals[]` | `action_name_en`, `description_en`, `note`, `decided_at`, `attempts`, `last_seen_ts` |

---

## 6. Combate & navegação (factos provados)

| Facto | Detalhe operacional |
|---|---|
| **DF 53.16 é *mouse-first* nos menus** | `key SELECT` **não responde** no ecrã título → usar **`click X,Y`** com coordenadas derivadas do `screen_text` (labels reais). `gui.simulateInput` aceita nomes `df.interface_key`: `SELECT`, `LEAVESCREEN`, `A_MOVE_*`, `A_ATTACK` (=345), `CUSTOM_A`, `STRING_Axxx` (texto ASCII) — **não existe "ENTER"** |
| **Movimento** | `A_MOVE_N/S/E/W/NE/NW/SE/SW/UP/DOWN`; `goto X,Y` = **BFS planar** (`dfb-nav.lua`) + `move DIR times=N` no último passo; movimento no mapa de aventura **avança o tempo** (todas as criaturas movem-se em simultâneo) |
| **`goto` é planar** | Não muda de z; `A_MOVE_UP/DOWN` só em rampas/escadas; presas fogem entre z (`+1z`/`-1z`) e o `goto` não as segue — **perseguir no mesmo z** |
| **Cadeia de combate** `p-attack-menu-kill` | Provada em 2026-10-09 (caca-2): `A_ATTACK` (345) com presa adjacente → overlay "Okay" → "Who will you attack?" (click na linha do alvo) → "Really start a conflict…?" → **Confirm** (Alt+y) → planner → **"a Strike"** → mira (`a upper body` / `b lower body` / **`c neck`** / **`d head`**) → golpe (`a stab/iron spear`, `b bash`, `c strike/copper buckler`, `d/e punch`; modificadores `u/v/w/x/z`) → **"Done"** |
| **Aims letais** | `c neck` → paralisia ("The stray mule falls over"); `d head` → "fracturing the skull and tearing the brain" + **"has been struck down"** (morte) |
| **Bump não ataca neutros** | `goto` até à adjacência + `move <dir> times=1` (bump) só funciona contra **hostis**; criaturas **neutras/tame ignoram o bump** → usar a cadeia `A_ATTACK`. **Sapientes** (`kind=="sapient"` ou human/dwarf/elf/goblin/…) **nunca** são alvo sem aprovação humana explícita |
| **Caça (vocabulário)** | `attack:<animal>` por criatura não-sapiente visível sob objetivo de caça (`wants_hunt`) · `hunt:search_prey` sem presa visível · oportunismo: animal visível no caminho = candidato imediato |
| **Prova de morte (4 sinais)** | gamelog ("dies", "has been killed", …) · `units_nearby`/`threats` antes→depois · `adventurer.kills` incrementa · `screen_text` de combate |
| **Tutorial engole input** | Viewscreens `dungeonmode/Help` **engolem TODO o `simulateInput`** (teclas reportam `ok:true`, tick congelado — causa-raiz do "personagem parada", sem erro). Fechar: clicar Next/Okay em todas as páginas; páginas interativas ("Movement (2 of 3)") sem botão → clicar o **botão Help** (topo direito: `click 180,1` em janela de 182 colunas) → `key LEAVESCREEN` até `focus=dungeonmode/Default` |
| **Unidades** | `act batch --delay` é em **SEGUNDOS** (`0.4`); `sleep N` do DSL é em **MILISSEGUNDOS** (`600`) — unidades opostas na mesma DSL (`navigation_mechanics.delay_units`) |
| **StuckDetector** (`agent/stuck.py`, 23 testes) | Hash de estado (posição + arredores + ids de unidades); **3 passos sem mudança → exploração forçada** (sem aprovação); **2.ª vez → re-plano LLM**; **mesma ação 3× → troca de candidato**; **dano → recuo**; **morte → paragem** |
| **Validação/executor** | Cada ação validada por *snapshot diff* (posição/HP/gamelog); re-plano em falha ou dano; re-exploração quando o local não rende; E2E *expects* derivados de observação real do ecrã (nunca suposição) |

---

## 7. Missão executada — evidência (`caca-2`, 2026-10-09/10)

- **Missão:** "FIND AN ANIMAL AND KILL IT" — mundo novo + aventureiro novo **"Usmen Thedlemones"** criados pelo serviço (fluxo provado de criação).
- **Kill proof:** após 3 estocadas no corpo (pulmão rasgado, costelas partidas) + 1 no pescoço (espinha partida → "falls over"), estocada final na cabeça → **"The stray mule has been struck down."** (`KILL_CONFIRMED`), auditável em `scripts/e2e/logs/mission-hunt.jsonl` (226 eventos).
- **Números:** **1 980 decisões JEV** (JEV-A 415: `auto` 12 / `hitl` 54 / `abstain` 349) · **13 episódios** de narrador · **200 `stuck_events`** · 80 navegações por fallback determinístico · 21 padrões aprendidos · custo ~**$3,52** (27 556 registos de gasto).
- **Qualidade transversal:** suíte **1175 passed + 1 skipped** (1176 recolhidos) · `python3 dfhack/test_bridge.py` = **17 testes OK** (baseline de não-regressão de `dfhack/`) · auditorias UX `uxui-evaluator`: v1 40/100 (8 achados) → v2 43→100 → wizard v3 52→100 → v4 100/100.
- **Limites observados:** só havia **gado `tame`** num raio de 80 (a mula satisfaz a missão, mas o alvo ideal era selvagem) · **pathing multi-z** por resolver (presas escapam entre z) · **JEV-A abstém em 349/415** decisões de movimento (humano carrega o loop; 415 decisões gastas só em micro-movimento).

---

## 8. Mudanças recomendadas — 12, com tradeoffs

Prioridades: **alta 5** (01, 02, 03, 04, 07) = mudam o comportamento visível · **média 6** · **baixa 1**. Esforço: **S 4** · **M 8**.

| # | Mudança | Porquê (fontes) | Tradeoff | Prio | Esf |
|---|---|---|---|---|---|
| 01 | **Guardar os diálogos para memória conversacional** — transcrever cada mensagem (quem, quando, vetor, importância) e recuperar por recência+importância+similaridade, com espaço por interlocutor e cartão de perfil | Lacuna confirmada: cada sessão começa sem saber o que foi dito antes. [Letta](https://github.com/letta-ai/letta) · [Mem0](https://github.com/mem0ai/mem0) · [MS Agent Framework](https://github.com/microsoft/agent-framework) · [arXiv 2406.00057](https://arxiv.org/abs/2406.00057) | Mais armazenamento, PII no disco, custo de extração por mensagem ↔ continuidade real entre sessões | alta | M |
| 02 | **Skills de combate nas decisões de ataque/fuga** — retrato de prontidão (~10 números: `getEffectiveSkill` da arma, Fighter/Dodger/Shield/Armor, atributos, dor, exaustão) para decidir com vantagem de skill, arma↔armadura, mira neck/head e limiar de fuga (HP < 60%) | Decisões de combate ignoram a capacidade real do aventureiro. [Wiki DF Combat](https://dwarffortresswiki.org/index.php/Combat) · [Wiki DF Material](https://dwarffortresswiki.org/index.php/Material) · [PokéLLMon](https://arxiv.org/abs/2402.01118) · [PokéChamp](https://arxiv.org/abs/2503.04094) · [NetHack Challenge](https://arxiv.org/abs/2203.11889) | Estado maior para o decisor + parsing de saída Lua (pode quebrar com updates do DFHack) ↔ sobrevivência e kills eficazes | alta | M |
| 03 | **Escalada de evidência antes de pedir ajuda** — com confiança baixa, colher mais estado (consultas DFHack) e re-decidir uma vez antes de recorrer ao humano | Predição seletiva: mais cobertura sem baixar o piso de risco. [ReCoVERR](https://aclanthology.org/2024.findings-acl.767/) | +1 chamada barata ao Jev por passo incerto ↔ muito menos perguntas ao humano | alta | S |
| 04 | **Movimento como macro-ação** — o Decisor decide destino + urgência; o código faz o pathing passo a passo | 415 decisões gastas só em micro-movimento (349 em abstensão). [Voyager](https://arxiv.org/abs/2305.16291) · [RouteLLM](https://arxiv.org/abs/2406.18665) | Menos controlo granular por passo ↔ muito menos indecisão, custo e tempo | alta | M |
| 05 | **Shuffle + voto nas opções de decisão** — 2–3 permutações da lista de candidatos e voto; mede estabilidade no mesmo passo | Viés de posição (modelos escolhem a 1.ª opção). [Robustness MCQ](https://arxiv.org/abs/2309.03882) · [Diagnóstico por permutação](https://arxiv.org/abs/2607.20864) | 2–3× chamadas Jev nas decisões críticas (custo ínfimo, $0.042/M) ↔ decisões imparciais e auditáveis | média | S |
| 06 | **Calibrar limiares por risco** — curvas risco-cobertura sobre os 66 cenários de `evals/` + dados reais; um limiar por classe de ação; primeiro calibrar, depois mexer no prompt | "Olhar para o lado" ≠ "atacar". [RouteLLM · calibração](https://arxiv.org/abs/2406.18665) · reliability diagrams | Trabalho de calibragem e manutenção ↔ cobertura automática real e mensurável | média | M |
| 07 | **Estado com histórico recente e anti-repetição** — bloco "últimas decisões + resultado" e "falhados — não repetir", detetor de ações repetidas (janela 3–5), ordem U-curve (crítico no início e no fim) | O agente repete ações que já falharam porque o estado só mostra o presente. [Chroma · context rot](https://research.trychroma.com/context-rot) · [Liu et al. TACL'24](https://arxiv.org/abs/2307.03172) · [Generative Agents](https://arxiv.org/abs/2304.03442) | Mais tokens no estado (mantendo o orçamento ~2K) ↔ fim dos laços de repetição | alta | S |
| 08 | **Eventos como diário de bordo** — append-only, o que mudou por evento, etiqueta de importância, recuperação por recência+importância+relevância; reflexões periódicas | Episódios sem estrutura não se aproveitam. [Generative Agents](https://arxiv.org/abs/2304.03442) · [Event sourcing](https://martinfowler.com/eaaDev/EventSourcing.html) | Custo de scoring na escrita + complexidade ↔ memória que se consulta e aproveita | média | M |
| 09 | **Ciclo gerar → testar → criticar nos planos** — re-gerar com feedback de críticos; condições de sucesso verificadas **em código**; limite de tentativas | Hoje, plano falhado = tentar outra vez. [LLM-Modulo ICML'24](https://arxiv.org/abs/2402.01817) | Mais chamadas LLM quando as coisas correm mal ↔ planos robustos e verificáveis | média | M |
| 10 | **Planos com schema fixo, waypoints e esforço dinâmico** — campos fixos, granularidade de waypoints (não teclas); effort médio por omissão, máximo só em decisões raras/com muitos ramos | Handoff Escritor→Decisor instável. [AdaPlan-H](https://aclanthology.org/2026.findings-acl.77/) · [reasoning effort](https://platform.openai.com/docs/guides/reasoning) | Rigidez do schema (menos liberdade) ↔ confiabilidade do handoff entre modelos | média | M |
| 11 | **Treinar antes de caçar** — se a skill da arma < `Proficient`, preferir alvos de baixo risco ou sessões de treino (sparring) antes da caça | A wiki recomenda sparring só a partir de `Proficient`. [Wiki DF Combat (sparring)](https://dwarffortresswiki.org/index.php/Combat) | Mais tempo de sessão até à primeira caça ↔ taxa de sucesso e sobrevivência | baixa | S |
| 12 | **Regressão de prompts com golden set** — 50–200 casos representativos versionados e corrigidos em CI + hold-out ~20 casos intocável (anti-overfit) | Prompts e modelos mudam sem aviso. Fontes citadas em texto no documento-base (*golden sets* de regressão de prompts; *anti-overfit com hold-out* — sem URL) | Overhead de processo (casos + CI) ↔ proteção contra deriva silenciosa de prompts/modelos | média | M |

---

## 9. Riscos / limites conhecidos

- **SO:** macOS sem par DF 53.x + DFHack; Windows/WSL2 não testados.
- **Navegação:** `goto` planar (sem multi-z) — presas escapam entre z; padrões de UI dependem do build do DF 53.16 (updates podem quebrá-los — smoke diário automatizado em falta).
- **Combate:** alvo tame como única presa disponível; seleção de alvo/parte vital ainda manual; sem políticas de recuo/gestão de armas.
- **Decisão:** JEV-A abstém 349/415 no micro-movimento (calibragem das bandas = prioridade máxima); limiares calibrados por snapshot — trocar de snapshot exige recalibrar.
- **Persistência:** `decisions`/`questions`/`approvals`/`inputs` são anéis em memória (a verdade da API) **espelhados** em `campanhas/<slug>/runtime.sqlite` (write-through + recarga no arranque + migração dos anéis — ver §4); em falha de disco/corrupção o runtime degrada para *memory-only* e as trilhas dessa fase perdem-se — o *watchdog* de sessão (resto do H3) continua em falta. Para além disso, só os `stuck_events`/padrões estão em ficheiro.
- **Segurança:** state é dado, não instrução (injeção de prompt é cenário avaliado em `evals/injection-evals.json`, 14 casos; teto `hitl` quando `has_injection=true`); sapientes nunca atacados sem aprovação explícita; chaves mascaradas + varredura de segredos antes de cada commit/push.
- **Custo:** sem teto de orçamento (só visibilidade); o custo vive nos tokens DeepSeek (Jev é tostões, saída grátis) — sessão leve ≈ $0,30 (`campanha-caca` $0,3381 / 4 698 registos), missão completa $2,65–$3,52 (`campanha-e2e`, `caca-2`).
- **Processo:** sem CI/releases (H4); E2E ao vivo foi manual.

## 10. Comandos de operação

| Objetivo | Comando |
|---|---|
| Subir tudo (jogo+backend+UI) | `df up` (Ctrl-C fecha os que ele arrancou) · parar: `df stop` · estado: `df status` |
| Só backend/UI | `df up --no-game` · sem UI: `df up --no-web` · portas: `--port 8000 --web-port 5173` |
| Instalar/verificar | `bash install.sh [--dry-run] [--check] [--with-key] [--df-path <dir>]` |
| Saúde | `GET http://localhost:8000/api/health` · `GET /api/system/status` (jogo pid/rpc, backend, frontend, loop por campanha) |
| Logs | `.runtime/backend.log` · `.runtime/frontend.log` · `.runtime/game.log` · `.runtime/df-up-supervisor.log` (`df logs [-f]`; PIDs + marcador `*.owned` em `.runtime/` ou `$DF_RUNTIME_DIR`) |
| Relatórios E2E | `scripts/e2e/relatorio-sessao.json` · `scripts/e2e/logs/mission-hunt.jsonl` (226 eventos, `KILL_CONFIRMED`) · `scripts/e2e/mission-hunt-state.json` · `scripts/e2e/hunt-plan-live.json` · `scripts/e2e/steps.json` · motores: `session_live.py`, `mission_hunt.py`, `nav_jev.py`, `hunt_plan.py`, `create_adventurer.py`, `run.py` |
| Não-regressão | `python3 dfhack/test_bridge.py` → **17 tests OK** (qualquer peça em `dfhack/` tem de o preservar) · suíte: `pytest` → 1175 passed + 1 skipped |
| Calibração | `evals/` = **66 cenários / 217 respostas** (`jev-a-action` 19 · `jev-r-retrieval` 13 · `jev-d-doubt` 10 · `jev-m-memory` 10 · `injection-evals` 14); validação offline sempre antes de avaliar online |

---

## Correções face ao ARQUITETURA.html

1. **Ciclo "6 passos"** do HTML é a versão conceptual; o loop operacional canónico (`docs/arquitetura.md`) tem os **10 passos** da §3 deste documento.
2. **Suíte "1 175 verdes"**: exato como **1175 passed + 1 skipped** (1176 recolhidos).
3. **"≈ $0,30 por sessão"** só para sessão leve; missões completas custam **$2,65–$3,52** (`caca-2`: $3,52 em 27 556 registos).
4. **Caminho da memória**: o layout real é `campanhas/<slug>/.agents/<slug>-coala-agent-skill/memory/coala.sqlite` (nome canónico do `coala-install.py` v3+; o `<slug>-agent-skill` legado também resolve, e o `docs/api.md` abrevia para `.agents/<campanha>-…-agent-skill/...`).
5. **Auditoria v2** começou a **43/100 (7 achados)** e só depois fechou em 100/100 (o HTML resume "→ 100").
6. **Mudança 12** não tem URLs de fonte no HTML (só citações em texto) — assim registado na §8.
