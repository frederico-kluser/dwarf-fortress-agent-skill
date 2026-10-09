import type { ReactNode } from 'react';

export interface SectionCardProps {
  title: string;
  subtitle?: string;
  /** Ações à direita do cabeçalho (botões, chips, filtros). */
  action?: ReactNode;
  children: ReactNode;
  /** Nível do heading — mantém a hierarquia sem saltos (a11y). */
  headingLevel?: 'h2' | 'h3';
  /** Sem padding no conteúdo (para listas/tabelas que gerem o seu próprio espaçamento). */
  flush?: boolean;
}

/**
 * Bloco de secção: cabeçalho + conteúdo, sempre sobre um cartão.
 *
 * Cartão composto no vocabulário semântico Motion UI/shadcn (`bg-card`,
 * `border-border`, `rounded-lg`): o catálogo @motion não tem invólucro de cartão
 * base (só expansões — expand-card/tilt-card/card-stack), e a camada de tokens
 * do projeto resolve as cores nos dois esquemas.
 */
export default function SectionCard({
  title,
  subtitle,
  action,
  children,
  headingLevel = 'h2',
  flush = false,
}: SectionCardProps) {
  const Heading = headingLevel;
  return (
    <section className="rounded-lg border border-border bg-card text-card-foreground shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
        <div className="min-w-0">
          <Heading className="text-sm font-bold text-card-foreground">{title}</Heading>
          {subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        {action ? <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
      </header>
      <div className="border-t border-border">
        <div className={flush ? '' : 'p-4'}>{children}</div>
      </div>
    </section>
  );
}
