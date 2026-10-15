/**
 * Loading (skeleton) (docs/design-rpg-medieval.md §5.8): blocos do registry
 * `skeleton` sobre `--surface-raised`, cor **neutra** (nunca de marca), alturas
 * 12/16/24px com raio 6 (rampa `sm`), manter o layout do estado final para não
 * haver salto. Label PT junto do estado (invariante: nenhuma ação ativa sobre
 * estado desconhecido). Com reduced-motion o registry mantém o bloco estático.
 */
import { Skeleton } from '@/components/motion-ui/skeleton';

/** Alturas canónicas §5.8: 12px · 16px · 24px (raio 6 = `rounded-sm`). */
export type SkeletonHeight = 12 | 16 | 24;

const HEIGHT_CLASS: Record<SkeletonHeight, string> = {
  12: 'h-3',
  16: 'h-4',
  24: 'h-6',
};

export interface RpgSkeletonProps {
  /** Blocos empilhados; larguras relativas alternadas p/ aproximar o conteúdo. */
  heights?: SkeletonHeight[];
  /** Label PT anunciado a leitores de ecrã (role=status). */
  label?: string;
  className?: string;
}

export function RpgSkeleton({
  heights = [12, 16, 24],
  label = 'A carregar estado…',
  className,
}: RpgSkeletonProps) {
  return (
    <div
      role="status"
      aria-label={label}
      className={className ? `flex flex-col gap-2 ${className}` : 'flex flex-col gap-2'}
    >
      {heights.map((height, index) => (
        <Skeleton
          key={`${height}-${index}`}
          className={`${HEIGHT_CLASS[height]} rounded-sm${index % 2 === 0 ? ' w-full' : ' w-2/3'}`}
        />
      ))}
    </div>
  );
}
