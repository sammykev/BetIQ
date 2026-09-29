"use client";

import { MotionConfig } from "motion/react";

/** Every animation follows the device's "reduce motion" setting (movement off, fades kept). */
export function MotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
