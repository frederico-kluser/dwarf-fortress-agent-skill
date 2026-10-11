# Relatório JEV — JEV-D — pedir ajuda humana e formato da pergunta

Run `88fff9a7-5d18-4cf3-baa4-e125303d03c1` · modo **compare** · status **finished** · 10 casos × 2 rep. · cliente node

## Competidores

| Competidor | Acurácia | Macro-F1 | Brier (p.p.) | ECE | Cobertura auto | Precisão auto | Errado c/ confiança | p50 / p95 | US$ / 1k decisões |
|---|---|---|---|---|---|---|---|---|---|
| jev-1.13 · original | 93,3% | 88,9% | 93,5 | 0,104 | 40,0% | 100,0% | 0 | 394 ms / 425 ms | US$ 0,0100 |
| jev-1.13 · criterios-v2 | 93,3% | 88,9% | 94,0 | 0,124 | 46,7% | 100,0% | 0 | 420 ms / 470 ms | US$ 0,0147 |

## Por pergunta

| Pergunta | Competidor | n | Acurácia | Brier (p.p.) | ECE | Cobertura auto | Precisão auto |
|---|---|---|---|---|---|---|---|
| ask_human | jev-1.13 · original | 10 | 100,0% | 97,2 | 0,142 | 50,0% | 100,0% |
| question_format | jev-1.13 · original | 5 | 80,0% | 86,1 | 0,140 | 20,0% | 100,0% |
| ask_human | jev-1.13 · criterios-v2 | 10 | 100,0% | 96,2 | 0,161 | 40,0% | 100,0% |
| question_format | jev-1.13 · criterios-v2 | 5 | 80,0% | 89,5 | 0,169 | 60,0% | 100,0% |

## Comparação pareada contra o controle

| Competidor | Métrica | Δ | IC95% | p (bilateral) | Δ acurácia | McNemar p |
|---|---|---|---|---|---|---|
| d:criterios-v2@typesafe/jev-1.13 | brierScore | +0,52 p.p. | [-6,37; 8,43] | 0,754 | 0,0 p.p. | 1,000 |

## Custo

Total medido: **US$ 0,00099** (decisões US$ 0,00099, LLM US$ 0).

## Avisos

- noul com hitl 0,60 (o simulador e a skill usam 0,5, onde uma noul nunca abstém).
- choice.no_exit: sem opção de saída (other/none/outro…) — estados que não cabem em nenhuma opção são forçados numa errada (J5).
- noul.polarity_mismatch: a rubrica de "true" tem polaridade oposta à da instrução (J2): P(sim) pode sair invertida.
- cases.too_few: "ask_human" tem 10 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "question_format" tem 5 caso(s) rotulado(s) (< 20): métricas instáveis.
