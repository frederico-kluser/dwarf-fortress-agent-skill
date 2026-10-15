import { Fragment, type KeyboardEvent } from 'react';

import { RunaGlyph } from '../components/rpg/RunaGlyph';
import { NAV_GROUPS, panelById, panelRegionId, type PanelId } from '../panels/registry';
import { panelMobileTabId } from './navIds';

export interface PanelNavProps {
  active: PanelId;
  onSelect: (id: PanelId) => void;
  /** Perguntas pendentes — badge em «Perguntas» (spec §5.2). */
  pendingQuestions?: number;
  /** Aprovações retidas pendentes — badge em «Controlo» (spec §5.2). */
  pendingApprovals?: number;
}

/** Contador de pendentes (spec §5.2): nº em mono 12px, fill `--warning` + `--on-solid`. */
export function PendingCountBadge({ count }: { count: number }) {
  return (
    <span
      aria-hidden="true"
      className="ml-1 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-sm bg-warning px-1.5 font-mono text-[0.75rem] font-medium leading-none tabular-nums text-on-solid"
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

/** Overline Cinzel de grupo (§5.2) com o diamante runa — ornamento nível 1 (§4.5). */
function RotuloGrupo({ label }: { label: string }) {
  return (
    <span
      aria-hidden="true"
      className="flex shrink-0 select-none items-center gap-1.5 self-center px-2 font-display text-overline text-muted-foreground"
    >
      <RunaGlyph name="diamond" className="text-[var(--accent)]" />
      {label}
    </span>
  );
}

/**
 * Navegação primária em ecrãs estreitos (F.1.1.04): a tira de separadores
 * mantém-se ÚNICA abaixo de `md`, mas agrupada pelos NAV_GROUPS
 * («Supervisão · Agente · Memória») com overline Cinzel — nunca a lista plana
 * de 9 itens idênticos. Item ativo = texto `--foreground` + sublinhado 2px
 * `--accent` + glifo 20px; inativo `--muted`; hover `snap` (§5.2).
 *
 * Tab↔painel ligados por `id`/`aria-controls`/`aria-labelledby`; alvos ≥ 44px.
 */
export default function PanelTabs({
  active,
  onSelect,
  pendingQuestions = 0,
  pendingApprovals = 0,
}: PanelNavProps) {
  /** Roving tabindex + setas/Home/End — paridade de teclado com o Tabs MUI. */
  const handleTabListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const tabs = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0) return;
    let next = -1;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    if (next >= 0) {
      event.preventDefault();
      tabs[next]?.focus();
    }
  };

  return (
    <div className="border-b border-border bg-card shadow-[inset_0_1px_0_var(--carve-top)] md:hidden">
      <div
        role="tablist"
        aria-label="Painéis disponíveis"
        onKeyDown={handleTabListKeyDown}
        className="flex items-stretch gap-1 overflow-x-auto px-2"
      >
        {NAV_GROUPS.map((group) => (
          <Fragment key={group.id}>
            <RotuloGrupo label={group.label} />
            {group.panels.map((id) => {
              const panel = panelById(id);
              const selected = id === active;
              const pending =
                id === 'perguntas' ? pendingQuestions : id === 'controlo' ? pendingApprovals : 0;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  id={panelMobileTabId(id)}
                  aria-controls={panelRegionId(id)}
                  aria-selected={selected}
                  tabIndex={selected ? 0 : -1}
                  aria-label={pending > 0 ? `${panel.label} (${pending} pendentes)` : panel.label}
                  onClick={() => onSelect(id)}
                  className={
                    selected
                      ? 'flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border-b-2 border-[var(--accent)] px-3 text-small text-foreground transition hover:-translate-y-px'
                      : 'flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border-b-2 border-transparent px-3 text-small text-muted-foreground transition hover:-translate-y-px hover:bg-surface-raised'
                  }
                >
                  <span aria-hidden="true" className="shrink-0">
                    {panel.icon}
                  </span>
                  <span>{panel.label}</span>
                  {pending > 0 ? <PendingCountBadge count={pending} /> : null}
                </button>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

/**
 * Acesso secundário para `md`+ (auditoria F.2.2.03): NÃO é uma segunda lista de
 * destinos — são os dois atalhos de trabalho humano («Perguntas» e «Controlo»,
 * os do spec §5.2 com badge de pendentes). A navegação completa e agrupada é o
 * `NavDrawer`; a duplicação de 9 controlos em 9 destinos ficou resolvida.
 */
export function NavQuickAccess({
  active,
  onSelect,
  pendingQuestions = 0,
  pendingApprovals = 0,
}: PanelNavProps) {
  const quickPanels: PanelId[] = ['perguntas', 'controlo'];

  return (
    <nav
      aria-label="Acesso rápido de navegação"
      className="hidden items-center gap-2 border-b border-border bg-card px-4 py-1.5 shadow-[inset_0_1px_0_var(--carve-top)] md:flex"
    >
      <RotuloGrupo label="Acesso rápido" />
      {quickPanels.map((id) => {
        const panel = panelById(id);
        const selected = id === active;
        const pending = id === 'perguntas' ? pendingQuestions : pendingApprovals;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            aria-current={selected ? 'page' : undefined}
            aria-label={pending > 0 ? `${panel.label} (${pending} pendentes)` : panel.label}
            className={
              selected
                ? 'flex min-h-10 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border-b-2 border-[var(--accent)] px-3 text-small text-foreground transition hover:-translate-y-px'
                : 'flex min-h-10 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border-b-2 border-transparent px-3 text-small text-muted-foreground transition hover:-translate-y-px hover:bg-surface-raised'
            }
          >
            <span aria-hidden="true" className="shrink-0">
              {panel.icon}
            </span>
            <span>{panel.label}</span>
            {pending > 0 ? <PendingCountBadge count={pending} /> : null}
          </button>
        );
      })}
    </nav>
  );
}
