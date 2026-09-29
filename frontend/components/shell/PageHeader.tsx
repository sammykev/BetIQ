import type { ReactNode } from "react";

/** Page title block: small eyebrow, sentence-case heading, one-line description. */
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
        <h1 className="heading text-[28px] sm:text-[36px] mt-2">{title}</h1>
        {description && <p className="text-sm text-n-400 mt-2 max-w-xl text-pretty">{description}</p>}
      </div>
      {right}
    </div>
  );
}
