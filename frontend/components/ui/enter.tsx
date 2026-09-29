"use client";

import * as React from "react";
import { motion } from "motion/react";
import { EASE } from "@/lib/utils";

// First-paint entrance (better-ui): opacity + 12px + 4px blur, 100ms apart.
// Only on the group's first paint: items that mount later (a filter change,
// a new day) appear at once, so the page never replays its intro.
const FIRST_PAINT_MS = 1200;
const Born = React.createContext<number | null>(null);

export function EnterGroup({ children }: { children: React.ReactNode }) {
  const [born] = React.useState(() => Date.now());
  return <Born.Provider value={born}>{children}</Born.Provider>;
}

export function Enter({ i = 0, children, className }: { i?: number; children: React.ReactNode; className?: string }) {
  const born = React.useContext(Born);
  const [animate] = React.useState(() => born === null || Date.now() - born < FIRST_PAINT_MS);
  if (!animate) return <div className={className}>{children}</div>;
  return (
    <motion.div className={className}
      initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      // Up to 6 steps: a long list shouldn't take seconds to finish arriving
      transition={{ duration: 0.4, ease: EASE, delay: Math.min(i, 6) * 0.1 }}>
      {children}
    </motion.div>
  );
}
