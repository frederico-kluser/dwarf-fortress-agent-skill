import { motion, type MotionProps, type MotionStyle, type MotionValue } from "motion/react"
import { useEffect, useRef, type ReactNode, type RefObject } from "react"

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Tabbable descendants of `container` in document order. */
export function getFocusableElements(
  container: HTMLElement | null
): HTMLElement[] {
  if (!container) return []
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

/** Options for {@link useFocusTrap}. */
export interface FocusTrapOptions {
  /** Whether the trap is engaged. */
  active: boolean
  /** Ref to the overlay panel. */
  container: RefObject<HTMLElement | null>
  /** Called when Escape is pressed. */
  onEscape?: () => void
  /** Element focused when the trap engages. */
  initialFocus?: RefObject<HTMLElement | null>
  /** Restore focus to the trigger on close. Default `true`. */
  restoreFocus?: boolean
}

/** Trap keyboard focus inside an open overlay. */
export function useFocusTrap({
  active,
  container,
  onEscape,
  initialFocus,
  restoreFocus = true,
}: FocusTrapOptions): void {
  const onEscapeRef = useRef(onEscape)
  onEscapeRef.current = onEscape

  useEffect(() => {
    if (!active) return
    const node = container.current
    const previouslyFocused = document.activeElement as HTMLElement | null

    const raf = requestAnimationFrame(() => {
      const target =
        initialFocus?.current ?? getFocusableElements(node)[0] ?? node
      target?.focus({ preventScroll: true })
    })

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        onEscapeRef.current?.()
        return
      }
      if (event.key !== "Tab" || !node) return
      const focusables = getFocusableElements(node)
      if (focusables.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const activeEl = document.activeElement
      const inside = node.contains(activeEl)
      if (event.shiftKey) {
        if (activeEl === first || !inside) {
          event.preventDefault()
          last.focus()
        }
      } else if (activeEl === last || !inside) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener("keydown", onKeyDown)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener("keydown", onKeyDown)
      if (restoreFocus) previouslyFocused?.focus?.({ preventScroll: true })
    }
  }, [active, container, initialFocus, restoreFocus])
}

/** Freeze background scroll while `active`. */
export function useScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active || typeof document === "undefined") return
    const root = document.documentElement
    const previousOverflow = root.style.overflow
    root.style.overflow = "hidden"
    return () => {
      root.style.overflow = previousOverflow
    }
  }, [active])
}

export interface BackdropProps
  extends Pick<MotionProps, "initial" | "animate" | "exit" | "transition"> {
  /** Scrim opacity as a number or bound MotionValue. */
  opacity?: number | MotionValue<number>
  /** Click-to-dismiss handler. */
  onClick?: () => void
  /** When set, renders as a labelled dismiss button. */
  label?: string
  /** Scrim surface class. Default `bg-background`. */
  className?: string
  /** Extra inline styles. */
  style?: MotionStyle
  /** Content inside the scrim. */
  children?: ReactNode
}

/** Dimming scrim behind an open overlay. */
export function Backdrop({
  opacity,
  onClick,
  label,
  className,
  style,
  children,
  initial,
  animate,
  exit,
  transition,
}: BackdropProps) {
  const mergedStyle: MotionStyle =
    opacity !== undefined ? { ...style, opacity } : { ...style }
  const surfaceClass = `absolute inset-0 ${className ?? "bg-background"}`

  if (label) {
    return (
      <motion.button
        type="button"
        aria-label={label}
        onClick={onClick}
        className={surfaceClass}
        style={mergedStyle}
        initial={initial}
        animate={animate}
        exit={exit}
        transition={transition}
      >
        {children}
      </motion.button>
    )
  }

  return (
    <motion.div
      aria-hidden="true"
      onClick={onClick}
      className={surfaceClass}
      style={mergedStyle}
      initial={initial}
      animate={animate}
      exit={exit}
      transition={transition}
    >
      {children}
    </motion.div>
  )
}
