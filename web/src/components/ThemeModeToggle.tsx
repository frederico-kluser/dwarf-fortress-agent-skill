import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import BrightnessAuto from '@mui/icons-material/BrightnessAuto';
import DarkMode from '@mui/icons-material/DarkMode';
import LightMode from '@mui/icons-material/LightMode';
import { useColorScheme } from '@mui/material/styles';

/**
 * Alterna o esquema de cores do spec §2 — escuro (basalto, por omissão) e claro
 * (pergaminho), mais «seguir o sistema». Contorno `--border-strong` (regra §2.3.4),
 * segmento ativo em fill `--primary` com `--on-solid` (regra §2.3.2) e alvos
 * ≥ 40×40px (auditoria I.2.2.02). `mode` é `undefined` no primeiro render —
 * tratado antes de usar (evita mismatch e o clássico "toggle sem valor").
 */
export default function ThemeModeToggle() {
  const { mode, setMode } = useColorScheme();

  if (!mode) {
    // Primeiro render: ainda não há esquema resolvido.
    return null;
  }

  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      value={mode}
      onChange={(_event, next) => {
        if (next !== null) setMode(next);
      }}
      aria-label="Esquema de cores"
      sx={{
        borderColor: 'var(--border-strong)',
        borderRadius: 'var(--radius-md)',
        backgroundColor: 'var(--surface-raised)',
        '& .MuiToggleButton-root': {
          minWidth: 40,
          minHeight: 40,
          px: 1,
          border: 'none',
          color: 'var(--muted)',
          '&.Mui-selected, &.Mui-selected:hover': {
            backgroundColor: 'var(--primary)',
            color: 'var(--on-solid)',
          },
        },
      }}
    >
      <ToggleButton value="dark" aria-label="Esquema escuro">
        <Tooltip title="Escuro (basalto)">
          <DarkMode fontSize="small" />
        </Tooltip>
      </ToggleButton>
      <ToggleButton value="system" aria-label="Esquema do sistema">
        <Tooltip title="Seguir o sistema">
          <BrightnessAuto fontSize="small" />
        </Tooltip>
      </ToggleButton>
      <ToggleButton value="light" aria-label="Esquema claro">
        <Tooltip title="Claro (pergaminho)">
          <LightMode fontSize="small" />
        </Tooltip>
      </ToggleButton>
    </ToggleButtonGroup>
  );
}
