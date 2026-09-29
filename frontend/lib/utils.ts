import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Class names, later ones winning over earlier Tailwind utilities (shadcn's `cn`). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** The redesign's one easing curve (better-ui): enters, presses, slides. */
export const EASE = [0.2, 0, 0, 1] as const;
