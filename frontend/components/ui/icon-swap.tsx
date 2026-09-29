"use client";

import * as React from "react";
import { AnimatePresence, motion } from "motion/react";

// better-ui icon swap: the new icon scales up from .25 out of a 4px blur;
// the old one leaves the same way (spring 0.3s, no bounce).
export function IconSwap({ state, children, className }: {
  /** Changes when the icon should swap (e.g. "copied" / "idle") */
  state: string | number | boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span className={className ?? "relative inline-flex items-center justify-center"}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span key={String(state)} className="inline-flex"
          initial={{ scale: 0.25, opacity: 0, filter: "blur(4px)" }}
          animate={{ scale: 1, opacity: 1, filter: "blur(0px)" }}
          exit={{ scale: 0.25, opacity: 0, filter: "blur(4px)" }}
          transition={{ type: "spring", duration: 0.3, bounce: 0 }}>
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
