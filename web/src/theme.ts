/**
 * Tema "mission control": escuro por omissão, denso mas calmo, orientado a dados.
 *
 * MUI v9: `colorSchemes` + `cssVariables` + `useColorScheme`.
 * `colorSchemeSelector: 'class'` é obrigatório para o toggle manual — o default
 * (`media`, `prefers-color-scheme`) não permite trocar de esquema pelo hook.
 */

import { createTheme } from '@mui/material/styles';

/** Números e ids alinham melhor em monoespaçado. */
export const MONO_FONT =
  'ui-monospace, "SFMono-Regular", "JetBrains Mono", "Fira Code", Menlo, Consolas, monospace';

/** Cores por banda de ação JEV (auto / hitl / abstain) — usadas em chips e séries. */
export const BAND_PALETTE = {
  auto: { palette: 'success', label: 'auto' },
  hitl: { palette: 'warning', label: 'hitl' },
  abstain: { palette: 'error', label: 'abstenção' },
} as const;

const darkPalette = {
  mode: 'dark' as const,
  // Material só garante 3:1 por default; a UI exige 4,5:1 (WCAG 2.2 AA).
  contrastThreshold: 4.5,
  primary: { main: '#5BC8FF', contrastText: '#04121B' },
  secondary: { main: '#B7C4D4', contrastText: '#0B0F14' },
  background: { default: '#0B0F14', paper: '#121821' },
  text: { primary: '#E7EDF3', secondary: '#A7B4C2' },
  divider: 'rgba(231, 237, 243, 0.14)',
  success: { main: '#5DD19A', contrastText: '#04150E' },
  warning: { main: '#F2C14E', contrastText: '#1A1305' },
  error: { main: '#FF8A7A', contrastText: '#1D0705' },
  info: { main: '#6FB6FF', contrastText: '#04121B' },
};

const lightPalette = {
  mode: 'light' as const,
  contrastThreshold: 4.5,
  primary: { main: '#0B6FA4', contrastText: '#FFFFFF' },
  secondary: { main: '#3F4C59', contrastText: '#FFFFFF' },
  background: { default: '#F2F5F8', paper: '#FFFFFF' },
  text: { primary: '#101820', secondary: '#48545F' },
  divider: 'rgba(16, 24, 32, 0.14)',
  success: { main: '#1B6B45', contrastText: '#FFFFFF' },
  warning: { main: '#8A5A00', contrastText: '#FFFFFF' },
  error: { main: '#A3241A', contrastText: '#FFFFFF' },
  info: { main: '#0B5FA5', contrastText: '#FFFFFF' },
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
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: '"Roboto", "Helvetica Neue", Arial, sans-serif',
    h1: { fontSize: '1.375rem', fontWeight: 700, letterSpacing: '-0.01em' },
    h2: { fontSize: '1.125rem', fontWeight: 700 },
    h3: { fontSize: '1rem', fontWeight: 700 },
    h4: { fontSize: '0.9375rem', fontWeight: 700 },
    h5: { fontSize: '0.875rem', fontWeight: 700 },
    h6: { fontSize: '0.8125rem', fontWeight: 700 },
    body1: { fontSize: '0.875rem' },
    body2: { fontSize: '0.8125rem' },
    button: { fontSize: '0.8125rem', fontWeight: 600 },
    caption: { fontSize: '0.75rem' },
    overline: { fontSize: '0.6875rem', fontWeight: 700, letterSpacing: '0.08em' },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        'html, body, #root': { height: '100%' },
        body: { backgroundColor: 'var(--mui-palette-background-default)' },
        // Foco sempre visível e legível em ambos os esquemas (WCAG 2.4.7).
        ':focus-visible': {
          outline: '2px solid var(--mui-palette-primary-main)',
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
      styleOverrides: { root: { textTransform: 'none' } },
    },
    MuiToggleButton: {
      styleOverrides: { root: { textTransform: 'none', fontWeight: 600 } },
    },
    MuiChip: {
      styleOverrides: { root: { fontWeight: 600 } },
    },
    MuiListItemButton: {
      styleOverrides: { root: { borderRadius: 8 } },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: DIVIDER, paddingBlock: 6, fontSize: '0.8125rem' },
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
    MuiLinearProgress: {
      styleOverrides: { root: { borderRadius: 6, height: 8 } },
    },
    MuiDialog: {
      defaultProps: { fullWidth: true },
    },
    MuiAlert: {
      styleOverrides: { root: { alignItems: 'center' } },
    },
  },
});
