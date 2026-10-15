/**
 * Pele «RPG medieval» das superfícies de Aventura · Objetivo · Memória ·
 * Inputs (perímetro exclusivo destes painéis) — composições locais sobre os
 * componentes partilhados `web/src/components/rpg/` e as classes de ornamento
 * de `web/src/index.css` (`placa-pedra`, `canto-runa`, `runa-rule`, …).
 *
 * Só fica aqui o que é destes painéis e não existe no kit partilhado:
 * - `TOKEN`/`runaType` — tokens e escala tipográfica do spec (§2–§3) para os
 *   elementos MUI, que não herdam as utilities Tailwind do kit;
 * - `RunaSection` — a placa de secção com glifo + título Cinzel (§4.5 nível 1),
 *   já que o `SectionCard` partilhado é liso e tem outros donos;
 * - `RunaLoading` — label PT visível + `RpgSkeleton` (§5.8);
 * - `RunaHover`/`useHoverLift` — hover de cartão com o token `snap` (§6).
 *
 * Zero cores cravadas: tudo resolve `var(--token)` do tema. Ornamento pelo
 * orçamento §4.5 (0 dados · 1 moldura de secção · 2 hero/missão).
 */

import { motion } from 'motion/react';
import type { CSSProperties, ReactNode } from 'react';

import {
  useMotionUITheme,
  useMotionUITransition,
} from '@/components/motion-ui/ui-theme';
import { RunaGlyph, RpgSkeleton, StoneTexture, type RunaGlyphName } from './rpg';

// ---------------------------------------------------------------------------
// Tokens semânticos (spec §2) — nunca cores literais.
// ---------------------------------------------------------------------------

/** Tokens canónicos do tema (spec §2.1/§2.2). */
export const TOKEN = {
  background: 'var(--background)',
  surface: 'var(--surface)',
  surfaceRaised: 'var(--surface-raised)',
  border: 'var(--border)',
  borderStrong: 'var(--border-strong)',
  foreground: 'var(--foreground)',
  mutedForeground: 'var(--muted)',
  /** Brasa da forja. */
  primary: 'var(--primary)',
  /** Dourado das runas. */
  accent: 'var(--accent)',
  danger: 'var(--danger)',
  success: 'var(--success)',
  warning: 'var(--warning)',
  /** Texto sobre qualquer fill sólido (§2.3, regra 2). */
  onSolid: 'var(--on-solid)',
  shadow1: 'var(--shadow-1)',
} as const;

// ---------------------------------------------------------------------------
// Tipografia (spec §3.2) — escala canónica para os componentes MUI.
// ---------------------------------------------------------------------------

const FONT_DISPLAY =
  'var(--font-display, "Cinzel", "Times New Roman", Georgia, serif)';
const FONT_TEXT =
  'var(--font-sans, "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif)';
const FONT_MONO =
  'var(--font-mono, "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace)';

export type RunaTextScale =
  | 'hero'
  | 'title'
  | 'section'
  | 'card'
  | 'body'
  | 'small'
  | 'micro'
  | 'overline'
  | 'data'
  | 'counter';

/** Escala tipográfica canónica do spec (§3.2). Cinzel nunca em corpo/dados. */
// eslint-disable-next-line react/only-export-components -- helper de estilo do kit
export function runaType(scale: RunaTextScale): CSSProperties {
  switch (scale) {
    case 'hero':
      return {
        fontFamily: FONT_DISPLAY,
        fontSize: 'var(--text-hero, clamp(2.25rem, 4.5vw, 3.25rem))',
        fontWeight: 700,
        lineHeight: 1.05,
        letterSpacing: '0.01em',
      };
    case 'title':
      return {
        fontFamily: FONT_DISPLAY,
        fontSize: 'var(--text-title, 1.75rem)',
        fontWeight: 600,
        lineHeight: 1.15,
        letterSpacing: '0.02em',
      };
    case 'section':
      return {
        fontFamily: FONT_DISPLAY,
        fontSize: 'var(--text-section, 1.125rem)',
        fontWeight: 600,
        lineHeight: 1.3,
        letterSpacing: '0.04em',
      };
    case 'card':
      return {
        fontFamily: FONT_TEXT,
        fontSize: 'var(--text-card, 1rem)',
        fontWeight: 600,
        lineHeight: 1.35,
      };
    case 'body':
      return {
        fontFamily: FONT_TEXT,
        fontSize: 'var(--text-body, 0.9375rem)',
        fontWeight: 400,
        lineHeight: 1.55,
      };
    case 'small':
      return {
        fontFamily: FONT_TEXT,
        fontSize: 'var(--text-small, 0.875rem)',
        fontWeight: 400,
        lineHeight: 1.5,
      };
    case 'micro':
      return {
        fontFamily: FONT_TEXT,
        fontSize: 'var(--text-micro, 0.75rem)',
        fontWeight: 500,
        lineHeight: 1.4,
        letterSpacing: '0.02em',
      };
    case 'overline':
      return {
        fontFamily: FONT_DISPLAY,
        fontSize: 'var(--text-overline, 0.75rem)',
        fontWeight: 700,
        lineHeight: 1.2,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
      };
    case 'data':
      return {
        fontFamily: FONT_MONO,
        fontSize: 'var(--text-data, 0.8125rem)',
        fontWeight: 400,
        lineHeight: 1.4,
        fontVariantNumeric: 'tabular-nums',
      };
    case 'counter':
      return {
        fontFamily: FONT_MONO,
        fontSize: 'var(--text-counter, 1.5rem)',
        fontWeight: 500,
        lineHeight: 1.1,
        letterSpacing: '-0.01em',
        fontVariantNumeric: 'tabular-nums',
      };
  }
}

// ---------------------------------------------------------------------------
// Placa de secção (§4.5 nível 1: glifo 20px + título Cinzel) — a superfície
// «pedra» das secções destes painéis (o `SectionCard` partilhado não aceita
// glifo/escala de título e tem outros donos).
// ---------------------------------------------------------------------------

export interface RunaSectionProps {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  /** Nível do heading — mantém a hierarquia sem saltos (a11y). */
  headingLevel?: 'h2' | 'h3';
  /** Sem padding no conteúdo (listas/tabelas com espaçamento próprio). */
  flush?: boolean;
  /** Glifo de secção 20px ao lado do título (§4.5, nível 1). */
  glyph?: RunaGlyphName;
  /** Título em `--text-card` (Inter) em vez de `--text-section` (Cinzel). */
  titleScale?: 'section' | 'card';
  /** Textura pergaminho só no cabeçalho (§8, `#/memoria`·`#/objetivo`·`#/inputs`). */
  texture?: boolean;
  /** Edge da forja: borda superior 2px `--primary` (missões/loops, §4.1). */
  forjaEdge?: boolean;
  /** Marcas de canto runa — só hero e cartão de missão (§4.1). */
  corners?: boolean;
}

export function RunaSection({
  title,
  subtitle,
  action,
  children,
  headingLevel = 'h2',
  flush = false,
  glyph,
  titleScale = 'section',
  texture = false,
  forjaEdge = false,
  corners = false,
}: RunaSectionProps) {
  const Heading = headingLevel;
  return (
    <section
      className="placa-pedra relative"
      data-forja={forjaEdge ? 'true' : undefined}
    >
      {corners ? (
        <>
          <i className="canto-runa" data-canto="tl" aria-hidden="true" />
          <i className="canto-runa" data-canto="tr" aria-hidden="true" />
          <i className="canto-runa" data-canto="bl" aria-hidden="true" />
          <i className="canto-runa" data-canto="br" aria-hidden="true" />
        </>
      ) : null}
      <header className="relative flex flex-wrap items-start justify-between gap-2 overflow-hidden p-5 pb-4 max-sm:p-4">
        {texture ? <StoneTexture /> : null}
        <div className="relative flex min-w-0 items-start gap-2">
          {glyph ? (
            <span style={{ color: TOKEN.accent, marginTop: 2 }}>
              <RunaGlyph name={glyph} size={20} />
            </span>
          ) : null}
          <div className="min-w-0">
            <Heading className="text-balance" style={runaType(titleScale)}>
              {title}
            </Heading>
            {subtitle ? (
              <p
                className="mt-1 text-pretty"
                style={{ ...runaType('small'), color: TOKEN.mutedForeground }}
              >
                {subtitle}
              </p>
            ) : null}
          </div>
        </div>
        {action ? (
          <div className="relative flex shrink-0 flex-wrap items-center gap-2">{action}</div>
        ) : null}
      </header>
      <div className="border-t" style={{ borderColor: TOKEN.border }}>
        <div className={flush ? '' : 'p-5 pt-4 max-sm:p-4'}>{children}</div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading (§5.8) — skeleton neutro do registry + label PT visível.
// ---------------------------------------------------------------------------

export function RunaLoading({ label }: { label: string }) {
  return (
    <div className="flex flex-col gap-2 py-2">
      <span style={{ ...runaType('small'), color: TOKEN.mutedForeground }}>{label}</span>
      <RpgSkeleton label={label} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Motion (§6) — movimento só com os tokens do registry.
// ---------------------------------------------------------------------------

/**
 * Hover de cartão (§6): `snap` + `translateY(-2px)`; sem travel em modo
 * reduzido (o conteúdo nunca depende do movimento).
 */
// eslint-disable-next-line react/only-export-components -- hook de motion do kit
export function useHoverLift() {
  const { motionMode } = useMotionUITheme();
  const snap = useMotionUITransition('snap');
  return {
    whileHover: motionMode === 'full' ? { y: -2 } : undefined,
    transition: snap,
  } as const;
}

/** Cartão com lift de hover — envolve placas/linhas clicáveis. */
export function RunaHover({
  children,
  className,
  id,
}: {
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  const hover = useHoverLift();
  return (
    <motion.div
      id={id}
      className={className}
      whileHover={hover.whileHover}
      transition={hover.transition}
    >
      {children}
    </motion.div>
  );
}
