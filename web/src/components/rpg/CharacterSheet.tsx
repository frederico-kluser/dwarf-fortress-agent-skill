/**
 * Ficha de personagem (docs/design-rpg-medieval.md §5.4): placa `--surface-raised`
 * com nome do aventureiro em Cinzel, stats em grid mono `tabular-nums`, barras
 * HP/energia (track 8px, raio 4) com **valor numérico sempre visível** — a barra
 * nunca é a única leitura — e skills em linha com tendência opcional.
 * Sem atributos inventados fora do contrato (`docs/api.md` / game state).
 */
import type { ReactNode } from 'react';

import { ProgressBar } from '@/components/motion-ui/progress-bar';

export interface CharacterStat {
  label: string;
  value: ReactNode;
}

export interface CharacterVital {
  label: string;
  current: number;
  max: number;
}

export interface CharacterSkill {
  label: string;
  value: ReactNode;
  /** Tendência (sparkline do registry) — slot opcional. */
  trend?: ReactNode;
}

export interface CharacterSheetProps {
  /** Título Cinzel `--text-section` («Ficha de personagem» por omissão). */
  title?: string;
  /** Nome do aventureiro — `--text-title` Cinzel. */
  name: string;
  stats: CharacterStat[];
  vitals: CharacterVital[];
  skills: CharacterSkill[];
}

/**
 * Cor do fill por limiar (§5.4): ≥50% `--success` · 20–49% `--warning` ·
 * <20% `--danger` — mas o valor numérico ao lado é sempre a leitura primária.
 */
function vitalTone(current: number, max: number): string {
  const share = max > 0 ? current / max : 0;
  if (share >= 0.5) return 'var(--success)';
  if (share >= 0.2) return 'var(--warning)';
  return 'var(--danger)';
}

export function CharacterSheet({
  title = 'Ficha de personagem',
  name,
  stats,
  vitals,
  skills,
}: CharacterSheetProps) {
  return (
    <section className="placa-pergaminho p-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-section font-display font-semibold text-muted-foreground">{title}</h2>
        <p className="text-title font-display font-semibold text-foreground">{name}</p>
      </header>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-3 xl:grid-cols-4">
        {stats.map((stat) => (
          <div key={stat.label} className="flex flex-col gap-0.5">
            <dt className="text-micro font-medium text-muted-foreground">{stat.label}</dt>
            <dd className="text-counter font-mono font-medium tabular-nums text-foreground">
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>

      {vitals.length > 0 ? (
        <div className="mt-5 flex flex-col gap-3">
          {vitals.map((vital) => (
            <ProgressBar
              key={vital.label}
              value={vital.max > 0 ? vital.current / vital.max : 0}
              size="md"
              progressbar
              aria-label={`${vital.label}: ${vital.current} de ${vital.max}`}
              tone={vitalTone(vital.current, vital.max)}
              label={<span className="text-micro font-medium text-muted-foreground">{vital.label}</span>}
              valueLabel={
                <span className="text-data font-mono tabular-nums text-foreground">
                  {vital.current} / {vital.max}
                </span>
              }
            />
          ))}
        </div>
      ) : null}

      {skills.length > 0 ? (
        <dl className="mt-5 flex flex-col gap-2">
          {skills.map((skill) => (
            <div key={skill.label} className="flex items-center gap-3">
              <dt className="text-small min-w-0 flex-1 text-foreground">{skill.label}</dt>
              {skill.trend != null ? <span aria-hidden="true">{skill.trend}</span> : null}
              <dd className="text-data font-mono tabular-nums text-foreground">{skill.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
