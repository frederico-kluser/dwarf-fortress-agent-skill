import { useEffect, useRef, useState } from 'react';

/**
 * Série rolante para os sparklines: acrescenta uma amostra por cada nova chave
 * de amostragem (ex.: o `ts` das métricas), ignorando renders repetidos.
 */
export function useSparkSeries(
  sampleKey: string | undefined,
  value: number | undefined,
  max = 40,
): number[] {
  const [series, setSeries] = useState<number[]>([]);
  const lastKey = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (sampleKey === undefined || value === undefined || Number.isNaN(value)) return;
    if (lastKey.current === sampleKey) return;
    lastKey.current = sampleKey;
    // oxlint-disable-next-line react/set-state-in-effect -- acumular amostras de um fluxo externo (WS) é precisamente o caso de uso de um efeito
    setSeries((previous) => [...previous, value].slice(-max));
  }, [sampleKey, value, max]);

  return series;
}

/** Relógio grosseiro para etiquetas "há X s" / "última atualização". */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);

  return now;
}
