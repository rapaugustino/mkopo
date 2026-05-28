"use client";

/**
 * Section heading + container for the /eval dashboard.
 *
 * Used to group the ~15 cards into 4 navigable regions (Health,
 * Golden gate, Production drift, Operations). The component
 * carries the ``id`` that EvalSectionTOC's smooth-scroll links
 * jump to.
 *
 * The heading style intentionally differs from card-internal
 * ``SectionLabel`` — that's used inside cards (12px, secondary
 * colour) and would lose hierarchy if reused at the page level.
 * This one is 14px bold + uppercase tracking, plus a one-line
 * sub-description that names *why* this section exists.
 */

import type { ReactNode } from "react";

interface Props {
  id: string;
  title: string;
  /** One-line description below the title. Explains the purpose
   *  of the group so the regulator scanning the dashboard can
   *  triage without reading every card. */
  description?: ReactNode;
  /** Top-right slot — typically a refresh button or last-run
   *  timestamp specific to the section. */
  trailing?: ReactNode;
  children: ReactNode;
}

export function EvalSection({
  id,
  title,
  description,
  trailing,
  children,
}: Props) {
  return (
    // ``scroll-mt-[110px]`` matches the JS handler's offset in
    // ``EvalSectionTOC`` — keeps the section heading visible
    // instead of tucked under the sticky TOC pill bar.
    <section id={id} className="scroll-mt-[110px] flex flex-col gap-2">
      <header className="flex items-end justify-between gap-3 border-b-[0.5px] border-[var(--color-border-tertiary)] pb-1.5">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-primary)]">
            {title}
          </h2>
          {description && (
            <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
              {description}
            </p>
          )}
        </div>
        {trailing && (
          <div className="shrink-0 text-[11px] text-[var(--color-text-tertiary)]">
            {trailing}
          </div>
        )}
      </header>
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  );
}
