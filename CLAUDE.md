# CLAUDE.md — System One Dwarf

Contrato operacional para agentes de código neste repositório. O contrato completo (memória CoALA + regras) está em [AGENTS.md](AGENTS.md); o estado humano do projeto está em [README.md](README.md).

## O que é

Autoplay do Dwarf Fortress (adventure mode): frota **JEV** decide (tipada, via OpenRouter), **DeepSeek** escreve (narrativa/perguntas/tradução de apoio), **DFHack** executa (ponte RPC), UI web em português supervisiona. **O modelo nunca gera texto para agir** — o código gera as macro-ações e o JEV escolhe entre elas. Contratos em [`docs/`](docs/).

## Comandos

| O quê | Comando |
|---|---|
| Testes (offline) | `.venv/Scripts/python -m pytest -q` (Windows) · `.venv/bin/python -m pytest -q` (Linux) |
| Estado da ponte | `python dfhack/df_bridge.py status --json` |
| Ler o ecrã do jogo | `python dfhack/df_bridge.py act screen --json` (`data.lines`, `data.focus`) |
| Sessão (Windows) | `pwsh scripts/df.ps1 up` · `stop` · `status` · `logs -Follow` |
| Smoke JEV | `python scripts/jev_smoke.py` |
| Memória do projeto | `python3 .agents/dwarf-fortress-game-coala-agent-skill/scripts/coala.py recall "<tarefa>" --budget 1500` |

## Tradução de ecrã (ferramenta ativa)

`ScreenTranslator` em `src/system_one_dwarf/translate/translator.py` — `xiaomi/mimo-v2.6-pro` via OpenRouter com `reasoning: {"effort": "none"}` e `temperature: 0`. Regras fixas: **nunca** degradar para effort ligado (thinking custa 2,3× e trunca lotes); system prompt em **PT-PT** preservando nomes próprios, keybinds e glifos (itens/materiais traduzem-se). Fábrica: `build_screen_translator` (glossário → TM → motor → identity). Evidência e preços: [`pesquisas/traducao-ecra-mimo.md`](pesquisas/traducao-ecra-mimo.md).

## Ambiente

- **Windows verificado** (DF Steam 53.16 + DFHack 53.16-r3): ver [`docs/setup-windows.md`](docs/setup-windows.md). Antes do `npm install` de `web/`: `$env:MOTION_TOKEN = [Environment]::GetEnvironmentVariable('MOTION_TOKEN','User')`.
- Chaves em `~/.secrets` (sem aspas nos valores!) — nunca imprimir, nunca commitar.
- Fora do git: `campanhas/`, `.venv/`, `web/node_modules/`, `memory/` (CoALA).

## Regras de trabalho

- Commits Convencionais (`tipo(escopo): descrição`), um commit por peça verificada; `main` sincronizada com `origin/main`; **merge, nunca rebase**.
- Pré-condição de commit: testes verdes (`.venv/Scripts/python -m pytest -q`).
- Código e estado atuais são a verdade; a memória CoALA aponta, o grep confirma.
