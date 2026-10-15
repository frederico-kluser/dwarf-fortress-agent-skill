import { useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
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
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';

import {
  Sheet,
  SheetBackdrop,
  SheetClose,
  SheetHandle,
  SheetPanel,
} from '@/components/motion-ui/sheet';
import { errorMessage } from '../api/events';
import {
  BAND_DESCRIPTION,
  BAND_LABEL,
  DECISION_STATUS_DESCRIPTION,
  DECISION_STATUS_LABEL,
  asText,
  formatConfidence,
  formatDateTime,
  formatTime,
  formatUsd,
} from '../api/format';
import { PAGE_SIZE, useDecisions } from '../api/queries';
import type { Decision, DecisionBand, DecisionStatus, JevAgent } from '../api/types';
import { AgentChip, BandChip, DecisionStatusChip } from '../components/Chips';
import FreshnessIndicator from '../components/FreshnessIndicator';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading } from '../components/PanelState';
import { EmptyState } from '../components/rpg/EmptyState';
import SectionCard from '../components/SectionCard';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

const ALL = 'todos';
/** Filtro de desfecho para decisões sem `status` no contrato (auxiliares). */
const NO_STATUS = 'sem-desfecho';

function answerSummary(decision: Decision): string {
  const entries = Object.entries(decision.answers ?? {});
  if (entries.length === 0) return '—';
  return entries.map(([key, value]) => `${key}=${asText(value)}`).join(' · ');
}

function probabilityList(decision: Decision): [string, number][] {
  return Object.entries(decision.probabilities ?? {}).sort((a, b) => b[1] - a[1]);
}

/**
 * Barra de probabilidade fina (§5.5): 2px, `--primary` para a escolhida e
 * `--border` para as restantes. Estática — §6/§7: linhas de tabela não animam e
 * dados nunca esperam por animação (o valor numérico é a leitura primária).
 */
function ProbabilityBar({ value, chosen }: { value: number; chosen: boolean }) {
  return (
    <div className="h-0.5 w-full overflow-hidden rounded-sm bg-muted" aria-hidden="true">
      <div
        className={chosen ? 'h-full bg-primary' : 'h-full bg-border'}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

/** Confiança baixa para o filtro rápido: ausente/0 conta como indisponível. */
function isLowConfidence(decision: Decision): boolean {
  return (decision.confidence ?? 0) <= 0.5;
}

/**
 * Log das decisões JEV.
 *
 * Divulgação progressiva (auditoria D.1.1.01): a tabela mostra só as colunas de
 * decisão rápida (hora, papel, banda, confiança, custo) e cada linha abre um
 * detalhe com o pedido completo, TODAS as probabilidades, respostas tipadas,
 * latência e custo — sem scroll horizontal.
 */
export default function DecisionsPanel() {
  const decisionsQuery = useDecisions();
  const now = useNow(1000);
  const [agent, setAgent] = useState<string>(ALL);
  const [band, setBand] = useState<string>(ALL);
  const [status, setStatus] = useState<string>(ALL);
  const [search, setSearch] = useState('');
  const [lowConfidenceOnly, setLowConfidenceOnly] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);

  /*
   * As páginas do contrato vêm mais antigas primeiro dentro da página; a tabela
   * promete «do mais recente para o mais antigo», por isso ordena-se a vista.
   * A paginação usa o cursor por `ts` e não depende da ordem de apresentação.
   */
  const decisions = useMemo(
    () => [...decisionsQuery.items].sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0)),
    [decisionsQuery.items],
  );

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return decisions.filter((decision) => {
      if (agent !== ALL && decision.agent !== agent) return false;
      if (band !== ALL && decision.band !== band) return false;
      if (status === NO_STATUS && decision.status) return false;
      if (status !== ALL && status !== NO_STATUS && decision.status !== status) return false;
      if (lowConfidenceOnly && !isLowConfidence(decision)) return false;
      if (!term) return true;
      return (
        decision.request_summary.toLowerCase().includes(term) ||
        answerSummary(decision).toLowerCase().includes(term) ||
        decision.id.toLowerCase().includes(term)
      );
    });
  }, [decisions, agent, band, status, search, lowConfidenceOnly]);

  const filtersActive =
    agent !== ALL || band !== ALL || status !== ALL || lowConfidenceOnly || search.trim().length > 0;
  const detail = decisions.find((decision) => decision.id === detailId) ?? null;

  const clearFilters = () => {
    setAgent(ALL);
    setBand(ALL);
    setStatus(ALL);
    setSearch('');
    setLowConfidenceOnly(false);
  };

  return (
    <PanelShell
      title="Log de decisões JEV"
      description="Uma linha por chamada ao JEV. Confidence ausente ou 0 significa indisponível — nunca «certo»."
      actions={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            size="small"
            variant="outlined"
            label={`${filtered.length} de ${decisions.length} carregadas`}
          />
          <FreshnessIndicator
            ts={decisionsQuery.dataUpdatedAt ? new Date(decisionsQuery.dataUpdatedAt).toISOString() : null}
            now={now}
            refreshing={decisionsQuery.isFetching}
            refreshDisabled={decisionsQuery.isFetching}
            onRefresh={() => void decisionsQuery.refetch()}
            refreshLabel="Atualizar decisões agora"
          />
        </Stack>
      }
    >
      {decisionsQuery.isLoading ? <PanelLoading label="A carregar decisões…" /> : null}

      {decisionsQuery.isError ? (
        <PanelError
          message={errorMessage(decisionsQuery.error)}
          onRetry={() => void decisionsQuery.refetch()}
        />
      ) : null}

      <Stack spacing={2}>
        <SectionCard
          title="Filtros"
          subtitle="Reduz o log por papel, banda, confiança baixa ou texto"
          action={
            filtersActive ? (
              <Button size="small" sx={{ minHeight: 40 }} onClick={clearFilters}>
                Limpar filtros
              </Button>
            ) : null
          }
        >
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1.5}
            sx={{ alignItems: { xs: 'stretch', sm: 'center' }, flexWrap: 'wrap' }}
          >
            <TextField
              select
              label="Papel JEV"
              value={agent}
              onChange={(event) => setAgent(event.target.value)}
              sx={{ minWidth: 170 }}
            >
              <MenuItem value={ALL}>Todos os papéis</MenuItem>
              {(['JEV-R', 'JEV-A', 'JEV-M', 'JEV-D'] as JevAgent[]).map((item) => (
                <MenuItem key={item} value={item}>
                  {item}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Banda"
              value={band}
              onChange={(event) => setBand(event.target.value)}
              sx={{ minWidth: 170 }}
              helperText={
                band === ALL
                  ? 'auto ≥ 0,90 · hitl 0,50–0,89 · abstain < 0,50'
                  : BAND_DESCRIPTION[band as DecisionBand]
              }
            >
              <MenuItem value={ALL}>Todas as bandas</MenuItem>
              {(['auto', 'hitl', 'abstain'] as DecisionBand[]).map((item) => (
                <MenuItem key={item} value={item}>
                  {BAND_LABEL[item]}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Estado"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              sx={{ minWidth: 190 }}
              helperText={
                status === ALL
                  ? 'O que aconteceu à ação escolhida (desfecho do contrato)'
                  : status === NO_STATUS
                    ? 'Decisões auxiliares (JEV-R/M/D) sem desfecho no contrato'
                    : DECISION_STATUS_DESCRIPTION[status as DecisionStatus]
              }
            >
              <MenuItem value={ALL}>Todos os estados</MenuItem>
              {(['executed', 'question', 'error'] as DecisionStatus[]).map((item) => (
                <MenuItem key={item} value={item}>
                  {DECISION_STATUS_LABEL[item]}
                </MenuItem>
              ))}
              <MenuItem value={NO_STATUS}>Sem desfecho</MenuItem>
            </TextField>

            <TextField
              label="Filtrar por texto"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="ex.: river_bank"
              sx={{ minWidth: 220, flexGrow: 1 }}
            />

            <FormControlLabel
              control={
                <Switch
                  checked={lowConfidenceOnly}
                  onChange={(event) => setLowConfidenceOnly(event.target.checked)}
                  slotProps={{ input: { 'aria-label': 'Mostrar apenas confiança baixa (≤ 50 %)' } }}
                />
              }
              label="Só confiança baixa"
            />
          </Stack>
        </SectionCard>

        <SectionCard
          title="Decisões"
          subtitle="Do mais recente para o mais antigo — clica numa linha para ver o detalhe completo"
          flush
        >
          {filtered.length === 0 ? (
            /* §5.7 — a mensagem nomeia o objeto real («Sem decisões: o loop
             * ainda não correu») e diz o que falta e porquê. */
            <EmptyState
              glyph="scroll"
              title={
                filtersActive
                  ? 'Nenhuma decisão corresponde aos filtros ativos'
                  : 'Sem decisões: o loop ainda não correu'
              }
              body={
                filtersActive
                  ? 'Os filtros ativos estão a esconder o resto do log — limpa-os para veres todas as decisões carregadas.'
                  : 'Cada chamada ao JEV entra aqui com banda, confiança, custo e probabilidades. O log enche-se assim que o loop começar a decidir.'
              }
              action={
                filtersActive
                  ? { label: 'Limpar filtros', onClick: clearFilters }
                  : { label: 'Atualizar decisões', onClick: () => void decisionsQuery.refetch() }
              }
            />
          ) : (
            <TableContainer sx={{ maxHeight: 620 }}>
              <Table stickyHeader size="small" aria-label="Log de decisões JEV">
                <caption style={{ captionSide: 'bottom', padding: 8 }}>
                  {filtered.length} decisões apresentadas de {decisions.length} carregadas (páginas
                  de {PAGE_SIZE} — o anel do contrato guarda até 5000)
                </caption>
                <TableHead>
                  <TableRow>
                    <TableCell scope="col">Hora</TableCell>
                    <TableCell scope="col">Papel</TableCell>
                    <TableCell scope="col">Banda</TableCell>
                    <TableCell scope="col">Estado</TableCell>
                    <TableCell scope="col">Confiança</TableCell>
                    <TableCell scope="col">Resumo do pedido</TableCell>
                    <TableCell scope="col" align="right">
                      Custo
                    </TableCell>
                    <TableCell scope="col" align="right">
                      Detalhe
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filtered.map((decision) => {
                    const confidence = formatConfidence(decision.confidence);
                    const probabilities = probabilityList(decision);
                    const top = probabilities[0];
                    return (
                      <TableRow
                        key={decision.id}
                        hover
                        /* H.1.1.01: o alvo do rato (linha inteira) passa a ser
                         * também o alvo do teclado — Enter/Space abrem o detalhe. */
                        tabIndex={0}
                        aria-label={`Ver detalhe da decisão ${decision.id}: ${decision.request_summary}`}
                        sx={{ cursor: 'pointer' }}
                        onClick={() => setDetailId(decision.id)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            setDetailId(decision.id);
                          }
                        }}
                      >
                        <TableCell
                          sx={{ fontFamily: MONO_FONT, whiteSpace: 'nowrap', color: 'text.secondary' }}
                        >
                          {formatTime(decision.ts)}
                        </TableCell>
                        <TableCell>
                          <AgentChip agent={decision.agent} />
                        </TableCell>
                        <TableCell>
                          <BandChip band={decision.band} />
                        </TableCell>
                        <TableCell>
                          <DecisionStatusChip status={decision.status} />
                        </TableCell>
                        <TableCell>
                          {confidence ? (
                            <Typography variant="body2" sx={{ fontFamily: MONO_FONT }}>
                              {confidence}
                            </Typography>
                          ) : (
                            <Tooltip title="Confiança ausente ou 0 no contrato = indisponível, não «certo».">
                              <Chip size="small" variant="outlined" label="indisponível" />
                            </Tooltip>
                          )}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 340 }}>
                          <Stack spacing={0.5}>
                            <Typography variant="body2" noWrap>
                              {decision.request_summary}
                            </Typography>
                            {top ? (
                              <>
                                <Typography
                                  variant="caption"
                                  sx={{
                                    color: 'text.secondary',
                                    fontFamily: MONO_FONT,
                                    fontSize: '0.8125rem',
                                    fontVariantNumeric: 'tabular-nums',
                                  }}
                                >
                                  melhor: {top[0]} {(top[1] * 100).toFixed(0)} %
                                  {probabilities.length > 1
                                    ? ` · +${probabilities.length - 1} opção(ões) no detalhe`
                                    : ''}
                                </Typography>
                                <ProbabilityBar value={top[1]} chosen />
                              </>
                            ) : null}
                          </Stack>
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                          {formatUsd(decision.cost_usd, { compact: true })}
                        </TableCell>
                        <TableCell align="right">
                          <Tooltip title="Ver detalhe completo">
                            <IconButton
                              size="small"
                              aria-label={`Ver detalhe da decisão ${decision.id}`}
                              sx={{ width: 40, height: 40 }}
                              onClick={(event) => {
                                event.stopPropagation();
                                setDetailId(decision.id);
                              }}
                            >
                              <ExpandMore />
                            </IconButton>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}

          {decisionsQuery.hasMore ? (
            <Stack sx={{ alignItems: 'center', p: 2 }}>
              <Button
                variant="outlined"
                onClick={decisionsQuery.loadMore}
                loading={decisionsQuery.isLoadingMore}
                disabled={decisionsQuery.isLoadingMore}
                aria-label="Carregar mais decisões (página anterior do log)"
              >
                Carregar mais decisões
              </Button>
              <Typography variant="caption" sx={{ color: 'text.secondary', mt: 0.5, textAlign: 'center' }}>
                {decisions.length} carregadas — mais antigas disponíveis com o cursor `before` do contrato
              </Typography>
            </Stack>
          ) : null}
        </SectionCard>
      </Stack>

      {/*
        Detalhe da decisão num `sheet` do registry Motion UI (arrastar para
        fechar, foco gerido, scrim clicável) — em vez do dialog centrado.
      */}
      <Sheet
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) setDetailId(null);
        }}
      >
        <SheetBackdrop label="Fechar o detalhe da decisão" />
        <SheetPanel labelledBy="detalhe-decisao-titulo" className="max-w-3xl!">
          <SheetHandle />
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="detalhe-decisao-titulo" className="text-sm font-bold text-foreground">
                Decisão {detail?.id ?? '—'}
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Pedido completo, probabilidades, respostas tipadas, latência e custo.
              </p>
            </div>
            <SheetClose label="Fechar o detalhe da decisão" />
          </div>
          <div className="mt-3 max-h-[65vh] overflow-y-auto">
            {detail ? (
            <Stack spacing={2} sx={{ mt: 0.5 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <AgentChip agent={detail.agent} />
                <BandChip band={detail.band} />
                <DecisionStatusChip status={detail.status} />
                <Chip
                  size="small"
                  variant="outlined"
                  label={`confiança ${formatConfidence(detail.confidence) ?? 'indisponível'}`}
                />
                <Chip size="small" variant="outlined" label={`${detail.latency_ms} ms`} />
                <Chip
                  size="small"
                  variant="outlined"
                  label={`custo ${formatUsd(detail.cost_usd, { compact: true })}`}
                />
                <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
                  {formatDateTime(detail.ts)}
                </Typography>
              </Stack>

              <Box>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Pedido
                </Typography>
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                  {detail.request_summary}
                </Typography>
              </Box>

              {detail.issues && detail.issues.length > 0 ? (
                <Box>
                  <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                    Notas de auditoria ({detail.issues.length})
                  </Typography>
                  <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2 }}>
                    {detail.issues.map((issue) => (
                      <Typography key={issue} component="li" variant="body2" sx={{ fontFamily: MONO_FONT }}>
                        {issue}
                      </Typography>
                    ))}
                  </Stack>
                </Box>
              ) : null}

              <Box>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Respostas tipadas
                </Typography>
                {Object.keys(detail.answers ?? {}).length === 0 ? (
                  <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                    Sem respostas registadas.
                  </Typography>
                ) : (
                  <Stack spacing={0.5}>
                    {Object.entries(detail.answers ?? {}).map(([key, value]) => (
                      <Stack key={key} direction="row" spacing={1}>
                        <Typography variant="body2" sx={{ fontFamily: MONO_FONT, minWidth: 120 }}>
                          {key}
                        </Typography>
                        <Typography variant="body2" sx={{ fontFamily: MONO_FONT }}>
                          {asText(value)}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                )}
              </Box>

              <Box>
                <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                  Todas as probabilidades ({probabilityList(detail).length})
                </Typography>
                {probabilityList(detail).length === 0 ? (
                  <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                    Sem probabilidades no payload.
                  </Typography>
                ) : (
                  <Stack spacing={1}>
                    {probabilityList(detail).map(([option, value], index) => (
                      <Stack key={option} spacing={0.25}>
                        <Stack
                          direction="row"
                          spacing={1}
                          sx={{ alignItems: 'center', justifyContent: 'space-between' }}
                        >
                          <Typography variant="body2" sx={{ fontFamily: MONO_FONT, fontSize: '0.8125rem' }}>
                            {option}
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{
                              fontFamily: MONO_FONT,
                              fontSize: '0.8125rem',
                              fontVariantNumeric: 'tabular-nums',
                            }}
                          >
                            {(value * 100).toFixed(1)} %
                            {index === 0 ? ' · escolhida' : ''}
                          </Typography>
                        </Stack>
                        <ProbabilityBar value={value} chosen={index === 0} />
                      </Stack>
                    ))}
                  </Stack>
                )}
              </Box>
            </Stack>
            ) : null}
          </div>
          <div className="mt-3 flex justify-end">
            <Button onClick={() => setDetailId(null)}>Fechar</Button>
          </div>
        </SheetPanel>
      </Sheet>
    </PanelShell>
  );
}
