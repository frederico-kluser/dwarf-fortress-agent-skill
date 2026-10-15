"use client"

import {
  AnimatePresence,
  animate,
  motion,
  useMotionValue,
  useTransform,
} from "motion/react"
import type { AnimationPlaybackControls, MotionValue } from "motion/react"
import { useRef, useState, type ReactNode } from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"

const HELD_SCALE = 0.8

export interface UseHoldToConfirmOptions {
  /** Seconds to hold for confirmation. Default 2. */
  holdSeconds?: number
  /** Fired when a hold completes. */
  onConfirm?: () => void
  /** Fired when a hold is released early. */
  onCancel?: () => void
}

export interface UseHoldToConfirmResult {
  /** 0 at rest, 1 confirmed. */
  progress: MotionValue<number>
  /** Snap progress back to 0 and clear the completed lock. */
  reset: () => void
  /** Spread onto the interactive element. */
  holdHandlers: {
    onPointerDown: (event: React.PointerEvent<HTMLButtonElement>) => void
    onPointerUp: (event: React.PointerEvent<HTMLButtonElement>) => void
    onPointerCancel: (event: React.PointerEvent<HTMLButtonElement>) => void
    onPointerLeave: (event: React.PointerEvent<HTMLButtonElement>) => void
    onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void
    onKeyUp: (event: React.KeyboardEvent<HTMLButtonElement>) => void
    onContextMenu: (event: React.MouseEvent<HTMLButtonElement>) => void
  }
}

/** Headless press-and-hold gesture with a `progress` MotionValue. */
export function useHoldToConfirm({
  holdSeconds = 2,
  onConfirm,
  onCancel,
}: UseHoldToConfirmOptions = {}): UseHoldToConfirmResult {
  const snapTransition = useMotionUITransition("snap")

  const progress = useMotionValue(0)
  const holdAnim = useRef<AnimationPlaybackControls | null>(null)
  const holding = useRef(false)
  const done = useRef(false)

  const startHold = () => {
    if (done.current || holding.current) return
    holding.current = true
    holdAnim.current?.stop()
    progress.set(0)
    // easeOut ramp: progress must reach 1 exactly at hold completion.
    holdAnim.current = animate(progress, 1, {
      duration: holdSeconds,
      ease: "easeOut",
      onComplete: () => {
        holding.current = false
        done.current = true
        onConfirm?.()
      },
    })
  }

  const cancelHold = () => {
    if (!holding.current) return
    holding.current = false
    holdAnim.current?.stop()
    holdAnim.current = animate(progress, 0, { ...snapTransition, type: "tween" })
    onCancel?.()
  }

  const reset = () => {
    holdAnim.current?.stop()
    holding.current = false
    done.current = false
    progress.set(0)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.repeat) return
    if (event.key === " ") {
      event.preventDefault()
      startHold()
    }
  }

  const handleKeyUp = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === " ") {
      event.preventDefault()
      cancelHold()
    }
  }

  return {
    progress,
    reset,
    holdHandlers: {
      onPointerDown: (event) => {
        event.currentTarget.setPointerCapture?.(event.pointerId)
        startHold()
      },
      onPointerUp: cancelHold,
      onPointerCancel: cancelHold,
      onPointerLeave: cancelHold,
      onKeyDown: handleKeyDown,
      onKeyUp: handleKeyUp,
      onContextMenu: (event) => event.preventDefault(),
    },
  }
}

export interface HoldToConfirmButtonProps {
  /** Seconds to hold. Default 2. */
  holdSeconds?: number
  /** Post-confirmation behaviour. Default `"callback"`. */
  mode?: "callback" | "success"
  /** Label in `"success"` mode. Default `"Confirmed"`. */
  successLabel?: ReactNode
  /** Fired when the hold completes. */
  onConfirm?: () => void
  /** Fired when the hold is released early. */
  onCancel?: () => void
  /** Button label, rendered in both progress layers. */
  children?: ReactNode
  /** Merged onto the button. */
  className?: string
  /** Wired to `aria-describedby`. */
  "aria-describedby"?: string
}

/** Destructive hold button with left-to-right fill wipe. */
export function HoldToConfirmButton({
  holdSeconds = 2,
  mode = "callback",
  successLabel = "Confirmed",
  onConfirm,
  onCancel,
  children,
  className,
  "aria-describedby": ariaDescribedby,
}: HoldToConfirmButtonProps) {
  const { motionMode } = useMotionUITheme()
  const still = motionMode === "off"
  const calm = motionMode === "calm"
  const motionAllowed = motionMode === "full"
  const successTransition = useMotionUITransition("ui")
  const restoreTransition = useMotionUITransition("snap")
  const successScale = useMotionValue(1)
  const [confirmed, setConfirmed] = useState(false)
  const { progress, holdHandlers } = useHoldToConfirm({
    holdSeconds,
    onConfirm: () => {
      if (mode === "success") {
        setConfirmed(true)
        animate(successScale, motionAllowed ? 1 / HELD_SCALE : 1, {
          ...restoreTransition,
        })
      }
      onConfirm?.()
    },
    onCancel,
  })

  const holdScale = useTransform(
    progress,
    [0, 1],
    [1, motionAllowed ? HELD_SCALE : 1]
  )
  const buttonScale = useTransform(
    () => holdScale.get() * successScale.get()
  )
  const fillClip = useTransform(
    progress,
    [0, 1],
    ["inset(0 100% 0 0)", "inset(0 0% 0 0)"]
  )
  const successInitial = still
    ? false
    : calm
      ? { opacity: 0 }
      : { opacity: 1, clipPath: "inset(0 100% 0 0)" }
  const successAnimate = calm
    ? { opacity: 1 }
    : { opacity: 1, clipPath: "inset(0 0% 0 0)" }
  const successSwapTransition = still
    ? { duration: 0 }
    : calm
      ? {
          duration: successTransition.opacity.duration,
          ease: successTransition.opacity.ease,
        }
      : successTransition
  const successComplete = mode === "success" && confirmed

  return (
    <motion.button
      type="button"
      aria-describedby={ariaDescribedby}
      aria-disabled={successComplete || undefined}
      {...(successComplete ? {} : holdHandlers)}
      style={{ scale: buttonScale }}
      className={`relative z-0 inline-flex h-[3.25rem] w-60 select-none touch-none items-center justify-center overflow-hidden rounded-full bg-secondary text-sm font-medium text-secondary-foreground shadow-sm [-webkit-tap-highlight-color:transparent] [-webkit-touch-callout:none] ${FOCUS_RING}${className ? ` ${className}` : ""}`}
    >
      <span
        aria-hidden={successComplete || undefined}
        className="relative z-10 inline-flex items-center gap-2"
      >
        {children}
      </span>
      <motion.span
        aria-hidden="true"
        style={{ clipPath: fillClip }}
        className="pointer-events-none absolute inset-0 z-20 inline-flex items-center justify-center gap-2 bg-destructive text-destructive-foreground"
      >
        {children}
      </motion.span>
      <AnimatePresence initial={false}>
        {successComplete ? (
          <motion.span
            key="success"
            role="status"
            initial={successInitial}
            animate={successAnimate}
            transition={successSwapTransition}
            className="pointer-events-none absolute inset-0 z-30 inline-flex items-center justify-center gap-2 bg-primary text-primary-foreground"
          >
            <SuccessIcon />
            {successLabel}
          </motion.span>
        ) : null}
      </AnimatePresence>
    </motion.button>
  )
}

function SuccessIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}
