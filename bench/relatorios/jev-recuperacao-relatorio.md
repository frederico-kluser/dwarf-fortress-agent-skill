# Relatório JEV — JEV-R — incluir conhecimento recuperado por candidato

Run `0422582e-7c86-4636-8508-fbb7fd03187d` · modo **compare** · status **finished** · 13 casos × 2 rep. · cliente node

## Competidores

| Competidor | Acurácia | Macro-F1 | Brier (p.p.) | ECE | Cobertura auto | Precisão auto | Errado c/ confiança | p50 / p95 | US$ / 1k decisões |
|---|---|---|---|---|---|---|---|---|---|
| jev-1.13 · original | 93,8% | 81,5% | 94,5 | 0,079 | 58,3% | 100,0% | 0 | 417 ms / 492 ms | US$ 0,00762 |
| jev-1.13 · criterios-v2 | 91,7% | 79,1% | 93,7 | 0,056 | 60,4% | 96,6% | 1 | 423 ms / 460 ms | US$ 0,00900 |

## Por pergunta

| Pergunta | Competidor | n | Acurácia | Brier (p.p.) | ECE | Cobertura auto | Precisão auto |
|---|---|---|---|---|---|---|---|
| include_c1 | jev-1.13 · original | 13 | 92,3% | 94,6 | 0,117 | 38,5% | 100,0% |
| include_c2 | jev-1.13 · original | 13 | 92,3% | 90,0 | 0,134 | 30,8% | 100,0% |
| include_c3 | jev-1.13 · original | 13 | 92,3% | 95,1 | 0,120 | 76,9% | 100,0% |
| include_c4 | jev-1.13 · original | 9 | 100,0% | 99,9 | 0,032 | 100,0% | 100,0% |
| include_c1 | jev-1.13 · criterios-v2 | 13 | 84,6% | 94,2 | 0,180 | 38,5% | 100,0% |
| include_c2 | jev-1.13 · criterios-v2 | 13 | 92,3% | 88,4 | 0,218 | 38,5% | 80,0% |
| include_c3 | jev-1.13 · criterios-v2 | 13 | 92,3% | 94,3 | 0,066 | 76,9% | 100,0% |
| include_c4 | jev-1.13 · criterios-v2 | 9 | 100,0% | 99,9 | 0,024 | 100,0% | 100,0% |

## Comparação pareada contra o controle

| Competidor | Métrica | Δ | IC95% | p (bilateral) | Δ acurácia | McNemar p |
|---|---|---|---|---|---|---|
| d:criterios-v2@typesafe/jev-1.13 | brierScore | -0,72 p.p. | [-2,07; 0,65] | 0,265 | -1,9 p.p. | 1,000 |

## Custo

Total medido: **US$ 0,00173** (decisões US$ 0,00173, LLM US$ 0).

## Avisos

- noul com hitl 0,60 (o simulador e a skill usam 0,5, onde uma noul nunca abstém).
- cases.too_few: "include_c1" tem 13 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "include_c2" tem 13 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "include_c3" tem 13 caso(s) rotulado(s) (< 20): métricas instáveis.
- cases.too_few: "include_c4" tem 9 caso(s) rotulado(s) (< 20): métricas instáveis.
