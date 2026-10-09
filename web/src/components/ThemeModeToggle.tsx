import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import BrightnessAuto from '@mui/icons-material/BrightnessAuto';
import DarkMode from '@mui/icons-material/DarkMode';
import LightMode from '@mui/icons-material/LightMode';
import { useColorScheme } from '@mui/material/styles';

/**
 * Alterna o esquema de cores (escuro por omissão).
 * `mode` é `undefined` no primeiro render — tratado antes de usar (evita
 * mismatch e o clássico "toggle sem valor").
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
    >
      <ToggleButton value="dark" aria-label="Esquema escuro">
        <Tooltip title="Escuro">
          <DarkMode fontSize="small" />
        </Tooltip>
      </ToggleButton>
      <ToggleButton value="system" aria-label="Esquema do sistema">
        <Tooltip title="Seguir o sistema">
          <BrightnessAuto fontSize="small" />
        </Tooltip>
      </ToggleButton>
      <ToggleButton value="light" aria-label="Esquema claro">
        <Tooltip title="Claro">
          <LightMode fontSize="small" />
        </Tooltip>
      </ToggleButton>
    </ToggleButtonGroup>
  );
}
