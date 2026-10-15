import type { SxProps, Theme } from '@mui/material/styles';

/**
 * Pele de badge do spec §5.10 (regra 3 do §2.3): height 24px, padding `0 8px`,
 * raio 6px, fundo `color-mix(in oklab, <status> 15%, transparent)`, texto no
 * token de estado, `--text-micro` (12px/500/0.02em), glifo 14px herdado da cor.
 * Etiqueta PT sempre presente — a cor nunca é o único sinal. Fills sólidos
 * ficam reservados ao risco extremo (kill-switch armado, erro de loop).
 *
 * Fora de `Chips.tsx` para o ficheiro de componentes não exportar utilitários
 * (react-refresh) — e para os painéis usarem a MESMA pele em chips locais.
 */
export type BadgeTone = 'success' | 'warning' | 'error' | 'info' | 'primary' | 'default';

const TONE_TOKEN: Record<Exclude<BadgeTone, 'default'>, string> = {
  success: 'var(--mui-palette-success-main)',
  warning: 'var(--mui-palette-warning-main)',
  error: 'var(--mui-palette-error-main)',
  info: 'var(--mui-palette-info-main)',
  primary: 'var(--mui-palette-primary-main)',
};

export function tintedChipSx(tone: BadgeTone): SxProps<Theme> {
  const token = tone === 'default' ? null : TONE_TOKEN[tone];
  return {
    height: 24,
    px: 1,
    borderRadius: '6px',
    fontSize: 12,
    fontWeight: 500,
    letterSpacing: '0.02em',
    ...(token
      ? {
          bgcolor: `color-mix(in oklab, ${token} 15%, transparent)`,
          color: token,
          borderColor: 'transparent',
        }
      : {
          bgcolor: 'transparent',
          color: 'text.secondary',
          borderColor: 'divider',
        }),
    '& .MuiChip-icon': { fontSize: 14, color: 'inherit', ml: 0.75, mr: -0.25 },
  };
}
