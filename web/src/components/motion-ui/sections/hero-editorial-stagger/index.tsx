"use client"

import {
  StaggerReveal,
  StaggerRevealHeadline,
  StaggerRevealItem,
} from "@/components/motion-ui/stagger-reveal"
import "./styles.css"

const HEADLINE = "Build what’s next with confidence."
const SUBLINE =
  "Modern interface parts for teams who ship thoughtful software and shape the future."
const CTA_PRIMARY = "Get started"
const CTA_SECONDARY = "Explore docs"

const FOCUS_RING =
  "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"

const CTA_BASE =
  "inline-flex h-10 items-center justify-center gap-3 whitespace-nowrap rounded-full px-3.5 text-[0.8125rem] font-medium transition-colors duration-[var(--motion-ui-transition-snap-duration)] ease-[var(--motion-ui-transition-snap)]"

const CTA_PRIMARY_CLASS = `${CTA_BASE} bg-primary text-primary-foreground hover:bg-primary/90 ${FOCUS_RING}`
const CTA_SECONDARY_CLASS = `${CTA_BASE} velocity-hero-editorial-cta-secondary relative border border-transparent text-foreground ${FOCUS_RING}`

export function HeroEditorialStaggerContent() {
  return (
    <section
      className="velocity-hero-editorial relative flex min-h-[var(--velocity-demo-min-h,auto)] w-full flex-col overflow-hidden bg-background font-sans text-foreground antialiased"
      aria-labelledby="velocity-hero-editorial-heading"
    >
      <div className="velocity-hero-editorial-stage flex w-full flex-1 flex-col">
        <StaggerReveal className="velocity-hero-editorial-copy flex w-full flex-col items-start">
          <StaggerRevealHeadline
            id="velocity-hero-editorial-heading"
            className="max-w-[24ch] text-4xl text-balance sm:text-6xl"
          >
            {HEADLINE}
          </StaggerRevealHeadline>

          <StaggerRevealItem
            as="p"
            className="max-w-[48ch] text-base text-balance text-muted-foreground sm:text-lg"
          >
            {SUBLINE}
          </StaggerRevealItem>

          <StaggerRevealItem className="velocity-hero-editorial-actions flex w-full flex-col sm:w-auto sm:flex-row sm:items-center">
            <a
              href="https://motion.dev/docs"
              target="_blank"
              rel="noreferrer"
              className={CTA_PRIMARY_CLASS}
            >
              {CTA_PRIMARY}
              <span aria-hidden="true">→</span>
            </a>
            <a
              href="https://motion.dev/docs"
              target="_blank"
              rel="noreferrer"
              className={CTA_SECONDARY_CLASS}
            >
              {CTA_SECONDARY}
            </a>
          </StaggerRevealItem>
        </StaggerReveal>
      </div>
    </section>
  )
}

export default HeroEditorialStaggerContent
