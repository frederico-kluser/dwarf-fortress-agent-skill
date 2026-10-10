# evals/golden — golden set de regressão de prompts

Dados **e** a lógica que os reconstrói (`golden_*.py`), versionados juntos.
Documentação completa: `docs/calibracao.md` §14–§15. CLI: `scripts/golden.py`.

| Ficheiro | O que é |
|---|---|
| `dev.jsonl`, `holdout.jsonl` | os casos (entradas + etiquetas + `notes`); o hold-out é ~20 % por `sha1(chave) % 100 < 20` |
| `HOLDOUT.sha256` | pin do hold-out (`sha256sum -c`); só `freeze-holdout --reason` o altera |
| `prompts.lock.json` | hash do pedido de cada caso com os builders actuais; só `bless --reason` o altera |
| `scenarios.json` | fonte dos casos sintéticos (snapshot sintético + regra de segurança) |
| `CHANGELOG.md` | registo das alterações intencionais (`bless`, `freeze-holdout`) |
| `baseline.json` | accuracy/ECE por papel da última corrida online aceite |

Fluxo curto: `python3 scripts/golden.py check` (CI, offline) · prompt mudou de
propósito ⇒ `bless --reason "…"` · afinar prompts ⇒ `tune-view` (o hold-out não é
aberto) · reportar no fim ⇒ `run --online --split holdout`.
