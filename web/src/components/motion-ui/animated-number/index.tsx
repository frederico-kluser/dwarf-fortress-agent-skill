import { useMemo, useRef, type ReactNode } from "react"
import { useInView } from "motion/react"
import { AnimateNumber, type AnimateNumberProps } from "motion-plus/react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"
import type { TransitionName } from "@/components/motion-ui/ui-theme"

/** `Intl.NumberFormat` options accepted by `AnimatedNumber`. */
export type NumberFormat = AnimateNumberProps["format"]

/**
 * Format for one value. A currency with no explicit fraction digits drops
 * zero minor units, so `24` reads "£24" and `24.5` still reads "£24.50".
 */
export function resolveNumberFormat(
  value: number,
  format?: NumberFormat,
  locales?: Intl.LocalesArgument
): NumberFormat {
  if (
    format?.style !== "currency" ||
    format.minimumFractionDigits !== undefined ||
    format.maximumFractionDigits !== undefined ||
    "trailingZeroDisplay" in format
  ) {
    return format
  }
  const fraction = new Intl.NumberFormat(
    locales as Intl.LocalesArgument,
    format as Intl.NumberFormatOptions
  )
    .formatToParts(value)
    .find((part) => part.type === "fraction")
  if (!fraction || /[^0]/.test(fraction.value)) return format
  return { ...format, minimumFractionDigits: 0, maximumFractionDigits: 0 }
}

/** Plain formatted string for a value (`prefix` + formatted number + `suffix`). */
export function formatNumberDisplay(
  value: number,
  options: {
    /** `Intl.NumberFormat` options, matching the `format` prop. */
    format?: NumberFormat
    /** Locale(s) for `Intl.NumberFormat`, matching the `locales` prop. */
    locales?: Intl.LocalesArgument
    /** Text before the number, matching the `prefix` prop. */
    prefix?: string
    /** Text after the number, matching the `suffix` prop. */
    suffix?: string
  } = {}
): string {
  const { format, locales, prefix = "", suffix = "" } = options
  const formatted = new Intl.NumberFormat(
    locales as Intl.LocalesArgument,
    resolveNumberFormat(value, format, locales) as Intl.NumberFormatOptions
  ).format(value)
  return `${prefix}${formatted}${suffix}`
}

/** Inputs that decide which value the roll currently targets. */
export interface ResolveDisplayValueOptions {
  /** The real, final value the figure represents. */
  value: number
  /** The value held before the reveal gate opens. */
  from?: number
  /** Whether motion is allowed at all. */
  motionAllowed: boolean
  /** Parent-driven gate: `true` shows `value`, `false` holds `from`. */
  revealed?: boolean
  /** Self-gate count-up on entering the viewport. */
  startOnView?: boolean
  /** Whether the figure is currently in view. */
  inView?: boolean
}

/** Resolves the value the roll should currently target. */
export function resolveDisplayValue({
  value,
  from = 0,
  motionAllowed,
  revealed,
  startOnView = false,
  inView = false,
}: ResolveDisplayValueOptions): number {
  if (!motionAllowed) return value
  const gateOpen = revealed ?? (startOnView ? inView : true)
  return gateOpen ? value : from
}

const SUFFIX_TREATMENT =
  "[&_.number-section-post]:self-end [&_.number-section-post]:text-sm [&_.number-section-post]:leading-[inherit] [&_.number-section-post]:font-medium [&_.number-section-post]:tracking-normal [&_.number-section-post]:normal-nums [&_.number-section-post]:text-muted-foreground"

export interface AnimatedNumberProps {
  /** The value the figure shows. */
  value: number
  /** Value held before the reveal gate opens. Defaults to `0`. */
  from?: number
  /** Hold at `from` until the figure scrolls into view. */
  startOnView?: boolean
  /** Parent-driven reveal gate. Overrides `startOnView`. */
  revealed?: boolean
  /** Named theme transition timing the roll. Defaults to `"ui"`. */
  transition?: TransitionName
  /** `Intl.NumberFormat` options. Currencies hide zero minor units unless fraction digits are set. */
  format?: NumberFormat
  /** Locale(s) for `Intl.NumberFormat`. */
  locales?: Intl.LocalesArgument
  /** Static text before the number. */
  prefix?: string
  /** Static text after the number, styled as the unit. */
  suffix?: string
  /** Digit spin direction (`trend`). */
  trend?: AnimateNumberProps["trend"]
  /** Accessible spoken value. Pass `null` to suppress built-in `sr-only`. */
  spokenValue?: ReactNode | null
  /** Merged onto the figure. */
  className?: string
}

/** Theme-bound digit roll with width fix and accessible pairing. */
export function AnimatedNumber({
  value,
  from = 0,
  startOnView = false,
  revealed,
  transition = "ui",
  format,
  locales,
  prefix,
  suffix,
  trend,
  spokenValue,
  className,
}: AnimatedNumberProps) {
  const uiTheme = useMotionUITheme()
  const motionAllowed = uiTheme.motionMode === "full"
  const token = useMotionUITransition(transition)

  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, {
    amount: uiTheme.inView.amount,
    once: uiTheme.inView.once,
  })

  const shown = resolveDisplayValue({
    value,
    from,
    motionAllowed,
    revealed,
    startOnView,
    inView,
  })
  const shownFormat = useMemo(
    () => resolveNumberFormat(shown, format, locales),
    [shown, format, locales]
  )

  const rollTransition = motionAllowed
    ? {
        ...token,
        opacity: { duration: token.duration, ease: "linear" as const },
        width: { duration: 0 },
      }
    : { duration: 0 }

  const spoken =
    spokenValue === undefined
      ? formatNumberDisplay(value, { format, locales, prefix, suffix })
      : spokenValue

  return (
    <>
      <AnimateNumber
        ref={ref}
        aria-hidden="true"
        className={`tabular-nums ${SUFFIX_TREATMENT}${className ? ` ${className}` : ""}`}
        format={shownFormat}
        locales={locales}
        prefix={prefix}
        suffix={suffix}
        trend={trend}
        transition={rollTransition}
      >
        {shown}
      </AnimateNumber>
      {spoken !== null && <span className="sr-only">{spoken}</span>}
    </>
  )
}
