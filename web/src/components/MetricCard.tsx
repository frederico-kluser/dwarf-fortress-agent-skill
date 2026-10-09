import type { ReactNode } from 'react';
import Typography from '@mui/material/Typography';
import Stack from '@mui/material/Stack';
import Box from '@mui/material/Box';

import { AnimatedNumber, type NumberFormat } from '@/components/motion-ui/animated-number';
import { MONO_FONT } from '../theme';
import Sparkline from './Sparkline';

export interface MetricCardProps {
  /** Nome do grupo/indicador. */
  title: string;
  /** Valor já formatado. */
  value: string;
  unit?: string;
  hint?: string;
  icon?: ReactNode;
  /** Série para o sparkline (opcional). */
  series?: number[];
  /** Cor CSS do indicador/sparkline. */
  color?: string;
  /** Marca o indicador como indisponível (ex.: confidence ausente). */
  unavailable?: boolean;
  /**
   * Valor numérico para o contador animado (Motion UI `AnimatedNumber`) —
   * ex.: gasto acumulado em USD. Quando presente, substitui o texto estático.
   */
  numericValue?: number;
  /** Formato `Intl.NumberFormat` do contador (moeda, casas decimais). */
  numericFormat?: NumberFormat;
  /** Texto antes do número do contador (ex.: `US$ `). */
  numericPrefix?: string;
}

/**
 * Cartão de métrica denso: valor grande, unidade pequena, sparkline opcional.
 * O invólucro é um cartão no vocabulário semântico Motion UI (`bg-card`,
 * `border-border`, `rounded-lg`); os números mutáveis contam com
 * `AnimatedNumber` (Motion+), que respeita reduced motion pelo tema.
 */
export default function MetricCard({
  title,
  value,
  unit,
  hint,
  icon,
  series,
  color = 'primary.main',
  unavailable = false,
  numericValue,
  numericFormat,
  numericPrefix,
}: MetricCardProps) {
  return (
    <div className="h-full rounded-lg border border-border bg-card text-card-foreground shadow-sm">
      <div className="p-3">
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', color, mb: 0.5 }}>
          {icon}
          <Typography variant="overline" sx={{ color: 'text.secondary' }}>
            {title}
          </Typography>
        </Stack>
        <Stack direction="row" spacing={0.5} sx={{ alignItems: 'baseline', flexWrap: 'wrap' }}>
          <Typography
            variant="h4"
            component="p"
            sx={{
              fontFamily: MONO_FONT,
              color: unavailable ? 'text.secondary' : 'text.primary',
            }}
          >
            {numericValue !== undefined && !unavailable ? (
              <AnimatedNumber
                value={numericValue}
                transition="lively"
                format={numericFormat}
                prefix={numericPrefix}
                locales="pt-PT"
              />
            ) : (
              value
            )}
          </Typography>
          {unit ? (
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {unit}
            </Typography>
          ) : null}
        </Stack>
        {hint ? (
          <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
            {hint}
          </Typography>
        ) : null}
        {series ? (
          <Box sx={{ mt: 1 }}>
            <Sparkline values={series} label={title} color={color} />
          </Box>
        ) : null}
      </div>
    </div>
  );
}
