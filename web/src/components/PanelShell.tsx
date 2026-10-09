import type { ReactNode } from 'react';
import Divider from '@mui/material/Divider';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

export interface PanelShellProps {
  /** Título do painel — é o `h1` da página. */
  title: string;
  description: string;
  /** Ações/filtros à direita do título. */
  actions?: ReactNode;
  children: ReactNode;
}

/** Moldura comum dos 8 painéis: título, descrição e o conteúdo. */
export default function PanelShell({ title, description, actions, children }: PanelShellProps) {
  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={1.5}
        sx={{ alignItems: { xs: 'flex-start', md: 'center' }, justifyContent: 'space-between' }}
      >
        <Stack spacing={0.25}>
          <Typography variant="h1" component="h1">
            {title}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {description}
          </Typography>
        </Stack>
        {actions ? (
          <Stack
            direction="row"
            spacing={1}
            useFlexGap
            sx={{ alignItems: 'center', flexWrap: 'wrap' }}
          >
            {actions}
          </Stack>
        ) : null}
      </Stack>
      <Divider />
      {children}
    </Stack>
  );
}
