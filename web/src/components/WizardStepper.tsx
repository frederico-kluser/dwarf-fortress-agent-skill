import { motion } from 'motion/react';

import {
  useMotionUITheme,
  useMotionUITransition,
} from '@/components/motion-ui/ui-theme';
import { ProgressBar } from '@/components/motion-ui/progress-bar';
import {
  RunaGlyph,
  type RunaGlyphName,
} from './rpg';
import { TOKEN, runaType } from './RunaKit';

export interface WizardStepperProps {
  /** Rótulos dos passos, por ordem. */
  steps: string[];
  /** Índice do passo atual (base 0). */
  active: number;
  /** Nome acessível do conjunto de passos. */
  label: string;
  /** Glifo runa por etapa (spec §8: map · key · hourglass) — alinhado com `steps`. */
  glyphs?: RunaGlyphName[];
}

/**
 * Stepper do wizard: passos com estado (concluído · atual · por fazer) sobre a
 * barra de progresso do registry Motion UI (`@motion/progress-bar`).
 *
 * Tema «RPG medieval» (spec §8): rótulos de passo em overline Cinzel
 * (`--text-overline`), glifo runa por etapa e marca de estado em forma —
 * losango para passos feitos/atual, círculo oco para por fazer (o estado nunca
 * depende só da cor: peso do rótulo + `aria-current="step"` acompanham).
 *
 * Composição justificada (cascata, passo 3/4): o catálogo @motion não tem
 * stepper/wizard (buscas «stepper», «wizard», «steps» devolvem vazio) — o rótulo
 * de passos é composto em classes semânticas shadcn, com movimento só em
 * `transform`/`opacity` e transições do token do tema (reduced motion via
 * `motionMode`).
 */
export default function WizardStepper({ steps, active, label, glyphs }: WizardStepperProps) {
  const { motionMode } = useMotionUITheme();
  const snap = useMotionUITransition('snap');
  const still = motionMode === 'off';
  const current = Math.min(Math.max(active, 0), steps.length - 1);
  const progress = steps.length > 1 ? current / (steps.length - 1) : 1;

  return (
    <div className="flex flex-col gap-2">
      <ol aria-label={label} className="flex items-center gap-2">
        {steps.map((step, index) => {
          const state = index < current ? 'done' : index === current ? 'current' : 'todo';
          const glyph = glyphs?.[index];
          return (
            <li
              key={step}
              aria-current={state === 'current' ? 'step' : undefined}
              className="flex min-w-0 flex-1 items-center gap-2 last:flex-none"
            >
              <motion.span
                aria-hidden="true"
                className="flex shrink-0 items-center justify-center"
                initial={still ? false : { scale: 0.7, opacity: 0.6 }}
                animate={{ scale: state === 'current' ? 1 : 0.8, opacity: 1 }}
                transition={{ ...snap }}
              >
                {state === 'todo' ? (
                  <span
                    className="size-2 rounded-full border"
                    style={{ borderColor: TOKEN.mutedForeground, opacity: 0.5 }}
                  />
                ) : (
                  <span
                    style={{ color: state === 'current' ? TOKEN.primary : TOKEN.accent }}
                  >
                    <RunaGlyph name="diamond" size={state === 'current' ? 12 : 10} />
                  </span>
                )}
              </motion.span>
              {glyph ? (
                <span
                  aria-hidden="true"
                  style={{
                    color: state === 'todo' ? TOKEN.mutedForeground : TOKEN.foreground,
                    opacity: state === 'todo' ? 0.5 : 1,
                  }}
                >
                  <RunaGlyph name={glyph} size={16} />
                </span>
              ) : null}
              <span
                className="truncate"
                style={{
                  ...runaType('overline'),
                  color: state === 'todo' ? TOKEN.mutedForeground : TOKEN.foreground,
                  opacity: state === 'done' ? 0.8 : 1,
                }}
              >
                {step}
              </span>
              {index < steps.length - 1 ? (
                <span
                  aria-hidden="true"
                  className="h-px min-w-3 flex-1"
                  style={{
                    backgroundColor: index < current ? TOKEN.primary : TOKEN.border,
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
      <ProgressBar
        value={progress}
        highlight
        size="sm"
        progressbar
        aria-label={label}
      />
    </div>
  );
}
