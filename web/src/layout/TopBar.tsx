import { useState } from 'react';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Menu from '@mui/material/Menu';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import KeyboardArrowDown from '@mui/icons-material/KeyboardArrowDown';
import { useTheme } from '@mui/material/styles';

import ConnectionBadge from '../components/ConnectionBadge';
import { ApprovalsBadgeButton } from '../components/ApprovalsSection';
import FreshnessIndicator from '../components/FreshnessIndicator';
import KillSwitchButton from '../components/KillSwitchButton';
import { ControlModeChip } from '../components/Chips';
import { QuestionsBadgeButton } from '../components/QuestionsQueueDrawer';
import ThemeModeToggle from '../components/ThemeModeToggle';
import { BandBadge } from '../components/rpg/BandBadge';
import { useApiContext, useConnection } from '../api/queries';
import { CONTROL_MODE_LABEL } from '../api/format';
import { useNow } from '../hooks/useSparkSeries';

export interface TopBarProps {
  pendingQuestions: number;
  onOpenQuestions: () => void;
  /** Ações retidas à espera de aprovação (modo `checkpoint`). */
  pendingApprovals: number;
  onOpenApprovals: () => void;
  onKillSwitch: (next: boolean) => void;
  controlBusy: boolean;
}

/** Barra global: identidade, campanha ativa, estado e controlos críticos. */
export default function TopBar({
  pendingQuestions,
  onOpenQuestions,
  pendingApprovals,
  onOpenApprovals,
  onKillSwitch,
  controlBusy,
}: TopBarProps) {
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down('sm'));
  const now = useNow(1000);
  const { campaigns, campaignId, campaign, selectCampaign, isMock } = useApiContext();
  const connection = useConnection();
  const [campaignAnchor, setCampaignAnchor] = useState<HTMLElement | null>(null);
  const campaignName = campaign?.name ?? 'Sem campanha';

  // §4.1 — edge da forja: o bloco de estado ganha `border-top: 2px var(--primary)`
  // enquanto o loop está a correr (brasa = a trabalhar; §1).
  const loopAtivo = campaign != null && campaign.control.mode !== 'paused' && !campaign.control.kill_switch;

  return (
    <AppBar
      position="fixed"
      sx={{
        zIndex: (theme) => theme.zIndex.drawer + 1,
        // §2.1/§4.1 — placa de pedra: superfície sólida + cantos talhados + shadow-1.
        backgroundColor: 'var(--surface)',
        backgroundImage: 'none',
        color: 'var(--foreground)',
        borderBottom: '1px solid var(--border)',
        boxShadow: 'var(--shadow-1), inset 0 1px 0 var(--carve-top)',
      }}
    >
      <Toolbar variant="dense" sx={{ gap: 1, px: { xs: 1, sm: 2 } }}>
        <Typography variant="h2" component="p" sx={{ whiteSpace: 'nowrap' }}>
          <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>System One Dwarf</Box>
          <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>SOD</Box>
        </Typography>

        {/*
          Badge de sistema (§5.10): «dados simulados» → `--info`, glifo 14px +
          etiqueta PT, fundo 15% do status — nunca cor como único sinal.
        */}
        {isMock ? (
          <span className="hidden md:block">
            <BandBadge band="stub" />
          </span>
        ) : null}

        <Box sx={{ flexGrow: 1 }} />

        {/* Campanha ativa: select em ecrãs largos, menu compacto em ecrãs estreitos. */}
        <TextField
          select
          label="Campanha"
          value={campaignId ?? ''}
          onChange={(event) => selectCampaign(event.target.value)}
          disabled={campaigns.length === 0}
          sx={{ display: { xs: 'none', sm: 'inline-flex' }, minWidth: 190, maxWidth: 260 }}
        >
          {campaigns.map((item) => (
            <MenuItem key={item.id} value={item.id}>
              {item.name}
            </MenuItem>
          ))}
        </TextField>

        <Tooltip title={`Campanha ativa: ${campaignName}`}>
          <Chip
            size="small"
            variant="outlined"
            label={campaignName}
            onClick={(event) => setCampaignAnchor(event.currentTarget)}
            onDelete={
              campaigns.length > 0 ? (event) => setCampaignAnchor(event.currentTarget) : undefined
            }
            deleteIcon={<KeyboardArrowDown />}
            aria-label={`Campanha ativa: ${campaignName}. Abrir lista de campanhas`}
            sx={{
              display: { xs: 'inline-flex', sm: 'none' },
              // I.2.2.02 — alvo de toque ≥ 40px em ação recorrente.
              height: 40,
              maxWidth: 132,
              '& .MuiChip-label': { overflow: 'hidden', textOverflow: 'ellipsis' },
            }}
          />
        </Tooltip>
        <Menu
          open={campaignAnchor !== null}
          anchorEl={campaignAnchor}
          onClose={() => setCampaignAnchor(null)}
        >
          {campaigns.length === 0 ? (
            <MenuItem disabled>Sem campanhas</MenuItem>
          ) : (
            campaigns.map((item) => (
              <MenuItem
                key={item.id}
                selected={item.id === campaignId}
                onClick={() => {
                  selectCampaign(item.id);
                  setCampaignAnchor(null);
                }}
              >
                {item.name}
              </MenuItem>
            ))
          )}
        </Menu>

        <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', sm: 'block' } }} />

        {/*
          Frescura do estado de controlo: o contrato não manda `ts` para o modo, logo
          mostramos quando a campanha foi lida. Visível em TODOS os breakpoints.
        */}
        <Tooltip
          title={
            campaign
              ? `Modo de autonomia: ${CONTROL_MODE_LABEL[campaign.control.mode]}${
                  campaign.control.kill_switch ? ' · kill-switch ativo' : ''
                }`
              : 'Sem campanha selecionada'
          }
        >
          <Stack
            direction="row"
            spacing={0.75}
            sx={{
              alignItems: 'center',
              px: 1,
              // §4.1 — edge da forja no bloco com o loop a correr (transparente quando parado).
              borderTop: '2px solid',
              borderColor: loopAtivo ? 'var(--primary)' : 'transparent',
            }}
          >
            <ControlModeChip
              mode={campaign?.control.mode ?? 'paused'}
              blocked={campaign?.control.kill_switch ?? false}
              compact={compact}
              pendingQuestions={pendingQuestions}
            />
            <Box sx={{ display: { xs: 'none', md: 'inline-flex' } }}>
              <FreshnessIndicator ts={campaign?.updated_at} now={now} />
            </Box>
          </Stack>
        </Tooltip>

        <ConnectionBadge connection={connection} isMock={isMock} />

        <QuestionsBadgeButton pendingCount={pendingQuestions} onClick={onOpenQuestions} />

        <ApprovalsBadgeButton pendingCount={pendingApprovals} onClick={onOpenApprovals} />

        {/*
          I.2.2.02: o kill-switch (ação destrutiva global) vive no seu próprio
          grupo — «corda» (§4.4) de separação + ≥12px de folga de ambos os lados.
          Nada cola ao kill-switch, nem os badges de uso frequente nem o toggle.
        */}
        <Box
          aria-hidden="true"
          sx={{
            display: { xs: 'none', sm: 'block' },
            width: 2,
            alignSelf: 'stretch',
            my: 1,
            mx: 1.5,
            background:
              'repeating-linear-gradient(180deg, var(--border) 0 6px, transparent 6px 10px)',
          }}
        />
        <Box sx={{ display: 'inline-flex', mx: 1.5 }}>
          <KillSwitchButton
            active={campaign?.control.kill_switch ?? false}
            onConfirm={onKillSwitch}
            busy={controlBusy}
          />
        </Box>

        <Stack sx={{ display: { xs: 'none', sm: 'flex' } }}>
          <ThemeModeToggle />
        </Stack>
      </Toolbar>
    </AppBar>
  );
}
