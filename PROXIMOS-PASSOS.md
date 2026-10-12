# Próximos Passos — System One Dwarf

## Onde estamos

O agente funciona de ponta a ponta e provou-o numa missão real: a corrida `caca-2` (2026-10-09/10) criou mundo e
aventureiro, navegou, caçou e abateu uma mula (`KILL_CONFIRMED` em `scripts/e2e/logs/mission-hunt.jsonl`), com 1 980
decisões JEV, 21 padrões de navegação aprendidos e ~$3,52 de gasto. A suíte está verde (**3597 passed, 2 skipped**), a
base de conhecimento tem **30 129 registos** e a frota JEV (R/A/M/D) + escritor DeepSeek + executor DFHack está ligada
ao backend e à UI. Tudo isto está documentado em `ARQUITETURA.md`, `PROBLEMAS-E-SOLUCOES.md` e `README.md`.

O que falta não é "mais código" — é **poder estatístico e decisão por dados**. A rodada de evolução de prompts de
2026-10-11 (`docs/prompt-builder-evolucao.md`, `bench/relatorios/evolucao-prompt-builder.html`) mediu tudo com
honestidade e mostrou o limite: com 10–19 casos por papel, nenhuma diferença é estatisticamente significativa. O MIMO
ganhou **+20 p.p. em plano-de-caça com p=0,156** e nada foi promovido; o JEV-R regrediu (0,938→0,917) e o prompt v1 foi
mantido; o JEV-A melhorou onde importa (ECE 0,165→0,136, cobertura auto 0,29→0,47) mas o jogo real continua a abstir em
**349/415** decisões de movimento — o humano carrega o loop. Os próximos passos abaixo resolvem exatamente isto:
primeiro dar ao benchmark poder para decidir, depois mudar o sistema nas 12 frentes já desenhadas (`ARQUITETURA.md` §8),
por ondas de dependência.

## Como ler este documento

Há duas rotas: a **ROTA IMEDIATA** (3 passos — fazer o benchmark passar a decidir) e a **ROTA DO SISTEMA** (16 passos —
as 12 mudanças de `ARQUITETURA.md` §8 mais 4 de infraestrutura, reagrupadas em 4 ondas de dependência). Cada passo tem
uma **ficha** com os mesmos sete campos: **O quê** (o que passa a existir) · **Porquê** (o problema real que resolve,
com números e fontes internas — vem sempre antes do *como*) · **Como** (o caminho de implementação concreto: ficheiros,
comandos, mecanismos) · **Desbloqueia** (o que fica possível depois) · **Tradeoff** (o que se paga em troca do ganho) ·
**Esforço · Prioridade** (S/M/L e ALTA/MÉDIA/BAIXA, alinhados com `ARQUITETURA.md` §8 onde existe) ·
**Definição de pronto** (o critério objetivo — como saber que está feito, com comandos/métricas).

Números reais aparecem como estão nas fontes; o que ainda ninguém mediu está marcado **[por medir]**. URLs externas só
aparecem quando a mudança as tem na fonte.

---

# ROTA IMEDIATA — poder estatístico do benchmark

Esta rota é o ponto central do relatório de evolução (`docs/prompt-builder-evolucao.md` §6): o benchmark está montado,
mas **não tem poder para decidir** — e sem isso cada decisão de prompt é opinião. Os três passos abaixo mudam isso por
ordem: dados → re-rodar → confirmar no jogo.

## Passo 1 — Expandir o gold para ≥20 casos rotulados por papel

**O que é o gold:** o conjunto de casos com entrada (estado/perguntas) + resposta esperada rotulada — a régua contra a
qual se comparam prompts. Hoje vivem em `evals/` (66 casos / 217 respostas: JEV-A 19 · JEV-R 13 · JEV-M 10 · JEV-D 10 ·
injeção 14) e são convertidos para o benchmark em `bench/jev/casos/*.jsonl`; os cenários de texto estão em
`bench/cenarios/` (**72 no total**, 8–14 por papel MIMO). Ora, o gold atual tem **10–19 casos por papel**.

Porquê 20: primeiro, o `jev train` (evolução automática por operadores — mais forte do que o `jev compare` usado nesta
rodada) **recusa <20 casos rotulados por pergunta** com `cases.too_few`; segundo, poder estatístico: com 10–19 casos
(jev) e 8–16 cenários (arena) só se detetam efeitos enormes (Δ ≈ 30–40 p.p.), e a ferramenta avisa `cases.too_few —
métricas instáveis`. Com ≥20 pares por papel, efeitos de ~20 p.p. como o do plano-de-caça passam a ter hipótese de
alcançar p<0,05 (número exato de pares necessário por efeito: **[por medir]** — fazer power analysis na primeira
re-rodada).

| Ficha | |
|---|---|
| **O quê** | Levar o gold de 10–19 para **≥20 casos rotulados por papel**: JEV-A/M/D/R (por pergunta tipada) + os 4 papéis MIMO (narrador, perguntas-PT, plano-de-caça, tradutor). |
| **Porquê** | Sem 20 casos/papel o `jev train` recusa e o arena pula os holdouts (`holdoutSkipped` ⇒ "confirmação fraca": sem teste cego a ferramenta não usa a palavra "validado"). Com n pequeno o +20 p.p. do plano-de-caça ficou em p=0,156 — inconclusivo por falta de dados, não por falta de efeito. |
| **Como** | (1) Reutilizar os 66 casos de `evals/` como seed (é assim que os `bench/jev/casos/*.jsonl` nascem). (2) Completar com cenários do domínio a partir dos **gaps da missão real** `caca-2` (`ARQUITETURA.md` §7): navegação (21 padrões + 200 `stuck_events` de `docs/padroes-navegacao.json`), combate (cadeia `p-attack-menu-kill`, mira neck/head, fugas HP<60%), oportunismo (animal visível no caminho = candidato imediato) e re-exploração (local sem rendimento → re-plano). (3) Rótulos por regra derivada sempre que possível (as regras de segurança já existem em `scripts/golden.py`, §14 de `docs/calibracao.md`), revisão humana no resto. (4) Para MIMO: ≥20 cenários por papel em `bench/cenarios/` (hoje 8–14) para o holdout do arena deixar de ser pulado. (5) Manter o espelho obrigatório: `bench/gerar.py --sync-evals` → `golden.py build` → `freeze-holdout --reason` → `bless --reason` → `check` → pytest. |
| **Desbloqueia** | `jev train` (evolução por operadores) em vez de só `jev compare`; holdouts válidos (teste cego) no arena; conclusões reais sobre o +20 p.p. do MIMO e sobre a regressão do JEV-R; suporte n≥30/classe para a calibragem (Passo 11). |
| **Tradeoff** | Horas de autorar + validar rótulos (o custo é humano, não de API) e corridas mais caras (mais casos = mais tokens) **versus** confiança das decisões; risco de ruído de rótulo — mitigado com regras derivadas + dupla revisão dos casos manuais. |
| **Esforço · Prioridade** | M–L · **ALTA** — é o destravamento dos Passos 2 e 11. |
| **Definição de pronto** | `prompt-builder jev train` corre sem `cases.too_few` em todos os papéis; holdout ≥20 casos/papel, intocado e pinado (`HOLDOUT.sha256`); relatório com p-values para todas as comparações. |

Fontes: `docs/prompt-builder-evolucao.md` §2, §6 · `bench/relatorios/evolucao-prompt-builder.html` (limite 06 e "próximo
passo" 01) · `docs/calibracao.md` §14 · `ARQUITETURA.md` §7.

## Passo 2 — Re-rodar a arena com 3+ iterações e decisão por significância

Uma rodada só serve se terminar em **decisões**. A de 2026-10-11 terminou em empates: 4 papéis MIMO sem promoção (o campeão de cada papel é o próprio prompt original), o JEV-A/M/D ganharam sobretudo na banda auto com `criterios-v2`, e o JEV-R regrediu (0,938→0,917) e ficou no v1. Mas a evolução foi **cortada em 2 iterações** — com 3 o estimador dava `estimate.high` > US$ 10 e o tool recusava com `--budget 10` (exit 2), e o narrador, que correu com 3 antes do corte, convergiu logo na 1ª. Mais: o datagen falhou por timeout de 180 s em 3 das 4 sessões.

| Ficha | |
|---|---|
| **O quê** | Nova rodada completa do benchmark (4 comparativos JEV + 4 arenas MIMO) com **3+ iterações de treino**, orçamento maior e regra de promoção escrita: **só promove com p<0,05 (gate max-T a 5%) ou efeito grande e consistente nas repetições**; o resto é aposentado ou mantido por decisão registada. |
| **Porquê** | O benchmark existe para decidir prompts; sem poder estatístico as conclusões são fracas (a ferramenta avisa-o). A rodada custou **$6,92** com 2 iterações — a evolução real ficou por fazer por causa do teto, e o +20 p.p. do plano-de-caça (p=0,156) pode ser real e está por confirmar. |
| **Como** | Com o gold ≥20 (Passo 1): pinar os `stages` do datagen (ou gerar cenários antecipadamente — o timeout de 180 s estragou 3/4 sessões); estimar sempre com `prompt-builder train … --dry-run` antes de gastar; correr com `--budget` maior (3+ iterações, 3+ repetições para medir `flipRate`); decidir por p-value + consistência; registar cada promoção/aposentação em `docs/prompt-builder-evolucao.md` e aplicar os diffs no código (`fleet.py`, `narrator.py`, `questions.py`, `planning.py`/`hunt_plan.py`, `translator.py`) com o espelho golden verde. |
| **Desbloqueia** | Decisão sobre o +20 p.p. do plano-de-caça; possível promoção de prompts de texto hoje "sem mudança"; re-avaliação informada do JEV-R (a regressão de −1,9 p.p. pode ser ruído de n pequeno); baseline sólido para o A/B do Passo 3. |
| **Tradeoff** | Dinheiro e tempo: 3+ iterações estimadas acima de $10/corrída **[por medir no `--dry-run`]**, teto diário da máquina US$ 20; risco de promover por sorte estatística se a regra de significância não for seguida à letra. |
| **Esforço · Prioridade** | M (mais orçamento e disciplina do que código) · **ALTA**. |
| **Definição de pronto** | 3+ iterações completas por papel; tabela antes→depois com p-values e `flipRate`; cada prompt promovido, mantido ou aposentado por regra escrita; alterações aplicadas no código com `golden.py check` + `pytest -q` verdes. |

Fontes: `docs/prompt-builder-evolucao.md` §3–§6 · `bench/relatorios/evolucao-prompt-builder.html` (secções 02, 04, 05) ·
`bench/README.md`.

## Passo 3 — A/B em produção (shadow runs na sessão real)

O bench mede respostas a cenários; o jogo real tem outra distribuição — 349/415 abstensões de movimento, guards a
abortar, overlays de tutorial, presas a fugir entre z. Um ganho de bench que não aparece na sessão real não vale nada, e
o mecanismo para o medir já existe: `scripts/calibrate.py from-decisions` extrai rótulos do log real (aprovada/executada
= acerto; rejeitada/abortada = erro).

| Ficha | |
|---|---|
| **O quê** | Correr o loop real com as duas versões de cada prompt promovido: a variante decide **em sombra** (regista, não executa) e compara-se com o que a versão em produção decidiu e executou. |
| **Porquê** | Confirmar (ou descartar) no jogo os ganhos medidos no bench — e recolher dados de produção que o bench nunca dá (viés de automação, guards, custo real por decisão). |
| **Como** | Flag de variante por campanha (`SOD_*` / meta da campanha, padrão já usado em `agent/config.py`); decisões shadow gravadas nas stores com `issues: ["shadow"]` e **nunca executadas** (a banda é teto, não permissão); comparar distribuição de bandas, precisão via proxy `from-decisions`, custo e latência entre as duas versões. |
| **Desbloqueia** | Confirmação de produção dos ganhos do Passo 2; amostras reais para a calibragem por risco (Passo 11); evidência para subir a cobertura auto com segurança. |
| **Tradeoff** | Chamadas duplicadas (JEV é tostões — $0,042/Mtok entrada, saída grátis — mas os papéis DeepSeek não) e complexidade de logging **versus** decisões com evidência de sessão real. |
| **Esforço · Prioridade** | S–M · **ALTA** (após o Passo 2). |
| **Definição de pronto** | Relatório A/B de ≥1 sessão real (dados pareados por passo de decisão); decisão de manter/trocar cada prompt registada; zero ações shadow executadas no jogo. |

Fontes: `docs/calibracao.md` §13 (proxy de rótulos reais) · `ARQUITETURA.md` §3 (bandas) · `PROBLEMAS-E-SOLUCOES.md`
§H1.

---

# ROTA DO SISTEMA — as 12 mudanças em ondas

As 12 mudanças recomendadas de `ARQUITETURA.md` §8 (prioridade: **alta 5** — 01, 02, 03, 04, 07 · **média 6** · **baixa
1**; esforço: **S 4** · **M 8**), mais 4 itens de infraestrutura de `PROBLEMAS-E-SOLUCOES.md` §H3/§H4/§H6, reagrupadas
por **dependência** em 4 ondas: a Onda 1 destrava o resto (decisões melhores no terreno), a Onda 2 dá memória viva, a
Onda 3 dá robustez, a Onda 4 fixa tudo em processo.

## Onda 1 — Destrava o resto (decidir bem no terreno)

### Passo 4 (Mudança 02) — Skills de combate nas decisões de ataque/fuga

Dwarf Fortress é *skill-driven*: a diferença entre um golpe que mata ("fracturing the skull and tearing the brain") e um
que é desviado está nas skills do aventureiro — e as decisões atuais de combate **ignoram-nas completamente**. Na
`caca-2` foram precisas 5 estocadas numa mula *tame*; contra presa selvagem não há margem para golpes desperdiçados, e a
seleção de alvo/parte vital continua manual (`ARQUITETURA.md` §7 e §9). Os agentes de jogos da literatura leem todos a
capacidade antes de escolher (PokéLLMon, PokéChamp, NetHack Challenge).

| Ficha | |
|---|---|
| **O quê** | Um **retrato de prontidão de combate** (~10 números por entidade: skill efetiva da arma via `dfhack.units.getEffectiveSkill`, Fighter/Dodger/Shield User/Armor User, atributos relevantes, dor, exaustão) injetado no estado que o JEV-A vê, para decidir atacar / fugir / mirar com vantagem real — incluindo a regra de fuga **HP < 60%**. |
| **Porquê** | Decisões de vida e morte sem informação de capacidade são apostas; o dicionário do jogo (arma↔armadura, mira `c neck` → paralisia, `d head` → morte) só paga se o decisor souber o que o aventureiro sabe fazer. |
| **Como** | Snapshot Lua por entidade (`dfhack.units.getEffectiveSkill` e companhia) → normalização para um bloco compacto no state (orçamento ~2K do estado, `ARQUITETURA.md` §3) → candidatos gerados por código condicionados pela vantagem (atacar com skill≥limiar; `flee` sob dano/dor; mira letal quando a skill compensa) → JEV-A escolhe entre eles com as bandas normais. |
| **Desbloqueia** | Kills eficazes e fugas certas; valida o plano de caça (Passo 13) e destrava o treino antes de caçar (Passo 14); menos golpes desperdiçados por combate. |
| **Tradeoff** | State maior para o decisor + parsing da saída Lua (pode quebrar com updates do DFHack — mitigar com os testes de snapshot, 42 já existem em `dfhack/test_snapshot.py`) **versus** sobrevivência e eficácia. |
| **Esforço · Prioridade** | M · **ALTA** (mudança 02 de `ARQUITETURA.md` §8). |
| **Definição de pronto** | Snapshot de skills testado (unit tests + leitura real) e a decisão de ataque/fuga usa a vantagem de skill — caso de teste que mostra um ataque rejeitado por desvantagem e uma fuga disparada abaixo de 60% HP. |

Fontes: `ARQUITETURA.md` §8 #02, §6 (cadeia de combate), §7 · [Wiki DF
Combat](https://dwarffortresswiki.org/index.php/Combat) · [Wiki DF
Material](https://dwarffortresswiki.org/index.php/Material) · [PokéLLMon](https://arxiv.org/abs/2402.01118) ·
[PokéChamp](https://arxiv.org/abs/2503.04094) · [NetHack Challenge](https://arxiv.org/abs/2203.11889).

### Passo 5 (Mudança 04) — Movimento como macro-ação

A maior dívida medida do sistema está aqui: a missão `caca-2` gastou **415 decisões JEV-A** só em micro-movimento e
**349 acabaram em abstain** — o humano carrega o loop e 415 decisões foram queimadas em mecânica que não precisa de
julgamento (`ARQUITETURA.md` §7). O Jev deve decidir *para onde* e *com que urgência*; o código sabe andar.

| Ficha | |
|---|---|
| **O quê** | O JEV-A decide **destino + urgência** (ex.: `navigate_to:<zona>`, `Safe`/`Pressing`/`Critical`) e o código faz o pathing passo a passo (BFS planar `dfb-nav.lua`, `move DIR times=N`). |
| **Porquê** | Micro-decisões de movimento dominam o log, esgotam a paciência do humano (abstain escala para pergunta) e gastam tokens; o pathing é determinístico e já está resolvido (`ARQUITETURA.md` §6). |
| **Como** | Candidatos de macro-ação gerados do estado (destinos relevantes + urgência) → `MacroExecutor` percorre o caminho com os guards já existentes (`guard:focus_changed`, `guard:threat_appeared`, `guard:adventurer_dead`, …) → `StuckDetector` cobre paragens (3 passos sem mudança → exploração forçada; 2.ª vez → re-plano). Movimento avança o tempo do jogo — manter `--stop-on-threat`. |
| **Desbloqueia** | Fim do domínio do micro-movimento no log; o JEV-A passa a gastar decisões em combate, oportunismo e exploração; com o Passo 6 ataca diretamente as 349/415 abstensões. |
| **Tradeoff** | Menos controlo granular por passo (uma rota má escolhida só é corrigida por guard/stuck) **versus** muito menos indecisão, custo e tempo de sessão. |
| **Esforço · Prioridade** | M · **ALTA** (mudança 04). |
| **Definição de pronto** | Decisões de movimento passam a ser macro-ações (destino+urgência) no log; a contagem de decisões JEV-A por missão cai drasticamente face a 415 **[por medir na próxima sessão]**; cobertura auto sobe sem perder precisão. |

Fontes: `ARQUITETURA.md` §8 #04, §6 (movimento/`goto`), §7 (349/415) · `docs/padroes-navegacao.json` (stats: auto 12 /
hitl 54 / abstain 349) · [Voyager](https://arxiv.org/abs/2305.16291) · [RouteLLM](https://arxiv.org/abs/2406.18665).

### Passo 6 (Mudança 03) — Evidência antes de abstain

Abstain é o resultado mais caro do loop: não age, escala e põe o humano a decidir. Mas muitas abstensões não são
indecisão — são **falta de evidência**. A predição seletiva ensina o truque: antes de desistir, colher mais estado e
re-decidir; isso sobe a cobertura sem baixar o piso de risco, desde que as confianças estejam calibradas (o ECE de
JEV-A desceu de 0,165 para 0,136 — ainda longe do "bom" ≤0,05, `docs/calibracao.md` §4).

| Ficha | |
|---|---|
| **O quê** | Quando a confiança cai abaixo do limiar `auto`, o agente recolhe **mais estado** (consultas DFHack baratas: unidades próximas, terreno, inventário, gamelog), monta um "state round 2" e **re-decide UMA vez**; só depois escala (abstain/pergunta). |
| **Porquê** | Cada abstain trava a missão; e o `abstain` do JEV-A é a fraqueza conhecida do sistema (over-abstention — exatamente o que o `criterios-v2` começou a corrigir: cobertura auto 0,29→0,47 com precisão a subir 0,91→0,94). |
| **Como** | No despacho por banda: se `band == abstain` (ou `hitl` sem humano disponível), injetar evidência extra num re-prompt compacto → nova chamada JEV-A (JEV custa $0,042/Mtok, saída grátis) → se continuar incerto, escala normalmente. **Uma única re-decisão por passo** — nunca um laço; registar `evidence_recollected` nos `issues` da decisão. |
| **Desbloqueia** | Amplifica o ganho da calibragem (Passo 11: confianças calibradas tornam a re-decisão segura); menos perguntas ao humano; converte estado extra (Passos 4 e 5) em decisões `auto`. |
| **Tradeoff** | +1 chamada Jev por passo incerto + o atraso de um ciclo **versus** muito menos escaladas; cuidado: re-decidir sobre o mesmo estado não muda nada — a evidência nova tem de ser real e nova. |
| **Esforço · Prioridade** | S · **ALTA** (mudança 03). |
| **Definição de pronto** | Cobertura auto sobe sem perder precisão, medido em `evals/` e na sessão real; cada abstain restante traz registo da evidência recolhida antes de escalar; zero laços de re-decisão (máx. 1 por passo, testado). |

Fontes: `ARQUITETURA.md` §8 #03 · `docs/calibracao.md` §4 (ECE) · `bench/relatorios/evolucao-prompt-builder.html`
(JEV-A: cobertura 0,29→0,47, precisão 0,91→0,94) · [ReCoVERR](https://aclanthology.org/2024.findings-acl.767/).

## Onda 2 — Memória viva

### Passo 7 (Mudança 01) — Diálogos retrieváveis

Hoje cada sessão começa sem saber o que foi dito antes — lacuna confirmada: as conversas com NPCs (dicas de caça,
preços, avisos) desaparecem quando a sessão morre. O padrão da indústria é transcrever, vetorizar e recuperar por
recência + importância + similaridade (Letta, Mem0, MS Agent Framework).

| Ficha | |
|---|---|
| **O quê** | Memória conversacional: cada mensagem é transcrita (quem, quando, vetor, importância) e recuperada por **recência + importância + similaridade**, com *namespace* por interlocutor e cartão de perfil por NPC; extração de factos/palavras dadas é assíncrona. |
| **Porquê** | Continuidade real entre sessões: o agente passa a poder cumprir o que prometeu, evitar o que já disse e perguntar ao humano coisas que já sabe. |
| **Como** | Schema de transcrição por mensagem na memória CoALA (busca híbrida FTS5+vetor com RRF já existe em `coala.py`) → score combinado (recência, importância, similaridade) no top-K do JEV-R → namespace por interlocutor + cartão de perfil → extração de factos em background (narrador/JEV-M). **Nunca apagar a transcrição bruta** — a extração é derivada e pode estar errada. |
| **Desbloqueia** | NPCs que "lembram"; perguntas ao humano informadas; base de conteúdo para o histórico de estado (Passo 8); dados para o cartão de perfil na UI. |
| **Tradeoff** | Mais armazenamento, **PII no disco** (diálogos com humanos) e custo de extração por mensagem **versus** continuidade real — exigir mascaramento de chaves e política de retenção. |
| **Esforço · Prioridade** | M · **ALTA** (mudança 01). |
| **Definição de pronto** | Conversar com alguém puxa automaticamente o contexto relevante da conversa anterior (teste: nova conversa com o mesmo NPC menciona tema dito antes); a transcrição bruta persiste e as extrações são citáveis. |

Fontes: `ARQUITETURA.md` §8 #01 · [Letta](https://github.com/letta-ai/letta) · [Mem0](https://github.com/mem0ai/mem0) ·
[MS Agent Framework](https://github.com/microsoft/agent-framework) · [arXiv
2406.00057](https://arxiv.org/abs/2406.00057).

### Passo 8 (Mudança 07) — Estado com histórico e anti-repetição

O agente repete ações que já falharam porque o estado só mostra o presente. O `StuckDetector` (23 testes, 200
`stuck_events`) apanha o congelamento depois de acontecer; esta mudança **previne-o** antes — e respeita a curva em U da
atenção de contexto (o que está no início e no fim do contexto é o que o modelo lembra).

| Ficha | |
|---|---|
| **O quê** | No state: bloco **"últimas decisões + resultado"** e lista **"falhados — não repetir"**; detetor de ações repetidas (janela 3–5); conteúdo disposto em ordem U-curve (crítico no início e no fim do prompt). |
| **Porquê** | Laços de repetição são a classe de falha silenciosa mais comum e custosa (o modo pausa, os overlays e as aprovações sem execução ensinaram que, sem erros, o progresso é o oráculo — `PROBLEMAS-E-SOLUCOES.md` §D18, §E26). |
| **Como** | Anel das últimas N decisões com resultado (`executed`/`guard`/rejeitada) já existe nas stores — comprimi-lo para o state (~2K de orçamento); lista negativa derivada de falhas; janela 3–5 para detetar laço (mesma ação 3× → trocar candidato, já em `stuck.py`); posicionar o histórico no início/fim do prompt. |
| **Desbloqueia** | Fim dos laços de repetição; menos `stuck_events`; decisões informadas sem chamadas extra; complementa o Passo 5 (macro-ações repetidas passam a ser detetadas). |
| **Tradeoff** | Mais tokens no estado (a narrativa tem de ceder quando o histórico cresce) **versus** fim de laços — dentro do orçamento ~2K. |
| **Esforço · Prioridade** | S · **ALTA** (mudança 07). |
| **Definição de pronto** | Teste de regressão em que uma ação que falhou não volta a ser escolhida no passo seguinte; na sessão real, queda de `stuck_events` por hora **[por medir]**; cobertura auto sobe sem perder precisão. |

Fontes: `ARQUITETURA.md` §8 #07, §6 (StuckDetector) · `docs/padroes-navegacao.json` (200 `stuck_events`) · [Chroma ·
context rot](https://research.trychroma.com/context-rot) · [Liu et al. TACL'24](https://arxiv.org/abs/2307.03172) ·
[Generative Agents](https://arxiv.org/abs/2304.03442).

### Passo 9 (Mudança 08) — Eventos como diário de bordo (event-sourcing)

Os 13 episódios do narrador na `caca-2` são texto corrido sem índice: não se conseguem recuperar por importância nem
distinguir "mata-se uma mula" de "come-se um pão". O event sourcing resolve isso na escrita, para não ter de adivinhar
na leitura.

| Ficha | |
|---|---|
| **O quê** | Eventos **append-only** (o que mudou por evento), com etiqueta de importância, recuperação por recência + importância + relevância, e reflexões periódicas agregadas. |
| **Porquê** | Episódios sem estrutura não se aproveitam — e a memória que não se consulta é custo puro (Generative Agents; Event Sourcing de Fowler). |
| **Como** | Schema de evento na camada episódica do CoALA (por campanha, `campanhas/<slug>/…/memory/coala.sqlite`); scoring de importância na escrita (JEV-M já decide `record_type`); recuperação composta no top-K do JEV-R; reflexões periódicas como eventos derivados. |
| **Desbloqueia** | Memória consultável que alimenta o Passo 8 (histórico comprimido) e o narrador; menos re-contagem de história; auditoria temporal da missão. |
| **Tradeoff** | Custo de *scoring* na escrita + complexidade de schema **versus** memória que se consulta e aproveita. |
| **Esforço · Prioridade** | M · **MÉDIA** (mudança 08). |
| **Definição de pronto** | Uma consulta por importância/relevância devolve o episódio certo (teste com eventos da `caca-2`); o narrador passa a consumir eventos estruturados; nada é reescrito — só acrescentado (append-only testado). |

Fontes: `ARQUITETURA.md` §8 #08 · [Generative Agents](https://arxiv.org/abs/2304.03442) · [Event
sourcing](https://martinfowler.com/eaaDev/EventSourcing.html).

## Onda 3 — Robustez das decisões e dos planos

### Passo 10 (Mudança 05) — Shuffle + voto nas opções de decisão

Modelos escolhem a 1.ª opção mais vezes do que deviam (viés de posição). O cuidado "baralhar a ordem dos candidatos" já
está nos contratos do JEV (`ARQUITETURA.md` §2.1); falta medir e neutralizar o viés que sobra.

| Ficha | |
|---|---|
| **O quê** | Nas decisões críticas, 2–3 permutações da lista de candidatos + voto por maioria; a divergência entre repetições é usada como sinal de baixa estabilidade. |
| **Porquê** | Decisões de classe `costly`/`irreversible` não podem depender da ordem em que as opções apareceram; o voto torna a decisão imparcial e **auditável** (mede-se o flip rate). |
| **Como** | Só nas decisões críticas (combate, `costly`+): N chamadas Jev com ordens baralhadas (mesma seed de baralhamento registada) → maioria = decisão; empate/divergência total = empurrar para `hitl` ou pedir evidência (Passo 6). |
| **Desbloqueia** | Decisões estáveis e auditáveis; métrica de estabilidade por candidato/papel; mais confiança nos resultados do Passo 2 (repetições independentes). |
| **Tradeoff** | 2–3× chamadas Jev nas decisões críticas (custo ínfimo: $0,042/Mtok entrada, saída grátis) **versus** imparcialidade; latência extra por decisão crítica. |
| **Esforço · Prioridade** | S · **MÉDIA** (mudança 05). |
| **Definição de pronto** | Voto ativo nas ações de classe `costly`/`irreversible`; taxa de inversão (`flipRate`) reportada por sessão **[por medir]**; empates tratados por regra fixa (nunca `auto`). |

Fontes: `ARQUITETURA.md` §8 #05 · [Robustness MCQ](https://arxiv.org/abs/2309.03882) · [Diagnóstico por
permutação](https://arxiv.org/abs/2607.20864).

### Passo 11 (Mudança 06) — Calibragem de limiares por risco

"Olhar para o lado" não é "atacar" — e um único limiar `auto = 0,90` trata as duas coisas igual. A política por classe
de risco já está **implementada** (`src/system_one_dwarf/jev/risk.py`, testada em `tests/jev/test_risk_classes.py`:
`reversible` 0,90 · `costly` ≥0,95 · `irreversible` ≥0,95 · `destructive` com `auto` desligado); falta a **tabela de
calibração por snapshot** com suporte real — hoje a maioria das classes tem `threshold: null` porque há ~25 respostas
`action` no total (mínimo honesto: n≥30 por classe, limite superior de Wilson 95%).

| Ficha | |
|---|---|
| **O quê** | Curvas **risco-cobertura** sobre os cenários rotulados + dados reais; um limiar por classe de ação, guardado por snapshot do modelo; regra: **primeiro calibrar, depois mexer no prompt**. |
| **Porquê** | Sem calibragem, as bandas são uma aposta; com ela, cada ponto de cobertura automática tem piso de risco medido — e é a única forma honesta de subir a autonomia. Os limiares não transferem entre snapshots do JEV (`ARQUITETURA.md` §2.1). |
| **Como** | `scripts/calibrate.py online` (gasta tokens de entrada) → `report --evals … --table calibration.json` (aceita também as `rows` do `jev.mjs eval` e amostras do log via `from-decisions`) → `thresholds` para ver os limiares efetivos → `--mark-verified` só com ato humano explícito e suporte ≥100 (e nunca na classe `destructive`). |
| **Desbloqueia** | Cobertura automática real e mensurável; base para subir limiares com segurança; re-avaliação informada da regressão do JEV-R; alimenta o A/B do Passo 3. |
| **Tradeoff** | Trabalho de calibragem e **manutenção contínua** (snapshot novo ⇒ recalibrar) **versus** cobertura auto com piso de risco garantido; o proxy do log real é enviesado (só o que passou o limiar executa) — complementa os cenários, nunca os substitui. |
| **Esforço · Prioridade** | M · **MÉDIA** (mudança 06; sobe de prioridade logo que o Passo 1 entregue suporte). |
| **Definição de pronto** | Tabela de calibração com entradas por classe (n≥30 aceite), relatório risco-cobertura por classe/papel, e `calibrate.py thresholds` confirma os limiares efetivos carregados pelo loop. |

Fontes: `ARQUITETURA.md` §8 #06 · `docs/calibracao.md` §5.3, §12, §13 · [RouteLLM ·
calibração](https://arxiv.org/abs/2406.18665).

### Passo 12 (Mudança 09) — Ciclo gerar → testar → criticar nos planos

Hoje, plano falhado = tentar outra vez sem aprender nada com a falha. O LLM-Modulo (ICML'24) mostrou que a correção está
no **feedback de críticos**: re-gerar com a crítica incorporada, com críticos de formato (JSON parsável + chaves
obrigatórias) primeiro e críticos de restrições (em código) depois — e as ablações mostram que os críticos se compõem
(só todos juntos se apanham os ganhos grandes).

| Ficha | |
|---|---|
| **O quê** | Pipeline de planos: gerar → **crítico de formato** → **críticos de restrições em código** → re-gerar com feedback completo (plano anterior + crítica) → limite de tentativas; condições de sucesso do plano verificadas **em código**. |
| **Porquê** | A missão real mostrou re-plano frequente (oportunismo + validação por ação); sem críticos, cada re-plano repete os mesmos erros de formato e de restrição. |
| **Como** | Reutilizar o plano estruturado existente (`scripts/e2e/hunt_plan.py`, `planning.py`): validador de schema (crítico de formato), verificadores de restrições (ex.: waypoints alcançáveis, sapientes nunca alvo), metacontrolador que re-promove com o prompt original + resposta anterior + feedback (só a 1.ª crítica já traz o grosso do ganho); teto de tentativas por **custo**, não por ótimo perfeito. |
| **Desbloqueia** | Planos robustos e verificáveis; menos re-planos cegos; funciona a par com o schema do Passo 13; dá uso aos erros como sinal. |
| **Tradeoff** | Mais chamadas LLM quando as coisas correm mal (o DeepSeek é onde vive o custo: $0,30/$1,20 por Mtok) **versus** planos que sobrevivem ao contacto com o jogo; exigir limite de tentativas para não estourar orçamento. |
| **Esforço · Prioridade** | M · **MÉDIA** (mudança 09). |
| **Definição de pronto** | Plano rejeitado por crítico é re-gerado com feedback e só avança quando os críticos de código aprovam; teste de regressão do ciclo com limite de tentativas; custo do ciclo medido por plano **[por medir]**. |

Fontes: `ARQUITETURA.md` §8 #09 · [LLM-Modulo ICML'24](https://arxiv.org/abs/2402.01817) · pesquisa verificada na
memória do projecto (arXiv 2411.14484: orçamento de iterações por custo, feedback completo > binário, críticos
compõem-se — ablação arXiv 2405.20625).

### Passo 13 (Mudança 10) — Planos com schema fixo, waypoints e effort dinâmico

O handoff Escritor→Decisor é a costura mais frágil da arquitetura: o planificador escreve texto, o JEV-A decide a partir
dele — e qualquer instabilidade no formato do plano vira decisão errada.

| Ficha | |
|---|---|
| **O quê** | Planos com **campos fixos** (onde estamos → zonas prováveis de animais → rota → regra de oportunismo), granularidade de **waypoints** (coordenadas, nunca teclas), effort **`medium` por omissão** e `max` só em decisões raras/com muitos ramos. |
| **Porquê** | Confiança no handoff: o decisor precisa de entradas previsíveis, e o custo de raciocínio deve seguir a raridade da decisão (AdaPlan-H; prática de *reasoning effort*). |
| **Como** | Schema JSON validado no builder do plano (falha = crítico de formato do Passo 12); waypoints como pontos consumidos pelo pathing do Passo 5 (cuidado: `goto` é planar — presas fogem entre z); `reasoning: {"effort": …}` **nested** (o top-level não aceita `max` — `ARQUITETURA.md` §2.2); contratos `neverBreak` como os do benchmark (`bench/configs/`). |
| **Desbloqueia** | Handoff plano→JEV-A confiável; planos reutilizáveis e comparáveis no bench; menos tokens por plano; reforça o Passo 12. |
| **Tradeoff** | Rigidez do schema (menos liberdade criativa do LLM) **versus** confiabilidade do handoff; waypoints exigem o pathing multi-z ainda por resolver (limitação conhecida, `ARQUITETURA.md` §9). |
| **Esforço · Prioridade** | M · **MÉDIA** (mudança 10). |
| **Definição de pronto** | Todos os planos passam por validação de schema (teste em que plano sem waypoints/sem regra de oportunismo é rejeitado); effort `medium` por omissão observado no código e documentado. |

Fontes: `ARQUITETURA.md` §8 #10, §2.2 · [AdaPlan-H](https://aclanthology.org/2026.findings-acl.77/) · [reasoning
effort](https://platform.openai.com/docs/guides/reasoning).

### Passo 14 (Mudança 11) — Treinar antes de caçar

A wiki do jogo é clara: sparring só a partir de `Proficient`. Na `caca-2` a presa era uma mula *tame* e mesmo assim
foram 5 estocadas — contra presa selvagem, um aventureiro sem treino é comida.

| Ficha | |
|---|---|
| **O quê** | Se a skill da arma < `Proficient`, o gerador de candidatos prefere **alvos de baixo risco** ou **sessões de treino (sparring)** antes da caça a sério. |
| **Porquê** | Sobrevivência e taxa de sucesso: caçar sem treino é perder personagens e sessões inteiras. |
| **Como** | Gate no gerador de candidatos usando o snapshot de skills do Passo 4: abaixo do limiar → candidatos `train:sparring` / `hunt:low_risk`; o objetivo de missão aceita o treino como sub-objetivo (contrato de objetivo já existe em `campanhas/*/objetivo.json`). |
| **Desbloqueia** | Caça real com margem de segurança; menos mortes estúpidas; dá propósito à skill de combate do Passo 4. |
| **Tradeoff** | Mais tempo de sessão até à primeira caça (custo de sessão e paciência humana) **versus** sucesso — por isso é prioridade baixa. |
| **Esforço · Prioridade** | S · **BAIXA** (mudança 11; só faz sentido depois do Passo 4). |
| **Definição de pronto** | Personagem com skill da arma abaixo de `Proficient` nunca recebe candidato de caça a presa perigosa (teste de candidatos); missão de treino completa antes da primeira caça numa sessão real **[por medir]**. |

Fontes: `ARQUITETURA.md` §8 #11 · [Wiki DF Combat (sparring)](https://dwarffortresswiki.org/index.php/Combat).

## Onda 4 — Processo e infraestrutura

### Passo 15 (Mudança 12) — Regressão de prompts com golden set em CI

Prompts e modelos mudam sem aviso — e a rodada de outubro mostrou como é fácil concluir demais com n pequeno. A boa
notícia: a infraestrutura **já existe e está verde**: `evals/golden/` tem **123 casos / 401 respostas** com hold-out de
**22 casos** pinado (`HOLDOUT.sha256`) e `scripts/golden.py check` deteta `prompt-drift`/`build-drift` offline em ~1 s.
Falta correr isto **sempre**, em CI, e crescer o hold-out para ~20 por papel.

| Ficha | |
|---|---|
| **O quê** | Golden set versionado (50–200 casos representativos, corrigidos por processo) correndo em CI + **hold-out ~20 casos intocável** (anti-overfit) — nunca usado para afinar. |
| **Porquê** | Duas derivas silenciosas: o prompt mudou (o pedido que os builders constroem já não bate certo) e o modelo mudou (as respostas deixam de bater com as etiquetas). Sem gate, ninguém nota até a missão falhar. |
| **Como** | Workflow de CI (Passo 17) que corre `python3 scripts/golden.py check` + `pytest -q`; disciplina já descrita em `docs/calibracao.md` §14: afina-se com `tune-view`/`dev`, `bless --reason` só para mudanças intencionais de prompt, `run --split holdout` **só no fim**; o hold-out cresce com o Passo 1. |
| **Desbloqueia** | Gate automático para os Passos 2–3 (e para qualquer mudança de `fleet.py`); proteção contra deriva de prompt/modelo; baseline versionado (`evals/golden/baseline.json`). |
| **Tradeoff** | Overhead de processo (casos + CI + disciplina de `bless`) **versus** proteção; armadilha: quem afina contra o hold-out destrói o seu valor — a disciplina é parte do deliverable. |
| **Esforço · Prioridade** | M · **MÉDIA** (mudança 12; sem URLs de fonte na base — prática de *golden sets* e anti-overfit, citada em texto em `ARQUITETURA.md` §8). |
| **Definição de pronto** | CI corre `golden.py check` em cada PR; hold-out ≥20 casos/papel, pinado e nunca aberto em tuning; `baseline.json` preenchido após a primeira corrida online aceite. |

Fontes: `ARQUITETURA.md` §8 #12 · `docs/calibracao.md` §14, §15 · `evals/golden/CHANGELOG.md`.

### Passo 16 — Persistir os stores em SQLite

`decisions` (5000), `questions` (2000) e `approvals` (cap 20) são anéis **em memória**: um restart do backend apaga a
auditoria da sessão — e com ela os dados que a calibragem precisa.

| Ficha | |
|---|---|
| **O quê** | Os três stores passam a SQLite (o projecto já tem o padrão CoALA/SQLite), mantendo exatamente a API REST atual (paginação `?limit=&before=`, cabeçalho `X-Source`). |
| **Porquê** | Sessões autónomas longas exigem auditoria persistente e recuperação após crash (`PROBLEMAS-E-SOLUCOES.md` §H3; `ARQUITETURA.md` §9); sem histórico persistente não há `from-decisions`. |
| **Como** | Uma base por campanha ao lado de `coala.sqlite`/`gastos.sqlite` (dir `0700`, ficheiros `0600`); migração de arranque a partir dos anéis como fallback; contratos REST/WS inalterados (os campos aditivos são tolerados pela UI). |
| **Desbloqueia** | Auditoria persistente; amostras reais para o Passo 11; recuperação após crash; base para o watchdog do Passo 18. |
| **Tradeoff** | I/O por decisão e trabalho de migração **versus** recuperação; atenção a PII/chaves (as chaves ficam na base da campanha, nunca nos stores). |
| **Esforço · Prioridade** | S–M · **MÉDIA**. |
| **Definição de pronto** | Restart do backend mantém decisões/perguntas/aprovações (teste de persistência); paginação idêntica à atual; suíte verde. |

Fontes: `PROBLEMAS-E-SOLUCOES.md` §H3, §C13 · `ARQUITETURA.md` §4 (stores rotativos), §9.

### Passo 17 — CI no GitHub (suíte + lint + web)

O repo está publicado **sem CI**: as 2 falhas pré-existentes do pytest que a rodada de outubro teve de corrigir passaram
despercebidas durante semanas. O histórico já cumpre Commits Convencionais — falta só o gate.

| Ficha | |
|---|---|
| **O quê** | GitHub Actions em PR e `main`: `pytest -q` (3597 testes, todos offline por desenho), lint do Python, *web gates* (`npm run build` + `npm run lint` em `web/`), `golden.py check` (Passo 15); depois, tags SemVer + changelog. |
| **Porquê** | Regressões de código e de DF/DFHack devem ser apanhadas antes do utilizador — e a rota do sistema inteira mexe em `fleet.py`, `dfhack/` e `web/`. |
| **Como** | Um workflow único e determinístico (sem chaves de API — a suíte é offline); cache de dependências (pip/npm); sharding da suíte só se o tempo o exigir **[por medir]**. |
| **Desbloqueia** | Gate para todos os outros passos; releases versionadas; confiança para contribuições externas. |
| **Tradeoff** | Minutos de CI por push e manutenção do workflow **versus** proteção contínua. |
| **Esforço · Prioridade** | S–M · **MÉDIA**. |
| **Definição de pronto** | Workflow verde em PR e `main`; badge no `README.md`; primeira tag SemVer com changelog a partir dos Commits Convencionais. |

Fontes: `PROBLEMAS-E-SOLUCOES.md` §H4 · `ARQUITETURA.md` §9 (processo) · `docs/ci.md`.

### Passo 18 — Watchdog global de sessão

O `StuckDetector` protege o **personagem**; ninguém protege a **sessão**. As falhas mais caras do projecto foram
silenciosas: overlays a engolir input (teclas reportavam `ok:true` com o tick congelado), aprovações sem execução, loops
em pausa — sem exceções, só o "o estado não muda" denuncia.

| Ficha | |
|---|---|
| **O quê** | Watchdog ao nível da sessão: sem progresso em N minutos → re-plano → escala para humano → kill-switch; a lógica do `StuckDetector` estendida ao loop inteiro. |
| **Porquê** | Em ambiente sem erros, o progresso é o oráculo (`PROBLEMAS-E-SOLUCOES.md` §D18, §E26, §H3) — e sessões autónomas longas só são possíveis se ninguém tiver de estar a olhar. |
| **Como** | Reusar o hash de estado + eventos (`action_executed`, `decisions`, gastos): sem progresso em N min → escalada gradual (re-plano → pergunta PT → kill-switch); configurável por campanha; nunca dispara em `paused`/kill-switch; cada disparo vira evento na biblioteca de padrões (hoje 200 `stuck_events`). |
| **Desbloqueia** | Sessões autónomas longas com recuperação; menos babysitting; dados de falha silenciosa para os padrões. |
| **Tradeoff** | Falsos positivos em pausas legítimas do jogador (o tempo de jogo pode não avançar por decisão humana) **versus** nunca ficar pendurado — exigir configurabilidade e uma janela de graça. |
| **Esforço · Prioridade** | S · **MÉDIA**. |
| **Definição de pronto** | Sessão artificialmente travada é detetada e escalada em ≤N minutos (teste unitário + integração); zero disparos em `paused`/kill-switch; eventos de watchdog registados nos padrões. |

Fontes: `PROBLEMAS-E-SOLUCOES.md` §H3, §D18 · `ARQUITETURA.md` §6 (StuckDetector), §9 · `docs/padroes-navegacao.json`
(200 `stuck_events`).

### Passo 19 — Cache de planos e tradução

O custo vive nos tokens DeepSeek: sessão leve ≈ $0,30, missão completa $2,65–$3,52 (`caca-2`: $3,52 em 27 556 registos
de gasto). Planos e narrativas são re-gerados mesmo quando nada mudou.

| Ficha | |
|---|---|
| **O quê** | Cache de planos LLM (chave: estado normalizado + objetivo) e de padrões de menu; o cache de tradução PT↔EN já existe — generalizar o padrão; narrativas em `none` quando nada mudou (o `narrator_effort` do JEV-M já existe). |
| **Porquê** | Menos tokens, menos ruído narrativo e sessões mais baratas sem perda de qualidade — o tradutor é fire-and-forget e por isso o maior candidato a cache (`ARQUITETURA.md` §2.2). |
| **Como** | Hash do estado normalizado + objetivo como chave; invalidação conservadora (mudança de zona, de objetivo, de ameaça ou de HP invalida); aproveitar o já aprendido: `narrator_effort` calibrado para `none` quando nada mudou (com o Passo 11). |
| **Desbloqueia** | Sessões mais baratas; menos latência no tradutor e no planificador; margem de orçamento para as rodadas do Passo 2. |
| **Tradeoff** | Risco de **plano stale** (um cache errado é pior do que re-gerar — daí invalidação conservadora) **versus** economia medida. |
| **Esforço · Prioridade** | S · **BAIXA**. |
| **Definição de pronto** | *Hit-rate* de cache reportado por sessão **[por medir]**; custo por missão desce comparando sessões equivalentes A/B; zero planos stale detetados em teste (invalidação coberta por testes). |

Fontes: `PROBLEMAS-E-SOLUCOES.md` §H6, §G34 · `ARQUITETURA.md` §2.2, §9 (custo) · `README.md` (aviso de custos).

---

# Mapa de dependências

O que precisa de quê — e porquê. Ler antes de escolher a ordem de execução.

| Passo | Depende de | Porquê |
|---|---|---|
| 2 (re-rodar arena) | **1** (gold ≥20) | Sem ≥20 casos/papel o `jev train` recusa e o arena pula os holdouts — a re-rodada voltaria a dar empates. |
| 3 (A/B em produção) | **2** (promovidos) | Só vale a pena shadow-runar prompts que o bench promoveu por significância. |
| 11 (calibragem por risco) | **1** + **3** | O suporte n≥30 por classe vem do gold dev (Passo 1) e do log real via `from-decisions` (Passo 3). |
| 6 (evidência antes de abstain) | amplifica **11** | Re-decidir com confiança calibrada é seguro; sem calibragem, a re-decisão herda os mesmos vieses. |
| 5 (movimento macro-ação) | — (independente) | Resolve sozinho a maior parte das 349/415 abstensões; com **6** fecha o problema por completo. |
| 4 (skills de combate) | — (independente) | Destrava **14** (treino exige skills) e valida **13** (plano de caça só é bom se o combate for eficaz). |
| 14 (treinar antes de caçar) | **4** | Sem snapshot de skills não há gate de `Proficient`. |
| 12 (ciclo gerar-testar-criticar) | **13** (schema) | O crítico de formato só faz sentido com um schema fixo para validar. |
| 8 (state com histórico) | **9** (eventos) | O bloco comprimido do state alimenta-se dos eventos estruturados; sem eles, o histórico é manual. |
| 15 (golden set em CI) | **1** (holdout ≥20) | O hold-out anti-overfit cresce com o gold expandido. |
| 17 (CI no GitHub) | **15** | O CI roda o `golden.py check` — sem golden set não há gate de prompt. |
| 16 (stores em SQLite) | — (independente) | Alimenta **11** (amostras reais) e **18** (watchdog precisa de histórico persistente). |
| 18 (watchdog de sessão) | **16** (desejável) | Com stores persistentes o watchdog audita e recupera; sem eles, só deteta. |
| 19 (cache) | — (independente) | Liberta orçamento para as rodadas do **2**. |

---

# Se só fizer 3 coisas

Se o tempo só chegar para três, são estes — o maior impacto pelo menor custo, e os três juntos cobrem as duas maiores
dívidas medidas do projecto (decisões sem poder estatístico e abstensões em movimento):

1. **Gold ≥20 casos/papel + re-rodar a arena (Passos 1 e 2).** Sem poder estatístico, toda a decisão de prompt é
   opinião: o +20 p.p. do plano-de-caça (p=0,156) pode ser o maior ganho já medido ou ser ruído, e hoje é impossível
   saber. É também o passo mais barato em código — é trabalho de dados — e destrava `jev train`, holdouts válidos e a
   calibragem inteira.
2. **Skills de combate nas decisões (Passo 4).** É o gap que mata personagens: decisões de ataque/fuga sem saber a
   capacidade do aventureiro. É a mudança de maior impacto comportamental das 12 (prioridade alta, `ARQUITETURA.md` §8)
   e destrava o plano de caça e o treino.
3. **Movimento como macro-ação + evidência antes de abstain (Passos 5 e 6).** Ataca a maior dívida medida do sistema —
   **349 abstensões em 415 decisões de movimento** na `caca-2`: o humano carrega o loop. Juntos, tiram o micro-movimento
   do decisor e dão-lhe uma segunda hipótese com evidência nova antes de escalar.

---

## Onde verificar (comandos reais)

```bash
# sistema
df up                      # jogo + backend + UI (Ctrl-C fecha o que ele arrancou)
df status · df logs --follow

# suíte e regressão
pytest -q                                          # 3597 passed, 2 skipped
python3 scripts/golden.py check                    # regressão de prompts, offline, ~1 s
python3 scripts/golden.py freeze-holdout --reason "…"   # único que altera o hold-out
python3 dfhack/test_bridge.py                      # 17 tests OK (baseline dfhack/)

# calibragem
python3 scripts/calibrate.py online --model typesafe/jev-1.13 --key-env SOD_OPENROUTER_KEY --out results.json
python3 scripts/calibrate.py report --evals evals --results results.json --out report.json --table calibration.json
python3 scripts/calibrate.py thresholds --table calibration.json --model typesafe/jev-1.13

# benchmark (sempre com --budget e --dry-run primeiro; teto diário US$ 20)
.venv/bin/python bench/gerar.py                    # regenera configs + espelho (--sync-evals)
prompt-builder jev validate bench/jev/<papel>.json
prompt-builder jev compare -c bench/jev/<papel>.json --budget 10 --dry-run
prompt-builder train --config bench/configs/train-<papel>.json --budget 10 --dry-run

# relatórios brutos
ls bench/relatorios/    # jev-*.md · train-*.html · evolucao-prompt-builder.html
```

Fontes internas deste documento: `ARQUITETURA.md` (§6–§10: factos provados, missão `caca-2`, as 12 mudanças, riscos) · `docs/prompt-builder-evolucao.md` + `bench/relatorios/evolucao-prompt-builder.html` (rodada de evolução e bloqueios da ferramenta) · `PROBLEMAS-E-SOLUCOES.md` (história e §H1–§H8) · `docs/calibracao.md` (bandas, classes de risco, golden set) · `docs/padroes-navegacao.json` (21 padrões, 200 `stuck_events`) · `README.md` (operação e custos).
