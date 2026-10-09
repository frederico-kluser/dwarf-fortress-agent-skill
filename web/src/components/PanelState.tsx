import type { ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import InboxOutlined from '@mui/icons-material/InboxOutlined';

/** Estado de carregamento anunciado a leitores de ecrã (role=status). */
export function PanelLoading({ label = 'A carregar…' }: { label?: string }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      role="status"
      sx={{ alignItems: "center", py: 4, justifyContent: 'center', color: 'text.secondary' }}
    >
      <CircularProgress size={18} aria-hidden="true" />
      <Typography variant="body2">{label}</Typography>
    </Stack>
  );
}

/** Falha de leitura com ação de repetição (o backend pode estar desligado). */
export function PanelError({
  title = 'Não foi possível carregar',
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert
      severity="error"
      role="alert"
      action={
        onRetry ? (
          <Button color="inherit" size="small" onClick={onRetry}>
            Tentar de novo
          </Button>
        ) : undefined
      }
    >
      <AlertTitle>{title}</AlertTitle>
      {message}
    </Alert>
  );
}

/** Lista vazia — diz o que falta, não só que está vazio. */
export function PanelEmpty({ message, hint }: { message: string; hint?: string }) {
  return (
    <Stack sx={{ alignItems: 'center', py: 4, color: 'text.secondary' }} spacing={0.5}>
      <InboxOutlined aria-hidden="true" />
      <Typography variant="body2">{message}</Typography>
      {hint ? (
        <Typography variant="caption" sx={{ textAlign: 'center' }}>
          {hint}
        </Typography>
      ) : null}
    </Stack>
  );
}

/** Aviso compacto reutilizável (ex.: mock ativo, contrato sem campo). */
export function PanelNotice({
  severity = 'info',
  children,
}: {
  severity?: 'info' | 'warning' | 'success';
  children: ReactNode;
}) {
  return (
    <Alert severity={severity} role="status">
      <Box sx={{ fontSize: '0.8125rem' }}>{children}</Box>
    </Alert>
  );
}
