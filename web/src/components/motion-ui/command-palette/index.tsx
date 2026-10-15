"use client"

import { Dialog } from "@base-ui/react/dialog"
import { AnimatePresence, motion } from "motion/react"
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"
import type { UITransition } from "@/components/motion-ui/ui-theme"

/** Command an item describes. */
export interface CommandPaletteItem {
  id: string
  label: string
  hint?: string
  group?: string
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>
  shortcut?: string[]
  keywords?: string[]
}

/** Filtered, non-empty group in render order. */
export interface CommandGroupView<
  T extends CommandPaletteItem = CommandPaletteItem,
> {
  group: string
  items: T[]
}

/** Subsequence fuzzy match: every query character must appear in order in `target`. */
export function fuzzyMatch(query: string, target: string): boolean {
  if (!query) return true
  let qi = 0
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) qi++
  }
  return qi === query.length
}

/** Whether a command survives the trimmed lower-cased query. */
export function commandMatches(
  command: CommandPaletteItem,
  query: string,
): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return (
    fuzzyMatch(q, command.label.toLowerCase()) ||
    (command.keywords?.some((keyword) => fuzzyMatch(q, keyword)) ?? false)
  )
}

function derivedGroupOrder(items: CommandPaletteItem[]): string[] {
  const seen: string[] = []
  for (const item of items) {
    const group = item.group ?? ""
    if (!seen.includes(group)) seen.push(group)
  }
  return seen
}

/** Filters `items` by `query` and buckets survivors into groups. */
export function filterCommandGroups<T extends CommandPaletteItem>(
  items: T[],
  groupOrder: string[] | undefined,
  query: string,
): CommandGroupView<T>[] {
  const order = groupOrder ?? derivedGroupOrder(items)
  return order
    .map((group) => ({
      group,
      items: items.filter(
        (command) =>
          (command.group ?? "") === group && commandMatches(command, query),
      ),
    }))
    .filter((view) => view.items.length > 0)
}

/** Wraps listbox keyboard navigation while keeping an empty result set at zero. */
export function nextCommandIndex(
  currentIndex: number,
  resultCount: number,
  direction: "next" | "previous",
): number {
  if (resultCount === 0) return 0
  return direction === "next"
    ? (currentIndex + 1) % resultCount
    : (currentIndex - 1 + resultCount) % resultCount
}

/** Formats shortcut badges for display and screen readers. */
export function commandShortcutPresentation(shortcut: string[]): {
  separator: "→" | "+"
  label: string
} {
  const sequence = shortcut.length === 2 && shortcut[0] === "G"
  return {
    separator: sequence ? "→" : "+",
    label: `Shortcut: ${shortcut.join(sequence ? " then " : " plus ")}`,
  }
}

export interface UseCommandKOptions {
  /** Whether the palette starts open. Defaults to `false`. */
  defaultOpen?: boolean
  /** Controlled open state. */
  open?: boolean
  /** Called whenever the open state changes. */
  onOpenChange?: (open: boolean) => void
}

export interface UseCommandKResult {
  /** Whether the palette is open. */
  open: boolean
  /** Set the open state directly. */
  setOpen: (open: boolean) => void
  /** Open the palette. */
  openPalette: () => void
  /** Close the palette. */
  closePalette: () => void
  /** Toggle the palette (Cmd/Ctrl+K). */
  toggle: () => void
}

/** Headless open-state engine with global Cmd/Ctrl+K listener. */
export function useCommandK({
  defaultOpen = false,
  open: openProp,
  onOpenChange,
}: UseCommandKOptions = {}): UseCommandKResult {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const isControlled = openProp !== undefined
  const open = isControlled ? openProp : uncontrolledOpen

  const onOpenChangeRef = useRef(onOpenChange)
  onOpenChangeRef.current = onOpenChange

  const setOpen = useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolledOpen(next)
      onOpenChangeRef.current?.(next)
    },
    [isControlled],
  )

  const openPalette = useCallback(() => setOpen(true), [setOpen])
  const closePalette = useCallback(() => setOpen(false), [setOpen])

  const openRef = useRef(open)
  openRef.current = open
  const toggle = useCallback(() => setOpen(!openRef.current), [setOpen])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setOpen(!openRef.current)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [setOpen])

  return { open, setOpen, openPalette, closePalette, toggle }
}

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"

const KBD_CLASS =
  "inline-flex h-5 min-w-5 items-center justify-center rounded-md border border-border/70 bg-muted px-1.5 font-mono text-[11px] text-muted-foreground"

const FAINT_TEXT =
  "text-[color-mix(in_srgb,var(--muted-foreground)_62%,var(--background))]"

const BORDER_SOFT =
  "border-[color-mix(in_srgb,var(--foreground)_8%,var(--background))]"

const HIGHLIGHT_FILL = "bg-[color-mix(in_srgb,var(--primary)_16%,var(--muted))]"

const BACKDROP_VEIL =
  "bg-[color-mix(in_srgb,var(--background)_72%,transparent)]"

function SearchGlyph({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  )
}

function CornerReturnGlyph({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <polyline points="9 10 4 15 9 20" />
      <path d="M20 4v7a4 4 0 0 1-4 4H4" />
    </svg>
  )
}

/** One footer-strip hint: a kbd badge and its label. */
export interface CommandFooterHint {
  keys: string
  label: string
}

const DEFAULT_FOOTER_HINTS: CommandFooterHint[] = [
  { keys: "↑↓", label: "navigate" },
  { keys: "↵", label: "select" },
  { keys: "esc", label: "close" },
]

function defaultRenderEmpty(query: string): ReactNode {
  return <>No commands match &ldquo;{query}&rdquo;.</>
}

function FooterHint({ keys, children }: { keys: string; children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
      <kbd className={KBD_CLASS}>{keys}</kbd>
      {children}
    </span>
  )
}

export interface CommandPaletteProps {
  /** Controlled open state. */
  open?: boolean
  /** Initial open state when uncontrolled. Defaults to `false`. */
  defaultOpen?: boolean
  /** Called whenever Base UI requests an open-state change. */
  onOpenChange?: (open: boolean) => void
  /** Commands the palette filters and lists. */
  items: CommandPaletteItem[]
  /** Order group headers render in. */
  groupOrder?: string[]
  /** Max height of the scrollable result list. */
  maxListHeight?: number
  /** Collapsed trigger bar placeholder text. */
  triggerLabel?: string
  /** Trigger trailing shortcut badge glyphs. */
  triggerShortcut?: string
  /** Open dialog input placeholder. */
  inputPlaceholder?: string
  /** Open dialog input accessible label. */
  inputAriaLabel?: string
  /** Dialog accessible label. */
  dialogLabel?: string
  /** Footer-strip hints. */
  footerHints?: CommandFooterHint[]
  /** Renders the no-results message. */
  renderEmpty?: (query: string) => ReactNode
  /** Called when a row is selected. */
  onSelect?: (item: CommandPaletteItem) => void
}

/** Fuzzy-filtered Cmd+K launcher on Base UI Dialog. */
export function CommandPalette({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  items,
  groupOrder,
  maxListHeight = 320,
  triggerLabel = "Search commands, funnels, cohorts…",
  triggerShortcut = "⌘K",
  inputPlaceholder = "Type a command or search…",
  inputAriaLabel = "Search commands",
  dialogLabel = "Command palette",
  footerHints = DEFAULT_FOOTER_HINTS,
  renderEmpty = defaultRenderEmpty,
  onSelect,
}: CommandPaletteProps) {
  const { motionMode } = useMotionUITheme()
  const still = motionMode === "off"
  const calm = motionMode === "calm"
  const motionAllowed = motionMode === "full"
  const { open, setOpen, closePalette } = useCommandK({
    open: openProp,
    defaultOpen,
    onOpenChange,
  })

  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [portalContainer, setPortalContainer] =
    useState<HTMLDivElement | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const baseActionsRef = useRef<{
    close: () => void
    unmount: () => void
  }>(null!)
  const [portalMounted, setPortalMounted] = useState(open)
  const listRef = useRef<HTMLDivElement>(null)
  const openRef = useRef(open)
  openRef.current = open

  const baseId = useId()
  const listboxId = `${baseId}-listbox`
  const highlightLayoutId = `${baseId}-highlight`
  const optionId = useCallback(
    (id: string) => `${baseId}-option-${id}`,
    [baseId],
  )

  const filteredGroups = useMemo(
    () => filterCommandGroups(items, groupOrder, query),
    [items, groupOrder, query],
  )
  const flatResults = useMemo(
    () => filteredGroups.flatMap((g) => g.items),
    [filteredGroups],
  )

  useEffect(() => {
    if (open) {
      setQuery("")
      setActiveIndex(0)
    }
  }, [open])

  useEffect(() => setActiveIndex(0), [query])

  useEffect(() => {
    if (open) setPortalMounted(true)
  }, [open])

  useEffect(() => {
    const active = listRef.current?.querySelector('[data-active="true"]')
    active?.scrollIntoView({ block: "nearest" })
  }, [activeIndex])

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setActiveIndex((i) => nextCommandIndex(i, flatResults.length, "next"))
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      setActiveIndex((i) => nextCommandIndex(i, flatResults.length, "previous"))
    } else if (event.key === "Enter") {
      event.preventDefault()
      const item = flatResults[activeIndex]
      if (item) {
        onSelect?.(item)
        closePalette()
      }
    }
  }

  const enterTransition = useMotionUITransition("gentle")
  const snapTransition = useMotionUITransition("snap")

  const barInitial = still
    ? false
    : { opacity: 0, transform: `translateY(${calm ? 0 : 8}px)` }

  const backdropInitial = still ? false : { opacity: 0 }
  const backdropExit = still ? undefined : { opacity: 0 }

  const dialogInitial = still
    ? false
    : {
        opacity: 0,
        transform: calm ? "translateY(0px)" : "translateY(16px)",
      }
  const dialogExit = still
    ? undefined
    : {
        opacity: 0,
        transform: calm ? "translateY(0px)" : "translateY(10px)",
        transition: { ...snapTransition },
      }

  let runningIndex = 0
  const resultCount = flatResults.length
  const statusMessage = query.trim()
    ? `${resultCount} ${resultCount === 1 ? "result" : "results"} for "${query.trim()}"`
    : `${resultCount} commands`

  const unmount = useCallback(() => {
    baseActionsRef.current.unmount()
    setPortalMounted(false)
  }, [])

  const handleOpenChange = useCallback(
    (next: boolean, details: { preventUnmountOnClose: () => void }) => {
      if (!next) details.preventUnmountOnClose()
      if (next) setPortalMounted(true)
      setOpen(next)
    },
    [setOpen],
  )

  return (
    <Dialog.Root
      open={open}
      onOpenChange={handleOpenChange}
      actionsRef={baseActionsRef}
    >
      <div ref={setPortalContainer} className="contents" />
      <motion.div
        initial={barInitial}
        animate={{ opacity: 1, transform: "translateY(0px)" }}
        transition={
          calm ? { ...snapTransition, type: "tween" } : enterTransition
        }
        className="relative z-10 w-full max-w-[480px]"
      >
        <Dialog.Trigger
          ref={triggerRef}
          render={
            <motion.button
              type="button"
              aria-keyshortcuts="Meta+K Control+K"
              className={`velocity-command-trigger flex h-12 w-full items-center gap-2.5 rounded-full border border-border/70 bg-card px-5 text-[14px] text-muted-foreground shadow-sm transition-colors duration-[var(--motion-ui-transition-snap-duration)] ease-[var(--motion-ui-transition-snap)] hover:bg-accent ${FOCUS_RING}`}
            >
              <SearchGlyph className={`size-4 shrink-0 ${FAINT_TEXT}`} />
              <span className="flex-1 truncate text-left">{triggerLabel}</span>
              <kbd className={`${KBD_CLASS} shrink-0`}>{triggerShortcut}</kbd>
            </motion.button>
          }
        />
      </motion.div>

      <AnimatePresence>
        {portalMounted && (
          <Dialog.Portal container={portalContainer} keepMounted>
            <AnimatePresence>
              {open && (
                <>
                  <Dialog.Backdrop
                    key="cmdk-backdrop"
                    render={
                      <motion.div
                        aria-hidden="true"
                        className={`fixed inset-0 z-50 ${BACKDROP_VEIL}`}
                        initial={backdropInitial}
                        animate={{ opacity: 1 }}
                        exit={backdropExit}
                        transition={{ ...enterTransition }}
                      />
                    }
                  />

                  <Dialog.Popup
                    key="cmdk-dialog"
                    aria-label={dialogLabel}
                    initialFocus={inputRef}
                    finalFocus={triggerRef}
                    render={
                      <motion.div
                        initial={dialogInitial}
                        animate={{
                          opacity: 1,
                          transform: "translateY(0px)",
                        }}
                        exit={dialogExit}
                        transition={{ ...enterTransition }}
                        onAnimationComplete={() => {
                          if (!openRef.current) unmount()
                        }}
                        onKeyDown={handleDialogKeyDown}
                        className="velocity-command-dialog fixed inset-x-4 top-[12vh] z-50 mx-auto flex w-auto max-w-[480px] flex-col overflow-hidden rounded-xl border border-border/70 bg-card p-0 shadow-xl sm:top-[16vh]"
                      >
                        <div
                          className={`flex items-center gap-2.5 border-b ${BORDER_SOFT} px-4 py-3.5`}
                        >
                          <SearchGlyph
                            className={`size-4 shrink-0 ${FAINT_TEXT}`}
                          />
                          <input
                            ref={inputRef}
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={inputPlaceholder}
                            aria-label={inputAriaLabel}
                            role="combobox"
                            aria-expanded="true"
                            aria-controls={listboxId}
                            aria-activedescendant={
                              flatResults[activeIndex]
                                ? optionId(flatResults[activeIndex].id)
                                : undefined
                            }
                            className="min-w-0 flex-1 appearance-none border-0 p-0 font-[inherit] tracking-[inherit] bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground"
                          />
                          <Dialog.Close
                            render={
                              <button
                                type="button"
                                className={`${KBD_CLASS} shrink-0 ${FOCUS_RING}`}
                              >
                                esc
                              </button>
                            }
                          />
                        </div>

                        <p className="sr-only" role="status" aria-live="polite">
                          {statusMessage}
                        </p>

                        <div
                          ref={listRef}
                          id={listboxId}
                          role="listbox"
                          aria-label="Commands"
                          style={{ maxHeight: maxListHeight }}
                          className="flex flex-col overflow-y-auto overscroll-contain px-2 py-2"
                        >
                          {filteredGroups.length > 0 ? (
                            filteredGroups.map(({ group, items }) => (
                              <motion.div
                                key={group}
                                layout={motionAllowed ? "position" : false}
                              >
                                <div className="px-2.5 pt-2.5 pb-1 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                                  {group}
                                </div>
                                {items.map((command) => {
                                  const index = runningIndex++
                                  const isActive = index === activeIndex
                                  return (
                                    <CommandRow
                                      key={command.id}
                                      command={command}
                                      optionId={optionId(command.id)}
                                      highlightLayoutId={highlightLayoutId}
                                      isActive={isActive}
                                      motionAllowed={motionAllowed}
                                      highlightTransition={snapTransition}
                                      onHover={() => setActiveIndex(index)}
                                      onSelect={() => {
                                        onSelect?.(command)
                                        closePalette()
                                      }}
                                    />
                                  )
                                })}
                              </motion.div>
                            ))
                          ) : (
                            <p className="px-3 py-8 text-center text-[13px] text-muted-foreground">
                              {renderEmpty(query.trim())}
                            </p>
                          )}
                        </div>

                        <div
                          className={`flex items-center gap-4 border-t ${BORDER_SOFT} px-4 py-2.5`}
                        >
                          {footerHints.map((hint) => (
                            <FooterHint key={hint.keys} keys={hint.keys}>
                              {hint.label}
                            </FooterHint>
                          ))}
                        </div>
                      </motion.div>
                    }
                  />
                </>
              )}
            </AnimatePresence>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

function CommandRow({
  command,
  optionId,
  highlightLayoutId,
  isActive,
  motionAllowed,
  highlightTransition,
  onHover,
  onSelect,
}: {
  command: CommandPaletteItem
  optionId: string
  highlightLayoutId: string
  isActive: boolean
  motionAllowed: boolean
  highlightTransition: UITransition
  onHover: () => void
  onSelect: () => void
}) {
  const Icon = command.icon
  const shortcut = command.shortcut
  const shortcutPresentation =
    shortcut && shortcut.length > 0
      ? commandShortcutPresentation(shortcut)
      : null

  return (
    <motion.div
      layout={motionAllowed ? "position" : false}
      id={optionId}
      role="option"
      aria-selected={isActive}
      data-active={isActive}
      onMouseEnter={onHover}
      onClick={onSelect}
      className="relative flex cursor-pointer items-center justify-between gap-4 px-2.5 py-3"
    >
      {isActive && motionAllowed && (
        <motion.div
          layoutId={highlightLayoutId}
          transition={{ ...highlightTransition }}
          className={`velocity-command-highlight ${HIGHLIGHT_FILL} absolute inset-0 rounded-lg`}
        />
      )}
      {isActive && !motionAllowed && (
        <div
          aria-hidden="true"
          className={`velocity-command-highlight ${HIGHLIGHT_FILL} absolute inset-0 rounded-lg`}
        />
      )}

      <span
        className={`relative z-10 flex min-w-0 items-center${Icon ? " gap-3" : ""}`}
      >
        {Icon ? (
          <span
            className={
              isActive
                ? "flex size-7 shrink-0 items-center justify-center rounded-md text-primary"
                : `flex size-7 shrink-0 items-center justify-center rounded-md ${FAINT_TEXT}`
            }
          >
            <Icon className="size-4" aria-hidden />
          </span>
        ) : null}
        <span className="flex min-w-0 flex-col">
          <span
            className={
              isActive
                ? "truncate text-[14px] font-medium text-foreground"
                : "truncate text-[14px] text-foreground"
            }
          >
            {command.label}
          </span>
          {command.hint ? (
            <span className="hidden truncate text-[12px] text-muted-foreground sm:block">
              {command.hint}
            </span>
          ) : null}
        </span>
      </span>

      <span
        className="relative z-10 flex shrink-0 items-center gap-1"
        aria-label={shortcutPresentation?.label}
      >
        {shortcut && shortcutPresentation ? (
          shortcut.map((key, index) => (
            <span key={`${key}-${index}`} className="contents" aria-hidden="true">
              {index > 0 ? (
                <span className="px-0.5 text-[10px] text-muted-foreground">
                  {shortcutPresentation.separator}
                </span>
              ) : null}
              <kbd className={KBD_CLASS}>{key}</kbd>
            </span>
          ))
        ) : (
          <CornerReturnGlyph className={`size-3.5 ${FAINT_TEXT}`} />
        )}
      </span>
    </motion.div>
  )
}
