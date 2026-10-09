"use client"

import { useRef, useState } from "react"
import { motion, useInView } from "motion/react"
import type { Variants } from "motion/react"
import { useMotionUITransition, useMotionUITheme } from "@/components/motion-ui/ui-theme"
import {
  AnimatedNumber,
  type NumberFormat,
} from "@/components/motion-ui/animated-number"
import "./styles.css"

const STAT_EVENTS_CAPTURED = 2_140_000_000
const STAT_TEAMS_ONBOARD = 6_800
const STAT_TIME_TO_INSIGHT = 4.2
const STAT_UPTIME = 99.98

interface StatDatum {
  label: string
  value: number
  format?: NumberFormat
  suffix?: string
}

interface StatsStripProps {
  eventsCaptured?: number
  teamsOnboard?: number
  timeToInsight?: number
  uptime?: number
}

export function StatsCountersContent({
  eventsCaptured = STAT_EVENTS_CAPTURED,
  teamsOnboard = STAT_TEAMS_ONBOARD,
  timeToInsight = STAT_TIME_TO_INSIGHT,
  uptime = STAT_UPTIME,
}: StatsStripProps = {}) {
  const uiTheme = useMotionUITheme()
  const still = uiTheme.motionMode === "off"
  const calm = uiTheme.motionMode === "calm"
  const motionAllowed = !still && !calm
  const uiTransition = useMotionUITransition("ui")

  const STATS: StatDatum[] = [
    {
      label: "Events this month",
      value: eventsCaptured,
      format: {
        notation: "compact",
        compactDisplay: "short",
        maximumFractionDigits: 1,
      },
    },
    {
      label: "Active workspaces",
      value: teamsOnboard,
      suffix: "+",
    },
    {
      label: "Median time to answer",
      value: timeToInsight,
      format: { maximumFractionDigits: 1 },
      suffix: "s",
    },
    {
      label: "Uptime",
      value: uptime,
      format: { minimumFractionDigits: 2, maximumFractionDigits: 2 },
      suffix: "%",
    },
  ]

  const container = {
    hidden: {},
    show: {
      transition: { staggerChildren: calm ? 0 : uiTheme.stagger.base },
    },
  }
  const item = {
    hidden: { opacity: 0, y: calm ? 0 : uiTheme.travel.enter },
    show: { opacity: 1, y: 0, transition: { ...uiTransition } },
  }
  const statGrid = {
    hidden: { opacity: 0, y: calm ? 0 : uiTheme.travel.enter },
    show: {
      opacity: 1,
      y: 0,
      transition: {
        ...uiTransition,
        staggerChildren: calm ? 0 : uiTheme.stagger.tight,
      },
    },
  }
  const statItem = {
    hidden: { opacity: 0, y: calm ? 0 : uiTheme.travel.enter },
    show: { opacity: 1, y: 0, transition: { ...uiTransition } },
  }

  return (
    <section
      className="velocity-stats relative flex min-h-[var(--velocity-demo-min-h,auto)] w-full flex-col justify-center overflow-hidden bg-background py-20 font-sans text-foreground antialiased md:py-28"
      aria-labelledby="velocity-stats-heading"
    >
      <motion.div
        className="mx-auto flex w-full max-w-[1120px] flex-col gap-12 px-6 md:px-10"
        variants={still ? undefined : container}
        initial={still ? false : "hidden"}
        whileInView={still ? undefined : "show"}
        viewport={{
          amount: uiTheme.inView.amount,
          once: uiTheme.inView.once,
        }}
      >
        <motion.h2
          id="velocity-stats-heading"
          className="max-w-2xl text-balance text-4xl sm:text-5xl"
          variants={item}
        >
          A clear picture of what shipped.
        </motion.h2>

        <motion.p
          className="max-w-xl text-pretty text-base leading-relaxed text-muted-foreground"
          variants={item}
        >
          Figures from the same pipeline your dashboard queries.
        </motion.p>

        <motion.div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
          variants={statGrid}
        >
          {STATS.map((stat) => (
            <StatTile
              key={stat.label}
              stat={stat}
              motionAllowed={motionAllowed}
              itemVariants={statItem}
            />
          ))}
        </motion.div>
      </motion.div>
    </section>
  )
}

function StatTile({
  stat,
  motionAllowed,
  itemVariants,
}: {
  stat: StatDatum
  motionAllowed: boolean
  itemVariants: Variants
}) {
  const [revealed, setRevealed] = useState(!motionAllowed)
  return (
    <motion.div
      className="h-full"
      variants={itemVariants}
      onAnimationComplete={
        motionAllowed
          ? (definition) => {
              if (definition === "show") setRevealed(true)
            }
          : undefined
      }
    >
      <div className="flex flex-col gap-2 h-full gap-4 rounded-lg border border-border bg-card p-6">
        <StatRule />
        <div className="tabular-nums text-foreground font-mono text-[clamp(2rem,3vw+1.1rem,3.5rem)] font-semibold leading-none">
          <AnimatedNumber
            value={stat.value}
            from={0}
            revealed={revealed}
            transition="gentle"
            format={stat.format}
            suffix={stat.suffix}
            className="velocity-stat-value"
          />
        </div>
        <span className="text-muted-foreground text-sm">{stat.label}</span>
      </div>
    </motion.div>
  )
}

function StatRule() {
  const ref = useRef<HTMLSpanElement>(null)
  const uiTheme = useMotionUITheme()
  const still = uiTheme.motionMode === "off"
  const calm = uiTheme.motionMode === "calm"
  const motionAllowed = !still && !calm
  const ui = useMotionUITransition("ui")
  const inView = useInView(ref, {
    amount: uiTheme.inView.amount,
    once: uiTheme.inView.once,
  })
  const revealed = !motionAllowed || inView

  return (
    <motion.span
      ref={ref}
      aria-hidden="true"
      className="block h-[2px] w-7 origin-left bg-primary"
      initial={motionAllowed ? { scaleX: 0 } : false}
      animate={{ scaleX: revealed ? 1 : 0 }}
      transition={{ ...ui }}
    />
  )
}

export default StatsCountersContent
