/**
 * Tema «RPG Medieval» — A Fortaleza-Vigia (docs/design-rpg-medieval.md).
 * Escuro (basalto) por omissão, light = pergaminho; denso mas calmo, dado primeiro.
 *
 * MUI v9: `colorSchemes` + `cssVariables` + `useColorScheme`.
 * `colorSchemeSelector: 'class'` é obrigatório para o toggle manual — o default
 * (`media`, `prefers-color-scheme`) não permite trocar de esquema pelo hook.
 *
 * Único sítio onde se escrevem cores MUI; os overrides auditados mantêm-se
 * (backgroundImage: 'none', :focus-visible, scrollbar, TableHead, etc.).
 */

import type { CSSProperties } from 'react';
import { createTheme } from '@mui/material/styles';

// §3.1 — a variante `display` (hero «portão de entrada») é custom: o MUI não a
// traz por omissão, acrescenta-se por module augmentation.
declare module '@mui/material/styles' {
  interface TypographyVariants {
    display: CSSProperties;
  }
  interface TypographyVariantsOptions {
    display?: CSSProperties;
  }
}
declare module '@mui/material/Typography' {
  interface TypographyPropsVariantOverrides {
    display: true;
  }
}

/** Dados/técnica: números, ids, timestamps em monoespaçado (§3.1). */
export const MONO_FONT =
  '"IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace';

/** Display «runa gravada»: hero, títulos de painel/secção, overlines (§3.1). */
export const DISPLAY_FONT = '"Cinzel", "Times New Roman", Georgia, serif';

/** Texto UI/corpo (§3.1). */
export const SANS_FONT = '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

/** Cores por banda de ação JEV (auto / hitl / abstain) — usadas em chips e séries. */
export const BAND_PALETTE = {
  auto: { palette: 'success', label: 'auto' },
  hitl: { palette: 'warning', label: 'hitl' },
  abstain: { palette: 'error', label: 'abstenção' },
} as const;

// §2.1 dark (basalto) — hex derivados do OKLCH canónico (o OKLCH manda; ver index.css).
const darkPalette = {
  mode: 'dark' as const,
  // Material só garante 3:1 por default; a UI exige 4,5:1 (WCAG 2.2 AA).
  contrastThreshold: 4.5,
  primary: { main: '#E97C48', contrastText: '#0B0F14' }, // brasa da forja
  secondary: { main: '#DFC05B', contrastText: '#0B0F14' }, // dourado das runas
  error: { main: '#F05653', contrastText: '#0B0F14' }, // --danger escuro (L 0.660: AA danger/surface-raised)
  success: { main: '#3CC078', contrastText: '#0B0F14' },
  warning: { main: '#EB8E28', contrastText: '#0B0F14' },
  info: { main: '#3A9EC8', contrastText: '#0B0F14' },
  background: { default: '#0B0F14', paper: '#151A1F' },
  text: { primary: '#F0ECE4', secondary: '#8D939B' },
  divider: '#30363E',
};

// §2.2 light (pergaminho) — on-solid #FFFFFF nos contrastText.
const lightPalette = {
  mode: 'light' as const,
  contrastThreshold: 4.5,
  primary: { main: '#A43E13', contrastText: '#FFFFFF' }, // cobre forjado escuro
  secondary: { main: '#846305', contrastText: '#FFFFFF' }, // dourado escurecido
  error: { main: '#BE222A', contrastText: '#FFFFFF' },
  success: { main: '#197746', contrastText: '#FFFFFF' },
  warning: { main: '#97580E', contrastText: '#FFFFFF' },
  info: { main: '#296580', contrastText: '#FFFFFF' },
  background: { default: '#EFE7D8', paper: '#FAF6EE' },
  text: { primary: '#161B22', secondary: '#555B63' },
  divider: '#C9BCAA',
};

const DIVIDER = 'var(--mui-palette-divider)';
const PAPER = 'var(--mui-palette-background-paper)';
const TEXT_SECONDARY = 'var(--mui-palette-text-secondary)';

export const theme = createTheme({
  // Toggle manual de esquema (useColorScheme) exige selector ≠ 'media'.
  cssVariables: { colorSchemeSelector: 'class' },
  colorSchemes: {
    dark: { palette: darkPalette },
    light: { palette: lightPalette },
  },
  // §2.4/§4.1 — base md 8px (botões); cartões lg 10px e modais xl 14px nos overrides.
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: SANS_FONT,
    // §3.1/§3.2 — Cinzel só em hero/título de painel/título de secção/overline.
    display: {
      fontFamily: DISPLAY_FONT,
      fontSize: 'clamp(2.25rem, 4.5vw, 3.25rem)',
      fontWeight: 700,
      lineHeight: 1.05,
      letterSpacing: '0.01em',
    },
    h1: {
      fontFamily: DISPLAY_FONT,
      fontSize: '1.75rem',
      fontWeight: 600,
      lineHeight: 1.15,
      letterSpacing: '0.02em',
    },
    h2: {
      fontFamily: DISPLAY_FONT,
      fontSize: '1.125rem',
      fontWeight: 600,
      lineHeight: 1.3,
      letterSpacing: '0.04em',
    },
    h3: {
      fontFamily: DISPLAY_FONT,
      fontSize: '1rem',
      fontWeight: 600,
      lineHeight: 1.35,
      letterSpacing: '0.04em',
    },
    h4: {
      fontFamily: DISPLAY_FONT,
      fontSize: '0.9375rem',
      fontWeight: 600,
      lineHeight: 1.35,
      letterSpacing: '0.04em',
    },
    h5: { fontSize: '0.875rem', fontWeight: 600, lineHeight: 1.35 },
    h6: { fontSize: '0.8125rem', fontWeight: 600, lineHeight: 1.35 },
    body1: { fontSize: '0.9375rem', lineHeight: 1.55 },
    body2: { fontSize: '0.875rem', lineHeight: 1.5 },
    button: { fontSize: '0.875rem', fontWeight: 600 },
    caption: { fontSize: '0.75rem', fontWeight: 500, lineHeight: 1.4, letterSpacing: '0.02em' },
    overline: {
      fontFamily: DISPLAY_FONT,
      fontSize: '0.75rem',
      fontWeight: 700,
      lineHeight: 1.2,
      letterSpacing: '0.14em',
      textTransform: 'uppercase',
    },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        'html, body, #root': { height: '100%' },
        body: { backgroundColor: 'var(--mui-palette-background-default)' },
        // Foco sempre visível e legível em ambos os esquemas (WCAG 2.4.7) —
        // §2.3 regra 5: ring 2px `--accent` com offset 2px.
        ':focus-visible': {
          outline: '2px solid var(--accent)',
          outlineOffset: '2px',
        },
        '*::-webkit-scrollbar': { width: 10, height: 10 },
        '*::-webkit-scrollbar-thumb': {
          backgroundColor: 'rgba(150, 165, 180, 0.35)',
          borderRadius: 8,
        },
        '*::-webkit-scrollbar-track': { backgroundColor: 'transparent' },
      },
    },
    MuiPaper: {
      styleOverrides: { root: { backgroundImage: 'none' } },
    },
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: PAPER,
          borderColor: DIVIDER,
          borderRadius: 10, // lg — placa de pedra (§4.1)
        },
      },
    },
    MuiAppBar: {
      defaultProps: { elevation: 0, color: 'transparent' },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: PAPER,
          borderBottom: `1px solid ${DIVIDER}`,
        },
      },
    },
    MuiToolbar: {
      styleOverrides: { dense: { minHeight: 52 } },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      // §5.11 — alvos ≥ 40px; primário brasa/`--on-solid`, secundário outline.
      styleOverrides: { root: { textTransform: 'none', minHeight: 40 } },
    },
    MuiToggleButton: {
      styleOverrides: { root: { textTransform: 'none', fontWeight: 600, borderRadius: 6 } },
    },
    MuiChip: {
      // §4.1 — chips/inputs em sm 6px.
      styleOverrides: { root: { fontWeight: 600, borderRadius: 6 } },
    },
    MuiListItemButton: {
      styleOverrides: { root: { borderRadius: 8 } },
    },
    MuiTableCell: {
      styleOverrides: {
        // §3.2 — tabelas em `--text-small`.
        root: { borderColor: DIVIDER, paddingBlock: 6, fontSize: '0.875rem' },
        head: { color: TEXT_SECONDARY, fontWeight: 700, backgroundColor: PAPER },
      },
    },
    MuiTooltip: {
      defaultProps: { arrow: true, enterDelay: 350 },
      styleOverrides: { tooltip: { fontSize: '0.75rem' } },
    },
    MuiTextField: {
      defaultProps: { size: 'small' },
    },
    MuiFormControl: {
      defaultProps: { size: 'small' },
    },
    // §5.11 — inputs outlined: contorno `--border-strong`, foco `--accent`.
    MuiOutlinedInput: {
      styleOverrides: {
        notchedOutline: { borderColor: 'var(--border-strong)' },
        root: {
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--accent)' },
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: { root: { borderRadius: 6, height: 8 } },
    },
    MuiDialog: {
      defaultProps: { fullWidth: true },
      styleOverrides: { paper: { borderRadius: 14 } }, // xl — modais/sheets (§4.1)
    },
    MuiAlert: {
      styleOverrides: { root: { alignItems: 'center' } },
    },
  },
});
