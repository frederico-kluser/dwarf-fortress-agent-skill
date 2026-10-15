"use client"

import {
  AnimatePresence,
  motion,
} from "motion/react"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"
import type { UITransition } from "@/components/motion-ui/ui-theme"
import { Backdrop, useFocusTrap, useScrollLock } from "../overlay"

interface ExpandCardsContextValue {
  /** Id of the expanded card, or `null`. */
  openId: string | null
  /** Expand the card with this id. */
  open: (id: string) => void
  /** Collapse the open card. */
  close: () => void
  /** Scrim opacity behind an open card (0-1). */
  scrimOpacity: number
  /** Stable prefix for shared `layoutId`s across groups. */
  group: string
  /** Reduced motion "off": no animation. */
  still: boolean
  /** Reduced motion "calm": opacity only, no travel/morph. */
  calm: boolean
  /** Full motion: shared-element layout morph. */
  animateLayout: boolean
  /** Theme "ui" transition for morphing parts. */
  ui: UITransition
  /** Theme "gentle" transition for the scrim. */
  gentle: UITransition
  /** Theme "snap" transition for the body cross-fade. */
  snap: UITransition
}

const ExpandCardsContext = createContext<ExpandCardsContextValue | null>(null)

function useExpandCardsContext(): ExpandCardsContextValue {
  const ctx = useContext(ExpandCardsContext)
  if (!ctx) {
    throw new Error(
      "ExpandCardTrigger, ExpandCardPanel, ExpandCardShared, ExpandCardBody and useExpandCard must be used inside <ExpandCards>."
    )
  }
  return ctx
}

const CardIdContext = createContext<string | null>(null)

function sharedId(group: string, part: string, id: string): string {
  return `${group}-${part}-${id}`
}

/** Open/close controls for a consumer's own UI. */
export interface UseExpandCard {
  /** Id of the expanded card, or `null`. */
  openId: string | null
  /** Expand the card with this id. */
  open: (id: string) => void
  /** Collapse the open card. */
  close: () => void
  /** Whether the card with this id is expanded. */
  isOpen: (id: string) => boolean
}

/** Read expand/collapse controls from inside an `ExpandCards` group. */
export function useExpandCard(): UseExpandCard {
  const { openId, open, close } = useExpandCardsContext()
  return {
    openId,
    open,
    close,
    isOpen: (id: string) => openId === id,
  }
}

export interface ExpandCardsProps {
  /** Grid of `ExpandCardTrigger`s and one `ExpandCardPanel`. */
  children?: ReactNode
  /** Scrim opacity (0-1). Default `0.72`. */
  scrimOpacity?: number
}

/** Provider for a set of expanding cards. Renders no DOM. */
export function ExpandCards({ children, scrimOpacity = 0.72 }: ExpandCardsProps) {
  const [openId, setOpenId] = useState<string | null>(null)
  const group = useId()

  const uiTheme = useMotionUITheme()
  const still = uiTheme.motionMode === "off"
  const calm = uiTheme.motionMode === "calm"
  const animateLayout = uiTheme.motionMode === "full"

  const ui = useMotionUITransition("ui")
  const gentle = useMotionUITransition("gentle")
  const snap = useMotionUITransition("snap")

  const open = useCallback((id: string) => setOpenId(id), [])
  const close = useCallback(() => setOpenId(null), [])

  const value = useMemo<ExpandCardsContextValue>(
    () => ({
      openId,
      open,
      close,
      scrimOpacity,
      group,
      still,
      calm,
      animateLayout,
      ui,
      gentle,
      snap,
    }),
    [openId, open, close, scrimOpacity, group, still, calm, animateLayout, ui, gentle, snap]
  )

  return (
    <ExpandCardsContext.Provider value={value}>
      {children}
    </ExpandCardsContext.Provider>
  )
}

export interface ExpandCardTriggerProps {
  /** Card id matched against `openId`. */
  id: string
  /** Visible card content. */
  children?: ReactNode
  /** Merged onto the morphing surface. */
  className?: string
  /** Button accessible name. */
  "aria-label"?: string
}

/** Grid tile that expands on activation. */
export function ExpandCardTrigger({
  id,
  children,
  className,
  "aria-label": ariaLabel,
}: ExpandCardTriggerProps) {
  const { openId, open, group, animateLayout, ui } = useExpandCardsContext()
  const isOpen = openId === id
  return (
    <CardIdContext.Provider value={id}>
      <button
        type="button"
        className="group block h-full w-full text-left focus-visible:outline-none"
        onClick={() => open(id)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-label={ariaLabel}
      >
        <motion.div
          layoutId={animateLayout ? sharedId(group, "surface", id) : undefined}
          className={className}
          transition={{ ...ui }}
        >
          {children}
        </motion.div>
      </button>
    </CardIdContext.Provider>
  )
}

export interface ExpandCardSharedProps {
  /** Stable part name (e.g. `"icon"`, `"head"`). */
  part: string
  /** Element to render. Default `"span"`. */
  as?: "span" | "div"
  /** Pass `"position"` to morph position only, not size. */
  layout?: "position" | "size" | boolean
  /** Merged onto the element. */
  className?: string
  /** Part content. */
  children?: ReactNode
}

/** Sub-element that morphs with its card's surface. */
export function ExpandCardShared({
  part,
  as = "span",
  layout,
  className,
  children,
}: ExpandCardSharedProps) {
  const { group, animateLayout, ui } = useExpandCardsContext()
  const cardId = useContext(CardIdContext)
  const MotionEl = as === "div" ? motion.div : motion.span
  return (
    <MotionEl
      layoutId={
        animateLayout && cardId != null ? sharedId(group, part, cardId) : undefined
      }
      layout={animateLayout ? layout : undefined}
      className={className}
      transition={{ ...ui }}
    >
      {children}
    </MotionEl>
  )
}

export interface ExpandCardPanelProps {
  /** Dialog body, or a render function receiving the open card's `id`. */
  children: ((card: { id: string }) => ReactNode) | ReactNode
  /** Merged onto the dialog surface. */
  className?: string
  /** Merged onto the inner content wrapper. */
  contentClassName?: string
  /** Builds `aria-labelledby` from the open card's id. */
  labelledBy?: (id: string) => string
  /** Builds `aria-describedby` from the open card's id. */
  describedBy?: (id: string) => string
  /** Where focus lands on open. */
  initialFocusRef?: RefObject<HTMLElement | null>
}

/** Expanded detail dialog. One per `ExpandCards` group. */
export function ExpandCardPanel({
  children,
  className,
  contentClassName,
  labelledBy,
  describedBy,
  initialFocusRef,
}: ExpandCardPanelProps) {
  const { openId, close, scrimOpacity, group, still, calm, animateLayout, ui, gentle } =
    useExpandCardsContext()
  const panelRef = useRef<HTMLDivElement>(null)
  const [scrollReadyId, setScrollReadyId] = useState<string | null>(null)
  const isOpen = openId != null
  const scrollReady = !animateLayout || scrollReadyId === openId

  useEffect(() => {
    if (!isOpen) setScrollReadyId(null)
  }, [isOpen])

  useScrollLock(isOpen)
  useFocusTrap({
    active: isOpen,
    container: panelRef,
    onEscape: close,
    initialFocus: initialFocusRef,
    restoreFocus: true,
  })

  return (
    <AnimatePresence>
      {openId != null && (
        <CardIdContext.Provider key="expand-card-panel" value={openId}>
          <div className="fixed inset-0 z-1000 flex items-center justify-center p-4 sm:p-6">
            <Backdrop
              onClick={close}
              initial={{ opacity: 0 }}
              animate={{ opacity: scrimOpacity }}
              exit={{ opacity: 0 }}
              transition={still ? { duration: 0 } : { ...gentle, type: "tween" }}
            />

            <motion.div
              ref={panelRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby={labelledBy?.(openId)}
              aria-describedby={describedBy?.(openId)}
              layoutId={animateLayout ? sharedId(group, "surface", openId) : undefined}
              className={className}
              transition={{ ...ui }}
              onLayoutAnimationStart={() => setScrollReadyId(null)}
              onLayoutAnimationComplete={() => setScrollReadyId(openId)}
              initial={animateLayout ? undefined : { opacity: 0, scale: calm ? 1 : 0.98 }}
              animate={animateLayout ? undefined : { opacity: 1, scale: 1 }}
              exit={animateLayout ? undefined : { opacity: 0, scale: calm ? 1 : 0.98 }}
            >
              <motion.div
                layout={animateLayout ? "position" : undefined}
                transition={{ ...ui }}
                className={contentClassName}
                style={{ overflowY: scrollReady ? undefined : "clip" }}
              >
                {typeof children === "function" ? children({ id: openId }) : children}
              </motion.div>
            </motion.div>
          </div>
        </CardIdContext.Provider>
      )}
    </AnimatePresence>
  )
}

export interface ExpandCardBodyProps {
  /** Non-shared dialog detail content. */
  children?: ReactNode
  /** Merged onto the wrapper. */
  className?: string
}

/** Non-shared body that cross-fades in after the surface morph settles. */
export function ExpandCardBody({ children, className }: ExpandCardBodyProps) {
  const { still, calm, snap } = useExpandCardsContext()
  const theme = useMotionUITheme()
  return (
    <motion.div
      className={className}
      initial={still ? false : { opacity: 0, transform: `translateY(${calm ? 0 : theme.travel.hover}px)` }}
      animate={{ opacity: 1, transform: "translateY(0px)" }}
      transition={
        still ? { duration: 0 } : { ...snap, delay: calm ? 0 : snap.duration }
      }
    >
      {children}
    </motion.div>
  )
}
