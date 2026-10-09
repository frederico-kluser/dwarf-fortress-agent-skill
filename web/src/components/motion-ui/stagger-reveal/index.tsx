"use client"

import { animate, stagger } from "motion"
import { splitText } from "motion-plus"
import {
  createElement,
  useLayoutEffect,
  useRef,
  type ReactElement,
  type ReactNode,
  type Ref,
  type RefObject,
} from "react"
import { useMotionUITheme, useMotionUITransition } from "@/components/motion-ui/ui-theme"

const LINE_CLASS = "motion-ui-stagger-line"
const WORD_CLASS = "motion-ui-stagger-word"
const HEADLINE_ATTR = "data-stagger-headline"
const ITEM_ATTR = "data-stagger-item"
const HEADLINE_RISE_FROM = "translateY(0.4em)"
const HEADLINE_RISE_TO = "translateY(0em)"
const ENTER_BLUR_FROM = "blur(4px)"
const ENTER_BLUR_TO = "blur(0px)"

/** Resolved reduced-motion state for the entrance. */
export type StaggerRevealMode = "full" | "calm" | "off"

/** Handle returned by `useStaggerReveal`. */
export interface StaggerRevealHandle {
  /** Attach to the container element. */
  ref: RefObject<HTMLElement | null>
}

/** Runs asynchronous measurement/splitting and always invokes the visible-state fallback. */
export async function runStaggerPreparation(
  prepare: () => Promise<void>,
  reveal: () => void,
): Promise<void> {
  try {
    await prepare()
  } catch {
    // The caller's reveal fallback is the recovery path.
  } finally {
    reveal()
  }
}

/** Headless orchestration for a headline plus follower items. */
export function useStaggerReveal(): StaggerRevealHandle {
  const ref = useRef<HTMLElement | null>(null)
  const theme = useMotionUITheme()
  const { motionMode } = theme

  // Theme hooks are not referentially stable; read them through refs inside the async fonts.ready callback.
  const gentle = useMotionUITransition("gentle")
  const ui = useMotionUITransition("ui")
  const gentleRef = useRef(gentle)
  const uiRef = useRef(ui)
  const themeRef = useRef(theme)
  gentleRef.current = gentle
  uiRef.current = ui
  themeRef.current = theme

  useLayoutEffect(() => {
    const container = ref.current
    if (!container) return
    const headlineEl = container.querySelector<HTMLElement>(
      `[${HEADLINE_ATTR}]`,
    )
    if (!headlineEl) return

    const animations: Array<ReturnType<typeof animate>> = []
    let cancelled = false

    container.style.visibility = "hidden"

    void runStaggerPreparation(
      async () => {
        await document.fonts?.ready
        if (cancelled || ref.current !== container) return

        const original =
          headlineEl.getAttribute("aria-label") ?? headlineEl.textContent ?? ""
        headlineEl.textContent = original

        const { lines } = splitText(headlineEl, {
          lineClass: LINE_CLASS,
          wordClass: WORD_CLASS,
        })

        lines.forEach((line) => {
          line.style.display = "block"
          line
            .querySelectorAll<HTMLElement>(`.${WORD_CLASS}`)
            .forEach((word) => {
              word.style.display = "inline-block"
            })
        })

        const followers = Array.from(
          container.querySelectorAll<HTMLElement>(`[${ITEM_ATTR}]`),
        )

        if (motionMode === "off") return

        const gentleTransition = gentleRef.current
        const uiTransition = uiRef.current
        const tokens = themeRef.current

        const calm = motionMode === "calm"
        const travel = calm ? 0 : tokens.travel.enter
        const beat = calm ? 0 : tokens.stagger.base
        const lineStagger = calm ? 0 : tokens.stagger.relaxed

        const gentleOpacity = {
          ...gentleTransition.opacity,
          ease: "easeIn" as const,
        }
        const followerDuration = uiTransition.duration * 1.25
        const followerTransition = {
          ...uiTransition,
          stiffness: uiTransition.stiffness / 1.25 ** 2,
          damping: uiTransition.damping / 1.25,
          duration: followerDuration,
        }
        const followerOpacity = {
          ...uiTransition.opacity,
          duration: followerDuration,
          ease: "easeIn" as const,
        }

        /* One animate() per set, so each set reads as a single staggered
         * animation to tooling rather than one call per element. */
        if (lines.length > 0) {
          animations.push(
            animate(
              lines,
              calm
                ? { opacity: [0, 1] }
                : {
                    opacity: [0, 1],
                    transform: [HEADLINE_RISE_FROM, HEADLINE_RISE_TO],
                    filter: [ENTER_BLUR_FROM, ENTER_BLUR_TO],
                  },
              {
                ...gentleTransition,
                delay: stagger(lineStagger),
                opacity: gentleOpacity,
              },
            ),
          )
        }

        // Followers begin two beats after the last headline line starts.
        const followerStart =
          lines.length > 0 ? (lines.length + 2) * lineStagger : 0

        if (followers.length > 0) {
          animations.push(
            animate(
              followers,
              calm
                ? {
                    opacity: [0, 1],
                    transform: ["translateY(0px)", "translateY(0px)"],
                  }
                : {
                    opacity: [0, 1],
                    transform: [`translateY(${travel}px)`, "translateY(0px)"],
                    filter: [ENTER_BLUR_FROM, ENTER_BLUR_TO],
                  },
              {
                ...followerTransition,
                delay: stagger(beat, { startDelay: followerStart }),
                opacity: followerOpacity,
              },
            ),
          )
        }
      },
      () => {
        if (!cancelled && ref.current === container) {
          container.style.visibility = "visible"
        }
      },
    )

    return () => {
      cancelled = true
      animations.forEach((animation) => animation.stop())
    }
  }, [motionMode])

  return { ref }
}

/** Elements the container can render as. Defaults to `"div"`. */
export type StaggerRevealTag = "div" | "section" | "header" | "article"

export interface StaggerRevealProps {
  /** One `StaggerRevealHeadline` and any number of `StaggerRevealItem` followers. */
  children: ReactNode
  /** The element to render. Defaults to `"div"`. */
  as?: StaggerRevealTag
  /** Merged onto the container. */
  className?: string
  /** Forwarded to the container element. */
  id?: string
}

/** Orchestration container for a headline plus staggered followers. */
export function StaggerReveal({
  children,
  as = "div",
  className,
  id,
}: StaggerRevealProps): ReactElement {
  const { ref } = useStaggerReveal()
  return createElement(
    as,
    {
      ref: ref as Ref<HTMLElement>,
      id,
      className,
    },
    children,
  )
}

/** Heading tags the split target can render as. Defaults to `"h1"`. */
export type StaggerRevealHeadlineTag = "h1" | "h2" | "h3"

export interface StaggerRevealHeadlineProps {
  /** Plain headline text to split and reveal. */
  children: string
  /** The element to render. Defaults to `"h1"`. */
  as?: StaggerRevealHeadlineTag
  /** Whole-headline announce for assistive tech. Defaults to `children`. */
  ariaLabel?: string
  /** Merged onto the heading. */
  className?: string
  /** Forwarded to the heading. */
  id?: string
}

/** Split-target heading the container reveals line by line. */
export function StaggerRevealHeadline({
  children,
  as = "h1",
  ariaLabel,
  className,
  id,
}: StaggerRevealHeadlineProps): ReactElement {
  return createElement(
    as,
    {
      id,
      className,
      "aria-label": ariaLabel ?? children,
      [HEADLINE_ATTR]: "",
    },
    children,
  )
}

/** Elements a follower can render as. Defaults to `"div"`. */
export type StaggerRevealItemTag =
  | "div"
  | "p"
  | "span"
  | "ul"
  | "section"
  | "figure"

export interface StaggerRevealItemProps {
  /** The follower's content. */
  children: ReactNode
  /** The element to render. Defaults to `"div"`. */
  as?: StaggerRevealItemTag
  /** Merged onto the follower. */
  className?: string
  /** Forwarded to the follower element. */
  id?: string
}

/** Follower the container staggers in after the headline. */
export function StaggerRevealItem({
  children,
  as = "div",
  className,
  id,
}: StaggerRevealItemProps): ReactElement {
  return createElement(
    as,
    {
      id,
      className,
      [ITEM_ATTR]: "",
    },
    children,
  )
}
