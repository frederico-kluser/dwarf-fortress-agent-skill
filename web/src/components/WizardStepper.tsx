import { motion } from 'motion/react';

import {
  useMotionUITheme,
  useMotionUITransition,
} from '@/components/motion-ui/ui-theme';
import { ProgressBar } from '@/components/motion-ui/progress-bar';

export interface WizardStepperProps {
  /** Rótulos dos passos, por ordem. */
  steps: string[];
  /** Índice do passo atual (base 0). */
  active: number;
  /** Nome acessível do conjunto de passos. */
  label: string;
}

/**
 * Stepper do wizard: passos com estado (concluído · atual · por fazer) sobre a
 * barra de progresso do registry Motion UI (`@motion/progress-bar`).
 *
 * Composição justificada (cascata, passo 3/4): o catálogo @motion não tem
 * stepper/wizard (buscas «stepper», «wizard», «steps» devolvem vazio) — o rótulo
 * de passos é composto em classes semânticas shadcn, com movimento só em
 * `transform`/`opacity` e transições do token do tema (reduced motion via
 * `motionMode`).
 */
export default function WizardStepper({ steps, active, label }: WizardStepperProps) {
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
          return (
            <li
              key={step}
              aria-current={state === 'current' ? 'step' : undefined}
              className="flex min-w-0 flex-1 items-center gap-2 last:flex-none"
            >
              <motion.span
                aria-hidden="true"
                className={
                  state === 'todo'
                    ? 'size-2 shrink-0 rounded-full bg-muted-foreground/40'
                    : 'size-2.5 shrink-0 rounded-full bg-primary'
                }
                initial={still ? false : { scale: 0.7, opacity: 0.6 }}
                animate={{ scale: state === 'current' ? 1 : 0.8, opacity: 1 }}
                transition={{ ...snap }}
              />
              <span
                className={
                  state === 'todo'
                    ? 'truncate text-xs font-medium text-muted-foreground'
                    : 'truncate text-xs font-semibold text-foreground'
                }
              >
                {step}
              </span>
              {index < steps.length - 1 ? (
                <span
                  aria-hidden="true"
                  className={`h-px min-w-3 flex-1 ${index < current ? 'bg-primary' : 'bg-border'}`}
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
