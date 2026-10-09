import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Badge from '@mui/material/Badge';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ApprovalIcon from '@mui/icons-material/Approval';
import Block from '@mui/icons-material/Block';
import TaskAlt from '@mui/icons-material/TaskAlt';

import { errorMessage } from '../api/events';
import { formatDateTime, formatRelative } from '../api/format';
import { useApprovals, useDecideApproval } from '../api/queries';
import { isDecisionBand, type Approval, type ControlMode } from '../api/types';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';
import ApprovalConfirmDialog from './ApprovalConfirmDialog';
import { BandChip } from './Chips';
import FreshnessIndicator from './FreshnessIndicator';
import { PanelEmpty, PanelError, PanelLoading, PanelNotice } from './PanelState';
import SectionCard from './SectionCard';

/** Botão do AppBar com o contador de aprovações pendentes (modo `checkpoint`). */
export function ApprovalsBadgeButton({
  pendingCount,
  onClick,
}: {
  pendingCount: number;
  onClick: () => void;
}) {
  return (
    <Tooltip title="Aprovações pendentes (modo checkpoints)">
      <IconButton
        onClick={onClick}
        aria-label={
          pendingCount === 0
            ? 'Aprovações pendentes: nenhuma'
            : `Aprovações pendentes: ${pendingCount}`
        }
      >
        <Badge badgeContent={pendingCount} color="warning" max={99}>
          <ApprovalIcon />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}

export interface ApprovalsSectionProps {
  /** Modo de autonomia da campanha — decide o estado vazio explicativo. */
  mode: ControlMode;
}

interface PendingDecision {
  approval: Approval;
  approve: boolean;
}

/**
 * Superfície de aprovação do modo `checkpoint` (achado #4 de
 * `docs/uxui-audit-ui.json`): o que o agente **quer** fazer e ainda não fez,
 * com Aprovar/Rejeitar — cada decisão passa por confirmação, porque a resposta
 * entra na memória do agente e não há caminho de edição.
 *
 * A lista é mantida viva pelos eventos `approval_new`/`approval_updated` do WS
 * (via cache do React Query) e pela leitura de `GET .../approvals`.
 */
export default function ApprovalsSection({ mode }: ApprovalsSectionProps) {
  const approvalsQuery = useApprovals();
  const now = useNow(1000);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);

  const decideMutation = useDecideApproval({
    onSuccess: (approval) =>
      setFeedback({
        tone: 'success',
        text:
          approval.status === 'approved'
            ? `Ação aprovada: ${approval.action_name_en}.`
            : `Ação rejeitada: ${approval.action_name_en}.`,
      }),
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao decidir a aprovação: ${errorMessage(error)}` }),
  });

  const approvals = approvalsQuery.data ?? [];
  const heldWhileAutopilot = mode === 'autopilot' && approvals.length > 0;

  const confirmDecision = () => {
    if (!pending) return;
    decideMutation.mutate({ approvalId: pending.approval.id, approve: pending.approve });
    setPending(null);
  };

  return (
    <SectionCard
      title="Aprovações retidas"
      subtitle="Em «Checkpoints» o agente retém cada ação aqui até a aprovares ou rejeitares"
      flush
      action={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            size="small"
            color={approvals.length > 0 ? 'warning' : 'default'}
            variant="outlined"
            label={`${approvals.length} pendentes`}
          />
          <FreshnessIndicator
            ts={
              approvalsQuery.dataUpdatedAt
                ? new Date(approvalsQuery.dataUpdatedAt).toISOString()
                : null
            }
            now={now}
            refreshing={approvalsQuery.isFetching}
            refreshDisabled={approvalsQuery.isFetching}
            onRefresh={() => void approvalsQuery.refetch()}
            refreshLabel="Atualizar aprovações agora"
          />
        </Stack>
      }
    >
      {approvalsQuery.isLoading ? <PanelLoading label="A carregar aprovações…" /> : null}

      {approvalsQuery.isError ? (
        <PanelError
          message={errorMessage(approvalsQuery.error)}
          onRetry={() => void approvalsQuery.refetch()}
        />
      ) : null}

      {feedback ? (
        <Stack sx={{ p: 2, pb: 0 }}>
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        </Stack>
      ) : null}

      {heldWhileAutopilot ? (
        <Stack sx={{ p: 2, pb: 0 }}>
          <PanelNotice severity="warning">
            O agente está em <strong>autopiloto</strong>: estas ações ficaram retidas quando o modo
            era «Checkpoints» e continuam à espera de decisão.
          </PanelNotice>
        </Stack>
      ) : null}

      {!approvalsQuery.isLoading && approvals.length === 0 ? (
        mode === 'autopilot' ? (
          <PanelEmpty
            message="Sem aprovações pendentes — o autopiloto decide sozinho"
            hint="O agente só retém ações para aprovação no modo «Checkpoints». Muda o modo para reveres cada ação antes de ser executada."
          />
        ) : (
          <PanelEmpty
            message="Sem aprovações pendentes."
            hint="Assim que o agente quiser executar uma ação retida, ela aparece aqui e no badge da barra superior."
          />
        )
      ) : null}

      {approvals.length > 0 ? (
        <List dense disablePadding>
          {approvals.map((approval, index) => (
            <ListItem
              key={approval.id}
              divider={index < approvals.length - 1}
              alignItems="flex-start"
              sx={{ gap: 1, flexWrap: 'wrap', py: 1.5 }}
            >
              <ListItemText
                sx={{ minWidth: 240, flexGrow: 1 }}
                primary={
                  <Stack
                    direction="row"
                    spacing={1}
                    useFlexGap
                    sx={{ alignItems: 'center', flexWrap: 'wrap' }}
                  >
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {approval.action_name_en}
                    </Typography>
                    {isDecisionBand(approval.band) ? <BandChip band={approval.band} /> : null}
                  </Stack>
                }
                secondary={
                  <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                    {approval.question ? (
                      <Typography variant="body2" component="span">
                        {approval.question}
                      </Typography>
                    ) : null}
                    <Typography
                      variant="caption"
                      component="span"
                      sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}
                    >
                      {approval.id} · {approval.action_id} · {formatRelative(approval.ts, now)} ·{' '}
                      {formatDateTime(approval.ts)}
                    </Typography>
                    {approval.description_en ? (
                      <Typography variant="caption" component="span" sx={{ color: 'text.secondary' }}>
                        {approval.description_en}
                      </Typography>
                    ) : null}
                  </Stack>
                }
              />
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexShrink: 0 }}>
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<TaskAlt />}
                  disabled={decideMutation.isPending}
                  onClick={() => setPending({ approval, approve: true })}
                >
                  Aprovar
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  color="error"
                  startIcon={<Block />}
                  disabled={decideMutation.isPending}
                  onClick={() => setPending({ approval, approve: false })}
                >
                  Rejeitar
                </Button>
              </Stack>
            </ListItem>
          ))}
        </List>
      ) : null}

      <ApprovalConfirmDialog
        approval={pending?.approval ?? null}
        approve={pending?.approve ?? true}
        busy={decideMutation.isPending}
        onCancel={() => setPending(null)}
        onConfirm={confirmDecision}
      />
    </SectionCard>
  );
}
