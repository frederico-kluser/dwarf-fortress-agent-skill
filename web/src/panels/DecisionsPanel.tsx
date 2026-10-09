import { useMemo, useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import { errorMessage } from '../api/events';
import { asText, formatConfidence, formatTime, formatUsd } from '../api/format';
import { useDecisions } from '../api/queries';
import type { Decision, DecisionBand, JevAgent } from '../api/types';
import { AgentChip, BandChip } from '../components/Chips';
import PanelShell from '../components/PanelShell';
import { PanelEmpty, PanelError, PanelLoading } from '../components/PanelState';
import SectionCard from '../components/SectionCard';
import { MONO_FONT } from '../theme';

const ALL = 'todos';

function answerSummary(decision: Decision): string {
  const entries = Object.entries(decision.answers ?? {});
  if (entries.length === 0) return '—';
  return entries.map(([key, value]) => `${key}=${asText(value)}`).join(' · ');
}

function probabilityList(decision: Decision): [string, number][] {
  return Object.entries(decision.probabilities ?? {}).sort((a, b) => b[1] - a[1]);
}

/** Log das decisões JEV: papel, respostas, probabilidades, banda, latência e custo. */
export default function DecisionsPanel() {
  const decisionsQuery = useDecisions();
  const [agent, setAgent] = useState<string>(ALL);
  const [band, setBand] = useState<string>(ALL);
  const [search, setSearch] = useState('');

  const decisions = useMemo(() => decisionsQuery.data ?? [], [decisionsQuery.data]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return decisions.filter((decision) => {
      if (agent !== ALL && decision.agent !== agent) return false;
      if (band !== ALL && decision.band !== band) return false;
      if (!term) return true;
      return (
        decision.request_summary.toLowerCase().includes(term) ||
        answerSummary(decision).toLowerCase().includes(term) ||
        decision.id.toLowerCase().includes(term)
      );
    });
  }, [decisions, agent, band, search]);

  const filtersActive = agent !== ALL || band !== ALL || search.trim().length > 0;

  return (
    <PanelShell
      title="Log de decisões JEV"
      description="Uma linha por chamada ao JEV. Confidence ausente ou 0 significa indisponível — nunca «certo»."
      actions={
        <Chip
          size="small"
          variant="outlined"
          label={`${filtered.length} de ${decisions.length} decisões`}
        />
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
          subtitle="Reduz o log por papel, banda ou texto"
          action={
            filtersActive ? (
              <Button
                size="small"
                onClick={() => {
                  setAgent(ALL);
                  setBand(ALL);
                  setSearch('');
                }}
              >
                Limpar filtros
              </Button>
            ) : null
          }
        >
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1.5}
            sx={{ alignItems: { xs: 'stretch', sm: 'center' } }}
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
            >
              <MenuItem value={ALL}>Todas as bandas</MenuItem>
              {(['auto', 'hitl', 'abstain'] as DecisionBand[]).map((item) => (
                <MenuItem key={item} value={item}>
                  {item}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              label="Filtrar por texto"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="ex.: river_bank"
              fullWidth
            />
          </Stack>
        </SectionCard>

        <SectionCard title="Decisões" subtitle="Do mais recente para o mais antigo" flush>
          {filtered.length === 0 ? (
            <PanelEmpty
              message="Sem decisões para mostrar."
              hint={
                filtersActive
                  ? 'Nenhuma decisão corresponde aos filtros ativos.'
                  : 'O log enche-se à medida que o JEV decide.'
              }
            />
          ) : (
            <TableContainer sx={{ maxHeight: 620 }}>
              <Table stickyHeader size="small" sx={{ minWidth: 1080 }} aria-label="Log de decisões JEV">
                <caption style={{ captionSide: 'bottom', padding: 8 }}>
                  {filtered.length} decisões apresentadas de {decisions.length} recebidas
                </caption>
                <TableHead>
                  <TableRow>
                    <TableCell scope="col">Hora</TableCell>
                    <TableCell scope="col">Papel</TableCell>
                    <TableCell scope="col">Pedido</TableCell>
                    <TableCell scope="col">Resposta</TableCell>
                    <TableCell scope="col">Probabilidades</TableCell>
                    <TableCell scope="col">Confiança</TableCell>
                    <TableCell scope="col">Banda</TableCell>
                    <TableCell scope="col" align="right">
                      Latência
                    </TableCell>
                    <TableCell scope="col" align="right">
                      Custo
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filtered.map((decision) => {
                    const confidence = formatConfidence(decision.confidence);
                    const probabilities = probabilityList(decision);
                    return (
                      <TableRow key={decision.id} hover>
                        <TableCell sx={{ fontFamily: MONO_FONT, whiteSpace: 'nowrap' }}>
                          {formatTime(decision.ts)}
                        </TableCell>
                        <TableCell>
                          <AgentChip agent={decision.agent} />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 260 }}>
                          <Tooltip title={decision.request_summary}>
                            <Typography variant="body2" noWrap>
                              {decision.request_summary}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell sx={{ maxWidth: 240 }}>
                          <Tooltip title={answerSummary(decision)}>
                            <Typography variant="body2" noWrap sx={{ fontFamily: MONO_FONT }}>
                              {answerSummary(decision)}
                            </Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell>
                          {probabilities.length === 0 ? (
                            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                              —
                            </Typography>
                          ) : (
                            <Stack spacing={0.25}>
                              {probabilities.slice(0, 2).map(([option, value]) => (
                                <Stack
                                  key={option}
                                  direction="row"
                                  spacing={0.5}
                                  sx={{ alignItems: 'center' }}
                                >
                                  <Box
                                    aria-hidden="true"
                                    sx={{
                                      width: 34,
                                      height: 6,
                                      borderRadius: 3,
                                      bgcolor: 'primary.main',
                                      opacity: 0.35 + Math.min(0.65, value),
                                    }}
                                  />
                                  <Typography
                                    variant="caption"
                                    sx={{ fontFamily: MONO_FONT, whiteSpace: 'nowrap' }}
                                  >
                                    {option} {(value * 100).toFixed(0)} %
                                  </Typography>
                                </Stack>
                              ))}
                            </Stack>
                          )}
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
                        <TableCell>
                          <BandChip band={decision.band} />
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                          {decision.latency_ms} ms
                        </TableCell>
                        <TableCell align="right" sx={{ fontFamily: MONO_FONT }}>
                          {formatUsd(decision.cost_usd, { compact: true })}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </SectionCard>
      </Stack>
    </PanelShell>
  );
}
