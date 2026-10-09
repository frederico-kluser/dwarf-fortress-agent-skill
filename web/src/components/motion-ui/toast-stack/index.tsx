"use client"

import { AnimatePresence, motion } from "motion/react"
import {
  Children,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ")
}

/** Id of a queued toast. Newest is highest. */
export type ToastItem = number

/** Headless toast queue returned by `useToastStack`. */
export interface UseToastStackResult {
  /** Toast ids, newest first. */
  toasts: ToastItem[]
  /** Push a toast onto the front of the queue. */
  add(): ToastItem
  /** Remove the toast with this id. */
  dismiss(id: ToastItem): void
}

/** Headless toast queue. Newest toasts are prepended. */
export function useToastStack(): UseToastStackResult {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(0)

  const add = useCallback<UseToastStackResult["add"]>(() => {
    const id = ++nextId.current
    setToasts((prev) => [id, ...prev])
    return id
  }, [])

  const dismiss = useCallback<UseToastStackResult["dismiss"]>((id) => {
    setToasts((prev) => prev.filter((t) => t !== id))
  }, [])

  return { toasts, add, dismiss }
}

interface ToastStackContextValue {
  maxVisible: number
  stackOffsetY: number
  stackScale: number
  stackOpacity: number
  calm: boolean
  still: boolean
  expanded: boolean
  expandedGap: number
  heights: number[]
  setHeight: (index: number, height: number) => void
  expand: () => void
  collapse: () => void
}

const COLLAPSE_DELAY_MS = 120

/* Distance from the front toast's resting place to this toast's, when fanned
 * out. Toasts can differ in height, so offsets add up the ones in front. */
function expandedOffset(heights: number[], index: number, gap: number): number {
  let offset = 0
  for (let i = 0; i < index; i++) offset += (heights[i] ?? heights[0] ?? 0) + gap
  return offset
}

const ToastStackContext = createContext<ToastStackContextValue | null>(null)
const ToastIndexContext = createContext(0)

interface ToastVisibility {
  isVisible: boolean
}

const ToastVisibilityContext = createContext<ToastVisibility>({ isVisible: true })

/** Whether the enclosing toast is in the visible window. Hidden toasts
 *  should drop interactive controls from the tab order. */
export function useToast(): ToastVisibility {
  return useContext(ToastVisibilityContext)
}

export interface ToastProps {
  /** Toast face. Toast only moves it. */
  children?: ReactNode
  /** Extra classes on the animated card. */
  className?: string
}

/** One stacked card. Hidden toasts stay mounted, aria-hidden, and pointer-inert. */
export function Toast({ children, className }: ToastProps) {
  const ctx = useContext(ToastStackContext)
  if (!ctx) throw new Error("Toast must be rendered inside <ToastStack>.")
  const index = useContext(ToastIndexContext)
  const {
    maxVisible,
    stackOffsetY,
    stackScale,
    stackOpacity,
    calm,
    still,
    expanded,
    expandedGap,
    heights,
    setHeight,
    expand,
    collapse,
  } = ctx
  const theme = useMotionUITheme()
  const ref = useRef<HTMLDivElement>(null)

  const settle = useMotionUITransition("ui")
  const exit = useMotionUITransition("snap")

  useEffect(() => {
    const node = ref.current
    if (!node || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => setHeight(index, node.offsetHeight))
    observer.observe(node)
    return () => observer.disconnect()
  }, [index, setHeight])

  const isVisible = index < maxVisible
  const y = expanded
    ? -expandedOffset(heights, index, expandedGap)
    : -index * stackOffsetY
  const scale = expanded ? 1 : 1 - index * stackScale
  const opacity = isVisible ? (expanded ? 1 : 1 - index * stackOpacity) : 0

  const enterRise = calm ? 0 : theme.travel.section
  const exitDrop = calm ? 0 : theme.travel.enter

  return (
    <ToastVisibilityContext.Provider value={{ isVisible }}>
      <motion.div
        ref={ref}
        onPointerEnter={isVisible ? expand : undefined}
        onPointerLeave={isVisible ? collapse : undefined}
        onFocus={expand}
        onBlur={collapse}
        className={cx("absolute bottom-0 left-0 w-full origin-bottom", className)}
        style={{
          zIndex: maxVisible - index,
          pointerEvents: isVisible ? "auto" : "none",
        }}
        initial={
          still
            ? false
            : {
                opacity: 0,
                transform: `translateY(${enterRise}px) scale(${calm ? 1 : 0.85})`,
              }
        }
        animate={{ opacity, transform: `translateY(${y}px) scale(${scale})` }}
        exit={
          still
            ? { opacity: 0, transition: { duration: 0 } }
            : {
                opacity: 0,
                transform: `translateY(${exitDrop}px) scale(${calm ? 1 : 0.8})`,
                // Do not coerce to type: "tween": opacity then leads transform.
                transition: { ...exit, delay: 0 },
              }
        }
        transition={{
          ...settle,
          delay: calm || expanded ? 0 : index * theme.stagger.tight,
        }}
        aria-hidden={isVisible ? undefined : true}
      >
        {children}
      </motion.div>
    </ToastVisibilityContext.Provider>
  )
}

export interface ToastStackProps {
  /** Toast children, newest first. */
  children?: ReactNode
  /** Visible count before the rest hide. Default 4. */
  maxVisible?: number
  /** Vertical offset per step, in px. Default 10. */
  stackOffsetY?: number
  /** Scale shed per step. Default 0.06. */
  stackScale?: number
  /** Opacity shed per step. Default 0.2. */
  stackOpacity?: number
  /** Gap between toasts, in px, while the stack is fanned out. Default 8. */
  expandedGap?: number
  /** Extra classes on the fixed viewport. */
  className?: string
}

/** Fixed stack viewport. Gaps are pointer-inert; each visible toast re-enables clicks. */
export function ToastStack({
  children,
  maxVisible = 4,
  stackOffsetY = 10,
  stackScale = 0.06,
  stackOpacity = 0.2,
  expandedGap = 8,
  className,
}: ToastStackProps) {
  const theme = useMotionUITheme()
  const still = theme.motionMode === "off"
  const calm = theme.motionMode === "calm"
  const [expanded, setExpanded] = useState(false)
  const [heights, setHeights] = useState<number[]>([])
  const setHeight = useCallback((index: number, height: number) => {
    setHeights((prev) => {
      if (prev[index] === height) return prev
      const next = [...prev]
      next[index] = height
      return next
    })
  }, [])
  const collapseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  /* The motion a plain toast region lacks: hover or focus fans the stack out
   * so every toast is readable, then it settles back. The short delay stops
   * it snapping shut while the pointer crosses the gap between toasts. */
  const expand = useCallback(() => {
    if (collapseTimer.current) clearTimeout(collapseTimer.current)
    setExpanded(true)
  }, [])
  const collapse = useCallback(() => {
    if (collapseTimer.current) clearTimeout(collapseTimer.current)
    collapseTimer.current = setTimeout(() => setExpanded(false), COLLAPSE_DELAY_MS)
  }, [])

  useEffect(
    () => () => {
      if (collapseTimer.current) clearTimeout(collapseTimer.current)
    },
    []
  )

  const items = Children.toArray(children)
    .filter(isValidElement)
    .slice(0, maxVisible + 2)

  const contextValue: ToastStackContextValue = {
    maxVisible,
    stackOffsetY,
    stackScale,
    stackOpacity,
    calm,
    still,
    expanded: expanded && items.length > 1 && (heights[0] ?? 0) > 0,
    expandedGap,
    heights,
    setHeight,
    expand,
    collapse,
  }

  return (
    <div
      className={cx(
        "pointer-events-none fixed inset-x-0 bottom-6 mx-auto w-[min(22rem,calc(100vw-2rem))]",
        className
      )}
      style={{ zIndex: maxVisible }}
    >
      <ToastStackContext.Provider value={contextValue}>
        <AnimatePresence initial={false}>
          {items.map((child, index) => (
            <ToastIndexContext.Provider key={child.key ?? index} value={index}>
              {child}
            </ToastIndexContext.Provider>
          ))}
        </AnimatePresence>
      </ToastStackContext.Provider>
    </div>
  )
}
