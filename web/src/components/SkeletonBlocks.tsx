import { Skeleton } from '@/components/motion-ui/skeleton';

/**
 * Placeholders com shimmer do registry Motion UI (`@motion/skeleton`) para as
 * superfícies vivas — métricas e gastos — enquanto o primeiro carregamento
 * corre. Formato aproximado do conteúdo real: a transição não salta.
 */

/** Tira de sinais vitais: 5 blocos densos. */
export function VitalsSkeleton() {
  return (
    <div
      role="status"
      aria-label="A carregar métricas…"
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
    >
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} className="rounded-lg border border-border bg-card p-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-2 h-6 w-20" />
          <Skeleton className="mt-2 h-3 w-full" />
        </div>
      ))}
    </div>
  );
}

/** Bloco de gastos: total em destaque + linhas da tabela por fornecedor. */
export function SpendSkeleton() {
  return (
    <div role="status" aria-label="A carregar gastos…" className="flex flex-col gap-2 p-4">
      <Skeleton className="h-3 w-28" />
      <Skeleton className="h-7 w-48" />
      <Skeleton className="mt-2 h-9 w-full" />
      <Skeleton className="h-9 w-full" />
      <Skeleton className="h-9 w-full" />
    </div>
  );
}

/** Grelha de cartões de métrica (indicadores + sparkline). */
export function MetricCardsSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div
      role="status"
      aria-label="A carregar indicadores…"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5"
    >
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="rounded-lg border border-border bg-card p-3">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="mt-2 h-6 w-24" />
          <Skeleton className="mt-2 h-9 w-full" />
        </div>
      ))}
    </div>
  );
}
