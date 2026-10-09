"use client"

import { AnimateView } from "motion/react-animate-view"
import { motion, useInView } from "motion/react"
import type { TargetAndTransition } from "motion/react"
import {
  createContext,
  useContext,
  useRef,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

/** The infinite compositor tween that drives a shimmer sweep. */
export interface SweepTransition {
  type: "tween"
  duration: number
  ease: "linear"
  repeat: number
}

/** What `useSkeletonSweep` resolves: the gate and the tween a bone's sweep overlay consumes. */
export interface SkeletonSweep {
  /** True when the shimmer should run. */
  shimmering: boolean
  /** The token-derived infinite tween for the sweep overlay. */
  sweepTransition: SweepTransition
}

/** Options for `useSkeletonSweep`. */
export interface UseSkeletonSweepOptions<T extends Element> {
  /** Ref to the element whose viewport presence gates the loop. */
  ref: RefObject<T | null>
  /** Your own gate, e.g. `!loaded`. Defaults to `true`. */
  active?: boolean
}

/** Resolves the shimmer gate and cadence for a skeleton bone. */
export function useSkeletonSweep<T extends Element>({
  ref,
  active = true,
}: UseSkeletonSweepOptions<T>): SkeletonSweep {
  const ambient = useMotionUITransition("ambient")
  const { motionMode } = useMotionUITheme()
  const motionAllowed = motionMode === "full"
  const inView = useInView(ref)
  const sweepTransition: SweepTransition = {
    type: "tween",
    duration: ambient.duration * 2.5,
    ease: "linear",
    repeat: Infinity,
  }
  return { shimmering: active && motionAllowed && inView, sweepTransition }
}

const SWEEP_GRADIENT =
  "linear-gradient(90deg, var(--muted) 0%, color-mix(in srgb, var(--foreground) 10%, var(--muted)) 50%, var(--muted) 100%)"

export interface SkeletonProps {
  /** Run the shimmer when `true`; hold a steady block when `false`. Defaults to `true`. */
  animate?: boolean
  /** Merged onto the bone. Size and shape the placeholder here. */
  className?: string
  /** Extra inline styles, merged onto the bone. */
  style?: CSSProperties
  /** Rendered invisibly to size the bone to the real content geometry. */
  children?: ReactNode
}

/** A single skeleton placeholder with a compositor-only shimmer sweep. */
export function Skeleton({
  animate = true,
  className,
  style,
  children,
}: SkeletonProps) {
  const ref = useRef<HTMLDivElement>(null)
  const { shimmering, sweepTransition } = useSkeletonSweep({ ref, active: animate })
  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={`relative overflow-hidden rounded-md bg-muted${className ? ` ${className}` : ""}`}
      style={style}
    >
      {children ? <div className="invisible">{children}</div> : null}
      <motion.span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        // Rest state parked off-screen so no highlight shows when the loop is paused.
        style={{ transform: "translateX(-100%)", backgroundImage: SWEEP_GRADIENT }}
        animate={
          shimmering
            ? { transform: ["translateX(-100%)", "translateX(100%)"] }
            : undefined
        }
        transition={shimmering ? sweepTransition : undefined}
      />
    </div>
  )
}

/** Injects the `@property --wipe` registration and `::view-transition-*` mask rules for one handoff `name`. */
function SkeletonRevealStyle({ name }: { name: string }) {
  const css = `@property --wipe {
  syntax: "<percentage>";
  inherits: true;
  initial-value: -100%;
}
::view-transition-group(${name}) {
  overflow: hidden;
}
::view-transition-image-pair(${name}) {
  mix-blend-mode: normal;
}
::view-transition-old(${name}) {
  z-index: 2;
  mask-image: linear-gradient(to right, black var(--wipe), transparent calc(var(--wipe) + 100%));
  -webkit-mask-image: linear-gradient(to right, black var(--wipe), transparent calc(var(--wipe) + 100%));
}`
  return <style dangerouslySetInnerHTML={{ __html: css }} />
}

export interface SkeletonRevealProps {
  /** Whether the skeleton is showing (`true`) or loaded content has taken over (`false`). */
  loading: boolean
  /** The skeleton placeholder, shown while `loading`. */
  skeleton: ReactNode
  /** The loaded content, shown once `loading` is `false`. */
  children: ReactNode
  /** Shared view-transition name for the handoff. Defaults to `velocity-skeleton-card`. */
  name?: string
  /** Merged onto the wrapper around the handoff. */
  className?: string
}

/** Single-card load handoff via a left-to-right skeleton wipe. */
export function SkeletonReveal({
  loading,
  skeleton,
  children,
  name = "velocity-skeleton-card",
  className,
}: SkeletonRevealProps) {
  const { motionMode } = useMotionUITheme()
  const calm = motionMode === "calm"
  const motionAllowed = motionMode === "full"
  const gentle = useMotionUITransition("gentle")
  const wipeTransition = {
    type: "tween" as const,
    duration: gentle.duration,
    ease: gentle.ease,
  }
  const update: TargetAndTransition = motionAllowed
    ? { "--wipe": ["100%", "-100%"], transition: wipeTransition }
    : calm
      ? { transition: wipeTransition }
      : { transition: { duration: 0 } }

  return (
    <div className={className}>
      <SkeletonRevealStyle name={name} />
      <AnimateView name={name} update={update}>
        {loading ? skeleton : children}
      </AnimateView>
    </div>
  )
}

/** What `useSkeletonResolve` resolves for one row. */
export interface SkeletonResolve {
  /** True once this row has resolved to its loaded content. */
  loaded: boolean
  /** Whether full motion is allowed. */
  motionAllowed: boolean
  /** Transition for both layers, carrying the per-row `delay`. */
  transition: ReturnType<typeof useMotionUITransition> & { delay: number }
  /** `animate` target for the real-content layer. */
  content: { opacity: number; transform: string }
  /** `animate` target for the bones overlay. */
  skeleton: { opacity: number }
}

/** Options for `useSkeletonResolve`. */
export interface UseSkeletonResolveOptions {
  /** This row's position, used to stagger its handoff. */
  index: number
  /** Whether the feed is still loading. */
  loading: boolean
  /** Per-row delay step, in seconds. Defaults to the theme's `base` stagger. */
  stagger?: number
}

/** Resolves crossfade timing and animate targets for one staggered skeleton row. */
export function useSkeletonResolve({
  index,
  loading,
  stagger,
}: UseSkeletonResolveOptions): SkeletonResolve {
  const reveal = useMotionUITransition("ui")
  const theme = useMotionUITheme()
  const motionAllowed = theme.motionMode === "full"
  const loaded = !loading
  const step = stagger ?? theme.stagger.base
  // Stagger only on the way in; the reset snaps every row back together.
  const delay = loaded && motionAllowed ? index * step : 0
  return {
    loaded,
    motionAllowed,
    transition: { ...reveal, delay },
    content: {
      opacity: loaded ? 1 : 0,
      transform:
        loaded || !motionAllowed
          ? "translateY(0px)"
          : `translateY(${theme.travel.hover}px)`,
    },
    skeleton: { opacity: loaded ? 0 : 1 },
  }
}

interface SkeletonResolveContextValue {
  loading: boolean
  stagger?: number
}

const SkeletonResolveContext = createContext<SkeletonResolveContextValue | null>(
  null
)

export interface SkeletonResolveListProps {
  /** Whether the feed is still loading. Shared with every nested row. */
  loading: boolean
  /** The rows. Rendered as-is so you own the list element. */
  children: ReactNode
  /** Per-row delay step, in seconds. Defaults to the theme's `base` stagger. */
  stagger?: number
}

/** Shares one `loading` flag with nested `SkeletonResolveRow`s. Renders no DOM of its own. */
export function SkeletonResolveList({
  loading,
  children,
  stagger,
}: SkeletonResolveListProps) {
  return (
    <SkeletonResolveContext.Provider value={{ loading, stagger }}>
      {children}
    </SkeletonResolveContext.Provider>
  )
}

export interface SkeletonResolveRowProps {
  /** This row's position, used to stagger its handoff. */
  index: number
  /** The real content layer in normal flow. */
  content: ReactNode
  /** The bones overlay, faded out as the row resolves. */
  skeleton: ReactNode
  /** Whether the feed is loading. Inherited from `SkeletonResolveList` when omitted. */
  loading?: boolean
  /** Per-row delay step, in seconds. */
  stagger?: number
  /** Merged onto the row wrapper. */
  className?: string
}

/** One row of a staggered skeleton resolve with zero layout shift. */
export function SkeletonResolveRow({
  index,
  content,
  skeleton,
  loading,
  stagger,
  className,
}: SkeletonResolveRowProps) {
  const ctx = useContext(SkeletonResolveContext)
  const isLoading = loading ?? ctx?.loading ?? false
  const step = stagger ?? ctx?.stagger
  const { loaded, transition, content: contentTarget, skeleton: skeletonTarget } =
    useSkeletonResolve({ index, loading: isLoading, stagger: step })

  return (
    <div className={`relative${className ? ` ${className}` : ""}`}>
      <motion.div
        initial={false}
        animate={contentTarget}
        transition={transition}
        aria-hidden={!loaded}
      >
        {content}
      </motion.div>
      <motion.div
        className="pointer-events-none absolute inset-0"
        aria-hidden="true"
        initial={false}
        animate={skeletonTarget}
        transition={transition}
      >
        {skeleton}
      </motion.div>
    </div>
  )
}
