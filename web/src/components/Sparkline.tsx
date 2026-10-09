import type { CSSProperties } from 'react';
import { Sparkline as MotionSparkline } from '@/components/motion-ui/sparkline';

export interface SparklineProps {
  /** Amostras, da mais antiga para a mais recente. */
  values: number[];
  /** Rótulo acessível (o SVG é decorativo do ponto de vista do texto). */
  label: string;
  width?: number;
  height?: number;
  /** Cor CSS — por default herda `currentColor` do contentor. */
  color?: string;
  showArea?: boolean;
}

/** Caminho do token de cor MUI para a variável CSS equivalente. */
const PALETTE_VAR: Record<string, string> = {
  'primary.main': 'var(--mui-palette-primary-main)',
  'secondary.main': 'var(--mui-palette-secondary-main)',
  'success.main': 'var(--mui-palette-success-main)',
  'warning.main': 'var(--mui-palette-warning-main)',
  'error.main': 'var(--mui-palette-error-main)',
  'info.main': 'var(--mui-palette-info-main)',
};

/**
 * Adaptador do sparkline do registry Motion UI (`@motion/sparkline`) para a API
 * densa das métricas: desenha a entrada, morfa com valores ao vivo e respeita
 * reduced motion pelo tema. A cor do indicador entra como `--primary` local — o
 * componente do registry lê `var(--primary)` e continua «theme-dumb».
 */
export default function Sparkline({
  values,
  label,
  width = 132,
  height = 34,
  color,
  showArea = true,
}: SparklineProps) {
  const tone = PALETTE_VAR[color ?? ''] ?? color ?? 'var(--mui-palette-primary-main)';
  return (
    <div className="leading-none" style={{ '--primary': tone } as CSSProperties}>
      <MotionSparkline
        history={values}
        width={width}
        height={height}
        tone="primary"
        area={showArea}
        dot
        draw
        label={`${label}: ${values.length} amostras`}
      />
      <p className="mt-0.5 text-xs text-muted-foreground">
        {values.length > 0 ? `${values.length} amostras` : 'sem amostras'}
      </p>
    </div>
  );
}
