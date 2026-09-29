"use client";

import * as React from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE } from "@/lib/utils";

/** Content that opens under a row: the height eases open, the content fades in. */
export function Reveal({ open, children, className }: { open: boolean; children: React.ReactNode; className?: string }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div className={className} style={{ overflow: "hidden" }}
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1, transition: { height: { duration: 0.25, ease: EASE }, opacity: { duration: 0.2, delay: 0.05 } } }}
          exit={{ height: 0, opacity: 0, transition: { height: { duration: 0.18, ease: EASE }, opacity: { duration: 0.1 } } }}>
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
