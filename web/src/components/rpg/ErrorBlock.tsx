/**
 * Bloco de erro (docs/design-rpg-medieval.md §5.9): moldura `1px var(--danger)`
 * sobre superfície — **nunca** ecrã full-bleed vermelho — glifo `alert` 20px,
 * título `--text-card`, causa `--text-small` `--muted`, e ações «Tentar de
 * novo» + «Ver logs» (botões do tema, minHeight 40). Erros de campanha/campos
 * continuam em `Alert` MUI (decisão auditada).
 */
import Button from '@mui/material/Button';

import { RunaGlyph } from './RunaGlyph';

export interface ErrorBlockProps {
  /** Título curto do que falhou (ex.: «Não foi possível ler o estado do jogo»). */
  title: string;
  /** Causa honesta, em `--text-small` `--muted`. */
  cause?: string;
  /** «Tentar de novo» — pode ser embrulhado em `multi-state-button` pelo dono. */
  onRetry?: () => void;
  /** «Ver logs» — diagnóstico sempre alcançável por clique (§7.11). */
  onLogs?: () => void;
}

export function ErrorBlock({ title, cause, onRetry, onLogs }: ErrorBlockProps) {
  return (
    <div className="moldura-erro flex flex-col gap-3 p-5" role="alert">
      <div className="flex items-start gap-3">
        <RunaGlyph name="alert" size={20} style={{ color: 'var(--danger)' }} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h3 className="text-card font-semibold text-foreground">{title}</h3>
          {cause ? <p className="text-small text-muted-foreground">{cause}</p> : null}
        </div>
      </div>
      {onRetry != null || onLogs != null ? (
        <div className="flex flex-wrap gap-2">
          {onRetry != null ? (
            <Button variant="contained" color="primary" onClick={onRetry}>
              Tentar de novo
            </Button>
          ) : null}
          {onLogs != null ? (
            <Button variant="outlined" color="inherit" onClick={onLogs}>
              Ver logs
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
