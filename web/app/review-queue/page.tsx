"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { IconListSearch } from "@tabler/icons-react";
import { api, type ReviewTask } from "@/lib/api";
import { humanizeField } from "@/lib/humanize";
import { BrandHeader } from "@/app/components/BrandHeader";
import { DocSourceViewer } from "@/app/components/DocSourceViewer";
import { Pill, type PillVariant } from "@/app/components/Pill";

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

export default function ReviewQueuePage() {
  const [activeTask, setActiveTask] = useState<ReviewTask | null>(null);

  const tasksQuery = useQuery<ReviewTask[], Error>({
    queryKey: ["review-tasks", "open"],
    queryFn: () => api.listReviewTasks("open"),
  });

  const tasks = tasksQuery.data ?? [];

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
        sub={`${tasks.length} item${tasks.length === 1 ? "" : "s"} awaiting human review`}
      />

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3">
        {tasksQuery.isPending && (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-text-tertiary)]">
            Loading…
          </p>
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
          <table className="w-full text-[12.5px]">
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
