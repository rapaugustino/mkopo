"use client";

import { useQuery } from "@tanstack/react-query";
import { IconLock } from "@tabler/icons-react";

import { api, type LockStatus } from "@/lib/api";

/**
 * Stage-based lock banner.
 *
 * Reads ``GET /loans/{id}/locks`` and renders nothing when nothing
 * is locked. Once the loan crosses into a locked stage, surfaces a
 * single banner above the loan detail content explaining what's
 * still allowed and what's frozen.
 *
 * Pairs with :class:`MaterialsFlow` — different concerns,
 * different colours. Drift is a *warning* ("the inputs changed but
 * the loan is still operable; rerun underwriting to refresh");
 * lock is a *guarantee* ("this loan is finalised; mutations are
 * refused server-side").
 *
 * Lock policy is server-authoritative — the same predicate functions
 * power the 409s on the agent + upload endpoints. This component
 * just renders the snapshot.
 */
export function LoanLockBanner({ loanId }: { loanId: string }) {
  const { data } = useQuery<LockStatus, Error>({
    queryKey: ["loan", loanId, "lock-status"],
    queryFn: () => api.getLockStatus(loanId),
    // Lock state changes only on stage transition. Refetch on
    // window focus + cache for 30s to catch transitions made in
    // another tab without polling every second.
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });

  if (!data || !data.headline) return null;

  return (
    <div
      role="status"
      className="flex items-start gap-2.5 rounded-lg border-[0.5px] px-4 py-3"
      style={{
        // Warm-grey tone — quieter than the danger banner used for
        // drift. The lock is informational, not alarming.
        background: "var(--color-background-secondary)",
        borderColor: "var(--color-border-tertiary)",
      }}
    >
      <span
        className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
        style={{
          background: "var(--color-background-primary)",
          color: "var(--color-text-secondary)",
        }}
      >
        <IconLock size={11} />
      </span>
      <div className="flex flex-col gap-0.5">
        <p className="text-[12.5px] font-medium text-[var(--color-text-primary)]">
          {data.headline}
        </p>
        {data.detail && (
          <p className="text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
            {data.detail}
          </p>
        )}
      </div>
    </div>
  );
}
