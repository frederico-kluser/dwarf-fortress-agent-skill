"use client"

import { animate, AnimatePresence, motion } from "motion/react"
import { useEffect, useRef, type ReactNode } from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"

const SHAKE_TRAVEL_FACTOR = 1.5
const CROSSFADE_BLUR = 6

/** Settle feedback when state changes. */
export type MultiStateFeedback = "none" | "shake" | "pop"

export interface MultiStateButtonProps {
  /** Current state key owned by the consumer. */
  state: string
  /** Visible label for the current state. */
  children: ReactNode
  /** Optional leading glyph for the current state. */
  icon?: ReactNode
  /** Surface classes for the current state. */
  surfaceClassName?: string
  /** Constant pill styling in every state. */
  pillClassName?: string
  /** Feedback fired when `state` settles. */
  feedback?: MultiStateFeedback
  /** Whether the pill animates width via layout projection. */
  widthMorph?: boolean
  /** Accessible status text via internal `aria-live`. */
  announce?: string
  /** Accessible button name. */
  "aria-label"?: string
  /** Button type. Default `"button"`. */
  type?: "button" | "submit"
  /** Disables the button. */
  disabled?: boolean
  /** Click handler. */
  onClick?: () => void
  /** Merged onto the button root. */
  className?: string
}

/** Pill button with content crossfade, width morph and optional settle feedback. */
export function MultiStateButton({
  state,
  children,
  icon,
  surfaceClassName = "bg-primary text-primary-foreground",
  pillClassName = "rounded-full px-5 py-3 text-sm font-medium shadow-sm",
  feedback = "none",
  widthMorph = true,
  announce,
  "aria-label": ariaLabel,
  type = "button",
  disabled,
  onClick,
  className,
}: MultiStateButtonProps) {
  const uiTheme = useMotionUITheme()
  const still = uiTheme.motionMode === "off"
  const motionAllowed = uiTheme.motionMode === "full"

  const snap = useMotionUITransition("snap")
  const ui = useMotionUITransition("ui")
  const lively = useMotionUITransition("lively")

  // Shake/pop on a wrapper, never the layout element.
  const feedbackRef = useRef<HTMLDivElement>(null)
  const shakeX = uiTheme.travel.hover * SHAKE_TRAVEL_FACTOR

  useEffect(() => {
    const node = feedbackRef.current
    if (!node || !motionAllowed || feedback === "none") return
    if (feedback === "shake") {
      animate(
        node,
        { x: [0, -shakeX, shakeX, -shakeX, 0] },
        { duration: ui.duration, ease: "easeInOut", times: [0, 0.25, 0.5, 0.75, 1] },
      )
    } else if (feedback === "pop") {
      animate(
        node,
        { scale: [1, 1.2, 1] },
        { duration: lively.duration, ease: "easeInOut", times: [0, 0.5, 1] },
      )
    }
  }, [state, feedback, motionAllowed, shakeX, ui.duration, lively.duration])

  const layoutOn = widthMorph && !still

  const contentInitial = still
    ? false
    : motionAllowed
      ? { opacity: 0, filter: `blur(${CROSSFADE_BLUR}px)` }
      : { opacity: 0 }
  const contentAnimate = motionAllowed
    ? { opacity: 1, filter: "blur(0px)" }
    : { opacity: 1 }
  const contentExit = motionAllowed
    ? { opacity: 0, filter: `blur(${CROSSFADE_BLUR}px)` }
    : { opacity: 0 }
  const contentTransition = still
    ? { duration: 0 }
    : motionAllowed
      ? { ...ui }
      : { type: "tween" as const, duration: ui.opacity.duration, ease: ui.opacity.ease }

  return (
    <>
      <motion.button
        type={type}
        onClick={onClick}
        disabled={disabled}
        aria-label={ariaLabel}
        className={`inline-flex rounded-full disabled:pointer-events-none ${FOCUS_RING}${className ? ` ${className}` : ""}`}
        whileTap={motionAllowed && !disabled ? { scale: 0.97 } : undefined}
        transition={{ ...snap }}
      >
        <motion.div ref={feedbackRef} className="inline-flex">
          <motion.div
            layout={layoutOn}
            transition={{ ...snap }}
            className={`relative flex items-center overflow-hidden transition-colors duration-[var(--motion-ui-transition-snap-duration)] ease-[var(--motion-ui-transition-snap)] ${pillClassName} ${surfaceClassName}`}
          >
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={state}
                className="flex items-center gap-2 whitespace-nowrap"
                initial={contentInitial}
                animate={contentAnimate}
                exit={contentExit}
                transition={contentTransition}
              >
                {icon != null && (
                  <span
                    aria-hidden="true"
                    className="flex shrink-0 items-center justify-center"
                  >
                    {icon}
                  </span>
                )}
                <span className="block">{children}</span>
              </motion.span>
            </AnimatePresence>
          </motion.div>
        </motion.div>
      </motion.button>

      {announce !== undefined && (
        <span aria-live="polite" className="sr-only">
          {announce}
        </span>
      )}
    </>
  )
}
