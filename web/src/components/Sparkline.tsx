import { useMemo } from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

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

/**
 * Sparkline em SVG puro (sem biblioteca de gráficos).
 * Usa `currentColor` para funcionar com as CSS vars do tema nos dois esquemas.
 */
export default function Sparkline({
  values,
  label,
  width = 132,
  height = 34,
  color,
  showArea = true,
}: SparklineProps) {
  const geometry = useMemo(() => {
    if (values.length === 0) return null;
    const max = Math.max(...values);
    const min = Math.min(...values);
    const span = max - min || Math.abs(max) || 1;
    const step = values.length > 1 ? width / (values.length - 1) : 0;
    const points = values.map((value, index) => {
      const x = index * step;
      const y = height - 2 - ((value - min) / span) * (height - 4);
      return [x, y] as const;
    });
    const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `0,${height} ${line} ${width},${height}`;
    const last = points[points.length - 1];
    return { line, area, last };
  }, [values, width, height]);

  return (
    <Box sx={{ color: color ?? 'primary.main', lineHeight: 0 }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${label}: ${values.length} amostras`}
        focusable="false"
      >
        {geometry ? (
          <>
            {showArea ? <polygon points={geometry.area} fill="currentColor" opacity={0.16} /> : null}
            <polyline
              points={geometry.line}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.6}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {geometry.last ? (
              <circle cx={geometry.last[0]} cy={geometry.last[1]} r={2.2} fill="currentColor" />
            ) : null}
          </>
        ) : (
          <line
            x1={0}
            y1={height - 2}
            x2={width}
            y2={height - 2}
            stroke="currentColor"
            strokeWidth={1}
            strokeDasharray="3 3"
            opacity={0.5}
          />
        )}
      </svg>
      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
        {values.length > 0 ? `${values.length} amostras` : 'sem amostras'}
      </Typography>
    </Box>
  );
}
