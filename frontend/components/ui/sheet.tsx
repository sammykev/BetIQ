"use client";

import * as React from "react";
import * as RD from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "motion/react";
import { cn, EASE } from "@/lib/utils";
import { CloseButton, Overlay } from "@/components/ui/dialog";

// A drawer: from the right on wide screens, from the bottom on phones.
// Same focus handling as Dialog.
export function Sheet({
  open, onOpenChange, title, description, children, className, side = "right",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  side?: "right" | "bottom";
}) {
  const from = side === "right" ? { x: 24, y: 0 } : { x: 0, y: 24 };
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <RD.Portal forceMount>
            <Overlay />
            <RD.Content forceMount asChild>
              <motion.div
                className={cn("fixed z-50 flex flex-col bg-surface [box-shadow:var(--shadow-pop)] focus:outline-none",
                  side === "right"
                    ? "inset-y-0 right-0 w-full sm:w-[420px] sm:rounded-l-3xl"
                    : "inset-x-0 bottom-0 max-h-[92dvh] rounded-t-3xl",
                  className)}
                initial={{ opacity: 0, ...from, filter: "blur(4px)" }}
                animate={{ opacity: 1, x: 0, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, x: from.x / 2, y: from.y / 2, transition: { duration: 0.15, ease: "easeOut" } }}
                transition={{ duration: 0.28, ease: EASE }}>
                <div className="flex items-start gap-3 px-5 pt-5 pb-3 shrink-0">
                  <div className="min-w-0 flex-1">
                    <RD.Title className="heading text-lg">{title}</RD.Title>
                    {description
                      ? <RD.Description className="mt-1 text-[13px] text-n-400 text-pretty">{description}</RD.Description>
                      : <RD.Description className="sr-only">{typeof title === "string" ? title : "Panel"}</RD.Description>}
                  </div>
                  <CloseButton className="-mr-2 -mt-1" />
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
              </motion.div>
            </RD.Content>
          </RD.Portal>
        )}
      </AnimatePresence>
    </RD.Root>
  );
}
