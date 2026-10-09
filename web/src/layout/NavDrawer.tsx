import Drawer from '@mui/material/Drawer';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import ListSubheader from '@mui/material/ListSubheader';
import Toolbar from '@mui/material/Toolbar';

import { PANELS, type PanelId } from '../panels/registry';

/** Largura da navegação lateral em desktop. */
export const DRAWER_WIDTH = 236;

export interface NavListProps {
  active: PanelId;
  onSelect: (id: PanelId) => void;
  /** Fecha o drawer temporário em mobile. */
  onNavigate?: () => void;
}

/** Lista de navegação pelos 8 painéis (a11y: `nav` + `aria-current` via seleção). */
export function NavList({ active, onSelect, onNavigate }: NavListProps) {
  return (
    <List
      component="nav"
      dense
      aria-label="Painéis"
      subheader={
        <ListSubheader component="div" disableSticky sx={{ bgcolor: 'transparent' }}>
          Painéis
        </ListSubheader>
      }
      sx={{ px: 1 }}
    >
      {PANELS.map((panel) => (
        <ListItem key={panel.id} disablePadding>
          <ListItemButton
            selected={panel.id === active}
            onClick={() => {
              onSelect(panel.id);
              onNavigate?.();
            }}
          >
            <ListItemIcon>{panel.icon}</ListItemIcon>
            <ListItemText primary={panel.label} />
          </ListItemButton>
        </ListItem>
      ))}
    </List>
  );
}

/** Navegação lateral fixa (desktop). */
export default function NavDrawer({ active, onSelect }: NavListProps) {
  return (
    <Drawer
      variant="permanent"
      sx={{
        display: { xs: 'none', md: 'block' },
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
      <NavList active={active} onSelect={onSelect} />
    </Drawer>
  );
}

/** Navegação lateral temporária (mobile). */
export function MobileNavDrawer({
  open,
  onClose,
  active,
  onSelect,
}: NavListProps & { open: boolean; onClose: () => void }) {
  return (
    <Drawer
      variant="temporary"
      anchor="left"
      open={open}
      onClose={onClose}
      slotProps={{ paper: { sx: { width: DRAWER_WIDTH } } }}
    >
      <Toolbar variant="dense" />
      <NavList active={active} onSelect={onSelect} onNavigate={onClose} />
    </Drawer>
  );
}
