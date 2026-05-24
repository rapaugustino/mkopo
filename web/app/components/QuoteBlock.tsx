import type { ReactNode } from "react";

import { MarkdownBlock } from "./MarkdownBlock";

interface Props {
  /** String children are rendered as markdown (LLM rationales,
   *  decision verdicts, email bodies — all of which produce
   *  markdown). ReactNode children render as-is (e.g. when the
   *  parent has already done its own formatting). */
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
 *
 * String children get markdown rendering — every LLM-produced surface
 * (underwriting rationale, decision verdict, AAL body) returns markdown
 * by default, and the pre-markdown behaviour leaked literal ``**``
 * and ``#`` into the timeline.
 */
export function QuoteBlock({ children, caption }: Props) {
  const body =
    typeof children === "string" ? (
      <MarkdownBlock variant="relaxed">{children}</MarkdownBlock>
    ) : (
      children
    );
  return (
    <div className="mt-2 rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
      {caption && (
        <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          {caption}
        </p>
      )}
      <div
        className="text-[12.5px] leading-relaxed text-[var(--color-text-primary)]"
        style={{ fontFamily: "var(--font-serif)" }}
      >
        {body}
      </div>
    </div>
  );
}
