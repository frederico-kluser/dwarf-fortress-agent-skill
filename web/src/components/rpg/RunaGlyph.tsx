/**
 * Sprite de glifos runa (docs/design-rpg-medieval.md §4.3) — SVG inline, sem
 * bibliotecas. Ícones genéricos de UI (fechar, refresh, filtros) continuam a ser
 * `@mui/icons-material`/`lucide-react`; estes glifos são só a linguagem RPG.
 *
 * Regras de desenho: viewBox `0 0 24 24`, `stroke="currentColor"`,
 * `stroke-width="1.5"`, `fill="none"`, `stroke-linecap="round"`; tamanhos
 * 16/20/24px (`shrink-0` em flex). Exceção canónica: `diamond` é 12×12 (núcleo
 * da régua runa, §4.4).
 */
import type { CSSProperties } from 'react';

export type RunaGlyphName =
  | 'anvil'
  | 'flame'
  | 'shield'
  | 'scroll'
  | 'map'
  | 'gem'
  | 'key'
  | 'hourglass'
  | 'alert'
  | 'diamond';

/** Paths canónicos da tabela §4.3 — geometria literal, não redesenhada. */
const PATHS: Record<RunaGlyphName, string> = {
  anvil: 'M4 9h7l2-2h7v3c0 2.2-1.8 4-4 4h-3l-1 3H9l-1-3H6a2 2 0 0 1-2-2V9z',
  flame: 'M12 3c3 4 5 6 5 9a5 5 0 0 1-10 0c0-2 1-3.5 2.5-5 .3 1.2 1 2 2 2.2C11 7 11 5 12 3z',
  shield: 'M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z',
  scroll: 'M6 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4z M9 8h6 M9 12h6',
  map: 'M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2V6z M9 4v14 M15 6v14',
  gem: 'M6 4h12l3 5-9 11L3 9l3-5z M3 9h18 M12 20l-3-11 3-5 3 5-3 11',
  key: 'M15 3a6 6 0 1 1-5.7 7.9L4 16.2V20h3.8l1-1v-2h2v-2h2l1.2-1.2A6 6 0 0 1 15 3z',
  hourglass: 'M7 3h10 M7 21h10 M8 3c0 5 8 5 8 9s-8 4-8 9 M16 3c0 5-8 5-8 9s8 4 8 9',
  alert: 'M12 4l9 16H3L12 4z M12 10v4 M12 17h.01',
  diamond: 'M6 1l5 5-5 5-5-5z',
};

export interface RunaGlyphProps {
  name: RunaGlyphName;
  /** 16 | 20 | 24 por omissão de uso; `diamond` usa 12 (régua runa). */
  size?: number;
  className?: string;
  style?: CSSProperties;
  /** Quando presente, o glifo passa a significativo (senão é decorativo). */
  title?: string;
}

export function RunaGlyph({ name, size, className, style, title }: RunaGlyphProps) {
  const resolved = size ?? (name === 'diamond' ? 12 : 20);
  const meaningful = title != null;
  return (
    <svg
      viewBox={name === 'diamond' ? '0 0 12 12' : '0 0 24 24'}
      width={resolved}
      height={resolved}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ? `shrink-0 ${className}` : 'shrink-0'}
      style={style}
      role={meaningful ? 'img' : undefined}
      aria-hidden={meaningful ? undefined : true}
      focusable="false"
    >
      {meaningful ? <title>{title}</title> : null}
      <path d={PATHS[name]} />
    </svg>
  );
}
