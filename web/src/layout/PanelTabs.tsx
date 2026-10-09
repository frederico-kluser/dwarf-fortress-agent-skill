import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import Box from '@mui/material/Box';

import { PANELS, panelRegionId, panelTabId, type PanelId } from '../panels/registry';

/**
 * Navegação por separadores em ecrãs estreitos (o drawer fica para desktop).
 * Tab↔painel ligados por `id`/`aria-controls`/`aria-labelledby`.
 */
export default function PanelTabs({
  active,
  onSelect,
}: {
  active: PanelId;
  onSelect: (id: PanelId) => void;
}) {
  return (
    <Box
      sx={{
        display: { xs: 'block', md: 'none' },
        borderBottom: '1px solid var(--mui-palette-divider)',
        backgroundColor: 'var(--mui-palette-background-paper)',
      }}
    >
      <Tabs
        value={active}
        onChange={(_event, value: PanelId) => onSelect(value)}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        aria-label="Painéis disponíveis"
      >
        {PANELS.map((panel) => (
          <Tab
            key={panel.id}
            value={panel.id}
            id={panelTabId(panel.id)}
            aria-controls={panelRegionId(panel.id)}
            label={panel.label}
            icon={panel.icon}
            iconPosition="start"
          />
        ))}
      </Tabs>
    </Box>
  );
}
