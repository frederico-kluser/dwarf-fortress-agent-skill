import { defineTheme } from "@/components/motion-ui/ui-theme"

export default defineTheme({
  transitions: {
    snap: { stiffness: 1218, damping: 70 },
    ui: { stiffness: 305, damping: 33 },
    gentle: { stiffness: 110, damping: 20 },
    lively: { stiffness: 622, damping: 17 },
    ambient: { stiffness: 43, damping: 13 },
  },
  stagger: { tight: 0.04, base: 0.08, relaxed: 0.15 },
  travel: { hover: 4, enter: 24, section: 48 },
  inView: { amount: 0.4, once: true },
  reducedMotion: "calm",
})
