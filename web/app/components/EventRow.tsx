import type { ReactNode } from "react";
import { ActorIcon, type ActorType, type EventIntent } from "./ActorIcon";

interface Props {
  actor: ActorType;
  intent?: EventIntent;
  /** The headline — e.g. "Borrower replied" or "J. Davis sent doc request". */
  actorLabel: ReactNode;
  /** Relative timestamp — "2h ago", "via broker portal · 2 days ago". */
  time: ReactNode;
  /** Optional chips next to the time (e.g. "2 of 3 docs", "Concentration"). */
  pills?: ReactNode;
  /** One-line description under the headline. */
  body?: ReactNode;
  /** Bigger callout (QuoteBlock or similar) below the body. */
  detail?: ReactNode;
}

/**
 * One event in the case-file timeline. Grid: icon column (fixed) + content
 * column. Borders between rows handled by the parent (so the last row
 * doesn't render a bottom border).
 *
 * This component owns the cf-event layout — the page just composes a list
 * of these from typed audit events.
 */
export function EventRow({
  actor,
  intent = "default",
  actorLabel,
  time,
  pills,
  body,
  detail,
}: Props) {
  return (
    <div className="grid grid-cols-[30px_1fr] gap-3 py-3">
      <div className="pt-0.5">
        <ActorIcon actor={actor} intent={intent} />
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[13px] font-medium">{actorLabel}</span>
          <span className="text-[11px] text-[var(--color-text-tertiary)]">
            {time}
          </span>
          {pills}
        </div>
        {body && (
          <p className="mt-0.5 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
            {body}
          </p>
        )}
        {detail}
      </div>
    </div>
  );
}
