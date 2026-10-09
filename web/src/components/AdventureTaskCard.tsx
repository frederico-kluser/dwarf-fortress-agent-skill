import { useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import Close from '@mui/icons-material/Close';
import Monitor from '@mui/icons-material/Monitor';
import Replay from '@mui/icons-material/Replay';
import Stop from '@mui/icons-material/Stop';

import type { AdventureTask } from '../api/types';
import FreshnessIndicator from './FreshnessIndicator';
import SectionCard from './SectionCard';
import { ProgressBar } from '@/components/motion-ui/progress-bar';
import { Skeleton } from '@/components/motion-ui/skeleton';

export interface AdventureTaskCardProps {
  /** Tarefa a mostrar (`null` = sem tarefa, o cartão não desenha nada). */
  task: AdventureTask | null;
  /** O que esta tarefa está a fazer (cabeçalho do cartão). */
  heading: string;
  /** Linha de contexto — ex.: o mundo ou o personagem em criação. */
  contextLine?: string;
  /** `ts` da última atualização do progresso (frescura durante esperas longas). */
  updatedAt?: string | null;
  /** Leitura de estado a decorrer (rede de segurança do WS). */
  refreshing?: boolean;
  /** Força uma leitura do `GET .../adventures/status`. */
  onRefresh?: () => void;
  /** Confirmação do cancelamento em curso (botão desativado). */
  cancelBusy?: boolean;
  /** Cancela a tarefa (já confirmado pelo diálogo do cartão). */
  onConfirmCancel: () => void;
  /** Ações quando a tarefa termina com sucesso (ex.: «Entrar em sessão»). */
  doneActions?: ReactNode;
  /** Quando a tarefa terminou em erro: recomeçar/tentar de novo. */
  onRetry?: () => void;
  /** Descartar o cartão (fechar o resultado). */
  onDismiss?: () => void;
}

/** Rótulo PT do tipo de tarefa — o contrato enumera `new_world | new_character | load`. */
const KIND_LABEL: Record<AdventureTask['kind'], string> = {
  new_world: 'Criação de mundo',
  new_character: 'Criação de personagem',
  load: 'Carregamento de aventura',
};

/** Alvo tátil das ações primárias destas superfícies (auditoria v3, achado 6). */
const TARGET_SX = { minHeight: 40 } as const;

/**
 * Progresso ao vivo de uma tarefa de aventura (`GET .../adventures/status` +
 * WS `adventure_task`): passo/total, último ecrã traduzido, frescura, cancelamento
 * com confirmação e estados finais claros (concluída · cancelada · erro).
 *
 * Anúncios a tecnologias de assistência (auditoria v3, achado 4): só transições —
 * o contador de passos é visível mas não é região live (evita dezenas de anúncios).
 */
export default function AdventureTaskCard({
  task,
  heading,
  contextLine,
  updatedAt,
  refreshing = false,
  onRefresh,
  cancelBusy = false,
  onConfirmCancel,
  doneActions,
  onRetry,
  onDismiss,
}: AdventureTaskCardProps) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  if (!task) return null;

  const running = task.state === 'running';
  const cancelled = task.state === 'error' && task.error === 'cancelled';
  const percent =
    task.steps_total > 0 ? Math.min(100, Math.round((task.step / task.steps_total) * 100)) : 0;

  return (
    <SectionCard
      title={heading}
      subtitle={`${KIND_LABEL[task.kind]}${contextLine ? ` · ${contextLine}` : ''}`}
      action={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          {running && onRefresh ? (
            <FreshnessIndicator
              ts={updatedAt}
              refreshing={refreshing}
              refreshDisabled={refreshing}
              onRefresh={onRefresh}
              refreshLabel="Atualizar o progresso agora"
            />
          ) : null}
          {running ? (
            <Button
              color="warning"
              variant="outlined"
              startIcon={<Stop />}
              disabled={cancelBusy}
              sx={TARGET_SX}
              aria-label="Cancelar a tarefa de aventura em curso"
              onClick={() => setConfirmCancel(true)}
            >
              Cancelar tarefa
            </Button>
          ) : onDismiss ? (
            <Button
              variant="outlined"
              startIcon={<Close />}
              sx={TARGET_SX}
              aria-label="Fechar o resultado da tarefa de aventura"
              onClick={onDismiss}
            >
              Fechar
            </Button>
          ) : null}
        </Stack>
      }
    >
      <Stack spacing={1.5}>
        {running ? (
          <Stack spacing={1.5}>
            {/*
              Região live só com o tipo de tarefa (muda uma vez por tarefa); o
              contador de passos abaixo é visível, nunca anunciado a cada passo.
            */}
            <Box
              role="status"
              sx={{
                position: 'absolute',
                width: 1,
                height: 1,
                p: 0,
                m: -1,
                overflow: 'hidden',
                clip: 'rect(0 0 0 0)',
                whiteSpace: 'nowrap',
                border: 0,
              }}
            >
              Tarefa de {KIND_LABEL[task.kind].toLowerCase()} em curso.
            </Box>
            {task.steps_total > 0 ? (
              <ProgressBar
                value={percent / 100}
                highlight
                progressbar
                aria-label={`Progresso da tarefa: passo ${task.step} de ${task.steps_total}`}
                label={
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {`Passo ${task.step} de ${task.steps_total}`}
                  </Typography>
                }
                valueLabel={
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    {`${percent} %`}
                  </Typography>
                }
              />
            ) : (
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  A arrancar a tarefa…
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
                  a preparar
                </Typography>
                <Skeleton className="mt-1 h-2 w-full rounded-full" />
              </Box>
            )}
            {task.last_screen_pt ? (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start' }}>
                <Monitor fontSize="small" sx={{ color: 'text.secondary', mt: 0.25 }} aria-hidden="true" />
                <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
                  {task.last_screen_pt}
                </Typography>
              </Stack>
            ) : null}
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              A tarefa conduz os menus do jogo automaticamente e pode demorar minutos. Podes cancelar
              a qualquer momento — o motor para de enviar teclas de imediato.
            </Typography>
          </Stack>
        ) : null}

        {task.state === 'done' ? (
          <Alert
            severity="success"
            role="status"
            action={
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                {doneActions}
              </Stack>
            }
          >
            Tarefa concluída — {KIND_LABEL[task.kind].toLowerCase()} terminada com sucesso.
          </Alert>
        ) : null}

        {cancelled ? (
          <Alert
            severity="info"
            role="status"
            action={
              onRetry ? (
                <Button
                  color="inherit"
                  startIcon={<Replay />}
                  sx={TARGET_SX}
                  onClick={onRetry}
                >
                  Recomeçar
                </Button>
              ) : undefined
            }
          >
            Tarefa cancelada — o motor parou de enviar teclas; podes recomeçar quando quiseres.
          </Alert>
        ) : null}

        {task.state === 'error' && !cancelled ? (
          <Alert
            severity="error"
            role="alert"
            action={
              onRetry ? (
                <Button
                  color="inherit"
                  startIcon={<Replay />}
                  sx={TARGET_SX}
                  onClick={onRetry}
                >
                  Tentar de novo
                </Button>
              ) : undefined
            }
          >
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              A tarefa terminou em erro
            </Typography>
            <Typography
              variant="caption"
              component="pre"
              sx={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', m: 0 }}
            >
              {task.error ?? 'Erro desconhecido.'}
            </Typography>
          </Alert>
        ) : null}
      </Stack>

      <Dialog
        open={confirmCancel}
        onClose={() => setConfirmCancel(false)}
        maxWidth="xs"
        aria-labelledby="cancelar-tarefa-titulo"
        aria-describedby="cancelar-tarefa-descricao"
      >
        <DialogTitle id="cancelar-tarefa-titulo">Cancelar a tarefa?</DialogTitle>
        <DialogContent>
          <Typography id="cancelar-tarefa-descricao" variant="body2">
            O motor para de enviar teclas de imediato e a tarefa termina como cancelada. O progresso
            desta tarefa é perdido — podes recomeçar depois.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button sx={TARGET_SX} onClick={() => setConfirmCancel(false)}>
            Continuar a tarefa
          </Button>
          <Button
            color="error"
            variant="contained"
            startIcon={<Stop />}
            disabled={cancelBusy}
            sx={TARGET_SX}
            onClick={() => {
              setConfirmCancel(false);
              onConfirmCancel();
            }}
          >
            Cancelar tarefa
          </Button>
        </DialogActions>
      </Dialog>
    </SectionCard>
  );
}
