# bench/ — benchmark de prompts do System One Dwarf (prompt-builder)

Material do benchmark de prompts conduzido com o **prompt-builder** (CLI em
`prompt-builder`, guia `terminal.md`): cenários-âncora do domínio, configs
`arena-config@1` (papéis de texto) e `jev-config@1` (papéis de decisão JEV),
relatórios e o gerador que mantém tudo sincronizado com o código.

## Estrutura

| Pasta/ficheiro | O que é |
|---|---|
| `cenarios/*.json` | **Conjunto-semente** (72 cenários, fixo para comparação antes/depois): `acao` (14), `recuperacao` (10), `memoria` (8), `perguntas-pt` (12), `narracao` (10), `plano-caca` (10), `traducao` (8). Formato `arena-scenarios@1` = a lista `scenarios[]` do `arena-config@1`, com `expected`+`labelSet` (veredito determinístico) ou `rubric`+`reference` (gabarito escrito). |
| `configs/train-*.json` | Um `arena-config@1` **por papel de prompt** (narrador, perguntas-pt, plano-caca, tradutor). O `prompt.text` é extraído DO CÓDIGO em tempo de geração. |
| `jev/*.json` | Um `jev-config@1` por papel do JEV (acao, duvida, memoria, recuperacao): `spec` = redação **original** (controle, espelho de `fleet.py`), `variants` = a variante `criterios-v2` (ver `jev/variantes-criterios.json`). |
| `jev/casos/*.jsonl` | Casos convertidos dos 66 rotulados de `evals/` (ouro). JEV-R usa slots `include_c1..c4` (as instruções de produção são por rótulo `[C1]..[C4]`). |
| `gerar.py` | Gera os configs a partir de `cenarios/` + `evals/` + os prompts vivos do código. `--sync-evals` re-espelha os blocos gerados de `evals/*.json` (nunca toca em `expected`/`bands`/`notes`). |
| `relatorios/` | Relatórios e saídas das runs (`.ndjson`, `.log`, `.md`, `.html`). |

## Reproduzir

```bash
# 1. gerar configs (extrai os prompts do código atual)
.venv/bin/python bench/gerar.py

# 2. validar
prompt-builder config validate bench/configs/train-narrador.json   # etc.
prompt-builder jev validate bench/jev/duvida.json                  # etc.

# 3. dry-run (mesmas flags da run real) e run, sempre com --budget
prompt-builder jev compare -c bench/jev/duvida.json --budget 10 --dry-run
prompt-builder jev compare -c bench/jev/duvida.json --budget 10 --output-format ndjson
prompt-builder train --config bench/configs/train-narrador.json --budget 10 --dry-run
prompt-builder train --config bench/configs/train-narrador.json --budget 10 \
  --idempotency-key pb-train-narrador-1 --output-format ndjson

# 4. relatórios
prompt-builder jev report <runId> --markdown bench/relatorios/jev-<papel>-relatorio.md
prompt-builder sessions report <sessionId> --html bench/relatorios/train-<papel>-relatorio.html
prompt-builder runs winner <runId> --json
```

## Notas de desenho

- **Mesmo seed antes/depois**: os cenários pinados são idênticos em todas as
  runs; o antes/depois é medido dentro da mesma sessão (controle × campeão/variante).
  Cada config deixa o datagen completar `stages` com +4 cenários suplementares
  (pedido do usuário), que entram na medição mas não alteram o seed.
- **JEV-A `action` e JEV-R `dominant_theme` não cabem no modo jev do
  prompt-builder**: os `jev-config@1` têm UMA definição de pergunta por run e
  estas têm opções/critérios por caso (`jev validate` rejeita com
  `questions_divergent`/`labels.uncovered`). A escolha de ação é coberta pelos
  cenários `acao.json` (rótulo esperado = veredito sem juiz) e a seleção de
  conhecimento por `recuperacao.json` (1 decisão por candidato, forma fiel do
  JEV-R por slot).
- **`train` do jev exige ≥ 20 casos rotulados por pergunta**; o ouro tem 10–19,
  por isso os papéis JEV correm em `jev compare` (original × criterios-v2,
  pareado por caso) — a métrica é a mesma (accuracy/ECE/Brier/bandas).
- **Critérios são `str` no runtime** (`jev/schema.py` valida descrições
  não-vazias): a variante usa rubricas `{what, not_for}` no bench e é achatada
  para string no `fleet.py` (conteúdo idêntico).
- Orçamento: `--budget 10` em todas as runs, `--dry-run` antes com as mesmas
  flags. `training.iterations` ficou em **2** (o default do `config example` é 3)
  porque o estimador recusava `estimate.high` acima de 10 — desvio documentado
  em `docs/prompt-builder-evolucao.md`.

## Depois de mudar prompts em `fleet.py`

1. `.venv/bin/python bench/gerar.py --sync-evals` (re-espelha `evals/*.json`);
2. `.venv/bin/python scripts/golden.py build`;
3. `.venv/bin/python scripts/golden.py freeze-holdout --reason "..."` se o
   build disser que o hold-out mudaria (etiquetas nunca mudam);
4. `.venv/bin/python scripts/golden.py bless --reason "..."` (aceita a mudança
   intencional de prompt) e `check` até `OK`;
5. `.venv/bin/python -m pytest -q` verde.
