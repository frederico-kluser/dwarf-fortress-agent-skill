import { motion } from "motion/react"
import type { CSSProperties, ReactNode, Ref } from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

/** Default neutral fill: 55% `--foreground` into `--card`. */
const DEFAULT_FILL_TONE = "color-mix(in srgb, var(--foreground) 55%, var(--card))"

/** Reference tick tone: 62% `--foreground` into `--background`. */
const TICK_TONE = "color-mix(in srgb, var(--foreground) 62%, var(--background))"

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

export interface ProgressBarProps {
  /** Fill length in [0,1]. Clamped; drives width and `aria-valuenow` when `progressbar` is set. */
  value: number
  /** Animate the fill with a left-anchored `scaleX` reveal on the theme `ui` spring. */
  reveal?: boolean
  /** Gate for `reveal`. While false the fill holds at `scaleX 0`. Default `true`. */
  revealed?: boolean
  /** Stagger index: delay = `index * theme.stagger.base`. Default `0`. */
  index?: number
  /** Optional reference marker at a share in [0,1]. Decorative; `aria-hidden`. */
  referenceTick?: number
  /** Paint the fill with `bg-primary`. Default `false`. */
  highlight?: boolean
  /** Opaque colour for a non-highlighted fill. Ignored when `highlight` is set. */
  tone?: string
  /** Track height: `"sm"` (4px) or `"md"` (8px). */
  size?: "sm" | "md"
  /** Expose the track as an ARIA `progressbar`. */
  progressbar?: boolean
  /** Accessible name when `progressbar` is set. */
  "aria-label"?: string
  /** Leading label slot. */
  label?: ReactNode
  /** Trailing label slot. */
  valueLabel?: ReactNode
  /** Label row placement relative to the track. Default `"top"`. */
  labelPlacement?: "top" | "bottom"
  /** Merged onto the root wrapper. */
  className?: string
  /** Forwarded ref on the root wrapper. */
  ref?: Ref<HTMLDivElement>
}

/** Horizontal progress track with optional reveal, reference tick, and label slots. */
export function ProgressBar({
  value,
  reveal = false,
  revealed = true,
  index = 0,
  referenceTick,
  highlight = false,
  tone = DEFAULT_FILL_TONE,
  size = "md",
  progressbar = false,
  "aria-label": ariaLabel,
  label,
  valueLabel,
  labelPlacement = "top",
  className,
  ref,
}: ProgressBarProps) {
  const uiTheme = useMotionUITheme()
  const uiTransition = useMotionUITransition("ui")
  const motionMode = uiTheme.motionMode
  const still = motionMode === "off"
  const calm = motionMode === "calm"

  const share = clamp01(value)
  const viewport = { once: uiTheme.inView.once, amount: uiTheme.inView.amount }

  const fillMotion =
    !reveal || still
      ? { initial: false as const }
      : calm
        ? {
            initial: { opacity: 0 },
            whileInView: { opacity: 1 },
            viewport,
            transition: { ...uiTransition },
          }
        : {
            initial: { scaleX: 0 },
            animate: { scaleX: revealed ? 1 : 0 },
            transition: {
              ...uiTransition,
              delay: index * uiTheme.stagger.base,
            },
          }

  const fillStyle: CSSProperties = {
    width: `${share * 100}%`,
    ...(highlight ? {} : { backgroundColor: tone }),
  }

  const hasLabelRow = label != null || valueLabel != null
  const labelRow = hasLabelRow ? (
    <div className="flex w-full items-baseline justify-between gap-4">
      <span className="min-w-0">{label}</span>
      {valueLabel != null ? <span className="shrink-0">{valueLabel}</span> : null}
    </div>
  ) : null

  return (
    <div
      ref={ref}
      className={`flex w-full flex-col gap-2${className ? ` ${className}` : ""}`}
    >
      {labelPlacement === "top" ? labelRow : null}

      <div
        role={progressbar ? "progressbar" : undefined}
        aria-label={progressbar ? ariaLabel : undefined}
        aria-valuemin={progressbar ? 0 : undefined}
        aria-valuemax={progressbar ? 100 : undefined}
        aria-valuenow={progressbar ? Math.round(share * 100) : undefined}
        aria-hidden={progressbar ? undefined : true}
        className={`relative w-full overflow-hidden rounded-full bg-muted ${size === "sm" ? "h-1" : "h-2"}`}
      >
        <motion.div
          className={`absolute inset-y-0 left-0 origin-left rounded-full${highlight ? " bg-primary" : ""}`}
          style={fillStyle}
          {...fillMotion}
        />

        {referenceTick !== undefined ? (
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-[2px] -translate-x-1/2"
            style={{
              left: `${clamp01(referenceTick) * 100}%`,
              backgroundColor: TICK_TONE,
            }}
          />
        ) : null}
      </div>

      {labelPlacement === "bottom" ? labelRow : null}
    </div>
  )
}
