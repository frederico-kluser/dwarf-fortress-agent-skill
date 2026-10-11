# Golden set — changelog

Cada linha regista uma alteração INTENCIONAL (`bless` = prompt mudou de propósito; `freeze-holdout` = o hold-out mudou). Sem linha aqui, a mudança não é legítima.

- 2026-10-10 · freeze-holdout · initial golden set: 66 seed cases from evals/ plus 57 synthetic scenarios — 22 cases · sha256 c2941b6964a0
- 2026-10-10 · bless · initial prompt lock for the golden set — 123 cases · 0 changed, 123 new, 0 removed
- 2026-10-11 · freeze-holdout · espelho dos evals regenerado para os critérios-v2 (fleet.py JEV-A/M/D); etiquetas intactas, split determinístico idêntico — 22 cases · sha256 80eeb2fd26cb
- 2026-10-11 · bless · criterios-v2 do benchmark prompt-builder (bench/jev): definições-e-não-rótulos, situações concretas simétricas e política anti-abstention em JEV-A/M/D; JEV-R mantido (sem ganho no compare) — 123 cases · 102 changed, 0 new, 0 removed
