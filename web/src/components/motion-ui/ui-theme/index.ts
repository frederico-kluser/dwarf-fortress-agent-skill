import { createGeneratorEasing, generateLinearEasing, spring } from "motion"
import { useReducedMotion } from "motion/react"
import { createContext, createElement, useContext, useMemo } from "react"
import type { ReactNode } from "react"

// ==========================================================================
// Types — the theme schema every section references by name (never by literal
// value), so retuning the whole site is a single-file edit.
// ==========================================================================

/** A cubic-bezier control-point tuple. */
export type CubicBezier = readonly [number, number, number, number]

/**
 * A named transition. One token carries both channels of a state change:
 *
 * - The physics half (`stiffness` + `damping`) drives travel — transforms,
 *   layout morphs, anything that moves. It is a valid Motion spring
 *   transition as-is (`type: "spring"`) and carries velocity across
 *   interruptions.
 * - The `ease` + `duration` half is the matched channel for fades
 *   (opacity, colour), so both channels land together.
 *
 * `duration` is the former spring's visual duration, retained as the explicit
 * timing for non-physics channels and CSS output.
 */
export interface TransitionToken {
  type: "spring"
  /** Spring stiffness, resolved from the default's former visual duration. */
  stiffness: number
  /** Spring damping, resolved from the default's former bounce. */
  damping: number
  /** Companion fade/colour duration in seconds. */
  duration: number
  /** Companion curve for fades (opacity/colour), over `duration`. */
  ease: CubicBezier
}

/** The five feel-vocabulary transitions every section resolves by name. */
export interface TransitionTokens {
  /** Instant feedback: toggles, tabs, hovers. */
  snap: TransitionToken
  /** Default: menus, cards, reveals. */
  ui: TransitionToken
  /** Large surfaces: sections, sheets, curtains. */
  gentle: TransitionToken
  /** Celebratory: confetti, badges, counters. */
  lively: TransitionToken
  /**
   * Continuous background motion: pulses, sweeps, blinks. `duration` is the
   * cycle length; consumers usually pair it with `repeat: Infinity`.
   */
  ambient: TransitionToken
}

/** Names of the transitions, usable as `useMotionUITransition(name)`. */
export type TransitionName = keyof TransitionTokens

/**
 * The resolved transition returned by `useMotionUITransition`: the token plus
 *
 * - `duration`, supplied by the token, so `{ ...t, type: "tween" }` degrades
 *   to an eased tween of the same visual length. Motion ignores this timing
 *   parameter while the physics spring is active.
 * - `opacity`, a baked per-value tween so fades ride along with the spring
 *   and land together. Its ease is always `"linear"` — opacity is
 *   perceptually compressed, so the token's decelerating curve front-loads
 *   visibility and reads as a pop — while the token's `ease` remains the
 *   curve for colour and CSS channels. It carries `inherit: true` so
 *   top-level keys a consumer adds next to the spread (`repeat`, `delay`,
 *   `times`, ...) still reach the opacity channel — Motion resolves a
 *   per-value transition INSTEAD of the top level unless it inherits.
 *   Override with `opacity: { ... }` after the spread when a section needs
 *   something else.
 */
export interface UITransition extends TransitionToken {
  opacity: {
    type: "tween"
    duration: number
    ease: "linear"
    inherit: true
  }
}

/** Orchestration: stagger between children, in seconds. */
export interface StaggerTokens {
  tight: number
  base: number
  relaxed: number
}

export type StaggerName = keyof StaggerTokens

/** How far things travel on enter/hover, in pixels. */
export interface TravelTokens {
  hover: number
  enter: number
  section: number
}

export type TravelName = keyof TravelTokens

/** Viewport-entry defaults for `whileInView` sections. */
export interface InViewTokens {
  /** Fraction of the element that must be visible before it animates in. */
  amount: number
  /** Animate only the first time it enters the viewport. */
  once: boolean
}

/**
 * How to degrade when the user asks for reduced motion.
 * - `"calm"`: kill travel, keep opacity fades (duration-based).
 * - `"off"`: no animation at all.
 */
export type ReducedMotionStrategy = "calm" | "off"

/** The full, resolved theme. `defineTheme` always returns this complete shape. */
export interface MotionUITheme {
  transitions: TransitionTokens
  stagger: StaggerTokens
  travel: TravelTokens
  inView: InViewTokens
  reducedMotion: ReducedMotionStrategy
}

/** Recursive partial: any subtree of a theme may be omitted in a config. */
export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends readonly unknown[]
    ? T[K]
    : T[K] extends object
      ? DeepPartial<T[K]>
      : T[K]
}

/** A partial theme config, as passed to `defineTheme`. */
export type MotionUIThemeConfig = DeepPartial<MotionUITheme>

// ==========================================================================
// The default theme — Eased restraint. Short perceived durations with no
// bounce, tight stagger, small travel. Motion is felt as crisp responsiveness,
// never as spectacle. These physics values are Motion's exact
// resolution of the former visualDuration/bounce tuning: physical springs
// preserve momentum when an animation is interrupted, unlike time-defined
// springs, while `duration` retains the original timing for fade and CSS
// channels.
// ==========================================================================

function transition(
  duration: number,
  stiffness: number,
  damping: number,
  ease: CubicBezier
): TransitionToken {
  return { type: "spring", duration, stiffness, damping, ease }
}

const defaultOut: CubicBezier = [0.22, 1, 0.36, 1]
const defaultInOut: CubicBezier = [0.65, 0, 0.35, 1]

/** The bundled default theme, used when no provider is mounted. */
export const defaultTheme: MotionUITheme = {
  transitions: {
    snap: transition(0.15, 1218.4696791468346, 69.81317007977319, defaultOut),
    ui: transition(0.3, 304.61741978670864, 33.16125578789226, defaultOut),
    gentle: transition(0.5, 109.6622711232151, 19.896753472735355, defaultOut),
    lively: transition(0.21, 621.668203646344, 17.453292519943293, defaultOut),
    ambient: transition(0.8, 42.8368246575059, 13.089969389957473, defaultInOut),
  },
  stagger: { tight: 0.04, base: 0.08, relaxed: 0.15 },
  travel: { hover: 4, enter: 24, section: 48 },
  inView: { amount: 0.4, once: true },
  reducedMotion: "calm",
}

// ==========================================================================
// defineTheme — build a complete theme from a partial config.
// ==========================================================================

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  )
}

/**
 * Deep-merge a partial config over a complete base. Plain objects merge
 * recursively; arrays (easing tuples) and primitives replace wholesale, so an
 * easing override is taken as a complete tuple rather than merged element-wise.
 */
function deepMerge<T>(base: T, override: unknown): T {
  // Only plain-object-over-plain-object recurses; anything else (primitive,
  // array/easing tuple) replaces the base value wholesale.
  if (!isPlainObject(base) || !isPlainObject(override)) return override as T

  const result: Record<string, unknown> = { ...base }
  for (const key of Object.keys(override)) {
    const overrideValue = override[key]
    if (overrideValue === undefined) continue
    result[key] = deepMerge(
      (base as Record<string, unknown>)[key],
      overrideValue
    )
  }
  return result as T
}

/**
 * Build a complete, resolved `MotionUITheme` from a partial config by
 * deep-merging over the bundled `defaultTheme`. Any omitted subtree falls back
 * to the default, so partial configs are always valid.
 *
 * @example
 * defineTheme({ transitions: { ui: { stiffness: 300, damping: 20 } } })
 */
export function defineTheme(config: MotionUIThemeConfig = {}): MotionUITheme {
  // structuredClone so the resolved theme never aliases defaultTheme's
  // sub-objects: deepMerge only recurses into overridden branches, leaving
  // untouched subtrees (e.g. `travel` when only `transitions` is overridden)
  // pointing at the shared default. Mutating one of those would corrupt
  // defaultTheme.
  return structuredClone(deepMerge(defaultTheme, config))
}

// ==========================================================================
// Presets — named motion personalities for previews and host applications.
// defaultTheme remains unchanged and becomes Motion's own preview feel.
// ==========================================================================

/** Relaxed, spacious motion for the Eased preview identity. */
export const easedTheme = defineTheme({
  transitions: {
    snap: transition(0.18, 846, 52, [0.16, 1, 0.3, 1]),
    ui: transition(0.42, 155, 24, [0.16, 1, 0.3, 1]),
    gentle: transition(0.7, 65, 15, [0.16, 1, 0.3, 1]),
    lively: transition(0.35, 250, 18, [0.2, 0.9, 0.2, 1.08]),
    ambient: transition(1.2, 19, 9, [0.65, 0, 0.35, 1]),
  },
  stagger: { tight: 0.06, base: 0.12, relaxed: 0.22 },
  travel: { hover: 6, enter: 32, section: 64 },
  inView: { amount: 0.3, once: true },
})

/** Motion's existing, precise motion vocabulary. */
export const motionTheme = defaultTheme

/** Restrained, decisive motion for the Edition 01 preview identity. */
export const edition01Theme = defineTheme({
  transitions: {
    snap: transition(0.14, 1400, 80, [0.16, 1, 0.3, 1]),
    ui: transition(0.28, 390, 40, [0.16, 1, 0.3, 1]),
    gentle: transition(0.42, 150, 27, [0.16, 1, 0.3, 1]),
    lively: transition(0.22, 700, 33, [0.16, 1, 0.3, 1]),
    ambient: transition(0.7, 55, 16, [0.65, 0, 0.35, 1]),
  },
  stagger: { tight: 0.03, base: 0.06, relaxed: 0.11 },
  travel: { hover: 2, enter: 14, section: 28 },
  inView: { amount: 0.5, once: true },
})

/** @deprecated Use easedTheme. */
export const editorialTheme = easedTheme

/** @deprecated Use edition01Theme. */
export const productTheme = edition01Theme

/** Stable names used by active preview controls and persisted URLs/state. */
export const MOTION_UI_THEME_NAMES = ["motion", "eased"] as const

export type MotionUIThemeName = (typeof MOTION_UI_THEME_NAMES)[number]

/** Named motion-preset lookup. Preview visual identifiers are defined separately. */
export const motionUIThemes: Record<MotionUIThemeName, MotionUITheme> = {
  eased: easedTheme,
  motion: motionTheme,
}

// ==========================================================================
// Reduced motion — resolve how a section degrades for the current visitor.
// ==========================================================================

/** Runtime motion posture resolved for the current visitor. */
export type MotionMode = "full" | ReducedMotionStrategy

/** The resolved motion posture a section should render with. */
export interface ResolvedReducedMotion {
  /** Which posture is in effect. */
  strategy: MotionMode
  /** Whether to animate at all (`false` only under `"off"`). */
  animate: boolean
  /** Whether positional/scale travel is permitted (`false` under `"calm"`/`"off"`). */
  travel: boolean
  /** Whether only opacity (duration-based fades) should animate. */
  opacityOnly: boolean
}

/**
 * Resolve how a section should behave given the theme's reduced-motion strategy
 * and whether the user currently prefers reduced motion.
 *
 * - Not reduced: `strategy: "full"` (animate, travel, no restriction).
 * - Reduced + theme `"calm"`: `strategy: "calm"` (animate opacity only, no travel).
 * - Reduced + theme `"off"`: `strategy: "off"` (do not animate).
 *
 * React components normally consume the equivalent `motionMode` from
 * `useMotionUITheme()`. This pure resolver remains useful outside React.
 */
export function resolveReducedMotion(
  theme: MotionUITheme,
  prefersReducedMotion: boolean
): ResolvedReducedMotion {
  if (!prefersReducedMotion) {
    return { strategy: "full", animate: true, travel: true, opacityOnly: false }
  }
  if (theme.reducedMotion === "off") {
    return { strategy: "off", animate: false, travel: false, opacityOnly: false }
  }
  return { strategy: "calm", animate: true, travel: false, opacityOnly: true }
}

// ==========================================================================
// CSS emit — mirror the JS vocabulary as `--motion-ui-*` custom properties so
// pure-CSS states (`:hover`, `[data-state]`) share the exact same feel as the
// JS-driven sections. Each transition token emits both of its channels: the
// fade ease + duration (the common CSS need, colour/opacity transitions), and
// the spring compiled to a `linear()` easing sampled from Motion's own spring
// generator (the same one that drives the JS animations), guaranteeing the two
// channels match.
// ==========================================================================

/** Format a number without a trailing `.0` / exponent, for CSS output. */
function num(value: number): string {
  return Number(value.toFixed(4)).toString()
}

function cubicBezier(points: CubicBezier): string {
  return `cubic-bezier(${points.map(num).join(", ")})`
}

/**
 * Compile a transition token's spring channel to a CSS `linear()` easing
 * string plus its natural settle duration in seconds, using Motion's spring
 * generator + `generateLinearEasing` (its own exported utilities), so no
 * bespoke sampler is needed.
 */
export function transitionToLinear(token: TransitionToken): {
  easing: string
  duration: number
} {
  const easing = createGeneratorEasing(
    { stiffness: token.stiffness, damping: token.damping },
    100,
    spring
  )
  return {
    // generateLinearEasing takes duration in milliseconds.
    easing: generateLinearEasing(easing.ease, easing.duration * 1000),
    duration: easing.duration,
  }
}

/**
 * Emit the theme as a flat record of `--motion-ui-*` custom properties. Per
 * transition token: the fade channel as `--motion-ui-transition-<name>`
 * (`cubic-bezier()`) + `--motion-ui-transition-<name>-duration` (the fade
 * duration, in `s`), and the spring channel as
 * `--motion-ui-transition-<name>-spring` (`linear()`) +
 * `--motion-ui-transition-<name>-spring-duration` (its settle time). Stagger
 * emits in `s`, travel in `px`.
 */
export function themeToCssVars(theme: MotionUITheme): Record<string, string> {
  const vars: Record<string, string> = {}

  const transitionNames = Object.keys(theme.transitions) as TransitionName[]
  for (const name of transitionNames) {
    const token = theme.transitions[name]
    vars[`--motion-ui-transition-${name}`] = cubicBezier(token.ease)
    vars[`--motion-ui-transition-${name}-duration`] =
      `${num(token.duration)}s`
    const { easing, duration } = transitionToLinear(token)
    vars[`--motion-ui-transition-${name}-spring`] = easing
    vars[`--motion-ui-transition-${name}-spring-duration`] = `${num(duration)}s`
  }

  for (const [name, value] of Object.entries(theme.stagger)) {
    vars[`--motion-ui-stagger-${name}`] = `${num(value)}s`
  }

  for (const [name, value] of Object.entries(theme.travel)) {
    vars[`--motion-ui-travel-${name}`] = `${num(value)}px`
  }

  return vars
}

/**
 * Render the theme's custom properties as a `:root { ... }` CSS block, for a
 * build step or a `<style>` tag.
 */
export function cssVarsToStyleString(theme: MotionUITheme): string {
  const vars = themeToCssVars(theme)
  const body = Object.entries(vars)
    .map(([name, value]) => `  ${name}: ${value};`)
    .join("\n")
  return `:root {\n${body}\n}`
}

// ==========================================================================
// React bindings — everything falls back to the bundled `defaultTheme` when no
// provider is mounted, so a section is always usable standalone (a buyer can
// paste one section in with no setup and it still reads a coherent theme).
// ==========================================================================

const UIThemeContext = createContext<MotionUITheme | null>(null)

export interface MotionUIThemeProviderProps {
  theme?: MotionUITheme
  children: ReactNode
}

/**
 * Provide a theme to every Motion UI section below it. Omitting `theme` supplies
 * the default, which is also what `useMotionUITheme` returns with no provider
 * present, so wrapping is optional.
 */
export function MotionUIThemeProvider({
  theme,
  children,
}: MotionUIThemeProviderProps) {
  const value = theme ?? defaultTheme
  return createElement(UIThemeContext.Provider, { value }, children)
}

function useThemeContext(): MotionUITheme {
  return useContext(UIThemeContext) ?? defaultTheme
}

/** The active theme plus the resolved runtime motion posture. */
export interface ResolvedMotionUITheme extends MotionUITheme {
  /**
   * The posture resolved from the visitor's reduced-motion preference and the
   * theme's configured `reducedMotion` strategy.
   */
  motionMode: MotionMode
}

/**
 * Read the active Motion UI theme and its resolved runtime motion posture.
 * Returns `defaultTheme` when no provider is mounted.
 *
 * `reducedMotion` remains the configured fallback (`"calm"` or `"off"`).
 * `motionMode` is the posture currently in effect (`"full"`, `"calm"` or
 * `"off"`), so components do not need their own reduced-motion hook.
 */
export function useMotionUITheme(): ResolvedMotionUITheme {
  const theme = useThemeContext()
  const prefersReducedMotion = !!useReducedMotion()
  const motionMode = resolveReducedMotion(theme, prefersReducedMotion).strategy

  return useMemo(
    () => ({ ...theme, motionMode }),
    [theme, motionMode],
  )
}

/**
 * Resolve a named transition to a Motion-compatible transition object, ready
 * to spread into `transition` on a `motion` component. Works with no provider
 * (falls back to the default).
 *
 * The result carries both channels of the token: the spring
 * (`stiffness` + `damping`) drives travel and preserves velocity across
 * interruptions, and a baked per-value
 * `opacity` tween fades over the same perceived duration so both land
 * together. The fade is deliberately LINEAR (the token's `ease` stays for
 * colour and CSS channels): opacity is perceptually compressed, so an eased
 * fade front-loads visibility and reads as a pop; linear is what looks
 * evenly paced. `{ ...transition, type: "tween" }` degrades to an eased
 * tween over the token's duration.
 */
export function useMotionUITransition(name: TransitionName): UITransition {
  const theme = useThemeContext()
  const token = theme.transitions[name]
  return useMemo(() => {
    const { duration, stiffness, damping, ease } = token
    return {
      type: "spring",
      stiffness,
      damping,
      duration,
      ease,
      // inherit: true lets consumer-added top-level keys (repeat, delay,
      // times, ...) reach the opacity channel; without it Motion resolves a
      // per-value transition INSTEAD of the top level, so a spread pulse
      // would play its fade exactly once.
      opacity: { type: "tween", duration, ease: "linear", inherit: true },
    }
  }, [token])
}
