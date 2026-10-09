import { animate, motion, useMotionValue } from "motion/react"
import { useEffect, useId, useRef } from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

const NEUTRAL_LINE = "color-mix(in srgb, var(--foreground) 45%, var(--muted))"
const NEUTRAL_AREA = "color-mix(in srgb, var(--foreground) 10%, var(--muted))"
const GRID_STROKE =
  "color-mix(in srgb, var(--foreground) 8%, var(--background))"

const DEFAULT_WIDTH = 280
const DEFAULT_HEIGHT = 64
const DEFAULT_PAD_Y = 10

/** Geometry options for `buildSparkPath`. */
export interface SparkPathOptions {
  /** viewBox width the points span horizontally. Default 280. */
  width?: number
  /** viewBox height the points are scaled within. Default 64. */
  height?: number
  /** Vertical inset so peaks and troughs never touch the edge. Default 10. */
  padY?: number
}

/** The shapes `buildSparkPath` returns. */
export interface SparkGeometry {
  /** The `M ... L ...` line path across every reading. */
  d: string
  /** `d` closed down to the baseline and back. */
  areaD: string
  /** Every reading mapped to a viewBox point. */
  points: { x: number; y: number }[]
  /** The first (left-most) point. */
  first?: { x: number; y: number }
  /** The last (right-most) point. */
  last?: { x: number; y: number }
}

/** Maps a numeric `history` window to sparkline geometry. */
export function buildSparkPath(
  history: number[],
  options: SparkPathOptions = {},
): SparkGeometry {
  const {
    width = DEFAULT_WIDTH,
    height = DEFAULT_HEIGHT,
    padY = DEFAULT_PAD_Y,
  } = options
  if (history.length === 0) {
    return {
      d: "",
      areaD: "",
      points: [],
      first: undefined,
      last: undefined,
    }
  }
  const max = Math.max(...history)
  const min = Math.min(...history)
  const range = Math.max(1, max - min)
  const step = width / Math.max(1, history.length - 1)
  const points = history.map((value, i) => {
    const x = i * step
    const norm = (value - min) / range
    const y = height - padY - norm * (height - padY * 2)
    return { x, y }
  })
  const d = points.reduce(
    (path, point, i) =>
      i === 0 ? `M ${point.x},${point.y}` : `${path} L ${point.x},${point.y}`,
    "",
  )
  const first = points[0]
  const last = points[points.length - 1]
  const areaD = `${d} L ${last.x},${height} L ${first.x},${height} Z`
  return { d, areaD, points, first, last }
}

/** Motion targets for a data-driven state change. */
export function buildSparkAnimation(geometry: SparkGeometry) {
  return {
    line: { d: geometry.d },
    area: { d: geometry.areaD },
    dot: geometry.last
      ? { cx: geometry.last.x, cy: geometry.last.y }
      : undefined,
  }
}

/** The line/area colour tier. */
export type SparklineTone = "primary" | "neutral"

export interface SparklineProps {
  /** Data mode: a window of readings mapped to the curve. */
  history?: number[]
  /** Literal mode: a hand-drawn SVG path `d` string. */
  path?: string
  /** viewBox width. Default 280. */
  width?: number
  /** viewBox height. Default 64. */
  height?: number
  /** Vertical inset for `history` mapping. Default 10. */
  padY?: number
  /** Line/area colour tier. Default `"primary"`. */
  tone?: SparklineTone
  /** Render the area fill under the line. Default false. */
  area?: boolean
  /** Faint horizontal gridline y-positions in viewBox coordinates. */
  grid?: number[]
  /** Render the accent endpoint dot. Default false. */
  dot?: boolean
  /** Endpoint-dot x in literal mode. Defaults to `width`. */
  dotX?: number
  /** Endpoint-dot y in literal mode. Defaults to 0. */
  dotY?: number
  /** Endpoint-dot radius. Default 3.5. */
  dotRadius?: number
  /** Draw the line in once via a `pathLength` reveal. Default false. */
  draw?: boolean
  /** Gate for the `draw` reveal. Default true. */
  revealed?: boolean
  /** Bump this on every live tick to pulse the endpoint dot. */
  tickKey?: number
  /** Line stroke width, in viewBox units. Default 2. */
  strokeWidth?: number
  /** Keep the stroke a constant screen width regardless of transform scaling. */
  nonScalingStroke?: boolean
  /** Reduced-motion gate. Derived from the theme when omitted. */
  motionAllowed?: boolean
  /** Accessible label. Exposes `role="img"` when set. */
  label?: string
  /** Merged onto the `<svg>`. */
  className?: string
}

/** Line and area micro-chart with optional draw-in and live morphing. */
export function Sparkline({
  history,
  path,
  width = DEFAULT_WIDTH,
  height = DEFAULT_HEIGHT,
  padY = DEFAULT_PAD_Y,
  tone = "primary",
  area = false,
  grid,
  dot = false,
  dotX,
  dotY,
  dotRadius = 3.5,
  draw = false,
  revealed = true,
  tickKey,
  strokeWidth = 2,
  nonScalingStroke = false,
  motionAllowed,
  label,
  className,
}: SparklineProps) {
  const { motionMode } = useMotionUITheme()
  const fullMotion = motionMode === "full"
  const allowMotion = motionAllowed ?? fullMotion
  const gentle = useMotionUITransition("gentle")
  const ui = useMotionUITransition("ui")
  const snap = useMotionUITransition("snap")

  // useId can emit colons, which are invalid inside a url(#...) reference.
  const gradientId = `spark-fill-${useId().replace(/:/g, "")}`

  const dotScale = useMotionValue(1)
  const firstTick = useRef(true)
  useEffect(() => {
    if (firstTick.current) {
      firstTick.current = false
      return
    }
    if (!allowMotion || tickKey === undefined) return
    const controls = animate(dotScale, [1, 1.7, 1], { ...snap })
    return () => controls.stop()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickKey, allowMotion])

  let lineD = ""
  let areaD = ""
  let dotPos = { x: dotX ?? width, y: dotY ?? 0 }
  let stateAnimation: ReturnType<typeof buildSparkAnimation> | undefined
  if (history) {
    const geometry = buildSparkPath(history, { width, height, padY })
    lineD = geometry.d
    areaD = geometry.areaD
    if (geometry.last) dotPos = geometry.last
    stateAnimation = buildSparkAnimation(geometry)
  } else if (path) {
    lineD = path
    areaD = `${path} L ${width} ${height} L 0 ${height} Z`
  }

  const drawing = draw && allowMotion
  const hasLine = lineD.length > 0
  const morphing = Boolean(history?.length && allowMotion)
  const strokeColor = tone === "primary" ? "var(--primary)" : NEUTRAL_LINE
  const morphTransition = { ...ui, type: "tween" as const }
  const lineAnimation = drawing
    ? {
        ...(morphing ? stateAnimation?.line : undefined),
        pathLength: revealed ? 1 : 0,
      }
    : morphing
      ? stateAnimation?.line
      : draw
        ? { pathLength: 1 }
        : undefined

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`w-full${className ? ` ${className}` : ""}`}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : "true"}
    >
      {area && hasLine && tone === "primary" && (
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
          </linearGradient>
        </defs>
      )}

      {grid?.map((y) => (
        <line
          key={y}
          x1="0"
          y1={y}
          x2={width}
          y2={y}
          strokeWidth="1"
          style={{ stroke: GRID_STROKE }}
        />
      ))}

      {area &&
        hasLine &&
        (tone === "primary" ? (
          <motion.path
            d={areaD}
            fill={`url(#${gradientId})`}
            animate={morphing ? stateAnimation?.area : undefined}
            transition={morphing ? morphTransition : undefined}
          />
        ) : (
          <motion.path
            d={areaD}
            style={{ fill: NEUTRAL_AREA }}
            animate={morphing ? stateAnimation?.area : undefined}
            transition={morphing ? morphTransition : undefined}
          />
        ))}

      <motion.path
        d={lineD}
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect={nonScalingStroke ? "non-scaling-stroke" : undefined}
        initial={drawing ? { pathLength: 0 } : false}
        animate={lineAnimation}
        transition={
          drawing
            ? {
                d: morphTransition,
                pathLength: { ...gentle, type: "tween" },
              }
            : morphing
              ? morphTransition
              : undefined
        }
      />

      {dot && hasLine && (
        <motion.circle
          cx={dotPos.x}
          cy={dotPos.y}
          r={dotRadius}
          fill="var(--primary)"
          animate={morphing ? stateAnimation?.dot : undefined}
          transition={morphing ? morphTransition : undefined}
          style={
            tickKey !== undefined
              ? {
                  scale: dotScale,
                  transformBox: "fill-box",
                  willChange: "transform",
                }
              : undefined
          }
        />
      )}
    </svg>
  )
}
