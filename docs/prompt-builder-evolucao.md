# Evolução de prompts com o prompt-builder — System One Dwarf (2026-10-11)

Relatório do benchmark de prompts conduzido com o **prompt-builder** (CLI
`prompt-builder`, versão `prompt-builder-cli 0.1.1`): melhorar os prompts do
projecto, criar cenários do nosso domínio e medir a evolução — sobretudo do
**modelo de decisão JEV** (`typesafe/jev-1.13`), e também do **MIMO v2.6 Pro**
(`xiaomi/mimo-v2.6-pro`) como respondedor dos papéis de texto. Todo o material
ficou em [`bench/`](../bench/README.md); relatórios brutos em
[`bench/relatorios/`](../bench/relatorios/).

## 1. Modelos validados (`models show` / `jev models`)

| Papel | Modelo | Think levels aceites (ferramenta) | Preço in/out US$/MTok |
|---|---|---|---|
| Juiz principal | `google/gemini-3.8-flash` | `low, medium, high` (default `medium`; raciocínio obrigatório) | 0,75 / 3,75 |
| Juiz de escalada | `meta/muse-spark-1.3` | `minimal…max` (default `medium`; obrigatório) | 1,25 / 4,25 |
| **Sob teste (MIMO)** | `xiaomi/mimo-v2.6-pro` | `off…max` (pode desligar) | 0,435 / 0,87 |
| Gabarito | `z-ai/glm-5.3-flash` | `low, high, max` (default `max`; obrigatório) | 0,15 / 0,50 |
| Gerador de cenários | `xiaomi/mimo-v2.6-pro` | idem MIMO | 0,435 / 0,87 |
| **Juiz JEV / decisão** | `typesafe/jev-1.13` (catálogo `jev models`, snapshot `typesafe/jev-1.13-20260917`) | n/a (modelo de decisão tipada) | 0,042 / grátis |

`models show typesafe/jev-1.13` **não existe** no catálogo LLM (é modelo de
decisão); o endereço suportado é `prompt-builder jev models --json` e, no
arena-config, o motor do juiz por omissão (`judging.engine: "jev"`, modelo
`judging.jev.model` = `typesafe/jev-1.13`). Todos os modelos recomendados em
`models.md` estão disponíveis; o MIMO v2.6 Pro foi escolhido pelo utilizador
para avaliação (respondedor dos papéis de texto), o que satisfaz a regra de
perguntar antes de trocar modelos.

## 2. Cenários criados (`bench/cenarios/`, 72 no total)

Ouro de origem: os **66 casos rotulados** de `evals/` (19 JEV-A + 13 JEV-R +
10 JEV-M + 10 JEV-D + 14 injeção), reutilizados como seed; os cenários de
decisão foram derivados desse ouro e os de qualidade escritos para o domínio.

| Ficheiro | Papel | n | Gabarito | Origem |
|---|---|---|---|---|
| `acao.json` | escolha de ação (combate/caça/movimento/duvida) | 14 | `expected`+`labelSet` (veredito sem juiz) | ouro `jev-a`/`jev-d` + caça |
| `recuperacao.json` | seleção de conhecimento (1 decisão/candidato, fiel ao JEV-R) | 10 | `expected`+`labelSet` | ouro `jev-r` |
| `memoria.json` | decisão de memória (episódio/fato/procedimento/pular) | 8 | `expected`+`labelSet` | ouro `jev-m` |
| `perguntas-pt.json` | formatação de perguntas PT (sem fugas de números) | 12 | `rubric`+`reference` | domínio |
| `narracao.json` | crônica rolante + registo (episódio) | 10 | `rubric`+`reference` | domínio |
| `plano-caca.json` | planeamento de caça (rotas por waypoints + oportunismo) | 10 | `rubric`+`reference` | domínio |
| `traducao.json` | tradução de ecrãs EN→PT (nomes/números/termos) | 8 | `rubric`+`reference` | domínio |

Cada config de treino deixa ainda o datagen do próprio tool completar `stages`
com **+4 cenários suplementares** (pedido do utilizador). O conjunto-semente é
fixo e igual em todas as runs — o antes/depois é sempre pareado dentro da
mesma sessão.

## 3. Configs usados

- **JEV** (`bench/jev/*.json`, `jev-config@1`, modo `compare`, 2 repetições,
  `typesafe/jev-1.13`): `spec` = redação **original** de `fleet.py` (controle),
  `variants[0]` = **`criterios-v2`** (`bench/jev/variantes-criterios.json`).
  Casos = ouro convertido (`bench/jev/casos/*.jsonl`). Split `holdout 0,3 /
  calib 0,2`, semente 42.
- **Texto** (`bench/configs/train-*.json`, `arena-config@1`, modo `training`):
  contestant `xiaomi/mimo-v2.6-pro`, juízes `google/gemini-3.8-flash` +
  `meta/muse-spark-1.3`, gabarito `z-ai/glm-5.3-flash`, datagen
  `xiaomi/mimo-v2.6-pro` (recomendados de `models.md` = `config example`),
  esforço juiz `high`/datagen `low`, técnicas `persona,constraints,format`,
  `finalists: 3`, contratos `neverBreak` por papel.
- **Desvio declarado**: `training.iterations: 2` (o `config example` traz 3).
  Com 3 iterações o estimador dava `estimate.high` > US$ 10 e o tool recusava a
  run com `--budget 10` (exit 2). Com 2 iterações todas couberam no orçamento.
  O narrador correu antes do corte, com 3 iterações (convergiu na 1ª).

## 4. Tabela de evolução (baseline → depois)

### 4.1 JEV (`typesafe/jev-1.13`) — original × `criterios-v2` (pareado por caso)

| Papel (células) | Accuracy | ECE | Brier (100·(1−B)) | cov@auto / prec@auto | Pareamento |
|---|---|---|---|---|---|
| JEV-A `urgency`+`needs_human` (38) | 0,921 → 0,921 | 0,165 → **0,136** | 94,3 → **95,1** | 0,29/0,91 → **0,47/0,94** | Δacc 0,0 p.p. (McNemar p=1,0); ΔBrier +0,82 p.p. (p=0,61) |
| JEV-M `update_memory`+`effort`+`record_type` (30) | 0,667 → **0,700** | 0,172 → 0,227 | 75,8 → 75,2 | 0,17/0,80 → **0,17/1,00** | Δacc +3,3 p.p. (p=1,0); ΔBrier −0,56 p.p. (p=0,89) |
| JEV-D `ask_human`+`question_format` (15) | 0,933 → 0,933 | 0,104 → 0,124 | 93,5 → **94,0** | 0,40/1,00 → **0,47/1,00** | Δacc 0,0 p.p.; ΔBrier +0,52 p.p. (p=0,75) |
| JEV-R `include_c1..c4` (48) | **0,938** → 0,917 | 0,079 → **0,056** | **94,5** → 93,7 | 0,58/1,00 → 0,60/0,97 | Δacc −1,9 p.p. (p=1,0); ΔBrier −0,72 p.p. (p=0,26) |

Leitura honesta: **nenhuma diferença é estatisticamente significativa** (n
pequeno: 10–19 casos por papel; a própria ferramenta avisa
`cases.too_few — métricas instáveis`). O ganho prático da variante está na
**banda auto** — a fraqueza conhecida de *over-abstention*: JEV-A passa a
decidir sozinho em 47% dos casos (era 29%) com precisão **superior** (0,94 vs
0,91), e JEV-M sobe a precisão auto de 0,80 para 1,00. JEV-R é o único papel
onde a variante perde accuracy (−1,9 p.p.) — **não foi aplicada**.
`flipRate` (2 reps): 0 em todos menos JEV-A (0,026 — 1 inversão em 38 células).

### 4.2 MIMO v2.6 Pro como respondedor — prompt original × campeão (arena `train`)

| Papel | Judge-score base → campeão | Veredito | Sessão | Custo |
|---|---|---|---|---|
| Narrador (crônica + registo) | 82,1 → 82,1 | **sem mudança** (o original segurou; melhor variante do ciclo 2: +11,5 p.p. bruto, p aj. 0,156 — n pequeno) | `2181e1aa-5fe5-4115-8a32-570d91d5a0c6` | US$ 2,52 |
| Perguntas PT (formatador) | 90,6 → 90,6 | **sem mudança** (ciclo 1: +3,1 p.p. bruto, p aj. 0,563) | `c04f317a-70cd-4b4a-bd09-c2443ab54006` | US$ 2,09 |
| Plano de caça | 85,0 → 85,0 | **sem mudança** (ciclo 2: +20,0 p.p. bruto/+15,2 corrigido, p aj. 0,156; ciclo 1 inconclusivo pelos pares excluídos) | `0fd7af73-05cd-482c-9c97-ad64572bf850` | US$ 1,70 |
| Tradutor EN→PT | 95,8 → 95,8 | **sem mudança** (nenhuma variante passou da régua nos 2 ciclos) | `e0b65679-3ccb-4e7f-8e8d-80409910a933` | US$ 0,60 |

Em todos os papéis o **campeão é o próprio prompt original** (n variante foi
promovida: n pequeno, gate max-T a 5% — os ganhos brutos de +11,5 e +20,0 p.p.
não alcançam significância com 10–14 pares). Os quatro holdouts foram pulados
(`holdoutSkipped`, seleção < 20 cenários ⇒ "confirmação fraca"): sem teste cego,
a ferramenta não usa a palavra "validado". Custo total do MIMO: US$ 6,91.
Custo por chamada (narrador): US$ 0,00048 (655 tokens in / 352 out).

### 4.3 Leitura comparativa (JEV × MIMO)

- **JEV** (`typesafe/jev-1.13`): a evolução de **critérios** (`criterios-v2`)
  produziu ganhos práticos sobretudo na banda auto (menos abstention à custa de
  zero perda de precisão em JEV-A/D e precisão auto 1,00 em JEV-M) — aplicados
  ao `fleet.py` em A/M/D; JEV-R ficou na redação original.
- **MIMO v2.6 Pro** (respondedor): com 8–16 cenários por papel, o gate estatístico
  não confirmou nenhuma reescrita — os prompts actuais dos papéis de texto são,
  dentro do poder medido, já competitivos (judge-score 82–96). O benchmark fica
  montado para o próximo ciclo com mais cenários (§6).

## 5. Decisões de prompt aplicadas ao código

| Onde | Decisão | Evidência |
|---|---|---|
| `src/system_one_dwarf/jev/fleet.py` | `criterios-v2` aplicado a JEV-A (`urgency`, `needs_human`), JEV-M (`update_memory`, `narrator_effort`, `record_type`) e JEV-D (`ask_human`, `question_format`), achatado para `str` (o runtime valida descrições não-vazias). Conteúdo: definição-e-não-rótulo, situações concretas simétricas, anti-abstention explícito ("uncertainty the agent can reduce by acting is NOT a reason to ask"). | §4.1 (ganhos na banda auto sem perda de precisão) |
| `src/system_one_dwarf/jev/fleet.py` (JEV-R) | **Sem mudança** — a variante perdeu accuracy no pareamento. | §4.1 |
| `evals/*.json` | Blocos `state`/`questions`/`ordering` **regenerados** (espelho MIRROR dos builders) via `bench/gerar.py --sync-evals`; `expected`/`bands`/`notes` intactos. Holdout refixado com os **mesmos ids e etiquetas** (`golden.py freeze-holdout`) e mudança aceite (`golden.py bless --reason`). Nota registada em `docs/calibracao.md` §16. | `evals/golden/CHANGELOG.md` |
| `scripts/e2e/hunt_plan.py` | Correcção de regressão do baseline do pytest (2 testes já falhavam antes deste trabalho): `first_plan` só quando não há contexto de feedback. | `tests/e2e/test_hunt_plan_cli.py` |
| Narrador/perguntas/plano/tradutor | **Sem mudança de texto**: em todos os papéis o campeão é o próprio prompt original (§4.2), logo `narrator.py`, `questions.py`, `planning.py`/`hunt_plan.py` e `translator.py` mantêm os prompts — os contratos `neverBreak` do benchmark afixam as invariantes já fixadas pelos testes. | §4.2 |

Suite de regressão: **3597 passed, 2 skipped** (baseline anterior: 3595 passed
com 2 falhas pré-existentes, corrigidas aqui).

## 6. Limitações e bloqueios registados

- **JEV-A `action` e JEV-R `dominant_theme` não são expressáveis no modo jev do
  prompt-builder**: `jev-config@1` tem UMA definição de pergunta por run e
  estas têm opções/critérios **por caso** (`jev validate` →
  `questions_divergent`/`labels.uncovered`). Não improvisámos um benchmark falso
  (nem opções-união): a escolha de ação é coberta pelos cenários `acao.json`
  (rótulo esperado = veredito determinístico) e a seleção de conhecimento por
  `recuperacao.json`. Um benchmark fiel destas duas superfícies precisa de um
  modo com `questions` por caso.
- **`jev train` recusa < 20 casos rotulados por pergunta** (`cases.too_few`):
  o ouro tem 10–19. Usámos `jev compare` (original × variante, pareado) —
  mesma métrica, sem a evolução automática por operadores.
- **n pequeno**: 10–19 casos (jev) e 8–16 cenários (arena) ⇒ só efeitos grandes
  são detectáveis (Δ ≈ 30–40 p.p.); as diferenças medidas são reais mas não
  significativas. Para o próximo ciclo: mais casos rotulados (≥ 20/pergunta) e
  `stages ≥ 20` por papel para o holdout do arena deixar de ser pulado.
- O estimador do prompt-builder passou a contar o juiz JEV+painel por inteiro a
  meio da sessão (as primeiras estimativas subestimaram ~5×); os runs foram
  re-estimados com `--dry-run` antes de cada execução real.
- O **datagen** (geração dos +4 cenários suplementares) falhou por *timeout* de
  180 s em 3 das 4 sessões (uma com JSON truncado na reposição); as runs
  prosseguiram só com os cenários pinados e parte das chamadas ficou **pendente
  de conciliação** (pode ter sido cobrada). Próximo ciclo: `stages` pinados ou
  geração antecipada.

## 7. Como re-correr o benchmark

```bash
.venv/bin/python bench/gerar.py                  # regenera configs (prompts vivos do código)
prompt-builder jev validate bench/jev/duvida.json # etc.
prompt-builder jev compare -c bench/jev/duvida.json --budget 10 --dry-run
prompt-builder jev compare -c bench/jev/duvida.json --budget 10 --output-format ndjson
prompt-builder train --config bench/configs/train-narrador.json --budget 10 --dry-run
prompt-builder train --config bench/configs/train-narrador.json --budget 10 \
  --idempotency-key pb-train-narrador-1 --output-format ndjson
prompt-builder jev report <runId> --markdown bench/relatorios/jev-<papel>-relatorio.md
prompt-builder sessions report <sessionId> --html bench/relatorios/train-<papel>-relatorio.html
```

Regras: `--budget` **sempre** (usámos 10), `--dry-run` com as mesmas flags antes
de cada run real, orçamento acumulado vigiado (`prompt-builder limits show`;
teto diário da máquina US$ 20). Depois de mudar prompts em `fleet.py`, seguir o
fluxo do espelho (`bench/gerar.py --sync-evals` → `golden.py build` →
`freeze-holdout --reason` → `bless --reason` → `check` → pytest) — ver
`bench/README.md`.
