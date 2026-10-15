/**
 * Cartão de missão/quest (docs/design-rpg-medieval.md §5.3): placa de pedra
 * `--surface` com glifo por tipo, título `--text-card`, barra de progresso 6px
 * (raio 3), estado como chip etiquetado, freshness + ações (minHeight 40) à
 * direita. `border-top: 2px solid var(--primary)` — «edge da forja» — quando a
 * tarefa está a correr. Marcas de canto runa permitidas (nível 2, §4.1);
 * detalhe abre em `sheet` — dono do painel decide quando.
 */
import type { ReactNode } from 'react';

import { RunaGlyph, type RunaGlyphName } from './RunaGlyph';

export type MissionProgressState = 'ativa' | 'concluida' | 'erro';

export interface MissionCardProps {
  /** flame (tarefa ativa) · map (aventura) · scroll (memória/narrativa). */
  glyph: Extract<RunaGlyphName, 'flame' | 'map' | 'scroll'>;
  title: string;
  /** Loop a correr → edge da forja 2px no topo (§4.1). */
  running?: boolean;
  /** Preenchimento em [0,1]. */
  progress?: number;
  progressState?: MissionProgressState;
  /** Chip de estado etiquetado (§5.10) — slot para não duplicar vocabulário. */
  status?: ReactNode;
  /** `FreshnessIndicator` (§5 invariantes) — slot do dono do painel. */
  freshness?: ReactNode;
  /** Ações alinhadas à direita (botões do tema, minHeight 40). */
  actions?: ReactNode;
  children?: ReactNode;
}

export function MissionCard({
  glyph,
  title,
  running = false,
  progress,
  progressState = 'ativa',
  status,
  freshness,
  actions,
  children,
}: MissionCardProps) {
  const share = progress == null ? null : Math.max(0, Math.min(1, progress));
  return (
    <article className="placa-pedra relative p-5" data-forja={running ? 'true' : undefined}>
      <i className="canto-runa" data-canto="tl" aria-hidden="true" />
      <i className="canto-runa" data-canto="tr" aria-hidden="true" />
      <i className="canto-runa" data-canto="bl" aria-hidden="true" />
      <i className="canto-runa" data-canto="br" aria-hidden="true" />

      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-3">
          <RunaGlyph name={glyph} size={24} style={{ color: 'var(--accent)' }} />
          <h3 className="text-card min-w-0 flex-1 font-semibold text-foreground">{title}</h3>
          {status}
        </div>

        {share != null ? (
          <div
            className="barra-missao"
            data-estado={progressState}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(share * 100)}
          >
            <i style={{ width: `${share * 100}%` }} />
          </div>
        ) : null}

        {children}

        {freshness != null || actions != null ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {freshness != null ? <div className="mr-auto min-w-0">{freshness}</div> : null}
            {actions}
          </div>
        ) : null}
      </div>
    </article>
  );
}
