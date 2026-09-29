import * as React from "react";
import { cn } from "@/lib/utils";

// Surfaces: a shadow ring, no border; 16px radius outside, 8px inside at 8px padding (concentric).
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }>(
  ({ className, interactive, ...props }, ref) => (
    <div ref={ref} className={cn(interactive ? "card-interactive" : "card", className)} {...props} />
  )
);
Card.displayName = "Card";

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start gap-3 px-4 sm:px-5 pt-4 pb-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("heading text-[15px]", className)} {...props} />;
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-[13px] text-n-400 text-pretty", className)} {...props} />;
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 sm:px-5 pb-4", className)} {...props} />;
}
