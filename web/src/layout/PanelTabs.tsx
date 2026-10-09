import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import Badge from '@mui/material/Badge';
import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';

import { panelsInNavOrder, panelRegionId, panelTabId, type PanelId } from '../panels/registry';

/**
 * Navegação em ecrãs estreitos. Abaixo de `md` é a ÚNICA navegação (auditoria
 * F.2.2.03: antes havia drawer temporário + 8 separadores com os mesmos itens).
 * De `md` para cima os separadores continuam a existir para alcançar «Perguntas»
 * num clique, mas sem rótulo visível — a navegação por teclado só toca no
 * `tablist` ativo (o inativo fica `visibility: hidden`, logo fora da ordem de foco).
 *
 * Tab↔painel ligados por `id`/`aria-controls`/`aria-labelledby`.
 */
export default function PanelTabs({
  active,
  onSelect,
}: {
  active: PanelId;
  onSelect: (id: PanelId) => void;
}) {
  const panels = panelsInNavOrder();

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
        {panels.map((panel) => (
          <Tab
            key={panel.id}
            value={panel.id}
            id={panelTabId(panel.id)}
            aria-controls={panelRegionId(panel.id)}
            label={panel.label}
            icon={panel.icon}
            iconPosition="start"
            sx={{ minHeight: 44, textTransform: 'none' }}
          />
        ))}
      </Tabs>
    </Box>
  );
}

/**
 * Barra de separadores só-ícone para `md`+: mantém a fila de perguntas a um clique
 * sem duplicar a lista completa de alternativas da navegação lateral.
 */
export function PanelIconTabs({
  active,
  onSelect,
  pendingQuestions = 0,
}: {
  active: PanelId;
  onSelect: (id: PanelId) => void;
  pendingQuestions?: number;
}) {
  const panels = panelsInNavOrder();

  return (
    <Box
      sx={{
        display: { xs: 'none', md: 'block' },
        borderBottom: '1px solid var(--mui-palette-divider)',
        backgroundColor: 'var(--mui-palette-background-paper)',
      }}
    >
      <Tabs
        value={active}
        onChange={(_event, value: PanelId) => onSelect(value)}
        variant="scrollable"
        scrollButtons={false}
        aria-label="Acesso rápido aos painéis"
        sx={{ minHeight: 40 }}
      >
        {panels.map((panel) => (
          <Tooltip key={panel.id} title={panel.label}>
            <Tab
              value={panel.id}
              id={panelTabId(panel.id)}
              aria-controls={panelRegionId(panel.id)}
              aria-label={
                panel.id === 'perguntas' && pendingQuestions > 0
                  ? `${panel.label} (${pendingQuestions} pendentes)`
                  : panel.label
              }
              icon={
                panel.id === 'perguntas' && pendingQuestions > 0 ? (
                  <Badge badgeContent={pendingQuestions} color="warning" max={99}>
                    {panel.icon}
                  </Badge>
                ) : (
                  panel.icon
                )
              }
              sx={{ minWidth: 52, minHeight: 40, px: 1.5 }}
            />
          </Tooltip>
        ))}
      </Tabs>
    </Box>
  );
}
