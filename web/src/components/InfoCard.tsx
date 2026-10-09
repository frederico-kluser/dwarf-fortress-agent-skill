import type { ReactNode } from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

export interface InfoCardProps {
  title: string;
  /** Nome do grupo no contrato (`docs/api.md`) — mostrado como legenda mono. */
  contractKey?: string;
  icon?: ReactNode;
  /** Cor CSS do ícone/realce. */
  color?: string;
  action?: ReactNode;
  children: ReactNode;
}

/** Cartão de grupo de métricas: título PT + chave do contrato + conteúdo livre. */
export default function InfoCard({
  title,
  contractKey,
  icon,
  color = 'primary.main',
  action,
  children,
}: InfoCardProps) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent sx={{ p: 1.75, '&:last-child': { pb: 1.75 } }}>
        <Stack
          direction="row"
          sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}
        >
          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: 'center', color }}
          >
            {icon}
            <Stack>
              <Typography variant="h5" component="h3">
                {title}
              </Typography>
              {contractKey ? (
                <Tooltip title={`Grupo «${contractKey}» de GET /api/campaigns/{id}/metrics`}>
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary', fontFamily: 'monospace' }}
                  >
                    {contractKey}
                  </Typography>
                </Tooltip>
              ) : null}
            </Stack>
          </Stack>
          {action}
        </Stack>
        {children}
      </CardContent>
    </Card>
  );
}
