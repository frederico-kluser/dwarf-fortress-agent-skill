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
