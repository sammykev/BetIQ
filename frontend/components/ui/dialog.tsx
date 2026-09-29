"use client";

import * as React from "react";
import * as RD from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { cn, EASE } from "@/lib/utils";

// Radix gives the focus trap, Escape and focus return; motion gives the
// enter (opacity + 12px + 4px blur) and a softer, shorter exit.

export function Overlay() {
  return (
    <RD.Overlay forceMount asChild>
      <motion.div className="fixed inset-0 z-50 bg-[rgb(7_11_20/0.6)] backdrop-blur-[2px]"
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.15 } }}
        transition={{ duration: 0.2, ease: EASE }} />
    </RD.Overlay>
  );
}

export function CloseButton({ className }: { className?: string }) {
  return (
    <RD.Close aria-label="Close"
      className={cn("inline-flex items-center justify-center h-9 w-9 rounded-lg text-n-400 hover:text-n-0 hover:bg-n-800/60",
        "transition-[color,background-color,scale] duration-150 active:scale-[0.96]", className)}>
      <X size={18} />
    </RD.Close>
  );
}

export function Dialog({
  open, onOpenChange, title, description, children, className, hideTitle,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** Keep the title for screen readers only (the body has its own header) */
  hideTitle?: boolean;
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <RD.Portal forceMount>
            <Overlay />
            <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-6 pointer-events-none">
              <RD.Content forceMount asChild>
                <motion.div
                  className={cn("pointer-events-auto w-full sm:max-w-lg max-h-[92dvh] overflow-y-auto bg-surface",
                    "rounded-t-3xl sm:rounded-3xl [box-shadow:var(--shadow-pop)] focus:outline-none", className)}
                  initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: 6, filter: "blur(2px)", transition: { duration: 0.15, ease: "easeOut" } }}
                  transition={{ duration: 0.25, ease: EASE }}>
                  <div className={cn("flex items-start gap-3 px-5 pt-5 pb-2", hideTitle && "sr-only")}>
                    <div className="min-w-0 flex-1">
                      <RD.Title className="heading text-lg">{title}</RD.Title>
                      {description && <RD.Description className="mt-1 text-[13px] text-n-400 text-pretty">{description}</RD.Description>}
                    </div>
                    <CloseButton className="-mr-2 -mt-1" />
                  </div>
                  {!description && <RD.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</RD.Description>}
                  {children}
                </motion.div>
              </RD.Content>
            </div>
          </RD.Portal>
        )}
      </AnimatePresence>
    </RD.Root>
  );
}
