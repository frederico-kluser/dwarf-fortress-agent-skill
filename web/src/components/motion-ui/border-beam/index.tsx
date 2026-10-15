"use client"

import { motion, useInView } from "motion/react"
import { useRef, type CSSProperties, type ReactNode } from "react"
import { useMotionUITheme } from "@/components/motion-ui/ui-theme"

const RING_MASK = "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)"

function beamGradient(size: number) {
  const tailStart = Math.max(0, 360 - size)
  const mid = tailStart + (360 - tailStart) * 0.65
  return (
    "conic-gradient(from 0deg at 50% 50%, " +
    "transparent 0deg, " +
    `transparent ${tailStart}deg, ` +
    `color-mix(in srgb, var(--primary) 55%, transparent) ${mid.toFixed(1)}deg, ` +
    "var(--primary) 360deg)"
  )
}

export interface BorderBeamProps {
  /** Angular length of the lit streak, in degrees. Defaults to `120`. */
  size?: number
  /** Seconds for one full lap. Defaults to `6`. */
  duration?: number
  /** Thickness of the rim, in px. Defaults to `3`. */
  thickness?: number
  /** Phase offset in seconds. Defaults to `0`. */
  delay?: number
  /** Run the beam when `true`. Defaults to `true`. */
  active?: boolean
  /** Merged onto the wrapping element. */
  className?: string
  /** The panel the beam traces. */
  children: ReactNode
}

/** Wrapper that traces an animated beam around a panel's rounded border. */
export function BorderBeam({
  size = 120,
  duration = 6,
  thickness = 3,
  delay = 0,
  active = true,
  className,
  children,
}: BorderBeamProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const { motionMode } = useMotionUITheme()
  const motionAllowed = motionMode === "full"
  const inView = useInView(trackRef)
  const enabled = active && motionAllowed
  const running = enabled && inView

  return (
    <div className={`relative${className ? ` ${className}` : ""}`}>
      {children}
      <div
        ref={trackRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-xl"
        style={
          {
            padding: thickness,
            WebkitMask: RING_MASK,
            WebkitMaskComposite: "xor",
            mask: RING_MASK,
            maskComposite: "exclude",
          } as CSSProperties
        }
      >
        {enabled && (
          <motion.span
            className="absolute inset-[-75%] block"
            style={{ background: beamGradient(size) }}
            initial={{ transform: "rotate(0deg)" }}
            animate={{
              transform: running
                ? ["rotate(0deg)", "rotate(360deg)"]
                : "rotate(0deg)",
            }}
            transition={
              running
                ? { duration, delay, repeat: Infinity, ease: "linear" }
                : { duration: 0 }
            }
          />
        )}
      </div>
    </div>
  )
}
