import type { ReactNode } from "react";

/** Page title block: small eyebrow, condensed headline, one-line description. */
export function PageHeader({ eyebrow, title, description, right }: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4 flex-wrap">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="display text-5xl sm:text-6xl text-white mt-2">{title}</h1>
        {description && <p className="text-sm text-zinc-400 mt-2 max-w-xl">{description}</p>}
      </div>
      {right}
    </div>
  );
}
