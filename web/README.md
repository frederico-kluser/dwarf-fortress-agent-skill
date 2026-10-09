# System One Dwarf — UI web (mission control)

React + Vite + TypeScript + **Material UI v9**, em português.
É a peça de interface do agente autónomo que joga **Dwarf Fortress (adventure mode)**:
8 painéis ligados ao backend FastAPI por REST + WebSocket, com um **mock** completo
que faz a UI correr com zero backend.

## Arrancar

```bash
npm install
npm run dev        # http://localhost:5173 — sem VITE_API_BASE corre com dados simulados
npm run build      # tsc -b && vite build
npm run lint       # oxlint (bloqueia barrel imports do MUI)
npm run preview    # serve o build
```

## Backend real

```bash
cp .env.example .env.local     # VITE_API_BASE=http://localhost:8000
```

Sem `VITE_API_BASE` definida, o cliente é o **mock** (`src/api/mock.ts`), que gera
eventos WebSocket simulados a cada poucos segundos: `metrics_update`,
`screen_translation`, `decision`, `spend_update`, `question_new`, `memory_update`,
`control_state` — e um `heartbeat` desconhecido, para exercitar a regra do contrato
("eventos desconhecidos devem ser ignorados, não fatais").

## Estrutura

| Caminho | Papel |
|---|---|
| `src/api/types.ts` | Espelho **exato** de `docs/api.md` (endpoints, envelope `{type, ts, payload}`, dinheiro em USD) |
| `src/api/client.ts` | Cliente tipado único: REST + WebSocket com reconexão automática (`HttpApiClient`) |
| `src/api/mock.ts` | Implementação mock, escolhida quando `VITE_API_BASE` não está definida |
| `src/api/queries.ts` | React Query: chaves, leituras, mutações e aplicação dos eventos na cache |
| `src/panels/` | Os 8 painéis (memória, objetivo, inputs, métricas, perguntas, decisões, ecrã, controlo) |
| `src/layout/` | AppBar, navegação lateral (drawer em desktop, tabs em mobile) e esqueleto |
| `src/components/` | Sparkline em SVG puro, chips de banda/papel, cartões, fila de perguntas |

## Regras do MUI v9 seguidas

- **Path imports** sempre (`@mui/material/Button`, `@mui/icons-material/Delete`) — barrel
  imports são erro de lint (`no-restricted-imports` no `.oxlintrc.json`);
- Sem APIs removidas na v9 (`GridLegacy`, system props fora do `sx`, `InputProps`/`inputProps`,
  `renderTags`, `MenuListProps`, `disableEscapeKeyDown`); `Grid` v2 com `size={{ xs, md }}`;
- Tema por `ThemeProvider` + `CssBaseline`, com `colorSchemes`, `cssVariables` e
  `useColorScheme()` (`mode` é tratado quando ainda é `undefined`);
- Acessibilidade: `aria-label` em todos os controlos só-ícone, labels em todos os campos,
  `id`/`aria-controls`/`aria-labelledby` nos Tabs, skip-link, contraste ≥ 4,5:1
  (`contrastThreshold: 4.5`) e foco visível.

## Notas de contrato

- O histórico de **inputs** é mantido localmente pelo cliente: `docs/api.md` define apenas
  `POST /api/campaigns/{id}/inputs` (não há `GET`).
- `wounds` e `combat_history` não têm forma fixada no contrato — a UI mostra o que existir.
- O objetivo não tem campo `_pt` no contrato: o painel mostra e envia o texto que o servidor
  traduz PT→EN antes de guardar.
