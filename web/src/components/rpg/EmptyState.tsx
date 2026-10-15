/**
 * Estado vazio «A masmorra ainda não foi explorada» (docs/design-rpg-medieval.md
 * §5.7): glifo 40px `--muted`, título Cinzel `--text-section`, corpo `--text-body`
 * que explica **o que falta e porquê**, e exatamente 1 CTA concreta. Zero
 * animação decorativa. Cada painel nomeia o objeto real na mensagem.
 */
import Button from '@mui/material/Button';

import { RunaGlyph, type RunaGlyphName } from './RunaGlyph';

export interface EmptyStateProps {
  glyph?: RunaGlyphName;
  /** Por omissão: «A masmorra ainda não foi explorada». */
  title?: string;
  /** O que falta e porquê (padrão auditado) — máx. 72ch (`leitura`). */
  body: string;
  /** Única CTA concreta (ex.: «Criar aventura» → wizard). */
  action?: { label: string; onClick: () => void };
}

export function EmptyState({
  glyph = 'map',
  title = 'A masmorra ainda não foi explorada',
  body,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <RunaGlyph name={glyph} size={40} className="text-muted-foreground" />
      <h2 className="text-section font-display font-semibold text-foreground">{title}</h2>
      <p className="leitura text-body text-pretty text-muted-foreground">{body}</p>
      {action ? (
        <Button variant="contained" color="primary" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}
