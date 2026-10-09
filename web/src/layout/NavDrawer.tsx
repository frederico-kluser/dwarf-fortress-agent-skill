import Drawer from '@mui/material/Drawer';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import ListSubheader from '@mui/material/ListSubheader';
import Toolbar from '@mui/material/Toolbar';
import Typography from '@mui/material/Typography';

import { NAV_GROUPS, panelById, type PanelId } from '../panels/registry';

/** Largura da navegação lateral em desktop. */
export const DRAWER_WIDTH = 236;

export interface NavListProps {
  active: PanelId;
  onSelect: (id: PanelId) => void;
  /** Fecho do drawer temporário, quando existir. */
  onNavigate?: () => void;
  /** Contador de perguntas pendentes (mostrado no item «Perguntas»). */
  pendingQuestions?: number;
}

/**
 * Navegação agrupada pelos 8 painéis (auditoria F.2.2.03): «Supervisão» primeiro,
 * com a fila de perguntas no topo, e `aria-current="page"` no painel ativo
 * (auditoria H.1.1.01 — o comentário prometia-o e o código não o tinha).
 */
export function NavList({ active, onSelect, onNavigate, pendingQuestions = 0 }: NavListProps) {
  return (
    <nav aria-label="Painéis" style={{ padding: '0 8px' }}>
      {NAV_GROUPS.map((group) => (
        <List
          key={group.id}
          component="ul"
          dense
          disablePadding
          aria-label={group.label}
          subheader={
            <ListSubheader component="div" disableSticky sx={{ bgcolor: 'transparent' }}>
              <Typography variant="overline" component="span" sx={{ color: 'text.secondary' }}>
                {group.label}
              </Typography>
            </ListSubheader>
          }
        >
          {group.panels.map((panelId) => {
            const panel = panelById(panelId);
            const selected = panelId === active;
            const badge = panelId === 'perguntas' && pendingQuestions > 0 ? pendingQuestions : 0;
            return (
              <ListItem key={panel.id} disablePadding>
                <ListItemButton
                  selected={selected}
                  aria-current={selected ? 'page' : undefined}
                  onClick={() => {
                    onSelect(panel.id);
                    onNavigate?.();
                  }}
                >
                  <ListItemIcon>{panel.icon}</ListItemIcon>
                  <ListItemText
                    primary={panel.label}
                    secondary={badge > 0 ? `${badge} à espera de resposta` : undefined}
                    slotProps={{
                      secondary: {
                        variant: 'caption',
                        sx: { color: 'warning.main', fontWeight: 600 },
                      },
                    }}
                  />
                </ListItemButton>
              </ListItem>
            );
          })}
        </List>
      ))}
    </nav>
  );
}

/** Navegação lateral fixa (desktop) — a única navegação a partir de `md`. */
export default function NavDrawer({
  active,
  onSelect,
  pendingQuestions = 0,
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
          borderRight: '1px solid var(--mui-palette-divider)',
          backgroundColor: 'var(--mui-palette-background-default)',
        },
      }}
      open
    >
      <Toolbar variant="dense" />
      <NavList active={active} onSelect={onSelect} pendingQuestions={pendingQuestions} />
    </Drawer>
  );
}
