import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Menu from '@mui/icons-material/Menu';

import ConnectionBadge from '../components/ConnectionBadge';
import KillSwitchButton from '../components/KillSwitchButton';
import { ControlModeChip } from '../components/Chips';
import { QuestionsBadgeButton } from '../components/QuestionsQueueDrawer';
import ThemeModeToggle from '../components/ThemeModeToggle';
import { useApiContext, useConnection } from '../api/queries';

export interface TopBarProps {
  pendingQuestions: number;
  onOpenQuestions: () => void;
  onOpenNav: () => void;
  onKillSwitch: (next: boolean) => void;
  controlBusy: boolean;
}

/** Barra global: identidade, campanha ativa, estado e controlos críticos. */
export default function TopBar({
  pendingQuestions,
  onOpenQuestions,
  onOpenNav,
  onKillSwitch,
  controlBusy,
}: TopBarProps) {
  const { campaigns, campaignId, campaign, selectCampaign, isMock } = useApiContext();
  const connection = useConnection();

  return (
    <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
      <Toolbar variant="dense" sx={{ gap: 1, px: { xs: 1, sm: 2 } }}>
        <IconButton
          onClick={onOpenNav}
          aria-label="Abrir navegação dos painéis"
          sx={{ display: { xs: 'inline-flex', md: 'none' } }}
        >
          <Menu />
        </IconButton>

        <Typography variant="h2" component="p" sx={{ whiteSpace: 'nowrap' }}>
          <Box sx={{ display: { xs: 'none', sm: 'inline' } }}>System One Dwarf</Box>
          <Box sx={{ display: { xs: 'inline', sm: 'none' } }}>SOD</Box>
        </Typography>

        {isMock ? (
          <Chip
            size="small"
            variant="outlined"
            color="info"
            label="dados simulados"
            sx={{ display: { xs: 'none', lg: 'inline-flex' } }}
          />
        ) : null}

        <Box sx={{ flexGrow: 1 }} />

        <TextField
          select
          label="Campanha"
          value={campaignId ?? ''}
          onChange={(event) => selectCampaign(event.target.value)}
          disabled={campaigns.length === 0}
          sx={{ minWidth: { xs: 124, sm: 190 }, maxWidth: 260 }}
        >
          {campaigns.map((item) => (
            <MenuItem key={item.id} value={item.id}>
              {item.name}
            </MenuItem>
          ))}
        </TextField>

        <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', sm: 'block' } }} />

        <Tooltip
          title={
            campaign
              ? `Modo de autonomia · atualizado ${new Date(campaign.updated_at).toLocaleTimeString('pt-PT')}`
              : 'Sem campanha selecionada'
          }
        >
          <Box sx={{ display: { xs: 'none', sm: 'inline-flex' } }}>
            <ControlModeChip
              mode={campaign?.control.mode ?? 'paused'}
              blocked={campaign?.control.kill_switch ?? false}
            />
          </Box>
        </Tooltip>

        <ConnectionBadge connection={connection} isMock={isMock} />

        <QuestionsBadgeButton pendingCount={pendingQuestions} onClick={onOpenQuestions} />

        <KillSwitchButton
          active={campaign?.control.kill_switch ?? false}
          onConfirm={onKillSwitch}
          busy={controlBusy}
        />

        <Stack sx={{ display: { xs: 'none', sm: 'flex' } }}>
          <ThemeModeToggle />
        </Stack>
      </Toolbar>
    </AppBar>
  );
}
