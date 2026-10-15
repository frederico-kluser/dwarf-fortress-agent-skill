# Design System «RPG Medieval» — web UI do System One Dwarf

**Tipo:** especificação normativa. As peças de implementação seguem este documento **à letra**; qualquer valor aqui tem número, não adjetivo.
**Âmbito:** a web UI (`web/`) — shell + os 9 painéis (`web/src/panels/registry.tsx`: `#/aventura`, `#/memoria`, `#/objetivo`, `#/inputs`, `#/metricas`, `#/perguntas`, `#/decisoes`, `#/ecra`, `#/controlo`) + overlays (wizard, sheets, toasts, diálogos).
**Stack assumida (verificada):** Vite 8 + React 19.2 + TS · Tailwind v4 + MUI v9 (`createTheme` com `colorSchemes` dark por omissão + light via `cssVariables`, `colorSchemeSelector: 'class'`) + Emotion · motion 14 + `motion-plus` (`@motionplus/core`) + registry Motion UI · TanStack Query · navegação por hash em `web/src/layout/AppShell.tsx`.
**Onde o tema entra (únicos ficheiros de pele):** `web/src/index.css` (tokens Tailwind `@theme` + `:root`/`.dark`), `web/src/theme.ts` (MUI `createTheme` + overrides), `web/motion.theme.ts` (tokens de motion), `web/index.html` (`meta theme-color`), `web/public/favicon.svg`, `web/src/main.tsx` (imports de fontes).
**Princípio reitor:** o tema é **pele sobre um dashboard de operação**. A imersão de RPG vem de materialidade, tipografia e vocabulário — nunca de esconder, atrasar ou decorar por cima de dado operacional. Um operador tem de conseguir vigiar o agente (bandas, confiança, erros, pendentes, kill-switch) em 3 segundos com o tema a pleno.

---

## 1. Conceito e narrativa visual

**«A Fortaleza-Vigia»** — o painel é o posto de comando de uma expedição anã: a **pedra** (basalto escuro, cantos talhados, molduras de placa) é a estrutura de todas as superfícies; a **forja** (brasa cobre `#E97C48`) marca o que está vivo e o que se faz — ações primárias, loop a correr, estados quentes; o **pergaminho** (no modo claro, e como metáfora) é o material da memória e da narrativa — painéis Memória, Objetivo, Inputs e Ecrã; as **runas** (dourado `#DFC05B`) são a sinalização ornamental e o foco da atenção humana — divisórias, títulos gravados, o que exige supervisão (`hitl`). Aplicado a um painel de comando de missão: cada cartão é uma placa de pedra com o seu glifo (bigorna, chama, escudo, pergaminho), cada secção abre com uma régua runa, o estado do agente lê-se como o estado de uma forja (brasa = a trabalhar, cinza = parado, vermelho = perigo), mas **toda a densidade de um dashboard de operação se mantém**: tabelas limpas, mono para dados, contraste AA verificado, zero ornamento por baixo de números.

---

## 2. Tokens semânticos

Valores canónicos em **OKLCH** com o **hex exato** (hex derivado do OKLCH por conversão direta; onde os dois divergirem num build, **o OKLCH manda**). Todos os rácios de contraste abaixo foram **calculados sobre os hex que o browser desenha** — método: OKLCH → sRGB → quantização 8-bit → luminância relativa WCAG 2.1 → contraste. (Uma versão anterior desta tabela calculou em OKLCH não-quantizado e divergia 0,02–0,09 dos hex reais em 19/46 células; os números atuais são os do render real.)

### 2.1 Modo dark (principal)

| Token | OKLCH | Hex | Papel |
|---|---|---|---|
| `--background` | `oklch(0.165 0.012 255)` | `#0B0F14` | Fundo basalto — página inteira |
| `--surface` | `oklch(0.215 0.013 255)` | `#151A1F` | Cartões, painéis, `paper` MUI |
| `--surface-raised` | `oklch(0.260 0.014 255)` | `#20242B` | Popovers, sheets, linhas hover, `Accordion` aberto |
| `--border` | `oklch(0.330 0.016 255)` | `#30363E` | Bordas decorativas 1px, divisórias |
| `--border-strong` | `oklch(0.535 0.018 255)` | `#666E78` | Contornos de controlo (inputs, botões outline) — ≥3:1 |
| `--foreground` | `oklch(0.945 0.012 85)` | `#F0ECE4` | Texto principal (quartzito quente) |
| `--muted` | `oklch(0.660 0.014 255)` | `#8D939B` | Texto secundário, legendas, timestamps |
| `--primary` | `oklch(0.700 0.150 45)` | `#E97C48` | **Brasa da forja** — ações primárias, foco de estado vivo |
| `--accent` | `oklch(0.815 0.125 92)` | `#DFC05B` | **Dourado das runas** — ornamento, foco, títulos gravados |
| `--danger` | `oklch(0.660 0.190 25)` | `#F05653` | Perigo, `abstain`, erros, kill-switch (L 0.640→0.660: o mínimo que garante `danger`/`surface-raised` ≥ 4.5:1) |
| `--success` | `oklch(0.720 0.155 155)` | `#3CC078` | Concluído, `auto`, saúde OK |
| `--warning` | `oklch(0.730 0.155 62)` | `#EB8E28` | Atenção, `hitl`, a degradar |
| `--info` | `oklch(0.660 0.110 230)` | `#3A9EC8` | Sistema/neutro: `stub`, «dados simulados», ligações |
| `--on-solid` | — | `#0B0F14` | Texto **sobre qualquer fill sólido** de status/primary/accent |

### 2.2 Modo light (pergaminho)

| Token | OKLCH | Hex | Papel |
|---|---|---|---|
| `--background` | `oklch(0.930 0.022 85)` | `#EFE7D8` | Pergaminho — fundo |
| `--surface` | `oklch(0.975 0.012 85)` | `#FAF6EE` | Cartões, `paper` MUI |
| `--surface-raised` | `oklch(0.995 0.006 85)` | `#FFFDF9` | Popovers, sheets, hover |
| `--border` | `oklch(0.800 0.028 75)` | `#C9BCAA` | Bordas decorativas 1px |
| `--border-strong` | `oklch(0.590 0.028 70)` | `#887B6C` | Contornos de controlo — ≥3:1 |
| `--foreground` | `oklch(0.220 0.015 255)` | `#161B22` | Tinta do pergaminho |
| `--muted` | `oklch(0.470 0.015 255)` | `#555B63` | Texto secundário |
| `--primary` | `oklch(0.500 0.145 40)` | `#A43E13` | Cobre forjado escuro |
| `--accent` | `oklch(0.520 0.105 85)` | `#846305` | Dourado escurecido (legível em pergaminho) |
| `--danger` | `oklch(0.520 0.190 25)` | `#BE222A` | Perigo |
| `--success` | `oklch(0.505 0.115 155)` | `#197746` | Concluído, `auto` |
| `--warning` | `oklch(0.522 0.115 62)` | `#97580E` | Atenção, `hitl` |
| `--info` | `oklch(0.480 0.075 230)` | `#296580` | Sistema/neutro, `stub` |
| `--on-solid` | — | `#FFFFFF` | Texto sobre fills sólidos |

### 2.3 Rácios de contraste verificados (WCAG AA)

Texto normal alvo ≥ 4.5:1 · texto grande/UI ≥ 3:1 · contornos de controlo ≥ 3:1 (`--border-strong`).

| Par | Dark | Light |
|---|---|---|
| `foreground` / `background` | **16.31:1** | **14.08:1** |
| `foreground` / `surface` | **14.86:1** | **16.05:1** |
| `foreground` / `surface-raised` | **13.22:1** | **17.03:1** |
| `muted` / `background` | **6.20:1** | **5.58:1** |
| `muted` / `surface` | **5.65:1** | **6.36:1** |
| `muted` / `surface-raised` | **5.03:1** | **6.75:1** |
| `primary` / `background` | **6.81:1** | **5.22:1** |
| `accent` / `background` | **10.83:1** | **4.54:1** |
| `danger` / `background` | **5.64:1** | **4.95:1** |
| `success` / `background` | **8.24:1** | **4.54:1** |
| `warning` / `background` | **7.72:1** | **4.60:1** |
| `info` / `background` | **6.32:1** | **5.23:1** |
| status / `surface` (pior caso) | **5.14:1** (`danger`) | **5.17:1** (`success`) |
| status / `surface-raised` (pior caso) | **4.57:1** (`danger`) | **5.48:1** (`success`) |
| badge `--foreground` sobre tinta 15% (pior caso) | **9.53:1** (`accent`) | **11.14:1** (`danger`) |
| glifo `<status>` sobre tinta 15% (UI ≥3:1, pior caso) | **3.81:1** (`danger`) | **3.73:1** (`success`) |
| `--on-solid` sobre `primary` (fill) | **6.81:1** | **6.41:1** |
| `--on-solid` sobre `accent` (fill) | **10.83:1** | **5.57:1** |
| `--on-solid` sobre `danger` (fill) | **5.64:1** | **6.08:1** |
| `--on-solid` sobre `success` (fill) | **8.24:1** | **5.57:1** |
| `--on-solid` sobre `warning` (fill) | **7.72:1** | **5.65:1** |
| `--on-solid` sobre `info` (fill) | **6.32:1** | **6.43:1** |
| `border-strong` / `background` · `surface` · `surface-raised` | **3.72 · 3.39 · 3.02:1** | **3.35 · 3.82 · 4.06:1** |

Pior caso global de texto: **4.54:1** (`accent`/`success` light sobre `background`) · de UI/bordas: **3.02:1** (`border-strong`/`surface-raised` dark) — ambos acima do limiar. Os pares de badge cobrem `badge × background/surface/surface-raised × dark/light` para os 6 tokens de estado (36 tintas + 36 glifos, todos verificados).

Regras derivadas (obrigatórias):
1. **Cor nunca é o único sinal** — todo chip/badge de estado tem etiqueta em PT + glifo (invariante da auditoria `uxui-audit-ui.md`).
2. **Fills sólidos** (botão primário, chip sólido, barra de status) levam **sempre** `--on-solid` por cima; nunca texto `--foreground` sobre um fill de status.
3. **Badges tintados** (o padrão por omissão, §5.10): fundo `color-mix(in oklab, var(--token-status) 15%, transparent)`, **etiqueta + confiança em `--foreground`** (AA sobre a tinta em qualquer fundo) e **glifo em `--token-status`** (objeto não-texto, ≥ 3:1 sobre a tinta). O texto em `--token-status` sobre a própria tinta **reprovou AA** (16/36 pares; pior caso 3,59:1 `danger` sobre tinta em `surface-raised` dark), pelo que o sinal cromático vive na tinta + glifo, nunca no texto. Regra de semântica fixa: `--accent` **nunca** codifica estado; `--warning`/`--danger`/`--success` **nunca** são decorativos.
4. Contornos de controlo usam `--border-strong`; `--border` é apenas para separação decorativa (isenta de 1.4.11).
5. Focus ring: `2px solid var(--accent)` com `outline-offset: 2px` (mantém a visibilidade `:focus-visible` auditada) — contraste accent/superfícies ≥ 4.5:1 em ambos os modos.

### 2.4 Como aplicar

**`web/src/index.css`** — os tokens entram em `:root` (light) e `.dark` (dark, o default). Os aliases Tailwind v4 (`--color-background`, `--color-card`, `--color-primary`, `--color-muted`, `--color-destructive`, `--color-border`, `--color-ring`, …) **continuam a apontar** para estes nomes; acrescentar apenas os que faltam (`--color-surface-raised`, `--color-border-strong`, `--color-on-solid`, `--color-info` existente realinhado). Mapeamentos de vocabulário (obrigatórios para o registry Motion UI continuar *theme-dumb*):

| shadcn/Motion UI | Token deste spec |
|---|---|
| `--background` · `--foreground` | `--background` · `--foreground` |
| `--card` (≡ `--surface`) · `--card-foreground` | `--surface` · `--foreground` |
| `--popover` · `--popover-foreground` | `--surface-raised` · `--foreground` |
| `--muted` · `--muted-foreground` | `color-mix(in oklab, var(--foreground) 8%, transparent)` · `--muted` |
| `--primary` · `--primary-foreground` | `--primary` · `--on-solid` |
| `--secondary` · `--secondary-foreground` | `--surface-raised` · `--foreground` |
| `--accent` (hover/seleção) · `--accent-foreground` | `color-mix(in oklab, var(--accent) 14%, transparent)` · `--foreground` |
| `--destructive` · `--destructive-foreground` | `--danger` · `--on-solid` |
| `--border` · `--input` · `--ring` | `--border` · `--border-strong` · `--accent` |
| `--chart-1..5` | `--primary`, `--accent`, `--info`, `--success`, `--danger` |
| `--radius` | `10px` (**mantém-se** — os componentes instalados já usam a rampa derivada: `sm 6px` `md 8px` `lg 10px` `xl 14px`) |

**`web/src/theme.ts`** (MUI) — só a pele muda; **todos os overrides auditados se mantêm** (`backgroundImage: 'none'`, `:focus-visible`, scrollbar, `TableHead`, etc.):

```ts
// dark (default) — trocar apenas valores de paleta/typography/shape
const darkPalette = {
  mode: 'dark',
  primary:   { main: '#E97C48', contrastText: '#0B0F14' }, // brasa
  secondary: { main: '#DFC05B', contrastText: '#0B0F14' }, // dourado
  error:     { main: '#F05653', contrastText: '#0B0F14' },
  success:   { main: '#3CC078', contrastText: '#0B0F14' },
  warning:   { main: '#EB8E28', contrastText: '#0B0F14' },
  info:      { main: '#3A9EC8', contrastText: '#0B0F14' },
  background:{ default: '#0B0F14', paper: '#151A1F' },
  text:      { primary: '#F0ECE4', secondary: '#8D939B' },
  divider:   '#30363E',
}
// light — idêntico com os hex da tabela 2.2 (on-solid #FFFFFF nos contrastText)
```

`shape.borderRadius: 8` (base `md` — botões, `ListItemButton`) · `colorSchemeSelector: 'class'` mantêm-se. **Raios:** `8px` é a base do MUI; cartões usam `10px` (`MuiCard`/`--radius-lg`, §4.1/§5.3) e modais/sheets `14px` (`MuiDialog`) — a rampa §4.1 (`--radius: 10px` → `sm 6` · `md 8` · `lg 10` · `xl 14`) é a única fonte de raios e o `web/src/theme.ts` segue-a literalmente (`shape: 8` · `MuiCard: 10` · `MuiDialog: 14`). `BANDS` do `theme.ts` **não muda de semântica**: `auto→success`, `hitl→warning`, `abstain→error` — só ganham os hex novos (`error` dark `#F05653`).

**`web/index.html`**: `meta theme-color` = `#0B0F14` (dark) com `media="(prefers-color-scheme: dark)"` e `#EFE7D8` (light). **`web/public/favicon.svg`**: bigorna/runa a `#DFC05B` sobre fundo `#0B0F14` (fundo arredondado `rx=4` de 64×64, traço 4px — legível a 16px).

---

## 3. Tipografia

### 3.1 Famílias (self-host, sem CDN)

| Papel | Família | Pacote (self-host) | Pesos | Fallback stack |
|---|---|---|---|---|
| Display («runa gravada») | **Cinzel** | `@fontsource/cinzel` | 600, 700 | `"Cinzel", "Times New Roman", Georgia, serif` |
| Texto (UI/corpo) | **Inter** | `@fontsource-variable/inter` | 400–600 (variable) | `"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` |
| Mono (dados/técnica) | **IBM Plex Mono** | `@fontsource/ibm-plex-mono` | 400, 500 | `"IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace` |

- **Instalação:** `npm i @fontsource/cinzel @fontsource-variable/inter @fontsource/ibm-plex-mono` em `web/`. Imports em `web/src/main.tsx`: `import '@fontsource/cinzel/latin-600.css'`, `import '@fontsource/cinzel/latin-700.css'`, `import '@fontsource-variable/inter'`, `import '@fontsource/ibm-plex-mono/latin-400.css'`, `import '@fontsource/ibm-plex-mono/latin-500.css'` (se o subconjunto `latin-*` não existir no pacote, usar o `*.css` full e pronto — ambos são locais). Os woff2 ficam no bundle do Vite: **zero CDN, zero `<link>` externo** (regra do projeto).
- **Substituições:** `--font-sans` passa de Roboto → Inter; `--font-heading` passa a `var(--font-display)` (Cinzel) em vez de `var(--font-sans)`; `--font-mono` → IBM Plex Mono. `@fontsource/roboto` deixa de ser importado; `@fontsource-variable/geist` (resíduo sem `font-family`) **é removido do `package.json`**.
- `font-display: swap` (default do fontsource). Preload opcional em `index.html` para `cinzel-latin-600.woff2` e o variable Inter (só se o build emitir nomes estáveis).
- **MUI:** `typography.fontFamily` = stack Inter; variantes `h1|display|h2|h3` (e `h4` de painel) ganham `fontFamily` Cinzel + `letterSpacing` da tabela; `overline` = Cinzel 700 maiúsculas, `tracking 0.14em`. Tailwind v4 `@theme`: `--font-display`, `--font-sans`, `--font-mono` com as stacks acima (classes `font-display`/`font-sans`/`font-mono`).

### 3.2 Escala (base 16px, tudo em `rem`)

| Papel | Tamanho | Família / peso | LH | Tracking | Onde se usa |
|---|---|---|---|---|---|
| `--text-hero` | `clamp(2.25rem, 4.5vw, 3.25rem)` (36→52px) | Cinzel 700 | 1.05 | 0.01em | Portão de entrada (hero do `#/aventura`) |
| `--text-title` | `1.75rem` (28px) | Cinzel 600 | 1.15 | 0.02em | Título de painel (cabeçalho do `PanelShell`) |
| `--text-section` | `1.125rem` (18px) | Cinzel 600 | 1.3 | 0.04em | Título de secção (junto à régua runa) |
| `--text-card` | `1rem` (16px) | Inter 600 | 1.35 | 0 | Títulos de cartão, diálogos, `InfoCard` |
| `--text-body` | `0.9375rem` (15px) | Inter 400 | 1.55 | 0 | Corpo, descrições, narrativa |
| `--text-small` | `0.875rem` (14px) | Inter 400 | 1.5 | 0 | Tabelas, helper text, navegação |
| `--text-micro` | `0.75rem` (12px) | Inter 500 | 1.4 | 0.02em | Chips, legendas, `FreshnessIndicator` |
| `--text-overline` | `0.75rem` (12px) | Cinzel 700, `uppercase` | 1.2 | 0.14em | Rótulos runa: «MISSÃO», «SISTEMA», «DIÁRIO» |
| `--text-data` | `0.8125rem` (13px) | IBM Plex Mono 400, `tabular-nums` | 1.4 | 0 | IDs, timestamps, probabilidades, custos |
| `--text-counter` | `1.5rem` (24px) | IBM Plex Mono 500, `tabular-nums` | 1.1 | −0.01em | Contadores (`animated-number`, gastos, HP) |

Regras: **nunca abaixo de 12px**; Cinzel só em hero/título de painel/título de secção/overline — **nunca em corpo, tabelas ou dados**; qualquer número que muda usa `tabular-nums`; comprimento de leitura máx. **72ch** (`text-pretty` em parágrafos, `text-balance` em títulos); IDs/caminhos do contrato (`goto:river_bank`, `data/save`) permanecem em mono, sem tradução (convenção auditada).

---

## 4. Ornamentos e materiais

### 4.1 Geometria

- **Espaçamento** (base 4px): `4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64` px. Padding de cartão: `20px` (mobile `16px`); gap entre cartões: `16px`; entre secções: `32px`; entre hero e conteúdo: `48px`.
- **Raios:** `--radius` = `10px` (mantido) → `sm 6px` (chips, inputs) · `md 8px` (botões) · `lg 10px` (cartões) · `xl 14px` (modais, sheets, hero).
- **Bordas:** `1px solid var(--border)` por omissão; controlos `1px solid var(--border-strong)`; foco `2px solid var(--accent)` + `offset 2px`. Nada de bordas > 2px, exceto o **edge da forja**: `border-top: 2px solid var(--primary)` no cartão de missão ativa / bloco com loop a correr.
- **Cantos talhados (pedra):** realce interno de topo `inset 0 1px 0 rgba(255,255,255,0.05)` (dark) / `rgba(255,255,255,0.6)` (light) em cartões `--surface`. **Marcas de canto runa** (4 «L» de 8px, traço 2px `--accent` a 45% opacidade) **só** no hero e no cartão de missão — nunca em cartões de dados.
- **Sombras (máx. 2 níveis):** `--shadow-1: 0 1px 2px rgba(0,0,0,0.45)` (light: `0 1px 2px rgba(60,45,25,0.12)`) · `--shadow-2: 0 8px 24px rgba(0,0,0,0.35)` (light: `0 8px 24px rgba(60,45,25,0.14)`) — `shadow-2` só em overlays. Sem glow: a única luminosidade permitida é o focus ring e o `border-beam` do badge de loop (§6).

### 4.2 Texturas (CSS/SVG inline, sem imagens)

**Ruído de pedra** — `background-image` no `body` (camada de fundo, sem overlay, sem `mix-blend-mode`): 

```css
body {
  background-color: var(--background);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
}
```
`opacity` interno do `rect`: **0.03 dark · 0.045 light** (nunca acima). No modo light acrescentar, por baixo do ruído, `radial-gradient(120% 80% at 50% -10%, rgba(255,255,255,0.5), transparent 60%)` para o brilho de pergaminho. **Superfícies de dados (`--surface`) são fill sólido, sem textura**; a textura vê-se só no fundo da página e no hero.

### 4.3 Glifos (SVG inline, sem bibliotecas)

Novo ficheiro `web/src/components/icons/RunaGlyph.tsx`: sprite de paths React (não instalar nada; `@mui/icons-material`/`lucide-react` continuam para ícones genéricos de UI — fechar, refresh, filtros). Regras de desenho: **viewBox `0 0 24 24`**, `stroke="currentColor"`, `stroke-width="1.5"`, `fill="none"`, `stroke-linecap="round"`, tamanhos `16/20/24px` (`shrink-0` em flex). Tabela canónica:

| Glifo | Path (resumo geométrico) | Uso |
|---|---|---|
| `anvil` | bigorna: `M4 9h7l2-2h7v3c0 2.2-1.8 4-4 4h-3l-1 3H9l-1-3H6a2 2 0 0 1-2-2V9z` | Sistema/Controlo (`#/controlo`) |
| `flame` | `M12 3c3 4 5 6 5 9a5 5 0 0 1-10 0c0-2 1-3.5 2.5-5 .3 1.2 1 2 2 2.2C11 7 11 5 12 3z` | Loop a correr / tarefa ativa |
| `shield` | `M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z` | Kill-switch / segurança |
| `scroll` | `M6 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4z M9 8h6 M9 12h6` | Memória / diário / narrativa |
| `map` | `M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2V6z M9 4v14 M15 6v14` | Aventura / mundo |
| `gem` | `M6 4h12l3 5-9 11L3 9l3-5z M3 9h18 M12 20l-3-11 3-5 3 5-3 11` | Gastos / recursos |
| `key` | `M15 3a6 6 0 1 1-5.7 7.9L4 16.2V20h3.8l1-1v-2h2v-2h2l1.2-1.2A6 6 0 0 1 15 3z` | Chaves API |
| `hourglass` | `M7 3h10 M7 21h10 M8 3c0 5 8 5 8 9s-8 4-8 9 M16 3c0 5-8 5-8 9s8 4 8 9` | À espera de humano (fila `hitl`) |
| `alert` | `M12 4l9 16H3L12 4z M12 10v4 M12 17h.01` | Erro / aviso |
| `diamond` | `M6 1l5 5-5 5-5-5z` (12×12) | Núcleo da régua runa |

### 4.4 Régua divisória runa

```css
.runa-rule { display: flex; align-items: center; gap: 12px; height: 24px; margin: 24px 0; }
.runa-rule::before, .runa-rule::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.runa-rule > svg { width: 12px; height: 12px; color: var(--accent); } /* .diamond */
```
Usar **só** entre secções dentro de um painel (máx. 1 por secção). Alternativa autorizada: `repeating-linear-gradient(90deg, var(--border) 0 6px, transparent 6px 10px)` («corda») para separar linhas de meta-informação.

### 4.5 Orçamento de ornamento (anti-skeuomorfismo)

Níveis: **0 — dados** (tabelas, métricas, logs): zero ornamento, só `--border` e tipografia. **1 — moldura de secção**: régua runa + glifo de secção 20px + título Cinzel. **2 — hero/missão**: textura, cantos talhados, edge da forja. Máx. **1 elemento de nível 2 por ecrã**. Limites duros: gradientes máx. 2 paragens dentro da mesma família de matiz; sem bevel 3D, madeira, pedra rasgada, velas, brasões, sombra de texto; textura ≤ 4.5% de opacidade; ornamento nunca por baixo de bloco de números. **Teste de pronto:** com `--texture: none` e sem `.runa-rule`/glifos, o ecrã tem de continuar 100% legível e hierárquico.

---

## 5. Padrões de superfície

Invariantes transversais (herdadas da auditoria 100/100 — **não regredir**): texto visível só em PT · cor nunca sozinha · confiança ausente = «indisponível» honesto com tooltip · `:focus-visible` visível · skip-link · alvos ≥ 40px (`minHeight: 40` nas ações primárias) · `aria-live` só em transições · `FreshnessIndicator` em todo painel com dados · chip «dados simulados» + badge `mock · …`.

1. **Hero / portão de entrada** (`#/aventura`, topo do `AventuraPanel`): fundo `--background` com textura; glifo `map` 48px em `--accent`; título `--text-hero` Cinzel 700 («A Fortaleza-Vigia» ou nome da campanha), subtítulo `--text-body` `--muted`, máx. 60ch; CTA primário (brasa, `--on-solid`, `minHeight: 40`) + secundário outline; sem imagem por baixo de texto (se algum dia houver arte: véu `rgba(11,15,20,0.72)` mínimo). Entrada: `stagger-reveal` com `travel.section` 48px no título.
2. **Navegação** (`AppShell`/`TopBar`/`PanelTabs`/`NavDrawer`): grupos existentes («Supervisão» / «Agente» / «Memória») com overline Cinzel; item ativo = texto `--foreground` + sublinhado `2px var(--accent)` + glifo 20px; inativo `--muted`; hover `snap` (`translateY(-1px)` — **único** valor de hover do spec, igual no §6; bg `--surface-raised`); badge de pendentes (nº em mono 12px, fill `--warning` + `--on-solid`) em `#/perguntas` e `#/controlo` quando há `hitl`/aprovações; uma só navegação em mobile (invariante).
3. **Cartão de missão/quest** (`AdventureTaskCard`): placa `--surface`, raio 10px, `--border`, cantos talhados; `border-top: 2px solid var(--primary` quando a tarefa está a correr; glifo por tipo (flame/map/scroll) 24px; título `--text-card`; `progress-bar` (registry) 6px raio 3 com fill `--primary` (concluída: `--success`; erro: `--danger`); estado como chip etiquetado (§5.10); `FreshnessIndicator` + ações (`minHeight: 40`) alinhadas à direita. Detalhe abre em `sheet`.
4. **Ficha de personagem** (novo padrão, `#/aventura` estado + futuro painel de sessão): placa `--surface-raised` com título Cinzel `--text-section` + nome do aventureiro em `--text-title`; **stats** em grid 2–4 colunas: rótulo `--text-micro` `--muted` + valor `--text-counter` mono `tabular-nums` (`stats-counters`/`animated-number` para variações, `lively`); **barras HP/energia**: track `8px` raio 4 `--surface`, fill `--success` (≥50%), `--warning` (20–49%), `--danger` (<20%) com **valor numérico sempre visível ao lado** («37 / 52») — a barra nunca é a única leitura; **skills**: linha rótulo + valor + `sparkline` (registry) opcional para tendência; sem inventar atributos fora do contrato (`docs/api.md` / game state).
5. **Painel de decisão** (`#/decisoes`, `DecisionsPanel`): tabela `--text-small` com colunas leitura-rápida (hora mono, papel, ação mono, banda, estado); `BandChip` com tooltip `BAND_DESCRIPTION`; probabilidades em `--text-data` com barra fina 2px (`--primary` para a escolhida, `--border` para as restantes); ordenação desc garantida; filtros MUI (§5.11); linha abre `sheet` com todas as probabilidades/respostas tipadas/`issues`; chips de `status` («executada»/«virou pergunta»/«erro»/«desconhecido») com glifo + etiqueta.
6. **Tabela de eventos/diário** (`#/decisoes` log, `#/inputs`, histórico de aprovações): linhas `36–40px`, separadas por `1px var(--border)` (zebra sem cor), hover `--surface-raised`, timestamps mono `--muted`; paginação «Carregar mais» (25/página) mantida; **novas linhas por WebSocket não animam** (a chegada relevante é anunciada por toast + `aria-live`); expansível «Diagnóstico do pedido» mantido.
7. **Empty state** — «A masmorra ainda não foi explorada»: glifo 40px `--muted`, título `--text-section` Cinzel, corpo `--text-body` explicando **o que falta e porquê** (padrão auditado), 1 CTA concreta (ex.: «Criar aventura» → wizard). Por painel, a mensagem nomeia o objeto real: «Ainda não há memória nesta campanha», «Nenhuma pergunta à espera — o agente segue em autopilot», «Sem decisões: o loop ainda não correu». Zero animação decorativa; entrada `stagger-reveal` simples.
8. **Loading (skeleton)**: `skeleton`/`loader-skeleton` do registry sobre `--surface-raised`, shimmer 1.5s linear, blocos de alturas `12/16/24px` com raio 6, cor **neutra** (nunca cor de marca); manter o layout final (mesmas alturas/gaps) para não haver salto; labels PT («A carregar estado…») junto a ações desativadas (invariante: nenhuma ação ativa sobre estado desconhecido).
9. **Estados de erro**: moldura `1px var(--danger)` sobre `--surface`, glifo `alert` 20px `--danger`, título `--text-card` («Não foi possível ler o estado do jogo»), causa `--text-small` `--muted`, ações: «Tentar de novo» (`multi-state-button`: idle→loading→ok/erro) + «Ver logs»; erro de campanha/campos = `Alert` MUI com `severity` mapped aos tokens; **nunca** ecrã full-bleed vermelho; error boundary mantém o shell por fora.
10. **Badges de sistema / bandas** (`rpg/BandBadge.tsx` via `Chips.tsx`): `auto` → `--success`, `hitl` → `--warning`, `abstain` → `--danger`, `stub`/«dados simulados» → `--info` (comportamentos `theme.ts` BANDS mantidos). Forma: height 24px, padding `0 8px`, raio 6, fundo `color-mix(in oklab, <status> 15%, transparent)`, **texto `--foreground`** (AA sobre a tinta), **glifo 14px `<status>`** (≥3:1 sobre a tinta, regra 3 §2.3), `--text-micro` Inter 500, etiqueta PT («auto», «abstenção»…) + tooltip com limiares («auto ≥ 0,90 · hitl 0,50–0,89 · abstain < 0,50»). Variante **só** para risco extremo: fill sólido `<status>` + `--on-solid` (kill-switch armado, erro de loop). Confiança em mono: número com 2 casas ou «indisponível». Nota: os chips MUI de `chipSkin.ts` partilham a forma mas ainda usam texto `<status>` sobre a tinta — a migrar para `--foreground` (fora desta revisão).
11. **Formulários MUI** (`#/controlo` chave/modo, `#/memoria`, `#/objetivo`, `#/inputs`, wizard): reutilizar o tema, **não** componentes novos. `TextField/Select/Switch/MenuItem/ToggleButton/Dialog/Accordion/Chip/Table/Typography/Button` continuam (decisão auditada) só com a pele: inputs `variant="outlined"` com `border-strong` e focus `--accent`; labels `--text-micro` `--muted`; helper 12px; botões `minHeight: 40`, primário brasa/`--on-solid`, secundário outline `--border-strong`, destrutivo `--danger` com confirmação `alertdialog` (kill-switch mantém fricção: `hold-to-confirm`); rascunho + «alterações por guardar» + «Reverter» mantidos em Memória/Objetivo. **Não partir o auditado:** nada de estilo que remova `aria-describedby`, tooltips de banda, ou os `slotProps` de acessibilidade já fixados.

---

## 6. Vocabulário de motion

Tokens canónicos (já em `web/motion.theme.ts` via `@/components/motion-ui/ui-theme` — **não reinventar números**; `MotionUIThemeProvider` já montado em `main.tsx`):

| Token | Física | Para quê |
|---|---|---|
| `snap` | `{ stiffness: 1218, damping: 70 }` | Feedback imediato: hover, aba, toggle, chevron |
| `ui` | `{ stiffness: 305, damping: 33 }` | O padrão: cartões, reveals, transição painel→painel |
| `gentle` | `{ stiffness: 110, damping: 20 }` | Superfícies grandes: sheets, diálogos, hero |
| `lively` | `{ stiffness: 622, damping: 17 }` | Comemorativo: contadores, sucesso de tarefa |
| `ambient` | `{ stiffness: 43, damping: 13 }` | Contínuo: pulso da chama do loop (ciclo ≥ 3000ms) |

Apoio: `stagger { tight: 0.04, base: 0.08, relaxed: 0.15 }` · `travel { hover: 4, enter: 24, section: 48 }` (px; `travel.hover` é o **teto de deslocação** do token de motion — o gesto de hover em si é `translateY(-1px)`, único valor, §5.2/§6) · `inView { amount: 0.4, once: true }` · `reducedMotion: "calm"`.

Disciplina: animar **só** `transform/opacity/filter` (layout por prop `layout`); durações máximas — micro-interação 180ms, UI 320ms, entrada 500ms, contador 600ms; nunca `stiffness/damping` escritos à mão em componente (sempre `useMotionUITransition(...)`).

| Superfície | Token | Componente registry | Comportamento |
|---|---|---|---|
| Entrada de painel (hash `#/x`) | `ui` | `motion.div` (já existe) | fade + `y: 12px`, 320ms; **sem** curtain full-screen |
| Hero / portão | `gentle` + `stagger.relaxed` | `hero-editorial-stagger` (instalar) ou `stagger-reveal` | título revela linha a linha, uma vez |
| Listas de cartões (perguntas, aprovações, saves) | `ui` + `stagger.base` | `stagger-reveal` (instalado) | reveal **por montagem**; itens WS não re-animam |
| Hover de cartão/botão | `snap` (+ teto `travel.hover` 4px) | — (whileHover) | `translateY(-1px)` + `shadow-1`; sem tilt 3D em dados |
| Tabs/filtros | `snap` | `smooth-tabs` (instalar) ou `segmented-toggle` | pílula ativa desliza |
| Sheet/detalhe/diálogo | `gentle` | `sheet` (instalado), `overlay` | fade-up 50px; drag-to-dismiss só em sheet |
| Contadores (gastos, stats, HP) | `lively` | `animated-number`, `stats-counters` (instalados) | 600ms até ao valor; `tabular-nums` |
| Barras de progresso | `ui` | `progress-bar` (instalado) | preenchimento contínuo; sem bounce |
| Sparklines/tendências | `ui` | `sparkline` (instalado) | desenho do traço ao revelar |
| Toasts (chegada de pergunta/re-pedido) | `ui` | `toast-stack` (instalado) | fan-in; `z-[60]` acima de sheets (como hoje) |
| Skeleton | — | `skeleton`, `loader-skeleton` | shimmer 1.5s; transição para conteúdo com mask-wipe ≤ 300ms |
| Estado do loop a correr | `ambient` | `border-beam` (instalar, **1 instância**) | varredura no badge «loop ativo»; desligada se loop parado |
| Conclusão de criação (mundo/personagem) | `lively` | `confetti` (instalar) | **única** celebração do produto; uma vez por tarefa concluída |
| Tabela de eventos/dados | — | — | **sem animação de linha**; só o fade do «Carregar mais» |

**Reduced-motion (obrigatório):** `reducedMotion: "calm"` cobre o registry (remove travel, mantém fades). Efeitos contínuos escritos à mão (`border-beam`, shimmer extra, pulse) exigem gate `useReducedMotion()`. **O estado reduzido mostra 100% do conteúdo**: reveals aparecem no estado final imediatamente, contadores saltam para o valor final, skeleton fica estático (sem shimmer), `confetti` não dispara (o feedback textual/`role=status` mantém-se). Verificação de pronto: com `prefers-reduced-motion: reduce` nenhum valor/dado desaparece ou chega atrasado.

---

## 7. Anti-padrões proibidos

1. **Tipografia temática ilegível** — Cinzel em corpo/tabelas, blackletter/uncial, maiúsculas corridas > 3 palavras em `--text-body`, corpo < 15px, tracking negativo em display < 28px.
2. **Grades arco-íris ou neon** — gradientes > 2 paragens, gradientes entre matizes distantes, glow colorido em blocos de dados.
3. **Cards dentro de cards** — um cartão é uma placa; agrupamentos usam régua runa + gap, nunca moldura dupla (inclui `InfoCard` dentro de `SectionCard` decorada).
4. **Texto sobre imagem/textura sem véu** — mínimo `rgba(11,15,20,0.72)` (dark) ou `rgba(250,246,238,0.86)` (light) atrás de qualquer texto sobre arte.
5. **Animação que atrasa leitura de dados** — contadores > 800ms, stagger em linhas de tabela ao vivo, transições de página > 400ms, skeleton que troca de layout ao carregar, números escondidos durante animação.
6. **Cor como único sinal** — badge sem etiqueta PT + glifo; barras sem valor numérico; estados de decisão sem texto.
7. **Skeuomorfismo pesado** — pergaminho rasgado, madeira, pedra fotográfica, velas, brasões 3D, sombras de texto, bevel > 2 paragens, textura > 4.5% opacidade.
8. **Sons, partículas, sparkles contínuos** — o único «brilho» permitido é o `border-beam` do badge de loop (§6).
9. **Efeitos CRT/glitch/flicker/scanlines** — arruínam leitura de logs longos.
10. **Cursor customizado em superfícies operacionais** — `magnetic-pull`/cursor substituto banido em tabelas, inputs e botões de segurança.
11. **Hover como única forma de aceder a dado** — tooltips são suplemento; `issues`/`source_data`/confiança continuam acessíveis por clique/foco.
12. **Emoji como ícone de estado** — estados usam `RunaGlyph`/ícones de UI; emoji banidos em badges, chips e botões.
13. **Bordas > 2px, glow com blur > 8px, sombras além de `--shadow-2`.**
14. **Skeleton com cor de marca ou shimmer em cores** — neutro sempre.
15. **Marquee/ticker em zonas de leitura** e animações em loop por baixo de números.
16. **Celebrar sem motivo** — `confetti`/`lively` só em conclusão de tarefa criativa; nunca em refresh de dados, kills de erro ou arranque de página.
17. **Traduzir identificadores do contrato** (`goto:river_bank`, `SOD_*`, `hitl` em filtro cru sem label) ou inventar vocabulário de RPG que substitua termos operacionais («HP do sistema» em vez de «estado do loop») — a metáfora veste, não renomeia.
18. **Regressões de acessibilidade auditadas** (§5 invariantes) — qualquer uma delas é bug de design.

---

## 8. Mapeamento final — superfície → componente do registry → customização permitida

Instalação sempre via `npx shadcn@latest add @motion/<name>` (ou `motion-ui.mjs add`); **nunca editar** `web/src/components/motion-ui/**` (o CLI é dono) — customizar em **wrappers** do projeto; visual só por classes semânticas/tokens; movimento só por `useMotionUITransition`. Já instalados: `animated-number`, `progress-bar`, `sheet`, `skeleton`, `sparkline`, `stagger-reveal`, `toast-stack`, `ui-theme`, `sections/stats-counters` (**adotar** — ver abaixo; alternativa: remover, nunca deixar sem uso).

| Superfície (ficheiro real) | Componente registry Motion UI | Customização permitida |
|---|---|---|
| `#/aventura` hero/portão (`AventuraPanel`) | `hero-editorial-stagger` (instalar) + `stagger-reveal` | Título Cinzel `--text-hero`, glifo `map` 48px, textura de fundo, CTA brasa; sem arte por baixo de texto |
| Wizard de criação (`AdventureWizard` + `WizardStepper`) | `WizardStepper` composto (projeto) + `progress-bar` + `skeleton` | Rótulos de passo em overline Cinzel, glifos por etapa (map/key/hourglass); manter as 3 saídas de fecho + `aria-live` só em transições |
| Cartão de missão (`AdventureTaskCard`) | `progress-bar` + `stagger-reveal` + `expand-card` (instalar, para detalhe inline) | Moldura pedra, edge da forja 2px quando ativo, cantos talhados, glifo por tipo; detalhe em `sheet` |
| Ficha de personagem (estado do aventureiro) | `stats-counters` (**adotar**) + `animated-number` + `progress-bar` + `sparkline` | Barras HP/energia 8px com valor visível; grid mono `tabular-nums`; sem atributos inventados |
| Navegação (`AppShell`/`TopBar`/`PanelTabs`/`NavDrawer`) | `smooth-tabs` (instalar, opcional) + `command-palette` (instalar, ⌘K) | Overlines Cinzel, glifos 20px, sublinhado accent 2px, badge de pendentes; ⌘K lista os 9 painéis + ações |
| `#/perguntas` (`QuestionsPanel`, `QuestionCard`, `QuestionsQueueDrawer`) | `stagger-reveal` (cartões) + `sheet` (fila/detalhe) + `toast-stack` (chegada) | Chips de banda (§5.10), `AttemptsChip`, expansível de diagnóstico; anúncio de chegada mantido |
| `#/decisoes` (`DecisionsPanel`) | `smooth-tabs`/`segmented-toggle` (filtros) + `sheet` (detalhe) + `skeleton` | Colunas leitura-rápida, barra de probabilidade 2px, chips de `status`; **sem** animação de linha |
| `#/metricas` (`MetricsPanel`, `VitalSignsStrip`, `MetricsGroups`) | `stats-counters` + `sparkline` + `accordion` (MUI mantido) | Sinais vitais acima da dobra; grupos por severidade; contadores `lively` só nos valores que mudam |
| `#/memoria`, `#/objetivo`, `#/inputs` (formulários + narrativa) | MUI (form) + `stagger-reveal` (blocos de narrativa) + `toast-stack` (gravado) | Textura pergaminho só no cabeçalho; corpo `--text-body` 72ch; glifos `scroll`/`key` nas secções |
| `#/ecra` (`ScreenPanel`, tradução ao vivo) | `stagger-reveal` (entradas de fala) — nada contínuo | Mono para EN, sans para PT; sem efeitos de máquina de escrever em dados |
| `#/controlo` (`ControlPanel`, `SystemSection`, `ApprovalsSection`, `KillSwitchButton`) | `hold-to-confirm`/`button-hold-to-confirm` (instalar, kill-switch) + `multi-state-button` (instalar, «Ligar o jogo»/«Definir chave») + `animated-number` (gastos) + `sheet` (aprovações) | Fills `--danger`/`--primary` com `--on-solid`; fricção e confirmações **intocáveis**; `gem`/`shield`/`anvil`/`key` |
| Estado vazio (`PanelState` — «a masmorra ainda não foi explorada») | `stagger-reveal` (composição do projeto) | Glifo 40px `--muted`, título Cinzel, CTA única; mensagem por painel (§5.7) |
| Loading (`SkeletonBlocks`) | `skeleton` + `loader-skeleton` | Alturas 12/16/24px, shimmer neutro 1.5s, mesmo layout do estado final |
| Erro (`PanelState`/`Alert`/error boundary) | `multi-state-button` («Tentar de novo») + `toast-stack` | Moldura `--danger` 1px, glifo `alert`; sem full-bleed vermelho |
| Toasts globais | `toast-stack` (instalado) | `z-[60]`, `role=status`, textos PT; cor só do token de status |
| Loop ativo (badge no TopBar) | `border-beam` (instalar) | **1 instância por ecrã**, `ambient`, gate `useReducedMotion()`, desligado quando o loop está parado |
| Conclusão de criação de mundo/personagem | `confetti` (instalar) | Uma vez por tarefa concluída; nunca em refresh |

**Regras finais de implementação:** (1) nenhum componente do registry lê hex — só tokens (§2.4); (2) qualquer CSS novo usa as classes semânticas/tokens, nunca `px`/hex cravados fora dos ficheiros de tema; (3) `motion-theme` **não** é reinstalado por cima do `web/motion.theme.ts` customizado; (4) verificação de pronto de cada peça: build/lint/typecheck a 0, contraste dos pares da tabela §2.3 inalterado, suíte headless com `prefers-reduced-motion: reduce` a mostrar 100% do conteúdo, e a auditoria `docs/uxui-audit-ui.md` sem novos achados.
