/**
 * Badge de banda de ação (docs/design-rpg-medieval.md §5.10): `auto` →
 * `--success`, `hitl` → `--warning`, `abstain` → `--danger`, `stub`/«dados
 * simulados» → `--info`. Contraste AA (§2.3 regra 3): etiqueta + confiança em
 * `--foreground` sobre a tinta 15% do status; o sinal de cor vive na tinta e no
 * glifo `--badge-status` (≥3:1, objeto não-texto). A cor nunca é o único sinal:
 * glifo + etiqueta PT sempre (§2.3 regra 1), tooltip com os limiares. Variante
 * sólida apenas para risco extremo (kill-switch armado, erro de loop).
 */
import Tooltip from '@mui/material/Tooltip';

import { BAND_DESCRIPTION, BAND_LABEL } from '../../api/format';
import { RunaGlyph, type RunaGlyphName } from './RunaGlyph';

export type BadgeBanda = 'auto' | 'hitl' | 'abstain' | 'stub';

/** Glifo por banda (§4.3): chama/relógio de areia/alerta/bigorna — nunca emoji. */
const BAND_GLYPH: Record<BadgeBanda, RunaGlyphName> = {
  auto: 'flame',
  hitl: 'hourglass',
  abstain: 'alert',
  stub: 'anvil',
};

const STUB_LABEL = 'dados simulados';
const STUB_DESCRIPTION = 'stub · dados simulados — valores não vêm do jogo real';

export interface BandBadgeProps {
  band: BadgeBanda;
  /** Fill sólido + `--on-solid`: só risco extremo (§5.10). */
  solid?: boolean;
  /** Confiança em mono (2 casas); `null` = «indisponível» honesto (§5 invariantes). */
  confidence?: number | null;
}

export function BandBadge({ band, solid = false, confidence }: BandBadgeProps) {
  const label = band === 'stub' ? STUB_LABEL : (BAND_LABEL[band] ?? band);
  const description =
    band === 'stub' ? STUB_DESCRIPTION : (BAND_DESCRIPTION[band] ?? band);
  return (
    <Tooltip title={description}>
      <span className="badge-banda" data-banda={band} data-solida={solid ? 'true' : undefined}>
        <RunaGlyph name={BAND_GLYPH[band]} size={14} />
        <span>{label}</span>
        {confidence !== undefined ? (
          <span className="font-mono tabular-nums">
            {confidence === null ? 'indisponível' : confidence.toFixed(2)}
          </span>
        ) : null}
      </span>
    </Tooltip>
  );
}
