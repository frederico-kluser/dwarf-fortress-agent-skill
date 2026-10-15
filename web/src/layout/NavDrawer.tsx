import Drawer from '@mui/material/Drawer';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import ListSubheader from '@mui/material/ListSubheader';
import Toolbar from '@mui/material/Toolbar';

import { RunaCorda } from '../components/rpg/RunaRule';
import { RunaGlyph } from '../components/rpg/RunaGlyph';
import { NAV_GROUPS, panelById, type PanelId } from '../panels/registry';
import { PendingCountBadge } from './PanelTabs';

/** Largura da navegação lateral em desktop. */
export const DRAWER_WIDTH = 236;

export interface NavListProps {
  active: PanelId;
  onSelect: (id: PanelId) => void;
  /** Fecho do drawer temporário, quando existir. */
  onNavigate?: () => void;
  /** Contador de perguntas pendentes (badge no item «Perguntas»). */
  pendingQuestions?: number;
  /** Contador de aprovações retidas (badge no item «Controlo»). */
  pendingApprovals?: number;
}

/**
 * Navegação agrupada pelos 9 painéis (auditoria F.2.2.03): «Supervisão»
 * primeiro, com a fila de perguntas no topo, e `aria-current="page"` no painel
 * ativo (auditoria H.1.1.01). Spec §5.2: grupos com overline Cinzel + diamante
 * runa, item ativo = texto `--foreground` + sublinhado 2px `--accent` + glifo
 * 20px, inativo `--muted`, hover `snap` (translateY −1px + `--surface-raised`),
 * badge de pendentes em «Perguntas» e «Controlo» (fill `--warning` + `--on-solid`).
 * Alvos ≥ 40px (auditoria I.2.2.02).
 */
export function NavList({
  active,
  onSelect,
  onNavigate,
  pendingQuestions = 0,
  pendingApprovals = 0,
}: NavListProps) {
  return (
    <nav aria-label="Painéis" style={{ padding: '0 8px' }}>
      {NAV_GROUPS.map((group, groupIndex) => (
        <div key={group.id}>
          {/* «Corda» (§4.4) a separar os grupos — meta-informação, sem moldura dupla. */}
          {groupIndex > 0 ? <RunaCorda className="mx-3 my-2" /> : null}
          <List
            component="ul"
            dense
            disablePadding
            aria-label={group.label}
            subheader={
              <ListSubheader component="div" disableSticky sx={{ bgcolor: 'transparent', pt: 1 }}>
                <span className="flex items-center gap-1.5 font-display text-overline text-muted-foreground">
                  <RunaGlyph name="diamond" className="text-[var(--accent)]" />
                  {group.label}
                </span>
              </ListSubheader>
            }
          >
            {group.panels.map((panelId) => {
              const panel = panelById(panelId);
              const selected = panelId === active;
              const badge =
                panelId === 'perguntas'
                  ? pendingQuestions
                  : panelId === 'controlo'
                    ? pendingApprovals
                    : 0;
              const badgeHint =
                panelId === 'perguntas'
                  ? `${badge} à espera de resposta`
                  : `${badge} aprovações por rever`;
              return (
                <ListItem key={panel.id} disablePadding>
                  <ListItemButton
                    selected={selected}
                    aria-current={selected ? 'page' : undefined}
                    onClick={() => {
                      onSelect(panel.id);
                      onNavigate?.();
                    }}
                    sx={{
                      minHeight: 40,
                      borderRadius: 'var(--radius-md)',
                      color: selected ? 'var(--foreground)' : 'var(--muted)',
                      boxShadow: selected ? 'inset 0 -2px 0 var(--accent)' : 'none',
                      transition: (theme) =>
                        theme.transitions.create(['transform', 'background-color'], {
                          duration: 150,
                        }),
                      '&:hover': {
                        transform: 'translateY(-1px)',
                        backgroundColor: 'var(--surface-raised)',
                      },
                      '&.Mui-selected, &.Mui-selected:hover': {
                        backgroundColor: 'var(--surface-raised)',
                      },
                    }}
                  >
                    <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>{panel.icon}</ListItemIcon>
                    <ListItemText
                      primary={
                        <span className="flex items-center gap-1">
                          {panel.label}
                          {badge > 0 ? <PendingCountBadge count={badge} /> : null}
                        </span>
                      }
                      secondary={badge > 0 ? badgeHint : undefined}
                      slotProps={{
                        secondary: {
                          variant: 'caption',
                          sx: { color: 'var(--warning)', fontWeight: 600 },
                        },
                      }}
                    />
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
        </div>
      ))}
    </nav>
  );
}

/** Navegação lateral fixa (desktop) — a única navegação completa a partir de `md`. */
export default function NavDrawer({
  active,
  onSelect,
  pendingQuestions = 0,
  pendingApprovals = 0,
}: NavListProps) {
  return (
    <Drawer
      variant="permanent"
      sx={{
        width: DRAWER_WIDTH,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: DRAWER_WIDTH,
          boxSizing: 'border-box',
          borderRight: '1px solid var(--border)',
          backgroundColor: 'var(--surface)',
          // Cantos talhados (§4.1): realce interno de topo da placa de pedra.
          boxShadow: 'var(--shadow-1), inset 0 1px 0 var(--carve-top)',
        },
      }}
      open
    >
      <Toolbar variant="dense" />
      <NavList
        active={active}
        onSelect={onSelect}
        pendingQuestions={pendingQuestions}
        pendingApprovals={pendingApprovals}
      />
    </Drawer>
  );
}
