import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Pause from '@mui/icons-material/Pause';
import PlayArrow from '@mui/icons-material/PlayArrow';
import VpnKey from '@mui/icons-material/VpnKey';
import Add from '@mui/icons-material/Add';

import { errorMessage } from '../api/events';
import {
  CONTROL_MODE_LABEL,
  USD_COUNTER_FORMAT,
  USD_PREFIX,
  formatDateTime,
  formatNumber,
  formatUsd,
} from '../api/format';
import {
  useApiContext,
  useKeys,
  useMetrics,
  useSetControl,
  useSetKey,
  useSpend,
} from '../api/queries';
import type { ControlMode, Provider } from '../api/types';
import ApprovalsSection from '../components/ApprovalsSection';
import AutopilotConfirmDialog, { type AutopilotReason } from '../components/AutopilotConfirmDialog';
import FreshnessIndicator from '../components/FreshnessIndicator';
import KillSwitchButton from '../components/KillSwitchButton';
import MetricCard from '../components/MetricCard';
import PanelShell from '../components/PanelShell';
import { PanelEmpty, PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import SectionCard from '../components/SectionCard';
import { SpendSkeleton } from '../components/SkeletonBlocks';
import SystemSection from '../components/SystemSection';
import { AnimatedNumber, type NumberFormat } from '@/components/motion-ui/animated-number';
import { useAutonomyPersistence, modeLabel } from '../hooks/useAutonomyControl';
import { useNow, useSparkSeries } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

const PROVIDERS: Provider[] = ['openrouter', 'typesafe', 'deepseek'];

function rhythmLabel(decisionsPerMin: number): string {
  if (decisionsPerMin === 0) return 'parado';
  if (decisionsPerMin < 5) return 'lento';
  if (decisionsPerMin < 15) return 'normal';
  return 'rápido';
}

/** Chaves de API: guardadas na SQLite da campanha, sempre mascaradas na leitura. */
function KeysSection() {
  const keysQuery = useKeys();
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<Provider>('openrouter');
  const [key, setKey] = useState('');
  const [feedback, setFeedback] = useState<string | null>(null);

  const setKeyMutation = useSetKey({
    onSuccess: (info) => {
      setOpen(false);
      setKey('');
      setFeedback(`Chave de ${info.provider} guardada: ${info.masked_key ?? '—'}`);
    },
    onError: (error) => setFeedback(`Falha ao guardar a chave: ${errorMessage(error)}`),
  });

  const keys = keysQuery.data ?? PROVIDERS.map((item) => ({ provider: item, masked_key: null, set: false }));

  return (
    <SectionCard
      title="Chaves de API"
      subtitle="Guardadas na base CoALA da campanha (ficheiro 0600) e mascaradas em exports e logs"
      action={
        <Button size="small" startIcon={<Add />} onClick={() => setOpen(true)}>
          Definir chave
        </Button>
      }
      flush
    >
      {keysQuery.isLoading ? <PanelLoading label="A carregar chaves…" /> : null}
      {keysQuery.isError ? (
        <PanelError message={errorMessage(keysQuery.error)} onRetry={() => void keysQuery.refetch()} />
      ) : null}

      <List dense disablePadding>
        {keys.map((item, index) => (
          <ListItem key={item.provider} divider={index < keys.length - 1}>
            <ListItemText
              primary={
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ alignItems: 'center' }}
                >
                  <VpnKey fontSize="small" />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {item.provider}
                  </Typography>
                  <Chip
                    size="small"
                    variant="outlined"
                    color={item.set ? 'success' : 'default'}
                    label={item.set ? 'definida' : 'não definida'}
                  />
                </Stack>
              }
              secondary={item.masked_key ?? 'sem chave guardada'}
              slotProps={{
                secondary: { variant: 'caption', sx: { fontFamily: MONO_FONT } },
              }}
            />
          </ListItem>
        ))}
      </List>

      {feedback ? (
        <Box sx={{ p: 2 }}>
          <Alert severity="info" role="status" onClose={() => setFeedback(null)}>
            {feedback}
          </Alert>
        </Box>
      ) : null}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" aria-labelledby="chave-titulo">
        <DialogTitle id="chave-titulo">Definir chave de API</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              select
              label="Fornecedor"
              value={provider}
              onChange={(event) => setProvider(event.target.value as Provider)}
              fullWidth
            >
              {PROVIDERS.map((item) => (
                <MenuItem key={item} value={item}>
                  {item}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Chave"
              type="password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              fullWidth
              autoComplete="off"
              helperText="A chave nunca é devolvida em claro; a leitura mostra apenas a versão mascarada."
            />
            <PanelNotice severity="warning">
              A chave é persistida na SQLite da campanha com permissões 0600.
            </PanelNotice>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancelar</Button>
          <Button
            variant="contained"
            loading={setKeyMutation.isPending}
            loadingPosition="start"
            startIcon={<VpnKey />}
            disabled={key.trim().length === 0}
            onClick={() => setKeyMutation.mutate({ provider, key: key.trim() })}
          >
            Guardar chave
          </Button>
        </DialogActions>
      </Dialog>
    </SectionCard>
  );
}

/** Criação de campanha (cada campanha tem a sua própria base CoALA). */
function NewCampaignSection() {
  const { client, selectCampaign, reloadCampaigns } = useApiContext();
  const [name, setName] = useState('');
  const [isNewGame, setIsNewGame] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const create = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      const created = await client.createCampaign({ name: name.trim(), is_new_game: isNewGame });
      setFeedback({
        tone: 'success',
        text: `Campanha «${created.name}» criada${created.is_new_game ? ' (jogo novo)' : ' (gameplay existente)'}.`,
      });
      setName('');
      reloadCampaigns();
      selectCampaign(created.id);
    } catch (error) {
      setFeedback({ tone: 'error', text: `Falha ao criar a campanha: ${errorMessage(error)}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard
      title="Campanha"
      subtitle="Uma base de memória CoALA por campanha"
    >
      <Stack spacing={1.5}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1.5}
          sx={{ alignItems: { sm: 'center' } }}
        >
          <TextField
            id="nova-campanha-nome"
            label="Nome da campanha"
            value={name}
            onChange={(event) => setName(event.target.value)}
            sx={{ minWidth: 240 }}
            helperText="Define o caminho da memória: .agents/<campanha>-agent-skill/"
          />
          <FormControlLabel
            control={
              <Switch
                checked={isNewGame}
                onChange={(event) => setIsNewGame(event.target.checked)}
                slotProps={{ input: { 'aria-label': 'Jogo novo (memória vazia)' } }}
              />
            }
            label="Jogo novo (memória vazia)"
          />
          <Button
            variant="contained"
            startIcon={<Add />}
            loading={busy}
            loadingPosition="start"
            disabled={name.trim().length === 0}
            onClick={() => void create()}
          >
            Criar campanha
          </Button>
        </Stack>

        {feedback ? (
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        ) : null}
      </Stack>
    </SectionCard>
  );
}

/** Controlo de autonomia: modo, pausa, ritmo, kill-switch, gastos e chaves. */
export default function ControlPanel() {
  const { campaign, campaignId } = useApiContext();
  const metricsQuery = useMetrics();
  const spendQuery = useSpend();
  const now = useNow(1000);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [pendingAutopilot, setPendingAutopilot] = useState<AutopilotReason | null>(null);

  const controlMutation = useSetControl({
    onSuccess: (control) =>
      setFeedback({
        tone: 'success',
        text: `Modo: ${CONTROL_MODE_LABEL[control.mode]}${control.kill_switch ? ' · kill-switch ativo' : ''}.`,
      }),
    onError: (error) => setFeedback({ tone: 'error', text: `Falha no controlo: ${errorMessage(error)}` }),
  });

  const mode: ControlMode = campaign?.control.mode ?? 'paused';
  const killSwitch = campaign?.control.kill_switch ?? false;
  const { restorableMode, rememberMode, persisted } = useAutonomyPersistence(campaignId, mode);
  const metrics = metricsQuery.data;
  const decisionsPerMin = metrics?.agent.decisions_per_min ?? 0;
  const decisionsSeries = useSparkSeries(metrics?.ts, metrics?.agent.decisions_per_min);

  const applyControl = (next: { mode?: ControlMode; kill_switch?: boolean }) => {
    controlMutation.mutate({
      mode: next.mode ?? mode,
      kill_switch: next.kill_switch ?? killSwitch,
    });
  };

  /**
   * Toda a transição PARA autopilot passa por confirmação (auditoria C.1.4.01) e
   * grava a escolha como modo a restaurar.
   */
  const requestMode = (next: ControlMode) => {
    if (next === mode) return;
    if (next === 'autopilot') {
      setPendingAutopilot(mode === 'paused' ? 'resume' : 'switch');
      return;
    }
    applyControl({ mode: next });
    if (next === 'checkpoint') rememberMode(next);
  };

  const confirmAutopilot = () => {
    rememberMode('autopilot');
    applyControl({ mode: 'autopilot', kill_switch: false });
    setPendingAutopilot(null);
  };

  /** Saída de «Em pausa»: restaura o último modo guardado, nunca força autopilot. */
  const requestResume = () => {
    if (restorableMode === 'autopilot') {
      setPendingAutopilot('resume');
      return;
    }
    applyControl({ mode: restorableMode });
  };

  const requestKillSwitch = (next: boolean) => {
    if (next) {
      applyControl({ mode: 'paused', kill_switch: true });
      return;
    }
    // Desarmar devolve o modo guardado (ou o modo atual se nunca esteve em pausa).
    const target = mode === 'paused' ? restorableMode : mode;
    if (target === 'autopilot') {
      setPendingAutopilot('disarm');
      return;
    }
    applyControl({ mode: target, kill_switch: false });
  };

  const killSwitchScope = killSwitch
    ? `Ao desligar, o agente fica em «${modeLabel(restorableMode)}» — o último modo não-pausado guardado nesta campanha — e não em autopiloto.`
    : 'Ao acionar, o agente fica em «Em pausa» até desligares o kill-switch ou escolheres outro modo.';

  return (
    <PanelShell
      title="Controlo de autonomia"
      description="Autopiloto ⇄ checkpoints, aprovações retidas, pausa, kill-switch, chaves e gastos da campanha."
      actions={
        <>
          <Chip
            size="small"
            variant="outlined"
            color={killSwitch ? 'error' : mode === 'paused' ? 'warning' : 'success'}
            label={killSwitch ? 'kill-switch ativo' : CONTROL_MODE_LABEL[mode]}
          />
          <Chip
            size="small"
            variant="outlined"
            label={campaign ? campaign.name : 'sem campanha'}
          />
        </>
      }
    >
      <Stack spacing={2}>
        {feedback ? (
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        ) : null}

        {killSwitch ? (
          <Alert severity="error" role="alert">
            Kill-switch ativo: o agente não executa nenhuma ação sobre o jogo.
          </Alert>
        ) : null}

        <SystemSection />

        <SectionCard
          title="Modo de autonomia"
          subtitle="Bandas de ação: auto ≥ 0,90 · hitl 0,50–0,89 · abstain < 0,50"
        >
          <Stack spacing={2}>
            <ToggleButtonGroup
              exclusive
              value={mode}
              onChange={(_event, next: ControlMode | null) => {
                if (next !== null) requestMode(next);
              }}
              aria-label="Modo de autonomia"
            >
              <Tooltip title="O agente decide e age sozinho — pede confirmação antes de ativar">
                <ToggleButton value="autopilot">Autopiloto</ToggleButton>
              </Tooltip>
              <Tooltip title="O agente para nos casos de banda hitl/abstain e pergunta na fila de perguntas">
                <ToggleButton value="checkpoint">Checkpoints</ToggleButton>
              </Tooltip>
              <Tooltip title="O agente não age sobre o jogo até retomares">
                <ToggleButton value="paused">Em pausa</ToggleButton>
              </Tooltip>
            </ToggleButtonGroup>

            <Alert severity="info" variant="outlined" role="note">
              <strong>Checkpoints</strong>: o agente retém cada ação para tua aprovação — as
              aprovações pendentes estão na secção <strong>Aprovações retidas</strong> abaixo e no
              badge da barra superior. <strong>Em pausa</strong> retoma sempre para{' '}
              <strong>{modeLabel(restorableMode)}</strong>
              {persisted ? '' : ' (valor por omissão; escolhe um modo para o fixar)'}.
            </Alert>

            <Stack
              direction="row"
              spacing={1}
              useFlexGap
              sx={{ alignItems: 'center', flexWrap: 'wrap' }}
            >
              <Button
                variant={mode === 'paused' ? 'contained' : 'outlined'}
                color={mode === 'paused' ? 'success' : 'warning'}
                startIcon={mode === 'paused' ? <PlayArrow /> : <Pause />}
                loading={controlMutation.isPending}
                loadingPosition="start"
                onClick={() => (mode === 'paused' ? requestResume() : applyControl({ mode: 'paused' }))}
              >
                {mode === 'paused' ? `Retomar ${modeLabel(restorableMode)}` : 'Pausar'}
              </Button>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {mode === 'paused'
                  ? 'Retomar devolve o último modo escolhido nesta campanha — não passa por autopiloto.'
                  : 'O JEV controla o tempo: não há tick fixo do lado do código.'}
              </Typography>
            </Stack>
          </Stack>
        </SectionCard>

        <ApprovalsSection mode={mode} />

        <SectionCard
          title="Ritmo de decisão"
          subtitle="Indicador de velocidade: quantas decisões o agente toma por minuto"
        >
          <Stack spacing={1.5}>
            <Box sx={{ maxWidth: 320 }}>
              <MetricCard
                title="Velocidade"
                value={formatNumber(decisionsPerMin, 1)}
                unit="decisões/min"
                hint={`ritmo ${rhythmLabel(decisionsPerMin)} · latência média ${formatNumber(metrics?.agent.latency_ms)} ms`}
                series={decisionsSeries}
                color="primary.main"
              />
            </Box>
            <LinearProgress
              variant="determinate"
              value={Math.min(100, (decisionsPerMin / 30) * 100)}
              aria-label="Velocidade relativa do agente (0 a 30 decisões por minuto)"
            />
            <PanelNotice severity="info">
              O contrato não expõe velocidade ajustável no <code>POST /control</code> (só{' '}
              <code>mode</code> e <code>kill_switch</code>): este indicador é derivado das métricas
              do agente.
            </PanelNotice>
          </Stack>
        </SectionCard>

        <SectionCard
          title="Kill-switch"
          subtitle="Para tudo imediatamente — a memória e as métricas ficam intactas"
        >
          <Stack
            direction="row"
            spacing={1.5}
            useFlexGap
            sx={{ alignItems: 'center', flexWrap: 'wrap' }}
          >
            <KillSwitchButton
              variant="button"
              active={killSwitch}
              busy={controlMutation.isPending}
              scope={killSwitchScope}
              disarmLabel={`Desligar e voltar a ${modeLabel(mode === 'paused' ? restorableMode : mode)}`}
              onConfirm={requestKillSwitch}
            />
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Sessões longas correm em janela visível; o kill-switch é a paragem de emergência.
            </Typography>
          </Stack>
        </SectionCard>

        <SectionCard
          title="Gastos da campanha"
          subtitle="Sem teto de orçamento — apenas visibilidade total, em tempo real"
          flush
          action={
            <FreshnessIndicator
              ts={spendQuery.data?.ts}
              now={now}
              refreshing={spendQuery.isFetching}
              refreshDisabled={spendQuery.isFetching}
              onRefresh={() => void spendQuery.refetch()}
              refreshLabel="Atualizar gastos agora"
            />
          }
        >
          {spendQuery.isLoading ? <SpendSkeleton /> : null}
          {spendQuery.isError ? (
            <PanelError message={errorMessage(spendQuery.error)} onRetry={() => void spendQuery.refetch()} />
          ) : null}

          {spendQuery.data ? (
            <Stack spacing={2} sx={{ p: 2 }}>
              <Stack spacing={0.5}>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Total acumulado
                </Typography>
                <Typography variant="h3" component="p" sx={{ fontFamily: MONO_FONT }}>
                  <AnimatedNumber
                    value={spendQuery.data.total_usd}
                    transition="lively"
                    format={USD_COUNTER_FORMAT as NumberFormat}
                    prefix={USD_PREFIX}
                    locales="pt-PT"
                  />
                </Typography>
              </Stack>

              {spendQuery.data.providers.length === 0 ? (
                <PanelEmpty message="Ainda sem chamadas pagas nesta campanha." />
              ) : (
                <TableContainer>
                  <Table size="small" aria-label="Gastos por fornecedor e modelo">
                    <TableHead>
                      <TableRow>
                        <TableCell scope="col">Fornecedor</TableCell>
                        <TableCell scope="col">Modelo</TableCell>
                        <TableCell scope="col" align="right">
                          Chamadas
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Tokens entrada
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Tokens saída
                        </TableCell>
                        <TableCell scope="col" align="right">
                          Custo
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {spendQuery.data.providers.flatMap((provider) =>
                        provider.models.map((model) => (
                          <TableRow key={`${provider.provider}-${model.model}`} hover>
                            <TableCell>{provider.provider}</TableCell>
                            <TableCell sx={{ fontFamily: MONO_FONT }}>{model.model}</TableCell>
                            <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                              {formatNumber(model.calls)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                              {formatNumber(model.input_tokens)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                              {formatNumber(model.output_tokens)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                              {formatUsd(model.cost_usd, { compact: true })}
                            </TableCell>
                          </TableRow>
                        )),
                      )}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}

              <Divider />
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Leitura de GET /api/campaigns/{'{id}'}/spend · {formatDateTime(spendQuery.data.ts)}
              </Typography>
            </Stack>
          ) : null}
        </SectionCard>

        <KeysSection />
        <NewCampaignSection />
      </Stack>

      <AutopilotConfirmDialog
        open={pendingAutopilot !== null}
        from={mode}
        reason={pendingAutopilot ?? 'switch'}
        busy={controlMutation.isPending}
        onCancel={() => setPendingAutopilot(null)}
        onConfirm={confirmAutopilot}
      />
    </PanelShell>
  );
}
