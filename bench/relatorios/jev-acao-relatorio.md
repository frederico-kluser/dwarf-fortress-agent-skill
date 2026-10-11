# Relatório JEV — JEV-A — urgência e necessidade de ajuda humana

Run `6ae8cb92-23de-4f49-986d-300f066eb3b7` · modo **compare** · status **finished** · 19 casos × 2 rep. · cliente node

## Competidores

| Competidor | Acurácia | Macro-F1 | Brier (p.p.) | ECE | Cobertura auto | Precisão auto | Errado c/ confiança | p50 / p95 | US$ / 1k decisões |
|---|---|---|---|---|---|---|---|---|---|
| jev-1.13 · original | 92,1% | 100,0% | 94,3 | 0,165 | 28,9% | 90,9% | 1 | 412 ms / 1730 ms | US$ 0,0118 |
| jev-1.13 · criterios-v2 | 92,1% | 100,0% | 95,1 | 0,136 | 47,4% | 94,4% | 1 | 415 ms / 507 ms | US$ 0,0134 |

## Por pergunta

| Pergunta | Competidor | n | Acurácia | Brier (p.p.) | ECE | Cobertura auto | Precisão auto |
|---|---|---|---|---|---|---|---|
| urgency | jev-1.13 · original | 19 | 84,2% | 93,1 | 0,169 | 42,1% | 87,5% |
| needs_human | jev-1.13 · original | 19 | 100,0% | 95,6 | 0,188 | 15,8% | 100,0% |
| urgency | jev-1.13 · criterios-v2 | 19 | 84,2% | 94,1 | 0,159 | 57,9% | 90,9% |
| needs_human | jev-1.13 · criterios-v2 | 19 | 100,0% | 96,1 | 0,170 | 36,8% | 100,0% |

## Comparação pareada contra o controle

| Competidor | Métrica | Δ | IC95% | p (bilateral) | Δ acurácia | McNemar p |
|---|---|---|---|---|---|---|
| d:criterios-v2@typesafe/jev-1.13 | brierScore | +0,82 p.p. | [-2,16; 3,55] | 0,605 | 0,0 p.p. | 1,000 |

## Custo

Total medido: **US$ 0,00192** (decisões US$ 0,00192, LLM US$ 0).

## Avisos

- noul com hitl 0,60 (o simulador e a skill usam 0,5, onde uma noul nunca abstém).
- noul.polarity_mismatch: a rubrica de "true" tem polaridade oposta à da instrução (J2): P(sim) pode sair invertida.
- cases.too_few: "urgency" tem 19 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "needs_human" tem 19 caso(s) rotulado(s) (< 20): métricas instáveis.
