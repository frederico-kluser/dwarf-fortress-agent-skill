# Auditoria UX/UI — interface web do System One Dwarf

**Âmbito:** 8 painéis (`web/src/panels/`), shell de navegação (`web/src/layout/`), tema (`web/src/theme.ts`), componentes e cliente/mock (`web/src/api/`), contra `docs/api.md` e `docs/arquitetura.md` §UI web.
**Tipo de interface:** `dashboard` (mission control), com `ai-chat` adjacente (fila de perguntas) e `data-table` (log de decisões) — ver `interface_type_note` no JSON.
**Limite:** avaliação feita a partir do código, não de renderização real (sem browser nem medição de viewport); contrastes calculados a partir dos hex da paleta.

## Resultado

| | |
|---|---|
| **Score** | **40 / 100** |
| **Banda** | **fair** |
| Critical / Warning / Suggestion | 2 / 3 / 3 |
| Enriquecimento por API | não (`api_enriched: false`) — sem `UXUI_API_KEY`; índice público de princípios usado para fixar códigos/slugs reais |

O veredicto em uma frase: **a fatura de craft é alta, mas a camada de supervisão segura tem defeitos reais** — o que o humano precisa para vigiar o agente (saber por que ele pergunta, e não perder a escolha de segurança que fez) é exatamente o que está mais fraco.

## Top-5 correções, por ordem

1. **Parar a escalada silenciosa para autopilot** (`C.1.4.01`, critical) — retomar de «Em pausa» ou desligar o kill-switch devolve **sempre** `autopilot`, mesmo quando estava `checkpoint` (`ControlPanel.tsx:361`, `ControlPanel.tsx:417`, `AppShell.tsx:80`); o modo anterior não é guardado. Fix: persistir o último modo e confirmar qualquer transição para autopilot. É a correção com melhor relação risco/esforço de todo o relatório.
2. **Dar proveniência às perguntas do agente** (`S.1.3.01`, critical) — o cartão mostra pergunta, id e hora, mas não a banda (`hitl`/`abstain`), a confiança, o papel JEV nem a decisão que a originou (`QuestionCard.tsx:47-61`; o tipo `Question` não tem esses campos, `types.ts:325-339`). A chegada da pergunta também é silenciosa (`queries.ts:188-192`: só cache, sem toast nem `aria-live`). Fix: estender o contrato com `band`/`confidence`/`decision_id` e anunciar a chegada.
3. **Dar prioridade ao painel de métricas** (`F.1.1.02`, warning) — é o painel de entrada (`registry.tsx:113`) e o mais denso: 8 grupos + 5 cartões do agente = 13 blocos num scroll, sem resumo nem ordenação por severidade (`MetricsPanel.tsx:135-403`). Fix: 4-5 sinais vitais acima da dobra e o resto em divulgação progressiva.
4. **Explicar (ou construir) o modo «Checkpoints»** (`F.1.1.03`, warning) — o modo promete aprovação humana e não tem superfície nenhuma: nada mostra o que aguarda aprovação nem que o agente está bloqueado; o contrato só tem `mode`/`kill_switch` (`docs/api.md:398`). Fix: descrever o comportamento real, mostrar o estado «à espera de resposta» e ligar o chip do modo à fila.
5. **Reordenar a navegação pelo trabalho real** (`F.2.2.03`, warning) — 8 itens planos sem grupos, e «Perguntas» (a função do humano) é o 5.º (`registry.tsx:79-86`); em ecrãs estreitos a navegação aparece duas vezes (`PanelTabs.tsx:19-44`). Fix: grupos «Supervisão» / «Contexto», Perguntas ao topo quando há pendentes, uma só navegação em mobile.

A seguir: detalhe do log de decisões (`D.1.1.01`, suggestion — 9 colunas × até 250 linhas, probabilidades cortadas nas 2 melhores, sem detalhe de linha nem filtro temporal), consistência de frescura/estado entre painéis e breakpoints (`C.1.1.01`, suggestion) e `aria-current`/`tabpanel` (`H.1.1.01`, suggestion).

## O que a UI já faz bem

- **Contraste e foco verificados, não assumidos:** todos os pares de texto e chips passam AA com folga (pior caso 5,49:1; `text.secondary` a 8,44:1 no escuro e 7,75:1 no claro), `:focus-visible` visível (`theme.ts:82-86`) e link «Saltar para o conteúdo principal» (`AppShell.tsx:89-104`).
- **A cor nunca é o único sinal:** as bandas JEV levam etiqueta além da cor (`Chips.tsx:13-26`) — cumpre o requisito de não depender só da cor.
- **Honestidade sobre confiança:** confiança ausente ou 0 aparece como «indisponível», com tooltip a dizer que não significa «certo» (`DecisionsPanel.tsx:244-252`, `format.ts:95-98`, `MetricsPanel.tsx:364-368`), e o mock inclui de propósito uma decisão sem confiança para manter o caminho exercitado (`mock.ts:731-733`).
- **Erro e vazio tratados a sério:** vazio que explica o que falta e por quê, erro com «Tentar de novo», ausência de backend com instruções concretas (`AppShell.tsx:179-204`) e *error boundary* a evitar ecrã branco (`main.tsx:30-46`).
- **Onde há risco, há fricção bem desenhada:** o kill-switch tem `alertdialog` que explica âmbito e reversibilidade (`KillSwitchButton.tsx:64-99`); memória e objetivo são editados em rascunho com chip «alterações por guardar» e «Reverter» antes de gravar (`MemoryPanel.tsx:53-77`, `ObjectivePanel.tsx:62-86`).
- **Modo simulado sempre divulgado:** chip «dados simulados» + badge `mock · …` com tooltip (`TopBar.tsx:56-64`, `ConnectionBadge.tsx:33-43`) — o utilizador sabe que está a ver dados falsos.
- **Consistência de sistema:** os 8 painéis partilham moldura, tema com CSS vars para os dois esquemas e a convenção de mostrar valores EN do contrato em PT, incluindo a chave do grupo (`InfoCard.tsx:45-54`).

## Nota de método

`api_enriched: false`: o endpoint `audit` e os princípios fora do lote gratuito exigem subscrição Pro (403 `PRO_REQUIRED`). O índice público de princípios respondeu sem autenticação e foi usado para fixar código/slug/título reais dos 12 princípios gratuitos (é de lá que vem, por exemplo, `H.1.1.01` para a Parte 6); os restantes códigos vêm da taxonomia do framework e das tabelas da skill `uxui-evaluator`. `business_impact` é derivado do framework, não citado da API.

---

# Auditoria v2 — `docs/uxui-audit-ui-v2.json`

**Âmbito:** o mesmo da v1 (8 painéis + shell + tema + cliente/mock), avaliado com a skill `uxui-evaluator` (classificação `dashboard`, Partes 1/2/4 + 5) sobre o **código atual** — leitura integral de `web/src/panels`, `web/src/components`, `web/src/layout`, `web/src/api`. Snapshot **pré-reparação** deste ciclo; a re-avaliação pós-reparação fica no fim desta secção.

## Resultado v2 (pré-reparação)

| | v1 | v2 (pré) |
|---|---|---|
| **Score** | **40 / 100** | **43 / 100** |
| **Banda** | fair | fair |
| Critical / Warning / Suggestion | 2 / 3 / 3 | 2 / 3 / 2 |
| Achados | 8 | 7 |

## Deltas vs v1

**Reparados desde a v1 (7 de 8 achados):**

| Achado v1 | Reparado em |
|---|---|
| #1 escalada silenciosa para autopilot (`C.1.4.01`, critical) | `useAutonomyControl.ts` guarda `restorableMode`; retomar/desligar kill-switch devolve o último modo explícito; `AutopilotConfirmDialog` nomeia o modo de destino (`AppShell.tsx`, `ControlPanel.tsx`) |
| #3 métricas densas no painel de entrada (`F.1.1.02`, warning) | `VitalSignsStrip` (5 sinais acima da dobra) + 8 grupos em acordeões **ordenados por severidade** (`MetricsPanel.tsx`) |
| #4 modo «Checkpoints» sem superfície (`F.1.1.03`, warning) | `ApprovalsSection.tsx` + `GET .../approvals` no contrato, badge na AppBar, Aprovar/Rejeitar com confirmação, estados vazios por modo |
| #5 navegação plana (`F.2.2.03`, warning) | `NAV_GROUPS` (Supervisão/Agente/Memória) com «Perguntas» primeiro; uma só navegação em mobile; `aria-current`/tablist ligados |
| #6 log de decisões monolítico (`D.1.1.01`, suggestion) | colunas de leitura rápida + diálogo de detalhe (todas as probabilidades, respostas tipadas, latência), filtros papel/banda/texto/confiança |
| #7 frescura inconsistente (`C.1.1.01`, suggestion) | `FreshnessIndicator` em todos os painéis (incluindo perguntas, decisões, memória, aprovações) |
| #8 acessibilidade (`H.1.1.01`, suggestion) | mantida e alargada: skip-link, `role=region`/`tabpanel` condicional, `aria-current`, alvos ≥ 40 px |

**Só parcialmente reparado:** #2 (proveniência das perguntas) — `band`/`role`/`action_id`/`why_pt`/`source_data` já chegam ao cartão, mas a chegada da pergunta continua sem anuncio e o novo material do contrato ainda não é mostrado.

**Novos na v2 (o contrato cresceu depois da v1):**

1. `finding-1` · **critical** · `S.1.3.01` — `attempts`/`last_seen_ts` invisíveis e sem coalescência visual: uma ação re-pedida 11 vezes (campanha viva) parece N perguntas novas ou uma só pergunta antiga; fila de aprovações limitada a 20 sem sinal de janela cheia.
2. `finding-2` · **critical** · `C.1.4.01` — `status` das decisões (executed|question|error) e `issues` não mostrados: erro, pergunta e execução são linhas idênticas; zero feedback de erro no log.
3. `finding-3` · **warning** · `F.1.1.02` — `?limit=&before=` não usado: o log puxa o anel inteiro (5000) sem «carregar mais».
4. `finding-4` · **warning** · `F.1.1.03` — vocabulário das bandas sem explicação: `BandChip` sem tooltip, filtro mostra `auto`/`hitl`/`abstain` em cru (EN).
5. `finding-5` · **warning** · `S.1.1.01` — chegada de pergunta ainda silenciosa (só cache + badge; sem toast nem `aria-live`).
6. `finding-6` · **suggestion** · `C.1.1.01` — `DEFAULT_PANEL_ID = 'metricas'` contradiz a ordem de navegação («Perguntas» primeiro).
7. `finding-7` · **suggestion** · `D.1.1.01` — `source_data` (diagnóstico do pedido) nunca mostrado; sem segundo nível de divulgação.

**Força mantida:** honestidade sobre incerteza («indisponível» em vez de 0), fricção onde há irreversibilidade, estados vazios que explicam, mock que exercita payloads antigos.

## Re-avaliação pós-reparação (`docs/uxui-audit-ui-v2-rescore.json`)

| | v1 | v2 (pré) | **v2.1 (pós)** |
|---|---|---|---|
| **Score** | 40 / 100 | 43 / 100 | **100 / 100** |
| **Banda** | fair | fair | **excellent** |
| Critical / Warning / Suggestion | 2 / 3 / 3 | 2 / 3 / 2 | **0 / 0 / 0** |

**Delta: 40 → 100 (+60)** — os 7 achados da v2 reparados, 0 restantes nas Partes 1/2/4 + 5 (rubrica da skill: 100 − 15/critical − 7/warning − 3/suggestion, com `findings: []`).

| Achado v2 | Reparação (`web/**`) | Evidência |
|---|---|---|
| 1 · `attempts`/`last_seen_ts` invisíveis (critical) | campos em `types.ts`; chip «pedido N vezes» (`AttemptsChip`) em `QuestionCard`/`ApprovalsSection` com último re-pedido; coalescência visual por `action_id` (`questionGroups.ts`); aviso de fila cheia (20) | DOM headless: «pedido 9 vezes», «Agrupados: 2 pedidos da mesma ação (goto:river_bank)» |
| 2 · `status` das decisões (critical) | `DecisionStatus` + coluna **Estado** + filtro + detalhe com `issues` (`released_by_approval`), «desconhecido» honesto para auxiliares | DOM: chips «executada»/«virou pergunta»/«erro»/«desconhecido»; diálogo «Notas de Auditoria (1)» |
| 3 · sem paginação (warning) | `?limit=&before=` em `API_PATHS`/client/mock; `usePagedQuery` (25/página, cursor exclusivo); «Carregar mais» em decisões e perguntas | CDP clique real: 25 → 51 linhas |
| 4 · vocabulário sem explicação (warning) | `BAND_DESCRIPTION` em tooltip de todos os `BandChip` + helperText dos filtros (labels PT, zero EN cru); `DECISION_STATUS_LABEL/DESCRIPTION` | DOM: helperText «auto ≥ 0,90 · hitl 0,50–0,89 · abstain < 0,50» |
| 5 · chegada silenciosa (warning) | toast `role=status` («Nova pergunta do agente…» / «Re-pedido do agente…») + região `aria-live` permanente em `AppShell` | CDP: toasts observados + «5 pergunta(s) do agente à espera de resposta» |
| 6 · painel de entrada (suggestion) | `DEFAULT_PANEL_ID = 'perguntas'` (primeiro da navegação = trabalho do humano) | render abre em «Fila de perguntas» |
| 7 · `source_data` escondido (suggestion) | expansível «Diagnóstico do pedido» no `QuestionCard` (chaves ordenadas, `{}` → «sem diagnóstico») | DOM: «Diagnóstico do pedido (6 campos)» |
| extra | histórico de aprovações (`?status=all`) — trilha auditável da decisão humana | acordeão «Histórico de decisões» em `ApprovalsSection` |

**Verificação final:** `npm run build && npm run lint && npm run typecheck` → 0 erros/0 avisos; render headless-Chrome/CDP dos **8 painéis** sem error boundary e sem exceções de runtime; tabela de decisões ordenada do mais recente para o mais antigo como promete a legenda.

**Forças reavaliadas (5):** transparência do agente (banda+papel+ação+porquê+contador de pedidos+diagnóstico), fluxo humano-agente anunciado, prevenção de erro com fricção alinhada à irreversibilidade + desfecho visível, carga cognitiva controlada (paginação/agrupamento/severidade) e consistência de vocabulário/estado/ordem.

---

# Auditoria v3 — `docs/uxui-audit-ui-v3.json`

**Âmbito:** a experiência NOVA — painel `aventura` (`AventuraPanel.tsx`), wizard modal `AdventureWizard.tsx`, cartão de progresso `AdventureTaskCard.tsx`, secção `Sistema` (`SystemSection.tsx` no controlo) e a camada `web/src/api/*` nova (`adventures`/`system` + evento WS `adventure_task`, com simulação de tarefas passo-a-passo no mock).
**Classificação:** `onboarding` **modal-dominante** — escolhido entre `onboarding` e `modal` (ver `interface_type_note`): o fluxo é configuração guiada em passos (Mundo → Personagem → Progresso → Pronto) e o wizard vive num `Dialog` modal; a superfície à volta (estado, escolhedor de saves, sistema) é suporte. Partes avaliadas: 1, 3, 4, 6 (onboarding) + 2 (error prevention/controlo — tarefas longas) + 5 (transparência da automação).
**Método:** skill `uxui-evaluator` sem `UXUI_API_KEY` (`api_enriched: false`) sobre o código **já validado por renderização real** (headless Chrome/CDP: fluxos completos, capturas de texto); contraste WCAG calculado dos hex do tema.

## Resultado v3 (pré-reparação)

| | v2.1 (pós) | v3 (pré) |
|---|---|---|
| **Score** | 100 / 100 | **52 / 100** |
| **Banda** | excellent | fair |
| Critical / Warning / Suggestion | 0 / 0 / 0 | 1 / 3 / 4 |
| Achados | 0 | 8 |

Os 8 achados da v3 (todos sobre a superfície nova; a base da v2 mantém-se limpa):

1. `finding-1` · **warning** · `C.1.2.01` — fecho do wizard com tarefa a correr só oferecia «continuar» ou «cancelar e fechar»: a saída não-destrutiva (fechar e deixar a tarefa correr, acompanhada no painel) estava ausente.
2. `finding-2` · **critical** · `C.1.4.01` — «Entrar em sessão» navegava para as Perguntas mesmo com o processo do jogo desligado: a ação primária de todo o fluxo terminava num beco silencioso.
3. `finding-3` · **warning** · `F.1.1.03` — carregar um save `world` (mundo sem jogo) terminava em «Entrar em sessão» sem personagem; a composição do contrato (`load` → `new-character`) não existia na UI.
4. `finding-4` · **warning** · `H.1.1.01` — bloco de progresso todo em `aria-live` (contador muda a cada 0,4–1,1 s) → fila de anúncios enterrava início/fim/erro.
5. `finding-5` · **suggestion** · `C.1.3.01` — worldgen de minutos sem sinal de vitalidade temporal («atualizado há X»/atualizar).
6. `finding-6` · **suggestion** · `I.2.2.02` — ações primárias por linha em `size="small"` (~30 px), desconfortáveis em tátil.
7. `finding-7` · **suggestion** · `H.1.1.01` — descrição do switch «Aleatório» sem associação programática (`aria-describedby`).
8. `finding-8` · **suggestion** · `I.1.1.03` — secção Sistema desenhava «Ligar o jogo» enquanto o estado ainda era desconhecido (loading/erro).

## Re-avaliação pós-reparação (`docs/uxui-audit-ui-v3-rescore.json`)

| | v2.1 | v3 (pré) | **v3.1 (pós)** |
|---|---|---|---|
| **Score** | 100 / 100 | 52 / 100 | **100 / 100** |
| **Banda** | excellent | fair | **excellent** |
| Critical / Warning / Suggestion | 0 / 0 / 0 | 1 / 3 / 4 | **0 / 0 / 0** |

**Delta: 52 → 100 (+48)** — os 8 achados reparados, 0 restantes (rubrica da skill: 100 − 15/critical − 7/warning − 3/suggestion, `findings: []`).

| Achado v3 | Reparação (`web/**`) | Evidência |
|---|---|---|
| 1 · sem saída não-destrutiva (warning) | 3 ações no confirmação de fecho: «Continuar a tarefa» · **«Fechar e deixar correr»** · «Cancelar tarefa e fechar»; o painel continua o progresso com cancelamento | CDP: 3 rótulos no diálogo + «wizard fechou e o progresso continua no painel» |
| 2 · sessão sem jogo (critical) | `useSystemStatus` no painel/wizard: aviso junto do CTA («O jogo está desligado…») e rota para o Controlo («Ligar o jogo») quando `game.running` é falso; no wizard, Alert de aviso no passo «Pronto» | CDP: parar o jogo pelo site → «Entrar em sessão» → `#/controlo` + aviso visível; «Ligar o jogo» recupera |
| 3 · composição `load(world)` (warning) | done card oferece **«Criar personagem»** (mundo pronto falta personagem) para `new_world` concluído ou `world` carregado; wizard ganha modo `character` (3 passos, só `new-character`); mock: `current` fica `null` sem aventureiro e compõe o `adventureId` na tarefa seguinte | CDP: worldgen concluído → «Criar personagem» → wizard 3 passos → «Pronto»; load de save `world` → «Criar personagem» |
| 4 · `aria-live` a cada passo (warning) | contador de passos fora de região live (visível + `LinearProgress` com aria-label); anúncios só em transições (`role=status` com o tipo de tarefa + Alerts concluída/erro/cancelada + fase «Etapa 1 de 2») | DOM: nenhum `[aria-live]` contém «Passo X de Y» (≤ 3 anúncios por tarefa) |
| 5 · espera sem vitalidade (suggestion) | `FreshnessIndicator` no cartão de tarefa («última atualização há X» + «Atualizar o progresso agora» → refetch do `GET .../status`) | DOM: «última atualização há …» no cartão durante o progresso |
| 6 · alvos pequenos (suggestion) | ações primárias («Continuar aventura», «Cancelar tarefa», «Entrar em sessão», «Fechar», «Criar personagem») a `minHeight: 40` px | CDP: alturas medidas ≥ 39 px nas duas superfícies |
| 7 · descrição não ligada (suggestion) | caption `id="aleatorio-descricao"` + `slotProps.input['aria-describedby']` no switch | DOM: `aria-describedby === 'aleatorio-descricao'` |
| 8 · ação sobre estado desconhecido (suggestion) | slot de ação da secção Sistema desativado durante loading/erro («A carregar estado…»/«Estado indisponível») | typecheck + render: ações de jogo só com estado conhecido |

**Verificação final:** `npm run build && npm run lint && npm run typecheck` → 0 erros/0 avisos; suite headless-Chrome/CDP — **46/46 verificações**: empty state + lista de saves, wizard completo (stepper avança com o mock, progresso anima `Passo 1 de 6 → Passo 3 de 6`, cadeia mundo→personagem→pronto), fecho com 3 saídas, cancelamento com confirmação + estado «Tarefa cancelada», load de save ativo e `world`, bloqueio 409 com aviso, secção Sistema (chips jogo/RPC/interface web + loop por campanha), paragem/arranque do jogo pelo site com confirmação, os **9 painéis** (8 anteriores + aventura) sem error boundary nem erros de consola; texto visível só em PT (varredura por palavras EN: restam apenas marca «System One Dwarf», caminho `data/save` e identificadores do contrato/API); contraste dos pares novos ≥ 7,75:1 (WCAG AA 4,5:1).

**Forças reavaliadas (7):** controlo do utilizador em tarefas longas (3 saídas + cancelamento sempre presente), prevenção de erro («Entrar em sessão» nunca termina em beco; ações bloqueadas sobre estado desconhecido; confirmações com consequências), modelo mental íntegro (a composição do contrato inteira na UI), percebibilidade (anúncios só em transições, descrições ligadas, alvos ≥ 40 px), feedback de espera longa (passo/total + ecrã traduzido + frescura + estados finais distintos), divulgação progressiva com predefinições sensatas e transparência da automação (quem age, o que vê, como parar, o que não é afetado).
