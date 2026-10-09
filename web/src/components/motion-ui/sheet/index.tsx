import {
  AnimatePresence,
  motion,
  useMotionValue,
  useTransform,
  type MotionValue,
} from "motion/react"
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  Children,
  isValidElement,
  useCallback,
  type ReactNode,
  type RefObject,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

interface SheetContextValue {
  /** Whether the sheet is open. */
  open: boolean
  /** Open or close the sheet. */
  setOpen: (open: boolean) => void
  /** Live y-offset driven by drag; read by the scrim. */
  y: MotionValue<number>
  /** Distance over which a downward drag fully fades the scrim. */
  hiddenY: number
  /** Ref to the trigger for focus return on close. */
  triggerRef: RefObject<HTMLButtonElement | null>
  /** Ref to the native dialog element. */
  dialogRef: RefObject<HTMLDialogElement | null>
  /** Ref to the visual sheet panel. */
  panelRef: RefObject<HTMLDivElement | null>
  /** Ref to the close button for initial focus. */
  closeRef: RefObject<HTMLButtonElement | null>
  /** Resolved reduced-motion mode. */
  motionMode: { still: boolean; calm: boolean; motionAllowed: boolean }
}

const SheetContext = createContext<SheetContextValue | null>(null)

function useSheetContext(part: string): SheetContextValue {
  const context = useContext(SheetContext)
  if (!context) {
    throw new Error(`${part} must be rendered inside <Sheet>.`)
  }
  return context
}

/** Open-state handle from `useSheet()`. */
export interface UseSheet {
  /** Whether the sheet is open. */
  open: boolean
  /** Open or close the sheet. */
  setOpen: (open: boolean) => void
}

/** Read or control the enclosing sheet's open state. */
export function useSheet(): UseSheet {
  const { open, setOpen } = useSheetContext("useSheet()")
  return { open, setOpen }
}

export interface SheetProps {
  /** Trigger, backdrop, panel and resting content. */
  children?: ReactNode
  /** Controlled open state. */
  open?: boolean
  /** Initial open state when uncontrolled. Default `false`. */
  defaultOpen?: boolean
  /** Called on open-state changes. */
  onOpenChange?: (open: boolean) => void
}

/** Root coordinating native `<dialog>` state, drag position and element refs. */
export function Sheet({
  children,
  open: openProp,
  defaultOpen = false,
  onOpenChange,
}: SheetProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const isControlled = openProp !== undefined
  const open = isControlled ? openProp : uncontrolledOpen

  const y = useMotionValue(0)
  const hiddenY = 420
  const triggerRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const { motionMode } = useMotionUITheme()
  const still = motionMode === "off"
  const calm = motionMode === "calm"
  const motionAllowed = motionMode === "full"

  const setOpen = useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolledOpen(next)
      onOpenChange?.(next)
    },
    [isControlled, onOpenChange]
  )

  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!open || !dialog || dialog.open) return
    dialog.showModal()
    closeRef.current?.focus({ preventScroll: true })
  }, [open])

  useEffect(() => {
    if (!open) return
    const root = document.documentElement
    const previousOverflow = root.style.overflow
    root.style.overflow = "hidden"
    return () => {
      root.style.overflow = previousOverflow
    }
  }, [open])

  const pageParts: ReactNode[] = []
  const overlayParts: ReactNode[] = []
  let labelledBy = "velocity-sheet-title"
  for (const child of Children.toArray(children)) {
    if (
      isValidElement(child) &&
      (child.type === SheetBackdrop || child.type === SheetPanel)
    ) {
      overlayParts.push(child)
      if (child.type === SheetPanel) {
        labelledBy =
          (child.props as SheetPanelProps).labelledBy ?? "velocity-sheet-title"
      }
    } else {
      pageParts.push(child)
    }
  }

  return (
    <SheetContext.Provider
      value={{
        open,
        setOpen,
        y,
        hiddenY,
        triggerRef,
        dialogRef,
        panelRef,
        closeRef,
        motionMode: { still, calm, motionAllowed },
      }}
    >
      {pageParts}
      <motion.dialog
        ref={dialogRef}
        aria-labelledby={labelledBy}
        className="fixed inset-0 z-50 m-0 h-dvh max-h-none w-screen max-w-none overflow-hidden border-0 bg-transparent p-0 text-foreground backdrop:bg-transparent"
        onCancel={(event) => {
          event.preventDefault()
          setOpen(false)
        }}
        onClose={() => {
          if (open) setOpen(false)
        }}
      >
        <AnimatePresence
          onExitComplete={() => {
            const dialog = dialogRef.current
            if (!open && dialog?.open) {
              dialog.close()
              triggerRef.current?.focus({ preventScroll: true })
            }
          }}
        >
          {open && overlayParts}
        </AnimatePresence>
      </motion.dialog>
    </SheetContext.Provider>
  )
}

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"

export interface SheetTriggerProps {
  /** Trigger contents. */
  children?: ReactNode
  /** Merged onto the trigger button. */
  className?: string
}

/** Button that opens the sheet. */
export function SheetTrigger({ children, className }: SheetTriggerProps) {
  const { setOpen, triggerRef, motionMode } = useSheetContext("SheetTrigger")
  const snap = useMotionUITransition("snap")

  return (
    <motion.button
      ref={triggerRef}
      type="button"
      className={`inline-flex h-11 items-center gap-2 rounded-full bg-primary px-5 text-[0.9375rem] font-medium text-primary-foreground shadow-sm ${FOCUS_RING}${className ? ` ${className}` : ""}`}
      onClick={() => setOpen(true)}
      whileHover={motionMode.motionAllowed ? { scale: 1.05 } : undefined}
      whileTap={motionMode.motionAllowed ? { scale: 0.95 } : undefined}
      transition={{ ...snap }}
    >
      {children}
    </motion.button>
  )
}

export interface SheetBackdropProps {
  /** Scrim surface class. Defaults to `bg-background/70`. */
  className?: string
  /** Accessible label; when set the scrim is a dismiss button. */
  label?: string
}

/** Dimming scrim whose opacity follows the sheet drag position. */
export function SheetBackdrop({ className, label }: SheetBackdropProps) {
  const { setOpen, y, hiddenY, motionMode } =
    useSheetContext("SheetBackdrop")
  const gentle = useMotionUITransition("gentle")
  const dragOpacity = useTransform(y, [0, hiddenY], [1, 0])
  const { still, motionAllowed } = motionMode
  const surfaceClass = className ?? "bg-background/70"

  if (label) {
    return (
      <motion.button
        type="button"
        aria-label={label}
        className={`fixed inset-0 z-40 border-0 p-0 ${surfaceClass}`}
        style={{ opacity: motionAllowed ? dragOpacity : 1 }}
        initial={still ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={still ? undefined : { opacity: 0 }}
        transition={{ ...gentle }}
        onClick={() => setOpen(false)}
      />
    )
  }

  return (
    <motion.div
      aria-hidden="true"
      className={`fixed inset-0 z-40 ${surfaceClass}`}
      style={{ opacity: motionAllowed ? dragOpacity : 1 }}
      initial={still ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={still ? undefined : { opacity: 0 }}
      transition={{ ...gentle }}
    />
  )
}

/** Whether a drag release should dismiss the sheet. */
export function shouldDismissSheet(
  offsetY: number,
  velocityY: number,
  dismissOffset: number,
  dismissVelocity: number
): boolean {
  return offsetY > dismissOffset || velocityY > dismissVelocity
}

export interface SheetPanelProps {
  /** Sheet contents. */
  children?: ReactNode
  /** Downward drag distance (px) to dismiss. Default `92`. */
  dismissOffset?: number
  /** Downward flick velocity (px/s) to dismiss. Default `840`. */
  dismissVelocity?: number
  /** `id` of the labelling element, wired to `aria-labelledby`. */
  labelledBy?: string
  /** Merged onto the panel. */
  className?: string
}

/** Draggable spring panel with fade-up entrance. */
export function SheetPanel({
  children,
  dismissOffset = 92,
  dismissVelocity = 840,
  className,
}: SheetPanelProps) {
  const {
    open,
    setOpen,
    y,
    panelRef,
    motionMode,
  } = useSheetContext("SheetPanel")
  const gentle = useMotionUITransition("gentle")
  const { still, calm, motionAllowed } = motionMode

  useEffect(() => {
    if (!open) return
    y.set(0)
  }, [open, y])

  return (
    <motion.div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50"
      initial={
        still
          ? false
          : {
              transform: calm ? "translateY(0px)" : "translateY(50px)",
              opacity: 0,
            }
      }
      animate={{ transform: "translateY(0px)", opacity: 1 }}
      exit={
        still
          ? undefined
          : {
              transform: calm ? "translateY(0px)" : "translateY(50px)",
              opacity: 0,
            }
      }
      transition={{ ...gentle }}
    >
      <motion.div
        ref={panelRef}
        className={`pointer-events-auto mx-auto mb-2 w-[calc(100%-1rem)] max-w-md rounded-xl border border-border/70 bg-card px-5 pb-6 text-card-foreground shadow-xl sm:mb-3 sm:w-[calc(100%-1.5rem)] ${motionAllowed ? "cursor-grab" : ""}${className ? ` ${className}` : ""}`}
        // touch-action:none so vertical drags are captured by Motion, not the page scroll.
        style={{ y, touchAction: "none" }}
        drag={motionAllowed ? "y" : false}
        dragConstraints={{ top: 0, bottom: 0 }}
        dragElastic={{ top: 0.15, bottom: 0.8 }}
        dragMomentum={motionAllowed}
        whileDrag={motionAllowed ? { cursor: "grabbing" } : undefined}
        onDragEnd={(_, info) => {
          if (
            shouldDismissSheet(
              info.offset.y,
              info.velocity.y,
              dismissOffset,
              dismissVelocity
            )
          ) {
            setOpen(false)
          }
        }}
      >
        {children}
      </motion.div>
    </motion.div>
  )
}

export interface SheetHandleProps {
  /** Merged onto the centring row. */
  className?: string
}

/** Decorative grab affordance at the top of the sheet. */
export function SheetHandle({ className }: SheetHandleProps) {
  return (
    <div
      className={`flex justify-center pt-3 pb-1${className ? ` ${className}` : ""}`}
      aria-hidden="true"
    >
      <div
        className="h-1 w-10 rounded-full"
        style={{
          backgroundColor:
            "color-mix(in srgb, var(--muted-foreground) 62%, var(--background))",
        }}
      />
    </div>
  )
}

export interface SheetCloseProps {
  /** Button contents. Defaults to a close glyph. */
  children?: ReactNode
  /** Accessible label. Default `"Close"`. */
  label?: string
  /** Merged onto the button. */
  className?: string
}

/** Close button for the top-right of the sheet. */
export function SheetClose({ children, label = "Close", className }: SheetCloseProps) {
  const { setOpen, closeRef } = useSheetContext("SheetClose")

  return (
    <button
      ref={closeRef}
      type="button"
      aria-label={label}
      className={`flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground transition-colors duration-[var(--motion-ui-transition-snap-duration)] ease-[var(--motion-ui-transition-snap)] hover:bg-accent hover:text-foreground ${FOCUS_RING}${className ? ` ${className}` : ""}`}
      onClick={() => setOpen(false)}
    >
      {children ?? <CloseGlyph />}
    </button>
  )
}

/** Default close glyph, inlined without an icon dependency. */
function CloseGlyph() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  )
}
