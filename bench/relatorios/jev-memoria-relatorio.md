# Relatório JEV — JEV-M — atualizar memória, esforço e tipo de registo

Run `4214a991-0aae-4341-80a8-8eb42b631b1f` · modo **compare** · status **finished** · 10 casos × 2 rep. · cliente node

## Competidores

| Competidor | Acurácia | Macro-F1 | Brier (p.p.) | ECE | Cobertura auto | Precisão auto | Errado c/ confiança | p50 / p95 | US$ / 1k decisões |
|---|---|---|---|---|---|---|---|---|---|
| jev-1.13 · original | 66,7% | 60,8% | 75,8 | 0,172 | 16,7% | 80,0% | 1 | 407 ms / 738 ms | US$ 0,00983 |
| jev-1.13 · criterios-v2 | 70,0% | 68,1% | 75,2 | 0,227 | 16,7% | 100,0% | 0 | 435 ms / 1099 ms | US$ 0,0127 |

## Por pergunta

| Pergunta | Competidor | n | Acurácia | Brier (p.p.) | ECE | Cobertura auto | Precisão auto |
|---|---|---|---|---|---|---|---|
| update_memory | jev-1.13 · original | 10 | 100,0% | 90,5 | 0,297 | 0,0% | — |
| narrator_effort | jev-1.13 · original | 10 | 40,0% | 61,2 | 0,278 | 0,0% | — |
| record_type | jev-1.13 · original | 10 | 60,0% | 75,5 | 0,264 | 50,0% | 80,0% |
| update_memory | jev-1.13 · criterios-v2 | 10 | 100,0% | 91,9 | 0,259 | 20,0% | 100,0% |
| narrator_effort | jev-1.13 · criterios-v2 | 10 | 40,0% | 53,5 | 0,403 | 0,0% | — |
| record_type | jev-1.13 · criterios-v2 | 10 | 70,0% | 80,2 | 0,151 | 30,0% | 100,0% |

## Comparação pareada contra o controle

| Competidor | Métrica | Δ | IC95% | p (bilateral) | Δ acurácia | McNemar p |
|---|---|---|---|---|---|---|
| d:criterios-v2@typesafe/jev-1.13 | brierScore | -0,56 p.p. | [-10,50; 9,91] | 0,889 | +3,3 p.p. | 1,000 |

## Custo

Total medido: **US$ 0,00135** (decisões US$ 0,00135, LLM US$ 0).

## Avisos

- noul com hitl 0,60 (o simulador e a skill usam 0,5, onde uma noul nunca abstém).
- choice.no_exit: sem opção de saída (other/none/outro…) — estados que não cabem em nenhuma opção são forçados numa errada (J5).
- noul.polarity_mismatch: a rubrica de "true" tem polaridade oposta à da instrução (J2): P(sim) pode sair invertida.
- cases.too_few: "update_memory" tem 10 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "narrator_effort" tem 10 caso(s) rotulado(s) (< 20): métricas instáveis.
- labels.unseen: opções nunca vistas no ouro de "narrator_effort": minimal — sem como medir se o modelo as usa bem.
- cases.too_few: "record_type" tem 10 caso(s) rotulado(s) (< 20): métricas instáveis.
