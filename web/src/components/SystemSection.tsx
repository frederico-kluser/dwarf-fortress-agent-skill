import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import PlayArrow from '@mui/icons-material/PlayArrow';
import Stop from '@mui/icons-material/Stop';

import { errorMessage } from '../api/events';
import { useApiContext, useGameAction, useSystemStatus } from '../api/queries';
import type { SystemStatus } from '../api/types';
import { modeLabel } from '../hooks/useAutonomyControl';
import { PanelError, PanelLoading } from './PanelState';
import SectionCard from './SectionCard';

/** Chip de estado com cor coerente (semântica ligada/desligado). */
function StatusChip({
  label,
  value,
  on,
}: {
  label: string;
  value: string;
  on: boolean;
}) {
  return (
    <Chip
      size="small"
      variant="outlined"
      color={on ? 'success' : 'default'}
      aria-label={`${label}: ${value}`}
      label={
        <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
          <Typography variant="caption" sx={{ opacity: 0.75 }}>
            {label}
          </Typography>
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {value}
          </Typography>
        </Stack>
      }
    />
  );
}

/** Rótulo do loop por campanha (id curto + modo + a correr/parado). */
function loopChips(status: SystemStatus, names: Map<string, string>) {
  return status.loop.campaigns.map((entry) => (
    <Chip
      key={entry.id}
      size="small"
      variant="outlined"
      color={entry.running ? 'success' : 'default'}
      aria-label={`Loop da campanha ${names.get(entry.id) ?? entry.id}: ${modeLabel(entry.mode)}, ${
        entry.running ? 'a correr' : 'parado'
      }`}
      label={
        <Typography variant="caption">
          {names.get(entry.id) ?? entry.id.slice(0, 8)} · {modeLabel(entry.mode)} ·{' '}
          {entry.running ? 'a correr' : 'parado'}
        </Typography>
      }
    />
  ));
}

/**
 * Secção «Sistema» do painel de controlo: estado global (jogo, RPC do DFHack,
 * frontend, loop por campanha) e arranque/paragem do processo do jogo. A paragem
 * é destrutiva → sempre com confirmação; o arranque quando já está a correr
 * responde `already_running` (nunca lança outro processo).
 */
export default function SystemSection() {
  const { campaigns } = useApiContext();
  const systemQuery = useSystemStatus();
  const [confirmStop, setConfirmStop] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const gameMutation = useGameAction({
    onSuccess: (result) =>
      setFeedback({
        tone: 'success',
        text:
          result.action === 'start'
            ? result.already_running
              ? 'O jogo já estava a correr — nenhum processo novo foi lançado.'
              : `Jogo ligado${typeof result.pid === 'number' ? ` (PID ${result.pid})` : ''}.`
            : 'Jogo desligado — o escalonamento terminou o grupo de processos.',
      }),
    onError: (error) => setFeedback({ tone: 'error', text: `Falha no jogo: ${errorMessage(error)}` }),
  });

  const status = systemQuery.data;
  const game = status?.game;
  const names = new Map(campaigns.map((campaign) => [campaign.id, campaign.name]));

  return (
    <SectionCard
      title="Sistema"
      subtitle="Estado global: processo do jogo, RPC do DFHack, interface web e loop de decisão por campanha"
      action={
        // Sem estado conhecido (a carregar/erro) não há ação segura (achado 8).
        systemQuery.isLoading || systemQuery.isError ? (
          <Button size="small" variant="outlined" disabled>
            {systemQuery.isLoading ? 'A carregar estado…' : 'Estado indisponível'}
          </Button>
        ) : game?.running ? (
          <Button
            size="small"
            color="error"
            variant="outlined"
            startIcon={<Stop />}
            loading={gameMutation.isPending}
            loadingPosition="start"
            onClick={() => setConfirmStop(true)}
          >
            Desligar o jogo
          </Button>
        ) : (
          <Button
            size="small"
            color="success"
            variant="contained"
            startIcon={<PlayArrow />}
            loading={gameMutation.isPending}
            loadingPosition="start"
            onClick={() => gameMutation.mutate({ action: 'start' })}
          >
            Ligar o jogo
          </Button>
        )
      }
    >
      <Stack spacing={1.5}>
        {systemQuery.isLoading ? <PanelLoading label="A carregar o estado do sistema…" /> : null}
        {systemQuery.isError ? (
          <PanelError
            message={errorMessage(systemQuery.error)}
            onRetry={() => void systemQuery.refetch()}
          />
        ) : null}

        {feedback ? (
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        ) : null}

        {status ? (
          <>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }} role="group" aria-label="Estado do sistema">
              <StatusChip
                label="jogo"
                value={game?.running ? `a correr${game.pid ? ` · PID ${game.pid}` : ''}` : 'parado'}
                on={game?.running ?? false}
              />
              <StatusChip
                label="RPC"
                value={game?.rpc ? 'ligado' : 'desligado'}
                on={game?.rpc ?? false}
              />
              <StatusChip
                label="interface web"
                value={
                  status.frontend.running ? `a correr · porta ${status.frontend.port}` : 'parado'
                }
                on={status.frontend.running}
              />
              {loopChips(status, names)}
            </Stack>

            {!game?.running ? (
              <Alert severity="warning" role="status">
                O jogo está desligado: as tarefas de aventura e o loop de decisão ficam à espera
                até voltares a ligá-lo.
              </Alert>
            ) : null}

            <Box>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Servidor {status.backend.version} · jogo em <code>{game?.path}</code> · leitura de
                GET /api/system/status. «Desligar o jogo» termina o grupo de processos (SIGTERM e,
                se preciso, SIGKILL ao fim de 10 s) — a memória e as métricas ficam intactas.
              </Typography>
            </Box>
          </>
        ) : null}
      </Stack>

      <Dialog
        open={confirmStop}
        onClose={() => setConfirmStop(false)}
        maxWidth="xs"
        aria-labelledby="desligar-jogo-titulo"
        aria-describedby="desligar-jogo-descricao"
      >
        <DialogTitle id="desligar-jogo-titulo">Desligar o jogo?</DialogTitle>
        <DialogContent>
          <Typography id="desligar-jogo-descricao" variant="body2">
            O processo do jogo recebe SIGTERM e, se não responder em 10 segundos, SIGKILL. As
            decisões em curso são interrompidas; a memória da campanha, as métricas e os jogos guardados não
            são alterados.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmStop(false)}>Cancelar</Button>
          <Button
            color="error"
            variant="contained"
            startIcon={<Stop />}
            loading={gameMutation.isPending}
            loadingPosition="start"
            onClick={() => {
              setConfirmStop(false);
              gameMutation.mutate({ action: 'stop' });
            }}
          >
            Desligar o jogo
          </Button>
        </DialogActions>
      </Dialog>
    </SectionCard>
  );
}
