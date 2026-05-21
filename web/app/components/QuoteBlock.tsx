import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Optional caption above the quote (e.g. "Subject:" or "From: borrower"). */
  caption?: ReactNode;
}

/**
 * Serif callout block for letter / email excerpts in the case file timeline.
 *
 * The mockup's `cf-quote` uses var(--font-serif) — it visually separates
 * "the AI's words to the borrower" from log-style event chrome around it.
 * Don't repurpose this for non-prose content; the serif/italic treatment is
 * pointed.
 */
export function QuoteBlock({ children, caption }: Props) {
  return (
    <div className="mt-2 rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
      {caption && (
        <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          {caption}
        </p>
      )}
      <div
        className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--color-text-primary)]"
        style={{ fontFamily: "var(--font-serif)" }}
      >
        {children}
      </div>
    </div>
  );
}
