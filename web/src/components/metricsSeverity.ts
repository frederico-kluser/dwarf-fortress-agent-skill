import { severityTone } from '../api/format';

/**
 * Severidade de um grupo de métricas — define a ORDEM dos cartões no ecrã
 * (auditoria F.1.1.02: os cartões tinham todos o mesmo peso visual e a ordem do
 * contrato, pelo que «ameaças» e «inventário» apareciam lado a lado).
 *
 * Fica fora dos componentes para não misturar constantes com ficheiros de UI.
 */
export type Severity = 'critico' | 'aviso' | 'atencao' | 'informativo';

export const SEVERITY_RANK: Record<Severity, number> = {
  critico: 0,
  aviso: 1,
  atencao: 2,
  informativo: 3,
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critico: 'crítico',
  aviso: 'aviso',
  atencao: 'atenção',
  informativo: 'informativo',
};

/** Cor da moldura/ícone do grupo, por severidade. */
export const SEVERITY_COLOR: Record<Severity, string> = {
  critico: 'error.main',
  aviso: 'warning.main',
  atencao: 'info.main',
  informativo: 'divider',
};

/** Tom do `severityTone` (vocabulário EN do contrato) → severidade de grupo. */
export function severityFromTone(tone: string): Severity {
  if (tone === 'error') return 'critico';
  if (tone === 'warning') return 'aviso';
  return 'informativo';
}

/** Pior severidade entre vários valores do contrato (ex.: fome/sede/sono). */
export function worstSeverity(values: string[]): Severity {
  const severities = values.map((value) => severityFromTone(severityTone(value)));
  if (severities.includes('critico')) return 'critico';
  if (severities.includes('aviso')) return 'aviso';
  return 'informativo';
}
