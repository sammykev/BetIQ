import * as React from "react";
import { cn } from "@/lib/utils";

/** An empty or error state: what happened, and what to do next. */
export function Empty({ icon, title, body, action, className }: {
  icon?: React.ReactNode; title: string; body?: React.ReactNode; action?: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("card px-6 py-14 text-center flex flex-col items-center gap-3", className)}>
      {icon && <div className="w-11 h-11 rounded-2xl bg-n-800/60 text-n-400 flex items-center justify-center">{icon}</div>}
      <p className="heading text-lg">{title}</p>
      {body && <div className="text-sm text-n-400 max-w-sm text-pretty">{body}</div>}
      {action && <div className="pt-1">{action}</div>}
    </div>
  );
}
