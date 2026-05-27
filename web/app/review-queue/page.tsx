"use client";

import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { IconListSearch, IconX } from "@tabler/icons-react";
import { api, type ReviewTask } from "@/lib/api";
import { humanizeField } from "@/lib/humanize";
import { BrandHeader } from "@/app/components/BrandHeader";
import { DocSourceViewer } from "@/app/components/DocSourceViewer";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { Skeleton } from "@/app/components/Skeleton";

/** Bucket the reason string into a tag for visual scanning.
 *
 * The intake agent writes specific reasons like "Low confidence (0.68)
 * below threshold (0.92) for annual_noi". Other future sources (conflict
 * detection between docs, missing-field detection) will produce different
 * shapes; this is where we pattern-match them into a small set of tags.
 */
function reasonTag(reason: string): {
  label: string;
  variant: PillVariant;
} {
  if (/low confidence/i.test(reason)) return { label: "Low conf", variant: "danger" };
  if (/conflict/i.test(reason)) return { label: "Conflict", variant: "warn" };
  if (/missing/i.test(reason)) return { label: "Missing", variant: "info" };
  return { label: "Review", variant: "neutral" };
}

function ConfidenceBadge({ pct }: { pct: number }) {
  const rounded = Math.round(pct * 100);
  const color =
    pct >= 0.9
      ? "var(--color-text-success)"
      : pct >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  return (
    <span className="font-medium" style={{ color }}>
      {rounded}%
    </span>
  );
}

// useSearchParams() requires a Suspense boundary in Next 14+ — the
// prerender pass bails out otherwise. The fallback can be ``null``
// because there's no perceptible render gap at runtime; the boundary
// only matters during ``next build``.
export default function ReviewQueuePage() {
  return (
    <Suspense fallback={null}>
      <ReviewQueueContent />
    </Suspense>
  );
}

function ReviewQueueContent() {
  const [activeTask, setActiveTask] = useState<ReviewTask | null>(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  // Optional ``?field=annual_noi`` deep-link from the eval dashboard.
  // The eval page sends operators here when a field's accuracy drifts
  // below threshold — pre-filtering the queue lets them investigate
  // without trawling through unrelated tasks.
  const fieldFilter = searchParams.get("field");

  const tasksQuery = useQuery<ReviewTask[], Error>({
    queryKey: ["review-tasks", "open"],
    queryFn: () => api.listReviewTasks("open"),
  });

  // Move the `?? []` fallback inside useMemo so the dependency is the
  // stable query reference, not a fresh `[]` on every render — the
  // linter rightly flagged the old form as "deps change every render".
  const queryData = tasksQuery.data;
  const tasks = useMemo(() => {
    const all = queryData ?? [];
    return fieldFilter
      ? all.filter((t) => t.extraction.field_name === fieldFilter)
      : all;
  }, [queryData, fieldFilter]);

  const clearFieldFilter = () => router.replace("/review-queue");

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        leading={
          <div
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-md"
            style={{
              background: "var(--color-background-warning)",
              color: "var(--color-text-warning)",
            }}
          >
            <IconListSearch size={16} />
          </div>
        }
        title="Review queue"
        sub={`${tasks.length} item${tasks.length === 1 ? "" : "s"} awaiting human review${fieldFilter ? ` · filtered by field` : ""}`}
      />

      {/* Active-filter chip — only renders when a ``?field=`` deep
          link is in play. Clicking the X clears the filter and
          drops the query param. Mirrors the pipeline view's filter
          UX so the visual language is consistent. */}
      {fieldFilter && (
        <div className="flex items-center gap-2 rounded-md bg-[var(--color-background-info)] px-3 py-2 text-[12px] text-[var(--color-text-info)]">
          <span className="font-medium">Field:</span>
          <span>{humanizeField(fieldFilter)}</span>
          <button
            type="button"
            onClick={clearFieldFilter}
            className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] hover:bg-[var(--color-background-primary)]"
            aria-label="Clear field filter"
          >
            <IconX size={11} />
            Clear
          </button>
        </div>
      )}

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3">
        {tasksQuery.isPending && (
          <div className="flex flex-col gap-3 px-2 py-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton width="w-16" height="h-5" shape="sm" />
                <Skeleton width="w-28" height="h-3" />
                <Skeleton width="w-32" height="h-3" />
                <Skeleton width="w-40" height="h-3" />
                <div className="ml-auto">
                  <Skeleton width="w-16" height="h-7" />
                </div>
              </div>
            ))}
          </div>
        )}
        {tasksQuery.error && (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-text-danger)]">
            {tasksQuery.error.message}
          </p>
        )}
        {!tasksQuery.isPending && tasks.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-text-secondary)]">
            No items in the queue. Re-run intake on a loan with low-confidence
            extractions to populate.
          </p>
        )}
        {tasks.length > 0 && (
          // Wrap in ``overflow-x-auto`` so the table can scroll
          // *internally* on narrow viewports — the page itself
          // stays at viewport width, no body-level horizontal
          // scroll. ``min-w-[560px]`` sets a sensible floor so
          // the headers stay legible.
          <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-[12.5px]">
            <thead>
              <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
                <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Reason
                </th>
                <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Loan
                </th>
                <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Field
                </th>
                <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Current value
                </th>
                <th className="px-2 py-3 text-right text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Confidence
                </th>
                <th className="px-2 py-3 text-right text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => {
                const tag = reasonTag(t.reason);
                return (
                  <tr
                    key={t.id}
                    className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
                  >
                    <td className="px-2 py-3">
                      <Pill variant={tag.variant}>{tag.label}</Pill>
                    </td>
                    <td className="px-2 py-3">
                      <Link
                        href={`/loans/${t.loan.id}`}
                        className="font-medium text-[var(--color-text-info)] hover:underline"
                      >
                        {t.loan.reference}
                      </Link>
                    </td>
                    <td className="px-2 py-3 text-[var(--color-text-secondary)]">
                      {humanizeField(t.extraction.field_name)}
                    </td>
                    <td className="max-w-[20ch] truncate px-2 py-3" title={t.extraction.value}>
                      {t.extraction.value}
                    </td>
                    <td className="px-2 py-3 text-right">
                      <ConfidenceBadge pct={t.extraction.confidence} />
                    </td>
                    <td className="px-2 py-3 text-right">
                      <button
                        onClick={() => setActiveTask(t)}
                        className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11px] hover:bg-[var(--color-background-secondary)]"
                      >
                        Review
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {activeTask && (
        <DocSourceViewer
          taskId={activeTask.id}
          onClose={() => setActiveTask(null)}
        />
      )}
    </div>
  );
}
