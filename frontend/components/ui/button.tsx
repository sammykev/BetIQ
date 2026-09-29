"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// shadcn-style button in BetIQ's tokens. One filled lime action per view;
// the rest are secondary, ghost or subtle. A 0.96 press on every variant.
export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-semibold select-none " +
  "transition-[color,background-color,box-shadow,scale] duration-150 ease-[cubic-bezier(0.2,0,0,1)] " +
  "active:scale-[0.96] disabled:opacity-50 disabled:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-brand-400 text-ink hover:bg-brand-300",
        secondary: "bg-surface text-n-100 hover:text-n-0 hover:bg-surface-raised [box-shadow:var(--ring-control)] hover:[box-shadow:var(--ring-control-hover)]",
        ghost: "text-n-300 hover:text-n-0 hover:bg-n-800/60",
        subtle: "bg-n-800/60 text-n-100 hover:bg-n-800 hover:text-n-0",
        danger: "bg-danger/10 text-danger hover:bg-danger/15",
      },
      size: {
        sm: "h-8 px-3 text-[13px] rounded-lg",
        md: "h-10 px-4 text-sm rounded-xl",
        lg: "h-12 px-5 text-[15px] rounded-2xl",
        icon: "h-10 w-10 rounded-xl",
        "icon-sm": "h-8 w-8 rounded-lg",
      },
      block: { true: "w-full" },
      // Where motion would distract (dense lists): no press scale
      static: { true: "active:scale-100" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, block, static: isStatic, type = "button", ...props }, ref) => (
    <button ref={ref} type={type} className={cn(buttonVariants({ variant, size, block, static: isStatic }), className)} {...props} />
  )
);
Button.displayName = "Button";
