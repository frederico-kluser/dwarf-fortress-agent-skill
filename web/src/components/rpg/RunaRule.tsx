/**
 * Régua divisória runa (docs/design-rpg-medieval.md §4.4) — só entre secções
 * dentro de um painel, máx. 1 por secão. O núcleo é o glifo `diamond` (12×12).
 * Variante «corda» (`RunaCorda`) para linhas de meta-informação.
 */
import { RunaGlyph } from './RunaGlyph';

export function RunaRule() {
  return (
    <div className="runa-rule" aria-hidden="true">
      <RunaGlyph name="diamond" />
    </div>
  );
}

/** Alternativa autorizada §4.4: «corda» — separa meta-informação. */
export function RunaCorda({ className }: { className?: string }) {
  return (
    <div
      className={className ? `runa-corda ${className}` : 'runa-corda'}
      aria-hidden="true"
    />
  );
}
