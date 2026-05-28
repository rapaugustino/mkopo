"use client";

/**
 * Side drawer rendering one InjectionDetectionDetail.
 *
 * Mirrors the shape of LLMCallDrawer / AgentRunDrawer in
 * /observability — right-side overlay, click-outside to close,
 * structured sections for the matched-patterns + Haiku verdict +
 * raw excerpt.
 *
 * The "raw excerpt" is the only place a reviewer sees what the
 * actual offending text looked like. Capped at 2000 chars at the
 * backend; whitespace-preserved with ``whitespace-pre-wrap`` so
 * line breaks in the input are visible.
 */

import { useQuery } from "@tanstack/react-query";
import { IconX } from "@tabler/icons-react";
import { api, type InjectionDetectionDetail } from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { IconButton } from "@/app/components/IconButton";

interface Props {
  detectionId: string;
  onClose: () => void;
}

function severityVariant(severity: string): PillVariant {
  if (severity === "high") return "danger";
  if (severity === "medium") return "warn";
  return "success";
}

function decisionVariant(decision: string): PillVariant {
  if (decision === "blocked") return "danger";
  if (decision === "flagged") return "warn";
  return "success";
}

export function InjectionDrawer({ detectionId, onClose }: Props) {
  const detailQuery = useQuery<InjectionDetectionDetail, Error>({
    queryKey: ["safety", "detection", detectionId],
    queryFn: () => api.getInjectionDetectionDetail(detectionId),
  });
  const detail = detailQuery.data;

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: "var(--color-overlay-light)" }}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[640px] flex-col overflow-y-auto bg-[var(--color-background-primary)] shadow-xl"
        aria-label="Injection detection detail"
      >
        <header className="sticky top-0 z-10 flex items-center justify-between border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              Injection detection
            </p>
            <p className="truncate text-sm font-medium">{detectionId}</p>
          </div>
          <IconButton onClick={onClose} label="Close drawer" Icon={IconX} />
        </header>

        <div className="flex-1 px-4 py-3">
          {!detail ? (
            <Skeleton className="h-[400px]" />
          ) : (
            <div className="flex flex-col gap-4">
              {/* Pills row — at-a-glance verdict */}
              <div className="flex flex-wrap items-center gap-2">
                <Pill variant={severityVariant(detail.severity)}>
                  severity: {detail.severity}
                </Pill>
                <Pill variant={decisionVariant(detail.decision)}>
                  decision: {detail.decision}
                </Pill>
                <Pill variant="info">{detail.source_kind.replace(/_/g, " ")}</Pill>
                {detail.llm_judge_called && (
                  <Pill variant="info">
                    haiku: {detail.llm_judge_severity ?? "—"}
                  </Pill>
                )}
              </div>

              {/* When / who / where */}
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                    Detected
                  </p>
                  <p className="mt-0.5">
                    {new Date(detail.created_at).toLocaleString()}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                    Actor
                  </p>
                  <p className="mt-0.5">
                    {detail.actor_kind}
                    {detail.actor_id ? ` · ${detail.actor_id}` : ""}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                    Loan
                  </p>
                  <p className="mt-0.5">
                    {detail.loan_id ? (
                      <a
                        href={`/loans/${detail.loan_id}`}
                        className="text-[var(--color-brand)] hover:underline"
                      >
                        {detail.loan_id.slice(0, 12)}…
                      </a>
                    ) : (
                      "—"
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                    Source id
                  </p>
                  <p className="mt-0.5 text-[var(--color-text-tertiary)]">
                    {detail.source_id
                      ? `${detail.source_id.slice(0, 12)}…`
                      : "—"}
                  </p>
                </div>
              </div>

              {/* Matched patterns */}
              <div>
                <SectionLabel>Matched patterns</SectionLabel>
                <ul className="mt-2 space-y-2">
                  {detail.matched_patterns.map((m, i) => (
                    <li
                      key={`${m.pattern_id}-${i}`}
                      className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] p-2.5"
                    >
                      <div className="flex items-center gap-2">
                        <Pill variant={severityVariant(m.severity_floor)}>
                          {m.severity_floor}
                        </Pill>
                        <span className="font-medium text-xs">
                          {m.pattern_id}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                        {m.description}
                      </p>
                      <code className="mt-1.5 block rounded-md bg-[var(--color-background-secondary)] px-2 py-1 text-[11px] text-[var(--color-text-primary)]">
                        {m.matched_text}
                      </code>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Haiku critique (only on medium-band escalation) */}
              {detail.llm_judge_called && (
                <div>
                  <SectionLabel>Haiku judge critique</SectionLabel>
                  <p className="mt-2 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] p-3 text-xs">
                    {detail.llm_judge_critique || "(no critique returned)"}
                  </p>
                </div>
              )}

              {/* Raw excerpt — the actual offending text */}
              <div>
                <SectionLabel>Raw text excerpt</SectionLabel>
                <pre className="mt-2 max-h-[300px] overflow-y-auto rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] p-3 text-[11px] whitespace-pre-wrap">
                  {detail.raw_text_excerpt}
                </pre>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
