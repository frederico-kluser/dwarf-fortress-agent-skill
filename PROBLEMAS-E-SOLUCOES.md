# System One Dwarf — Problemas, Soluções e Otimizações

Retrospetiva do projeto (MVP e missão de caça ao vivo, 2026-10-08 → 2026-10-10). Cada entrada segue
o formato **Problema → Causa → Solução → Lição**. Números, commits e ficheiros citados são
verificáveis nos artefactos do repo (`git log`, `docs/`, `docs/padroes-navegacao.json`,
`scripts/e2e/logs/mission-hunt.jsonl`, `evals/`). As correções face ao relatório da sessão estão em
[`## Correções e verificação`](#correções-e-verificação).

## Sumário executivo

- **O que é:** agente autónomo para Dwarf Fortress 53.16 (adventure mode) + DFHack 53.16-r2. Um
  modelo Jev decide (tipado, calibrado, em milissegundos), o DeepSeek escreve (planos, perguntas,
  narrativa, tradução) e código executa (DFHack DSL), com supervisão humana por aprovação.
- **Prova de missão:** mula abatida ao vivo — `KILL_CONFIRMED` com gamelog
  "The stray mule has been struck down." em `scripts/e2e/logs/mission-hunt.jsonl`
  (campanha `caca-2`, 2026-10-09/10).
- **Números da corrida `caca-2`:** 21 padrões de navegação comprovados · 1 980 decisões JEV
  (JEV-A: 415 — auto 12 / hitl 54 / abstain 349) · 13 episódios de narrador · 200 eventos de
  anti-congelamento · custo ~$3,52 (27 556 registos de gasto).
- **Qualidade:** suíte **1175 passed + 1 skipped** · UI auditada por `uxui-evaluator`:
  v1 40/100 → v2 100/100 → wizard v3 52/100 → v3 100/100 → v4 100/100 (Motion UI).
- **Os problemas mais caros não foram de código:** foram de *ambiente* (jogo sem som/sem ecrã,
  macOS sem par DF 53.x + DFHack), de *input engolido por overlays* (personagem "congelada") e de
  *fila de aprovações* com 391 pedidos duplicados da mesma ação.

---

## A. Arquitetura

### A1 · Jev não é um LLM — arquitetura híbrida
- **Problema:** precisávamos de decisões discretas de alto volume (mover, atacar, perguntar) com
  custo baixo e latência baixa; um LLM generativo devolve texto e alucina ações fora do vocabulário.
- **Causa:** confundir "modelo de linguagem" com "decisor". O modelo Jev (TypeSafe) devolve
  **decisões tipadas com probabilidades calibradas** — não gera texto.
- **Solução:** arquitetura híbrida em três camadas: **Jev decide** (frota JEV-R/A/M/D),
  **DeepSeek escreve** (narrador, planos de caça, perguntas ao humano, tradução PT↔EN) e
  **código executa** (DFHack DSL). O prompt do Jev é **em inglês** (precisão tipada), a UI é em PT
  e os *inputs* são traduzidos antes de entrar nos prompts.
- **Lição:** escolhe o modelo pela tarefa: decisor calibrado para ações, gerador para linguagem,
  código para efeitos. Nada de "tudo num LLM".
- **Evidência:** `docs/arquitetura.md` (papéis JEV-R/A/M/D, §prompts em EN), `docs/openrouter.md`
  (`typesafe/jev-1.13` para decisões; `deepseek/deepseek-v4.1-flash` para texto), commit `bb16e07`.

### A2 · Frota de JEVs especializados e bandas de ação
- **Problema:** prompts grandes com todo o contexto degradam-se (*context rot*) e perdem calibração.
- **Causa:** um único prompt multi-tarefa acumula instruções contraditórias e estado demais.
- **Solução:** frota de 4 JEVs com prompts pequenos e especializados (R recuperação · A ação ·
  M memória/meta · D dúvida), chamadas paralelas, estado compacto em EN. Bandas de ação por
  confiança: **`auto` ≥ 0.90 · `hitl` 0.50–0.89 · `abstain` < 0.50**; **confiança ausente ou `0`
  limita a `hitl` — nunca `auto`**. O caminho humano→execução é a **libertação por aprovação**
  (`released_by_approval`).
- **Lição:** a segurança tem de viver no contrato (bandas + falha por omissão), não na boa vontade
  do modelo. Menos contexto por chamada = mais calibração e paralelismo.
- **Evidência:** `docs/arquitetura.md` §bandas, `src/system_one_dwarf/jev/bands.py`, `docs/calibracao.md`.

### A3 · Memória CoALA por campanha + base de conhecimento partilhada
- **Problema:** o agente precisa de memória durável (o que aprendeu, o que decidiu, como se faz)
  sem misturar estado volátil com conhecimento de referência.
- **Causa:** um único "grande contexto" mistura episódios, factos e wiki, e não sobrevive a sessões.
- **Solução:** memória CoALA **por campanha** (`campanhas/<nome>/` — chaves, gastos, objetivo) +
  base de conhecimento partilhada em `conhecimento/`: **30 129 registos** (30 111 semânticos),
  chaves `df/skills/<dominio>/…` (referências da wiki) e `df/essencia/<dominio>` (destilações) —
  **não existem chaves `knowledge/*`**; o episódico vive na campanha e na memória CoALA do projeto.
- **Lição:** separa *episódico* (quando/porquê), *semântico* (o que é verdade) e *procedimental*
  (como se faz) com chaves estáveis; guarda em EN, exibe em PT.

---

## B. Ambiente

### B4 · DFHack não estava instalado
- **Problema:** sem DFHack não há RPC nem DSL — o agente não lê estado nem executa ações.
- **Causa:** máquina nova, jogo só com o binário vanilla.
- **Solução:** tarball **DFHack 53.16-r2** instalada manualmente sobre o DF 53.16 com **sha256
  verificado antes de extrair** (`sha256sum -c` → SUCESSO).
- **Lição:** qualquer binário baixado leva verificação de hash registada no guia
  (`docs/setup-local.md`, `docs/setup.md`, commit `6995ca0`).

### B5 · Ecrã preto + som no Linux (jogo Steam)
- **Problema:** o DF 53.16 Steam não pintava ecrã (renderização GPU) e o áudio bloqueava/cantava.
- **Causa:** driver de vídeo GL problemático e dispositivo de áudio ocupado; a versão Steam
  acrescenta camadas (overlay, áudio) que não controlamos.
- **Solução:** decisão de usar a **versão NÃO-Steam** (binário livre `df_53_16_linux` da bay12) +
  **som desligado em 3 camadas**: `SDL_AUDIODRIVER=dummy` (confirmado em
  `/proc/$(pgrep -x dwarfort)/environ`) · `[SOUND:NO]` no init · todas as chaves de volume a `0` +
  **software rendering** (`SDL_VIDEODRIVER=x11`, `LIBGL_ALWAYS_SOFTWARE=1`).
- **Lição:** para um agente que lê estado por Lua/RPC, "ecrã preto aceitável" é uma *feature* —
  mas o áudio tem de morrer em camadas redundantes, cada uma verificável.
- **Evidência:** `docs/setup-local.md` (§som, §ecrã preto), commit `8b77df6`.

### B6 · macOS não tem par DF 53.x + DFHack — caminho Linux
- **Problema:** o alvo original era macOS (Mac mini M1).
- **Causa:** o DF Classic macOS e o DFHack macOS terminam ambos em **0.47.05**; não existe par 53.x.
  O par 0.47.05 carrega, mas o **servidor RPC nunca arranca** (o stack usa `df-structures` 53.16).
- **Solução:** caminho **Linux** (CachyOS). Foi construída uma rota de VM (Lima + `vz` + Rosetta) e
  depois **ABORTADA por ordem do utilizador** (sem emuladores) — limpeza exemplar: só os artefactos
  nossos foram removidos, a VM de terceiros ficou intocada.
- **Lição:** valida a matriz SO×versão **antes** de investir em infra; quando abortas uma rota,
  limpa só o que é teu e documenta a decisão. A matriz fica em `install.sh --help` e no README
  (Linux ✅ · macOS ❌ inexistente par 53.x · Windows ⚠️ não testado · WSL2 ⚠️ experimental).

### B7 · Armadilhas de shell (pgrep/pkill/SIGTERM)
- **Problema:** deteção e paragem do jogo davam falsos positivos/negativos e deixavam processos órfãos.
- **Causa:** (a) o padrão `dwar[s]fort` **nunca casa** com `dwarfort` (a classe não se aplica a si
  própria) → falsos zeros no `pgrep`; (b) `pkill` com um padrão presente na própria *cmdline* mata o
  shell; (c) **`dwarfort` ignora SIGTERM**.
- **Solução:** `pgrep -x dwarfort` (ou `[d]warfort`); separar *kill*/*start* com padrões
  anti-self-match (classe de carateres na *cmdline*); **paragem por grupo de processos**
  (`pgrep -g`) com escalão **TERM → KILL** — a sobrevivência é medida pelo grupo, não pelo líder.
- **Lição:** padrões de auto-correspondência enganam sempre; processos que ignoram SIGTERM exigem
  kill escalonado por grupo. Ver `scripts/df.sh` (`game_running`, `stragglers`, bloco de paragem).

### B8 · Bridge: auto-detecção erra no Steam; override sem editar código
- **Problema:** a bridge não encontrava o DF ou apontava para o diretório errado.
- **Causa:** a auto-detecção por bibliotecas Steam aponta para o dir do jogo **Steam**, que não é o
  binário que corremos (versão não-Steam).
- **Solução:** precedência explícita **flag `--df-path` → env `DF_PATH` → auto-detecção**; o backend
  arranca sempre com `DF_PATH` definido. Nada de forks: configuração resolve.
- **Lição:** *seams* de configuração (flag > env > auto) evitam patches locais em código de terceiros.
- **Evidência:** `dfhack/df_bridge.py` (`find_df_path`).

---

## C. Loop e execução

### C9 · Gate `no_api_keys` exige dois providers
- **Problema:** o loop recusava-se a arrancar "sem chaves" mesmo com uma chave inserida.
- **Causa:** o gate exige chave para **ambos** os providers em uso — `typesafe` (decisões Jev) e
  `openrouter` (DeepSeek). A **mesma chave** serve os dois papéis, mas tem de existir nos dois slots.
- **Solução:** inserir a chave nos dois providers (UI → painel de chaves); o erro lista os providers
  em falta (`missing_key_providers`).
- **Lição:** gates de arranque devem dizer *o que* falta, não só "sem chaves"; documentar que uma
  chave preenche dois papéis evita tickets.
- **Evidência:** `src/system_one_dwarf/app/loop_runner.py` (`REASON_NO_KEYS`, `missing_key_providers`).

### C10 · `metrics` 500 determinístico — nulls do contrato
- **Problema:** `GET .../metrics` devolvia 500 sempre (visível no relatório E2E).
- **Causa:** `ResponseValidationError`: campos como `weight_kg` eram `float` obrigatório, mas o
  snapshot tem **nulls legítimos** (sem inventário, sem relógio) — o contrato JSON tinha `null` e o
  schema Python não.
- **Solução:** campos opcionais (`float | None`) em tudo o que vem do snapshot; validação
  contrato↔schema alinhada.
- **Lição:** **nulls explícitos do contrato têm de ser refletidos nos schemas** — um `null` legítimo
  não é um erro de dados. Teste de regressão cobre o caso.
- **Evidência:** `src/system_one_dwarf/app/schemas.py:175-201`, commits `6073701`, `8f654bb`.

### C11 · Espelho de aprovações partido (skew de versão)
- **Problema:** aprovações libertadas não chegavam às perguntas: `answer_mode`/`source_data` não se
  propagavam e ações ficavam presas.
- **Causa:** o espelho question↔approval não era idempotente por ação e havia um **backend antigo em
  execução** (skew de versão entre código novo e processo velho).
- **Solução:** espelho **por `action_id`** (idempotente, re-pedido atualiza a entrada) +
  `ApprovalChecker` que **consome UMA aprovação por libertação** (`released_by_approval`); reinício
  do backend para matar o skew.
- **Lição:** um espelho entre duas filas precisa de identidade estável (a *ação*, não o pedido) e de
  consumo unitário; "está a estranhar? confirma que o processo em execução é o código que estás a ler".
- **Evidência:** `src/system_one_dwarf/app/runtime.py`, `tests/` (fluxo de aprovação), commit `6073701`.

### C12 · Flood de aprovações: 391 pendentes da mesma ação
- **Problema:** a fila humana tinha 391+ aprovações pendentes — 1 por passo do loop, todas da mesma
  macro-ação.
- **Causa:** cada passo de checkpoint re-emitiu a pergunta/aprovação sem dedupe; a fila crescia a
  cada iteração e enterrava o humano.
- **Solução:** **dedupe por `action_id`** (re-pedido atualiza a entrada pendente com
  `attempts`/`last_seen_ts` aditivos) + **cap de 20** na fila (drop oldest) + colapso da pilha
  pré-existente. **Provado ao vivo: 391 → 1** (q-1-1 `explore:savanna_temperate`, attempts=11/10 em 45 s).
- **Lição:** qualquer fila humana precisa de dedupe + cap antes do primeiro utilizador real.
- **Evidência:** `scripts/e2e/relatorio-sessao.json` (391 pendentes), commit `4040e8e`.

### C13 · Stores rotativos eviccionavam provas
- **Problema:** o log de decisões (~500) e as perguntas (~100) rodavam e apagavam exatamente a
  evidência de que precisávamos para depurar.
- **Causa:** anéis em memória pequenos demais, sem paginação nem histórico.
- **Solução:** limites **5000 decisões / 2000 perguntas** (evicção de *answered* primeiro) +
  paginação `?limit=&before=` (cursor por `ts`, mais recentes primeiro).
- **Lição:** registos de auditoria não devem rodar abaixo do tamanho da sessão; paginação por cursor
  escala melhor que offset.
- **Evidência:** `src/system_one_dwarf/app/runtime.py:39-43`, commit `4040e8e`.

### C14 · Execução invisível no log
- **Problema:** o log de decisões mostrava a *decisão*, mas não se ela foi executada, perguntada ou
  falhada — impossível auditar.
- **Causa:** o contrato de decisão não tinha estado de execução.
- **Solução:** campo aditivo `status` (`executed|question|error`) espelhado em `state.apply_event` +
  evento `action_executed` publicado pelo loop após execução.
- **Lição:** todo o artefacto de decisão precisa de *outcome*; sem isso o "porquê" fica irrecuperável.

### C15 · Só `attack` para hostis — caça impossível
- **Problema:** o gerador de candidatos só propunha `attack` para unidades hostis; animais passivos
  nunca eram caçados — a missão de caça era impossível.
- **Causa:** vocabulário de ações desenhado só para combate defensivo.
- **Solução:** vocabulário de **caça**: `attack:<animal>` por criatura **não-sapiente** visível sob
  objetivo de caça (`wants_hunt`) e `hunt:search_prey` sem presa visível; **sapientes nunca são
  alvo** (`kind != "sapient"` + lista de raças: humanos, anões, elfos, goblins, …).
- **Lição:** o vocabulário de ações define o que o agente *consegue querer*; e regras de segurança
  ("nunca atacar sapiens sem aprovação explícita") ficam no gerador, não no prompt.
- **Evidência:** `src/system_one_dwarf/agent/candidates.py`, `tests/agent/test_candidates.py`.

### C16 · Movimento linear trava em paredes — `goto`
- **Problema:** andar em linha reta ("move N") emperra em obstáculos e o personagem fica preso.
- **Causa:** movimento passo-a-passo sem noção de mapa; o spawn nasce dentro de edifícios.
- **Solução:** `goto X,Y` com **BFS planar** (`dfb-nav.lua`) + `move DIR times=N` para o último passo.
  Limites conhecidos: o `goto` **não muda de z** e as presas fogem entre z-níveis (`+1z`/`-1z`) —
  perseguir no mesmo z (pathing multi-z por resolver, ver H).
- **Lição:** navegação é um problema de procura de caminho, não de repetição de tecla; documenta os
  limites do plano para o planeador não os ignorar.

---

## D. Navegação e combate — o que custou aprender

### D17 · DF 53.16 é *mouse-first* nos menus
- **Problema:** `key SELECT` não respondia no ecrã título nem em vários menus — o agente não avançava.
- **Causa:** DF 53.16 (interface nova) trata menus por rato; `gui.simulateInput` aceita nomes de
  `df.interface_key` (`SELECT`, `LEAVESCREEN`, `A_MOVE_N`, `A_ATTACK`, `CUSTOM_A`, `STRING_A…` para
  texto — **não existe "ENTER"**), mas o foco real está em cliques.
- **Solução:** **cliques sintéticos** `click X,Y` com coordenadas derivadas do `screen_text`
  (labels reais), e teclas só onde respondem.
- **Lição:** em UIs novas, primeiro descobre *como* o jogo recebe input (rato vs tecla) antes de
  escrever automação; guarda os padrões comprovados num ficheiro de verdade (ver D22).
- **Evidência:** `docs/padroes-navegacao.json` (`navigation_mechanics.title_menu`,
  `conventions.keys_dsl`), `dfhack/df_bridge.py:1263`, `src/system_one_dwarf/game/driver.py:238`.

### D18 · O overlay de tutorial ENGOLIA TODO o input — causa-raiz do "personagem parada"
- **Problema:** desde o início da sessão, o personagem não se movia; os *moves* devolviam `ok:true`
  e nada acontecia. Foi o bug mais caro da sessão.
- **Causa:** o viewscreen **`dungeonmode/Help` (tutorial) engolia TODO o `simulateInput`** — teclas
  reportavam sucesso, mas o **tick do jogo estava congelado a 17250**. Não havia exceção nem erro:
  *silêncio*. Só aparentava "o agente está parado".
- **Solução:** sequência de fecho mapeada e guardada: clicar Next/Okay por todas as páginas; nas
  páginas interativas sem botão ("Movement (2 of 3)"), clicar o **botão Help** (topo direito,
  `click 180,1` em janela de 182 colunas) para concluir o tutorial; depois `key LEAVESCREEN` até
  `focus=dungeonmode/Default`. Teste de regressão cobre a sequência.
- **Lição:** **overlays/estado de ecrã bloqueiam input SEM erro visível** — deteta por "estado não
  muda" (posição/tick estáticos), nunca por exceções. Isto justifica o StuckDetector (F26).
- **Evidência:** `docs/padroes-navegacao.json` (`overlays.tutorial_help`), registo CoALA da missão
  `caca-2`, testes de regressão do *driver*.

### D19 · Unidades importam: `act batch --delay` é em SEGUNDOS
- **Problema:** um *batch* de ações "pendurava" o agente durante minutos.
- **Causa:** `--delay` do `act batch` é em **SEGUNDOS** (`--delay 400` = 400 s pendurado), enquanto o
  `sleep N` do DSL é em **MILISSEGUNDOS** — unidades opostas na mesma DSL.
- **Solução:** valores corretos (ex.: `--delay 0.4`) + regra registada na biblioteca de padrões.
- **Lição:** DSLs com unidades heterogéneas precisam de uma tabela de unidades no contrato
  (`navigation_mechanics.delay_units`) — não de memória.

### D20 · `steps.json` com *expects* vazios
- **Problema:** o arnês de criação de aventureiro avançava às cegas; os `expects` nunca casavam.
- **Causa:** (a) o *split* de comentários `#` reduzia a "agulha" da regex; (b) `\bGo!\b` nunca casava
  com o label real; (c) a seleção de raça exige **2 cliques** (realce da linha + botão
  "Select &lt;raça&gt;") e só havia um.
- **Solução:** labels reais verificados ao vivo no `screen_text` + regex corrigidos + passos extra de
  clique (ver `p-select-race-row-then-confirm` / `p-select-race-row-then-button`).
- **Lição:** *expects* de E2E têm de ser derivados de observação real do ecrã, nunca de suposição;
  um `expect` que nunca falha não está a testar nada.

### D21 · Cadeia de combate mapeada de raiz
- **Problema:** não sabíamos como matar um animal no adventure mode; o "bump" não funcionava.
- **Causa:** o *bump-attack* só ataca **hostis** — criaturas **neutras/tame ignoram o bump**. E a
  cadeia de menus de ataque era desconhecida.
- **Solução:** padrão **`p-attack-menu-kill`** comprovado ao vivo: `A_ATTACK` (345) → *overlay*
  "Okay" → "Who will you attack?" (clique na linha do alvo) → "Really start a conflict…?" →
  **Confirm** → planner → **Strike** → mira (`c neck` / `d head`, `a upper body`) → golpe
  (`a stab/iron spear`) → **Done**. Verificação de morte em 4 sinais (gamelog de morte, unidades
  antes→depois, `adventurer.kills`, `screen_text` de combate).
- **Lição:** mapeia a cadeia de menus inteira e guarda-a como padrão; separa mecanismo de *bump*
  (hostis) de cadeia explícita (neutros/tame). Nunca atacar sapiens sem aprovação humana.
- **Evidência:** `docs/padroes-navegacao.json` (`combat.attack_menu_chain`, `combat.bump_attack`,
  `combat.kill_verification`), `scripts/e2e/logs/mission-hunt.jsonl`.

---

## E. Planeamento e caça (a arquitetura exigida: LLM planeia, JEV decide, código executa)

### E22 · Padrões aprendidos UMA vez
- **Problema:** cada sessão re-descobria as mesmas teclas, cliques e armadilhas.
- **Causa:** conhecimento de navegação vivia só na cabeça da sessão.
- **Solução:** `docs/padroes-navegacao.json` — fonte de verdade dos padrões **comprovados**:
  **21 padrões** (`p-title-create-world` … `p-attack-menu-kill`), cadeia de combate,
  mecânicas de navegação, overlays e **200 `stuck_events`** registados ao vivo.
- **Lição:** padrão aprendido uma vez = guardado com data, prova e limites; nunca re-derivado.

### E23 · JEV conduz a navegação até entrar no jogo
- **Problema:** entrar no jogo (mundo → aventureiro → in-game) é uma sequência longa de menus.
- **Causa:** cada ecrã tem opções e estados diferentes; sem política, o agente divaga.
- **Solução:** o JEV escolhe entre os padrões do *library* (navegação guiada por decisão tipada),
  com **fallback determinístico quando abstém** (80 navegações por fallback na corrida `caca-2`).
- **Lição:** para fluxos determinísticos, o modelo escolhe o *padrão* e código executa os passos;
  o fallback garante progresso mesmo sem confiança.

### E24 · LLM traça o plano de caça estruturado
- **Problema:** "mate um animal" é um objetivo vago; o agente vagueava sem direção.
- **Causa:** falta de plano intermédio entre objetivo e ação imediata.
- **Solução:** o LLM produz um **plano estruturado** (onde estamos → zonas prováveis de animais →
  rota → regra de oportunismo) que entra como *input* do JEV-A. Divisão de trabalho clara:
  **LLM planeia, JEV decide, código executa**.
- **Lição:** planos estruturados (campos fixos) são o melhor formato de handoff LLM→decisor.

### E25 · Oportunismo + validação por ação + re-plano
- **Problema:** planos rígidos quebravam ao primeiro imprevisto (presa vista a meio do caminho).
- **Causa:** execução sem verificação e sem política de desvio.
- **Solução:** **oportunismo** (animal visível no caminho = candidato imediato), **validação de cada
  ação** por *snapshot diff* (posição/HP/gamelog), **re-plano** em falha ou dano, **re-exploração**
  quando o local não rende.
- **Lição:** valida efeitos, não intenções; o plano é uma direção, não uma escritura.

### E26 · StuckDetector — o personagem nunca mais congela
- **Problema:** o personagem ficava parada "para sempre" (ver D18) sem qualquer erro.
- **Causa:** inputs engolidos, aprovações sem execução, modo pausa — todas silenciosas.
- **Solução:** `agent/stuck.py` (**23 testes**): **hash de estado** (posição + arredores + ids de
  unidades); **3 passos sem mudança → exploração forçada** (sem aprovação); **2.ª vez → re-plano
  LLM**; **mesma ação 3× → troca de candidato**; **dano → recuo**; **morte → paragem**.
  **200 eventos registados ao vivo** na biblioteca de padrões.
- **Lição:** em ambiente sem erros, o progresso é o oráculo: deteta estagnação por *diferença de
  estado* e escala por níveis. É a peça que torna sessões longas viáveis.
- **Evidência:** `src/system_one_dwarf/agent/stuck.py`, `tests/agent/test_stuck.py` (23 testes).

### E27 · Prova da missão: mula abatida
- **Problema:** como provar que o agente autónomo funciona de verdade?
- **Causa:** demos não contam; é preciso *kill proof* com evidência de jogo.
- **Solução:** corrida `caca-2` (mundo novo + aventureiro novo "Usmen Thedlemones"): após 3 estocadas
  no corpo (pulmão rasgado, costelas partidas) e uma no pescoço (espinha partida → "falls over"),
  estocada final na cabeça — *"fracturing the skull and tearing the brain"* +
  **"The stray mule has been struck down."** (`KILL_CONFIRMED`). Só havia gado *tame* num raio de 80 —
  a missão "FIND AN ANIMAL AND KILL IT" foi satisfeita com a mula. Corrida completa: **1 980 decisões
  JEV** (JEV-A 415), **13 episódios de narrador**, 200 eventos *stuck*.
- **Lição:** prova de missão = evento de jogo canónico + unidades antes→depois + log estruturado
  (`mission-hunt.jsonl`, 226 eventos) — auditable por terceiros.

---

## F. UI/UX

### F28 · Auditoria uxui-evaluator: 40 → 100 → 52 → 100 → 100
- **Problema:** a UI tinha 40/100 e defeitos reais de supervisão segura.
- **Causa:** v1 com **8 achados** (2 critical · 3 warning · 3 suggestion) — o pior:
  **escalada silenciosa para autopilot** (retomar/desligar kill-switch devolvia sempre `autopilot`,
  perdendo o modo `checkpoint` escolhido — segurança invertida); perguntas sem proveniência
  (banda/confiança/papel JEV); densidade de métricas; "checkpoints" sem superfície de aprovação;
  navegação plana mal ordenada.
- **Solução:** correções → **v2: 43/100 (7 achados) → reparos → rescore 100/100** (dedupe por
  `action_id` visível, status, paginação `?limit=&before=`, tooltips de banda, proveniência,
  fila de aprovações) → **wizard de aventura v3 52/100 → 100/100** → **v4 100/100 com Motion UI**
  (7 componentes do registry; MUI mantido só nos controlos auditados, decisão justificada).
- **Lição:** auditoria externa + re-score fechado é o ciclo que move qualidade de UI; o achado
  crítico mais valioso foi um bug de *segurança de modo*, não visual.
- **Evidência:** `docs/uxui-audit-ui*.md|json`, commits `a61b605`, `ff728fd`, `f31835e`, `10034d5`.

### F29 · Tudo se faz pelo site
- **Problema:** operar o agente por linha de comandos espalhada não escala para o utilizador.
- **Causa:** a UI nasceu como telemetria, não como consola.
- **Solução:** painéis para **chaves · objetivo · memória · perguntas · aprovações · modos** +
  **aventura completa** (escolher/criar mundo e personagem — wizard) + **sistema** (jogo on/off).
- **Lição:** o humano que supervisiona precisa de uma superfície única; cada decisão do agente tem
  de ter explicação, proveniência e um botão.

### F30 · Comandos globais: `df up` e `install.sh`
- **Problema:** arrancar jogo + backend + UI eram 3 comandos; instalar era tribal.
- **Causa:** falta de embrulho operacional.
- **Solução:** comando global **`df`** (`df up` sobe jogo+UI num comando; **Ctrl-C fecha ambos**;
  `df stop`/`df status`) com **despacho inteligente** para não partir o `df` de disco do sistema
  (colisão de nomes tratada: o `df` real passa adiante) — substituiu o `sod` depreciado.
  `install.sh` com **matriz de SO** (Linux ✅ verificado · macOS ❌ par 53.x inexistente ·
  Windows ⚠️ não testado · WSL2 ⚠️ experimental) e guarda de SO por `uname -s`.
- **Lição:** um bom CLI respeita os comandos do sistema; o instalador deve *recusar* o que não foi
  testado, com mensagem e exit code claros.
- **Evidência:** `scripts/df.sh`, `install.sh --help`, commits `0c343e9`, `b4834e3`, `eebb059`.

---

## G. Processo e ops (aprendizados de orquestração)

### G31 · Peças de background estagnavam sem escrever
- **Problema:** subagentes em background ficavam "a trabalhar" sem nunca entregar artefacto.
- **Causa:** *briefs* largos sem obrigação de escrita imediata; sem verificação independente.
- **Solução:** padrão que funcionou: **steer de "escrever já" → interromper → executar em primeiro
  plano com *tight briefs***; **verificação independente por peça**; **commit por peça verificada**
  (gate `oss-gate.sh commit-msg`, títulos Conventional Commits ≤72 carateres).
- **Lição:** trabalho delegado precisa de *entregável escrito* cedo e de gate por peça; se não
  escreveu, não existe — e execução em primeiro plano é mais barata que esperar.

### G32 · Segredos: nunca imprimir
- **Problema:** risco de vazar chaves em logs, commits e documentos.
- **Causa:** chaves vivem em `~/.secrets`, `~/.dsh/.credentials.yaml`, envs — fáceis de imprimir
  por acidente num comando de diagnóstico.
- **Solução:** extração *non-displaying* (comandos que leem sem eco); testes com chaves
  **inequivocamente falsas** (`TEST-KEY-NOT-REAL-*`); **varredura de segredos antes de cada commit**
  e em cada push.
- **Lição:** política de "zero impressão" + falsidade óbvia nos testes + sweep no gate = defesa em
  profundidade. Custa 30 segundos por commit.

### G33 · Bugs no script partilhado `oss-gate.sh` (fora do repo)
- **Problema:** o gate de commit/push comportava-se mal de formas não determinísticas.
- **Causa:** (a) o *dispatcher* chamava `gate_push "${1:-}"` e **descartava `$2`** — invocação
  `--branch main` rebentava com `set -u`; (b) a contagem de commits do *scan* dava **n−1** por falta
  de *newline* final no `git log`.
- **Solução:** *workaround* documentado (`oss-gate.sh push <branch>` ou `push --scan-only`) e bugs
  reportados aos donos — **fora do repo**, não parcheados às escondidas.
- **Lição:** ferramenta partilhada: reporta e contorna; nunca fazes *fork* silencioso de infra comum.

### G34 · Custos reais por campanha
- **Problema:** sem contabilidade, não há decisão informada sobre modelos.
- **Causa:** custos por request invisíveis.
- **Solução:** custo rastreado **por campanha** na UI (`campanhas/*/gastos.sqlite`,
  6 decimais, `usage.cost` quando presente). Preços de referência: **Jev $0,042/M entrada · saída
  grátis**; **DeepSeek $0,30/M entrada · $1,20/M saída**. Sessões ligeiras ficam na ordem de **$0,30**
  (campanha `campanha-caca`: $0,3381 em 4 698 registos); missões autónomas completas custam alguns
  USD (`campanha-e2e` $2,65 · `caca-2` $3,52 em 27 556 registos).
- **Lição:** decisões tipadas do Jev custam tostões (a saída é grátis); o custo vive nos tokens de
  texto do DeepSeek — logo, *batching* e cache de planos/narrativas são a alavanca de custo.
- **Evidência:** `docs/openrouter.md` §preços, `campanhas/*/gastos.sqlite`.

---

## H. Onde otimizar o processo

Prioridades propostas (impacto × esforço). Cada ponto tem o primeiro passo concreto.

### H1 · Calibragem das bandas do JEV (prioridade máxima)
- **Estado:** o JEV-A abstém em **349/415** decisões de movimento (auto só 12) — seguro, mas o
  humano carrega o loop; 13 perguntas numa ronda podiam ir numa chamada.
- **Ação:** usar `evals/` (**66 cenários**: `jev-a-action` 19 · `jev-r-retrieval` 13 ·
  `jev-d-doubt` 10 · `jev-m-memory` 10 · `injection-evals` 14) + **eval ao vivo** (accuracy/ECE por
  banda) para afinar *state*/*candidates*/*thresholds*; reduzir `abstain` sem tocar no piso `auto ≥ 0.90`.
- **Ganho:** mais autonomia real com a mesma segurança; **batching de perguntas** (≈13→1 chamada)
  reduz custo/latência ~10×.

### H2 · Pathing multi-z e *map-aware hunting*
- **Estado:** `goto` é planar; as presas fogem entre z-níveis e escapam (`+1z`/`-1z`).
- **Ação:** navegação por z (rampas/escadas em `A_MOVE_UP/DOWN` + BFS 3D) e uso do conhecimento de
  `conhecimento/` (biomas, fauna) para **prever zonas de caça** no plano do LLM.
- **Ganho:** taxa de sucesso de caça e menos passos desperdiçados.

### H3 · Persistir stores + *watchdog* de sessão
- **Estado:** `decisions`/`questions`/`approvals` são *rings* em memória — perdem-se no restart;
  o StuckDetector cobre o *personagem*, não a *sessão*.
- **Ação:** SQLite para os três stores (o projeto já tem padrão CoALA/SQLite) + *watchdog* global
  (estender a lógica do StuckDetector à sessão: sem progresso em N minutos → re-plano/escala).
- **Ganho:** sessões autónomas longas com auditoria persistente e recuperação após crash.

### H4 · CI no GitHub + versão (SemVer)
- **Estado:** repo publicado **sem CI** e sem releases/versão.
- **Ação:** GitHub Actions (suíte + lint + *web gates* — build/typecheck) em PR e main; tags
  SemVer + changelog a partir de Commits Convencionais (o histórico já cumpre).
- **Ganho:** regressões de DF/DFHack e de código apanhadas antes do utilizador.

### H5 · Combate com políticas
- **Estado:** a cadeia de ataque funciona, mas a escolha de alvo/parte vital é manual e não há
  gestão de risco.
- **Ação:** seleção automática de alvo/parte vital (prioriza neck/head quando o dano compensa),
  gestão de armas (arma presa, trocar), **políticas de recuo** e **avaliação de ameaça real**
  (evitar "grande perigo" sem aprovação).
- **Ganho:** combate mais eficiente e menos mortes estúpidas.

### H6 · Caches e narrativas
- **Estado:** a tradução já tem cache; planos e narrativas são re-gerados.
- **Ação:** cache de planos LLM (por estado+objetivo) e de padrões de menu; reduzir narrativas
  redundantes via JEV-M (`narrator_effort` já existe — calibrar para `none` quando nada mudou).
- **Ganho:** menos tokens (ver G34), menos ruído narrativo.

### H7 · E2E reais automatizados (smoke diário)
- **Estado:** o E2E ao vivo foi manual (`session_live.py`, `mission_hunt.py`, `steps.json`).
- **Ação:** *smoke* diário automatizado: `df up` → criar aventura → movimento com prova →
  `df stop`; alerta quando padrões de UI mudam (regressões de DF/DFHack updates).
- **Ganho:** deteção precoce da classe de bug de D18/D20.

### H8 · Onboarding: presets e "modo caça"
- **Estado:** o wizard de aventura cobre o básico, mas cada missão é configurada à mão.
- **Ação:** presets de personagem/missão (caçador, sobrevivente) e um **modo caça dedicado**
  (objetivo, zona, política de oportunismo pré-configurados).
- **Ganho:** do clone à primeira missão provada em minutos.

---

<a name="correções-e-verificação"></a>
## Correções e verificação

Itens do relatório original corrigidos ou detalhados após verificação nos artefactos:

| # | O que dizia | O que os artefactos mostram |
|---|---|---|
| 1 | "suíte 1175 green" | Confirmado com nuance: **1175 passed + 1 skipped** (1176 recolhidos) — os 1175 são os verdes. |
| 2 | "correções → v2 100" | A auditoria **v2 começou a 43/100 (7 achados)**; os reparos fecharam o **rescore a 100/100**. |
| 3 | "sessão completa de MVP ~$0,30" | Verdadeiro para sessão ligeira (`campanha-caca` $0,3381); missões completas custam **$2,65–$3,52** (`campanha-e2e`, `caca-2` com 27 556 registos de gasto). |
| 4 | "`act batch --delay` (400 = pendura)" | Confirmado: `--delay` é em **segundos** (400 s pendura); acrescentado que o `sleep N` do DSL é em **milissegundos** — unidades opostas (`delay_units` nos padrões). |
| 5 | "select de raça precisa de 2 cliques" | Confirmado nos padrões: `p-select-race-row-then-confirm` **e** `p-select-race-row-then-button` (realce + "Select &lt;raça&gt;"). |
| 6 | "StuckDetector … `stuck-detection`" | Chave CoALA usada: **`stuck-detector-design`** (chave já existente — supersessão) em vez de criar `stuck-detection` duplicado. Idem para `df5316-navegacao-mouse` (atualizada com overlay + cadeia de combate). |
| 7 | "gui.simulateInput aceita … CUSTOM_A../STRING_A.." | Confirmado: `CUSTOM_A` citado em `dfhack/df_bridge.py:1263` e `STRING_Axxx` (ASCII) em `game/driver.py:238`; o que **não** existe é "ENTER". |
| 8 | "JEV-A abstém 349/415" | Confirmado exato em `docs/padroes-navegacao.json.stats` (auto 12 / hitl 54 / abstain 349; 1 980 totais; fallback 80). |

Números-chave verificados: 21 padrões · 200 `stuck_events` · 23 testes de `stuck.py` ·
66 cenários de `evals/` · 30 129 registos na base `conhecimento/` (chaves `df/skills/*`,
`df/essencia/*`; sem `knowledge/*`) · 226 eventos em `mission-hunt.jsonl` com `KILL_CONFIRMED` ·
auditorias UX 40/100 (8 achados) → v2 43→100 → v3 52→100 → v4 100.
