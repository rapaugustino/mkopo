/**
 * Small formatting helpers shared across the app.
 *
 * The code review audit found ``new Date(iso).toLocaleString(...)``
 * patterns repeated in 19+ places — each with slightly different
 * options. Same with the "days since X" math. Centralising both
 * here means:
 *
 * - one place to change the locale / date style when we localise
 * - no risk of two surfaces disagreeing on what "May 25" means
 *   when one passes ``dateStyle: "medium"`` and the other ``"long"``
 * - shorter call sites — components are noisier than they need to be
 *
 * Add a helper here when you find yourself reaching for raw
 * ``new Date()`` arithmetic. Don't add format helpers that wrap
 * a one-liner; the value is in *consistent* options.
 */

/** Default mid-density date+time used by the loan-detail page, the
 *  trace tab, audit timeline, etc. "May 25, 2026, 3:47 PM" — short
 *  enough for inline labels, long enough to disambiguate years. */
export function formatDateTime(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Date-only variant. Used in the apply form review step + the
 *  borrower account dashboard where the time-of-day is noise. */
export function formatDate(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** Long-form day-of-week + month-day-year, used by the AAL
 *  "Date: " line and other formal communications. "Monday,
 *  May 25, 2026" reads as a finished letter, not a UI label. */
export function formatLongDate(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

/** Whole days between ``iso`` and now (always positive). The
 *  pipeline page uses this for stage-aging ("3d in underwriting"),
 *  the kanban cards for the same. Both previously inlined the
 *  ``86_400_000`` arithmetic; centralising means the day boundary
 *  is consistent. */
export function daysSince(iso: string | Date): number {
  const t = typeof iso === "string" ? new Date(iso).getTime() : iso.getTime();
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
}

/** Relative time, shortened. "5m" / "2h" / "3d" / "May 12".
 *  Used in compact contexts (chat timestamps, kanban card aging,
 *  pipeline columns). Falls through to the absolute date past
 *  30 days because relative time stops being legible at that
 *  scale. */
export function relativeTime(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const ms = Date.now() - d.getTime();
  if (ms < 0) return "soon";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
