"use client";

/**
 * Per-loan safety status pill rendered on the loan-detail header.
 *
 * Visibility rules (kept conservative — silence on the happy path
 * is the goal):
 * - 0 detections                          → not rendered.
 * - 1+ flagged but 0 blocked              → amber pill: "N flags".
 * - 1+ blocked                            → red pill: "N blocked".
 *
 * Clicks deep-link to /safety pre-filtered by loan. (The /safety
 * page itself doesn't yet honor ``?loan=`` — when it does, this
 * link will just work.)
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { IconShieldX, IconShieldHalf } from "@tabler/icons-react";
import { api, type InjectionDetectionRow } from "@/lib/api";

interface Props {
  loanId: string;
}

export function SafetyChip({ loanId }: Props) {
  const detectionsQuery = useQuery<InjectionDetectionRow[], Error>({
    queryKey: ["safety", "loan", loanId],
    queryFn: () => api.getLoanInjectionDetections(loanId),
    // The loan-detail header reads this. Refresh every 60s so newly-
    // blocked uploads surface without a manual reload.
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  const detections = detectionsQuery.data ?? [];
  if (detections.length === 0) return null;

  const blocked = detections.filter((d) => d.decision === "blocked").length;
  const flagged = detections.filter((d) => d.decision === "flagged").length;

  // No interesting detections (only "allowed" rows) → silent.
  if (blocked === 0 && flagged === 0) return null;

  const isBlock = blocked > 0;
  const label = isBlock
    ? `${blocked} blocked`
    : `${flagged} flag${flagged === 1 ? "" : "s"}`;
  const Icon = isBlock ? IconShieldX : IconShieldHalf;
  const color = isBlock
    ? "var(--color-text-danger)"
    : "var(--color-text-warning)";
  const bg = isBlock
    ? "var(--color-background-danger)"
    : "var(--color-background-warning)";

  return (
    <Link
      href={`/safety?loan=${loanId}`}
      title={`${blocked} blocked · ${flagged} flagged — open Safety dashboard`}
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium transition-opacity hover:opacity-80"
      style={{ background: bg, color }}
    >
      <Icon size={12} />
      {label}
    </Link>
  );
}
