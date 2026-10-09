import type { ReactNode } from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';

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
}

/** Cartão de métrica denso: valor grande, unidade pequena, sparkline opcional. */
export default function MetricCard({
  title,
  value,
  unit,
  hint,
  icon,
  series,
  color = 'primary.main',
  unavailable = false,
}: MetricCardProps) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent sx={{ p: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: 'center', color, mb: 0.5 }}
        >
          {icon}
          <Typography variant="overline" sx={{ color: 'text.secondary' }}>
            {title}
          </Typography>
        </Stack>
        <Stack
          direction="row"
          spacing={0.5}
          sx={{ alignItems: 'baseline', flexWrap: 'wrap' }}
        >
          <Typography
            variant="h4"
            component="p"
            sx={{
              fontFamily: MONO_FONT,
              color: unavailable ? 'text.secondary' : 'text.primary',
            }}
          >
            {value}
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
      </CardContent>
    </Card>
  );
}
