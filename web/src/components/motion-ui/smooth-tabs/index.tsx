"use client"

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"
import { AnimatePresence, motion } from "motion/react"
import {
  Children,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"
import type { UITransition } from "@/components/motion-ui/ui-theme"

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"

/** The panel crossfade transition. */
type PanelTransition =
  | { duration: number }
  | { type: "tween"; duration: number; ease: UITransition["ease"] }

interface SmoothTabsContextValue {
  /** The currently selected tab's value. */
  value: string
  /** Select a tab by value. */
  selectValue: (value: string) => void
  /** Sign of the last change (+1 forward, -1 back, 0 initial). */
  direction: number
  /** The tablist container for direction computation. */
  listRef: RefObject<HTMLDivElement | null>
  /** The shared-layout id the sliding pill animates under. */
  indicatorLayoutId: string
  /** The theme transition the pill slides on. */
  snapTransition: UITransition
  /** Whether the pill should slide. */
  animateIndicator: boolean
  /** The theme transition the panel viewport resizes on. */
  heightTransition: UITransition
  /** Horizontal slide distance (px) for the panel crossfade. */
  slide: number
  /** Resting opacity of an off-stage panel. */
  restOpacity: number
  /** Resting blur of an off-stage panel. */
  restBlur: string
  /** The enter/active transition. */
  enterTransition: PanelTransition
  /** The exit transition. */
  exitTransition: PanelTransition
  /** Custom passed to AnimatePresence + each panel. */
  presenceCustom: number
}

const SmoothTabsContext = createContext<SmoothTabsContextValue | null>(null)

function useContextOrThrow(who: string): SmoothTabsContextValue {
  const ctx = useContext(SmoothTabsContext)
  if (!ctx) throw new Error(`${who} must be rendered inside <SmoothTabs>.`)
  return ctx
}

/** Read the active tab value and a selector from outside the parts. */
export function useSmoothTabsContext(): {
  /** The currently selected tab's value. */
  value: string
  /** Select a tab by value. */
  select: (value: string) => void
} {
  const ctx = useContextOrThrow("useSmoothTabsContext")
  return { value: ctx.value, select: ctx.selectValue }
}

export interface SmoothTabsProps {
  /** The uncontrolled initial selected tab value. */
  defaultValue?: string
  /** The selected tab value (controlled). */
  value?: string
  /** Called when the selection changes. */
  onValueChange?: (value: string) => void
  /** Horizontal distance (px) the panel slides during the crossfade. Default 50. */
  contentOffsetX?: number
  /** Merged onto the root wrapper. */
  className?: string
  /** `SmoothTabsList` and `SmoothTabsPanels`. */
  children?: ReactNode
}

/** Tabs root with a sliding indicator and directional panel crossfade. */
export function SmoothTabs({
  defaultValue = "",
  value: controlledValue,
  onValueChange,
  contentOffsetX = 50,
  className,
  children,
}: SmoothTabsProps) {
  const baseId = useId()
  const listRef = useRef<HTMLDivElement>(null)
  const [internalValue, setInternalValue] = useState(defaultValue)
  const [direction, setDirection] = useState(0)

  const value = controlledValue ?? internalValue

  const { motionMode } = useMotionUITheme()
  const still = motionMode === "off"
  const motionAllowed = motionMode === "full"
  const snapTransition = useMotionUITransition("snap")
  const uiTransition = useMotionUITransition("ui")

  const selectValue = useCallback(
    (next: string) => {
      let dir = 0
      const list = listRef.current
      if (list) {
        const values = Array.from(
          list.querySelectorAll<HTMLElement>('[role="tab"]')
        ).map((el) => el.dataset.value)
        const from = values.indexOf(value)
        const to = values.indexOf(next)
        if (from !== -1 && to !== -1 && from !== to) dir = to > from ? 1 : -1
      }
      setDirection(dir)
      if (controlledValue === undefined) setInternalValue(next)
      onValueChange?.(next)
    },
    [value, controlledValue, onValueChange]
  )

  const slide = motionAllowed ? contentOffsetX : 0
  const restBlur = motionAllowed ? "blur(4px)" : "blur(0px)"
  const restOpacity = still ? 1 : 0
  const enterTransition: PanelTransition = still
    ? { duration: 0 }
    : { type: "tween", duration: uiTransition.duration, ease: uiTransition.ease }
  const exitTransition: PanelTransition = still
    ? { duration: 0 }
    : {
        type: "tween",
        duration: uiTransition.duration * 0.5,
        ease: uiTransition.ease,
      }

  const ctx = useMemo<SmoothTabsContextValue>(
    () => ({
      value,
      selectValue,
      direction,
      listRef,
      indicatorLayoutId: `${baseId}-indicator`,
      snapTransition,
      animateIndicator: motionAllowed,
      heightTransition: uiTransition,
      slide,
      restOpacity,
      restBlur,
      enterTransition,
      exitTransition,
      presenceCustom: direction,
    }),
    [
      value,
      selectValue,
      direction,
      baseId,
      snapTransition,
      motionAllowed,
      uiTransition,
      slide,
      restOpacity,
      restBlur,
      enterTransition,
      exitTransition,
    ]
  )

  return (
    <SmoothTabsContext.Provider value={ctx}>
      <TabsPrimitive.Root
        value={value}
        onValueChange={(next) => selectValue(next as string)}
        className={className}
      >
        {children}
      </TabsPrimitive.Root>
    </SmoothTabsContext.Provider>
  )
}

export interface SmoothTabsListProps {
  /** Accessible name for the `role="tablist"`. */
  ariaLabel?: string
  /** Merged onto the tablist shell. */
  className?: string
  /** The `SmoothTabsTab` children. */
  children?: ReactNode
}

/** The tablist rail with roving-tabindex keyboard navigation. */
export function SmoothTabsList({
  ariaLabel,
  className,
  children,
}: SmoothTabsListProps) {
  const ctx = useContextOrThrow("SmoothTabsList")

  return (
    <TabsPrimitive.List
      activateOnFocus
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      ref={ctx.listRef}
      className={`flex rounded-full border border-border/70 bg-card p-1${className ? ` ${className}` : ""}`}
    >
      {children}
    </TabsPrimitive.List>
  )
}

export interface SmoothTabsTabProps {
  /** This tab's value. Must match a `SmoothTabsPanel`. */
  value: string
  /** The tab's label. */
  children?: ReactNode
  /** Merged onto the tab `<button>`. */
  className?: string
}

/** One tab with a shared sliding pill when selected. */
export function SmoothTabsTab({ value, children, className }: SmoothTabsTabProps) {
  const ctx = useContextOrThrow("SmoothTabsTab")
  const selected = ctx.value === value

  return (
    <TabsPrimitive.Tab
      value={value}
      data-value={value}
      className={`relative z-10 flex-1 rounded-full px-4 py-2.5 text-sm font-medium transition-colors duration-[var(--motion-ui-transition-snap-duration)] ease-[var(--motion-ui-transition-snap)] ${FOCUS_RING} ${
        selected
          ? "text-foreground"
          : "text-muted-foreground hover:text-foreground"
      }${className ? ` ${className}` : ""}`}
    >
      <span className="relative z-10">{children}</span>
      {selected && (
        <motion.span
          layoutId={ctx.indicatorLayoutId}
          aria-hidden="true"
          className="absolute inset-0 z-0 rounded-full bg-muted shadow-sm"
          transition={
            ctx.animateIndicator ? { ...ctx.snapTransition } : { duration: 0 }
          }
        />
      )}
    </TabsPrimitive.Tab>
  )
}

export interface SmoothTabsPanelsProps {
  /** The `SmoothTabsPanel` children. */
  children?: ReactNode
  /** Merged onto the crossfade viewport. */
  className?: string
}

/** Crossfade viewport for the active panel. */
export function SmoothTabsPanels({ children, className }: SmoothTabsPanelsProps) {
  const ctx = useContextOrThrow("SmoothTabsPanels")

  const panels = Children.toArray(children).filter(
    (child): child is ReactElement<SmoothTabsPanelProps> =>
      isValidElement(child) && "value" in (child.props as SmoothTabsPanelProps)
  )
  const active = panels.find((panel) => panel.props.value === ctx.value)
  const measureRef = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number | null>(null)

  /* The one motion Base UI tabs lack: when panels differ in height, the
   * viewport eases to the new height instead of jumping the page. */
  useEffect(() => {
    const node = measureRef.current
    if (!node || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(() => setHeight(node.offsetHeight))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const contentVariants = {
    enter: (d: number) => ({
      x: d * ctx.slide,
      opacity: ctx.restOpacity,
      filter: ctx.restBlur,
    }),
    active: { x: 0, opacity: 1, filter: "blur(0px)" },
    exit: (d: number) => ({
      x: d * -ctx.slide,
      opacity: ctx.restOpacity,
      filter: ctx.restBlur,
      transition: ctx.exitTransition,
    }),
  }

  return (
    <motion.div
      animate={height === null ? undefined : { height }}
      transition={ctx.animateIndicator ? ctx.heightTransition : { duration: 0 }}
      className={`relative overflow-hidden${className ? ` ${className}` : ""}`}
    >
      <div ref={measureRef} className="grid">
      {/* mode="sync" keeps equal-height panels from collapsing between swaps. */}
      <AnimatePresence mode="sync" initial={false} custom={ctx.presenceCustom}>
        {active && (
          <TabsPrimitive.Panel
            key={active.props.value}
            value={active.props.value}
            keepMounted
            render={
              <motion.div
                tabIndex={0}
                custom={ctx.presenceCustom}
                variants={contentVariants}
                initial="enter"
                animate="active"
                exit="exit"
                transition={ctx.enterTransition}
                className={`col-start-1 row-start-1 [&[hidden]]:block ${FOCUS_RING}${active.props.className ? ` ${active.props.className}` : ""}`}
              >
                {active.props.children}
              </motion.div>
            }
          />
        )}
      </AnimatePresence>
      </div>
    </motion.div>
  )
}

export interface SmoothTabsPanelProps {
  /** This panel's value. Must match a `SmoothTabsTab`. */
  value: string
  /** The panel's content. */
  children?: ReactNode
  /** Merged onto the animated `role="tabpanel"` wrapper. */
  className?: string
}

/** Declarative panel config read by `SmoothTabsPanels`. Renders nothing itself. */
export function SmoothTabsPanel(_props: SmoothTabsPanelProps) {
  return null
}
