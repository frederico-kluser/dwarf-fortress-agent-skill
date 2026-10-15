"use client"

import { animate } from "motion/react"
import type { DOMKeyframesDefinition } from "motion/react"
import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Ref,
} from "react"
import { useMotionUITheme } from "@/components/motion-ui/ui-theme"

const CONFETTI_DURATION = 1.6
const CONFETTI_DECAY = 0.91
const CONFETTI_GRAVITY = 1
const CONFETTI_DRIFT = 0
const CONFETTI_SIZE = 0.85
const CONFETTI_KEYFRAME_STEPS = 40
const CONFETTI_SCALE_DURATION_FRACTION = 0.08
const CONFETTI_SHAPES: ConfettiParticleData["shape"][] = [
  "circle",
  "rect",
  "rect",
  "strip",
  "strip",
]

const CONFETTI_COLORS = [
  "var(--primary)",
  "color-mix(in srgb, var(--primary) 70%, var(--foreground))",
  "var(--foreground)",
]

/** One confetti piece's pre-computed animation track. */
export interface ConfettiParticleData {
  /** Transform and opacity keyframe arrays. */
  keyframes: DOMKeyframesDefinition
  /** Play length in seconds. */
  duration: number
  /** Base size in px. */
  size: number
  /** Fill colour as a live `var()` reference. */
  color: string
  /** Piece silhouette. */
  shape: "circle" | "rect" | "strip"
}

/** One fired burst with stable id and spawned particles. */
export interface ConfettiBurst {
  /** Stable id for cleanup. */
  id: number
  /** Particles this burst spawned. */
  particles: ConfettiParticleData[]
}

/** Imperative handle to fire a burst. */
export interface ConfettiHandle {
  /** Fire one celebration burst from the layer's centre. */
  burst: () => void
}

/** Builds confetti particles with pre-computed keyframe tracks. */
export function buildConfettiParticles(
  particleCount: number,
  spread: number,
  startVelocity: number,
  colors: readonly string[] = CONFETTI_COLORS
): ConfettiParticleData[] {
  const ticks = Math.round(CONFETTI_DURATION * 60)
  const palette = colors.length > 0 ? colors : CONFETTI_COLORS

  return Array.from({ length: particleCount }, () => {
    const radSpread = spread * (Math.PI / 180)
    const angle = -Math.PI / 2 + (0.5 * radSpread - Math.random() * radSpread)
    const velocity = startVelocity * 0.5 + Math.random() * startVelocity
    const wobbleSpeed = Math.min(0.11, Math.random() * 0.1 + 0.05)
    const wobbleOffset = Math.random() * 10
    const pieceSize = 6 * CONFETTI_SIZE + Math.random() * 6 * CONFETTI_SIZE
    const tiltRotations = 2 + Math.random() * 4
    const rotation = Math.random() * 360

    const keyframes = computeConfettiKeyframes({
      angle,
      startVelocity: velocity,
      decay: CONFETTI_DECAY,
      gravity: CONFETTI_GRAVITY,
      drift: CONFETTI_DRIFT,
      wobbleSpeed,
      wobbleOffset,
      size: CONFETTI_SIZE,
      ticks,
      tiltRotations,
      rotation,
    })

    return {
      keyframes,
      duration: CONFETTI_DURATION,
      size: pieceSize,
      color: palette[Math.floor(Math.random() * palette.length)],
      shape: CONFETTI_SHAPES[Math.floor(Math.random() * CONFETTI_SHAPES.length)],
    }
  })
}

/** Simulates one particle's flight and samples keyframes. */
export function computeConfettiKeyframes(params: {
  angle: number
  startVelocity: number
  decay: number
  gravity: number
  drift: number
  wobbleSpeed: number
  wobbleOffset: number
  size: number
  ticks: number
  tiltRotations: number
  rotation: number
}): DOMKeyframesDefinition {
  const {
    angle,
    startVelocity,
    decay,
    gravity,
    drift,
    wobbleSpeed,
    wobbleOffset,
    size,
    ticks,
    tiltRotations,
    rotation,
  } = params

  const transform: string[] = []
  const opacity: number[] = []

  let velocity = startVelocity
  let x = 0
  let y = 0
  let wobble = wobbleOffset
  let tick = 0

  for (let step = 0; step <= CONFETTI_KEYFRAME_STEPS; step++) {
    const t = step / CONFETTI_KEYFRAME_STEPS

    if (step > 0) {
      const targetTick = Math.round((step * ticks) / CONFETTI_KEYFRAME_STEPS)
      while (tick < targetTick) {
        x += Math.cos(angle) * velocity + drift
        y += Math.sin(angle) * velocity + gravity * 3
        velocity *= decay
        wobble += wobbleSpeed
        tick++
      }
    }

    const wx = step === 0 ? 0 : x + Math.cos(wobble) * 15 * size
    const wy = y

    let scale: number
    if (t < CONFETTI_SCALE_DURATION_FRACTION * 0.6) {
      scale = (t / (CONFETTI_SCALE_DURATION_FRACTION * 0.6)) * 1.15
    } else if (t < CONFETTI_SCALE_DURATION_FRACTION) {
      const st =
        (t - CONFETTI_SCALE_DURATION_FRACTION * 0.6) /
        (CONFETTI_SCALE_DURATION_FRACTION * 0.4)
      scale = 1.15 - st * 0.15
    } else {
      scale = 1
    }

    const rotateY = tiltRotations * 360 * t

    let opacityKeyframe: number
    if (t <= 0.5) {
      opacityKeyframe = 1
    } else if (t <= 0.8) {
      opacityKeyframe = 1 - ((t - 0.5) / 0.3) * 0.5
    } else {
      opacityKeyframe = 0.5 - ((t - 0.8) / 0.2) * 0.5
    }

    transform.push(
      `translate(${wx}px, ${wy}px) scale(${scale}) rotateY(${rotateY}deg) rotate(${rotation}deg)`
    )
    opacity.push(opacityKeyframe)
  }

  return { transform, opacity }
}

/** Renders and plays one confetti particle. */
export function ConfettiPiece({ particle }: { particle: ConfettiParticleData }) {
  const ref = useRef<HTMLDivElement>(null)
  const { keyframes, duration, size, color, shape } = particle

  const width =
    shape === "strip" ? size * 0.3 : shape === "rect" ? size * 0.7 : size
  const height = shape === "strip" ? size * 2 : size
  // Particle corners scale with each piece's size, which the `--radius` ramp
  // cannot express. They are aria-hidden marks, not interface shapes.
  const borderRadius =
    shape === "circle" ? "50%" : shape === "strip" ? size * 0.12 : 2

  useEffect(() => {
    if (!ref.current) return
    const controls = animate(ref.current, keyframes, { duration, ease: "linear" })
    return () => controls.cancel()
  }, [])

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        width,
        height,
        borderRadius,
        backgroundColor: color,
        pointerEvents: "none",
      }}
    />
  )
}

export interface ConfettiProps {
  /** How many pieces each burst spawns. Default 30. */
  particleCount?: number
  /** Launch cone width in degrees. Default 90. */
  spread?: number
  /** Initial particle speed. Default 20. */
  startVelocity?: number
  /** Live CSS colour values for the burst. */
  colors?: readonly string[]
  /** Merged onto the burst layer. */
  className?: string
  /** Imperative handle exposing `burst()`. */
  ref?: Ref<ConfettiHandle>
}

/** Pointer-events-none layer that fires celebration bursts on demand. */
export function Confetti({
  particleCount = 30,
  spread = 90,
  startVelocity = 20,
  colors = CONFETTI_COLORS,
  className,
  ref,
}: ConfettiProps) {
  const { motionMode } = useMotionUITheme()
  const motionAllowed = motionMode === "full"
  const [bursts, setBursts] = useState<ConfettiBurst[]>([])
  const nextBurstId = useRef(0)
  const burstTimers = useRef(new Set<ReturnType<typeof setTimeout>>())

  useEffect(() => {
    const timers = burstTimers.current
    return () => {
      timers.forEach((timer) => clearTimeout(timer))
    }
  }, [])

  useImperativeHandle(
    ref,
    () => ({
      burst: () => {
        if (!motionAllowed) return
        const id = nextBurstId.current++
        setBursts((prev) => [
          ...prev,
          {
            id,
            particles: buildConfettiParticles(
              particleCount,
              spread,
              startVelocity,
              colors
            ),
          },
        ])
        const cleanupTimer = setTimeout(
          () => {
            setBursts((prev) => prev.filter((burst) => burst.id !== id))
            burstTimers.current.delete(cleanupTimer)
          },
          (CONFETTI_DURATION + 0.5) * 1000
        )
        burstTimers.current.add(cleanupTimer)
      },
    }),
    [colors, motionAllowed, particleCount, spread, startVelocity]
  )

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 z-20 overflow-visible${className ? ` ${className}` : ""}`}
    >
      {bursts.map((burst) => (
        <div key={burst.id} className="absolute left-1/2 top-1/2">
          {burst.particles.map((particle, i) => (
            <ConfettiPiece key={i} particle={particle} />
          ))}
        </div>
      ))}
    </div>
  )
}
