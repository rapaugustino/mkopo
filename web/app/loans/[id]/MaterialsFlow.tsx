"use client";

import { useQuery } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconCheck,
  IconFileText,
  IconGavel,
  IconScale,
  IconShieldCheck,
} from "@tabler/icons-react";
import { motion } from "motion/react";

import { api, type LoanStage, type MaterialsStatus } from "@/lib/api";

/**
 * Materials lineage flow + drift indicator.
 *
 * Visual story of what a "decision integrity" check actually is. Four
 * dots, three directed edges, one cryptographic hash:
 *
 *     [Documents] → [Extractions] → [Rules] → [Decision]
 *                                              ╰─ sha256(canonical(…))
 *
 * The hash you see is the materials hash that fed the latest
 * decision. When anything upstream changes — a document re-upload,
 * an extraction override, a borrower meta edit — the current hash
 * diverges from the decision-time hash. The graph turns red and
 * forward stage transitions get blocked server-side (see
 * services/loans.py).
 *
 * Replaces the older single-line yellow ``MaterialsDriftBanner``
 * because the graph carries the concept better than prose: anyone
 * who sees it instantly groks "these four inputs are what gets
 * hashed; if any change, the decision is stale."
 *
 * Stages: ``decision``, ``conditions``, ``closing``, ``approved`` —
 * earlier stages don't have a decision to drift against.
 */

interface Props {
  loanId: string;
  stage: LoanStage;
}

const RELEVANT_STAGES: LoanStage[] = [
  "decision",
  "conditions",
  "closing",
  "approved",
];

/** Four nodes in pipeline order: where the data starts vs. where
 *  it ends up. Each renders as a circle with an icon and a label
 *  underneath; the SVG canvas places them at fixed x-coordinates
 *  so the connecting edges have stable endpoints. */
const NODES = [
  { id: "documents", label: "Documents", Icon: IconFileText },
  { id: "extractions", label: "Extractions", Icon: IconScale },
  { id: "rules", label: "Rules", Icon: IconShieldCheck },
  { id: "decision", label: "Decision", Icon: IconGavel },
] as const;

export function MaterialsFlow({ loanId, stage }: Props) {
  const relevant = RELEVANT_STAGES.includes(stage);
  const { data } = useQuery<MaterialsStatus, Error>({
    queryKey: ["materials-status", loanId],
    queryFn: () => api.getMaterialsStatus(loanId),
    enabled: relevant,
    refetchInterval: relevant ? 15_000 : false,
  });

  if (!relevant || !data) return null;

  const drifted = data.drifted;
  // Edge colour — green when clean, red when drifted. Single
  // accent everywhere keeps the visual language consistent with
  // the rest of the app (we don't introduce a third palette).
  const accent = drifted ? "var(--color-text-danger)" : "var(--color-brand)";
  const accentBg = drifted
    ? "var(--color-background-danger)"
    : "var(--color-background-success)";

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="rounded-lg border-[0.5px] bg-[var(--color-background-primary)] px-4 py-3"
      style={{
        borderColor: drifted
          ? "var(--color-text-danger)"
          : "var(--color-border-tertiary)",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-flex h-5 w-5 items-center justify-center rounded-full"
            style={{ background: accentBg, color: accent }}
          >
            {drifted ? <IconAlertTriangle size={11} /> : <IconCheck size={11} />}
          </span>
          <p
            className="text-[12px] font-medium"
            style={{ color: drifted ? "var(--color-text-danger)" : "var(--color-text-primary)" }}
          >
            {drifted
              ? "Materials drifted — decision is stale"
              : "Materials integrity verified"}
          </p>
        </div>
        <span
          className="hidden font-mono text-[10px] tracking-tight text-[var(--color-text-tertiary)] sm:inline"
          title="current materials hash"
        >
          {data.current_hash.slice(0, 16)}…
        </span>
      </div>

      {/* The flow itself. SVG so we can render the edges as smooth
          lines connecting node centers; arrowheads are a small
          polygon marker. ``viewBox`` is a fixed 320×56 box so the
          shapes scale with the container width. */}
      <div className="mt-3">
        <svg
          viewBox="0 0 320 56"
          className="h-12 w-full"
          aria-label="Materials lineage flow"
        >
          {/* Arrow marker definition — re-used by each edge. */}
          <defs>
            <marker
              id={`mat-arrow-${drifted ? "red" : "green"}`}
              viewBox="0 0 8 8"
              refX="6"
              refY="4"
              markerWidth="6"
              markerHeight="6"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 z" fill={accent} />
            </marker>
          </defs>

          {NODES.map((n, i) => {
            const cx = 28 + i * 88;
            const cy = 18;
            return (
              <g key={n.id}>
                <circle
                  cx={cx}
                  cy={cy}
                  r={11}
                  fill={accentBg}
                  stroke={accent}
                  strokeWidth={0.8}
                />
                {i < NODES.length - 1 && (
                  <line
                    x1={cx + 12}
                    y1={cy}
                    x2={28 + (i + 1) * 88 - 14}
                    y2={cy}
                    stroke={accent}
                    strokeWidth={1.2}
                    strokeDasharray={drifted ? "3 3" : "none"}
                    markerEnd={`url(#mat-arrow-${drifted ? "red" : "green"})`}
                  />
                )}
                <text
                  x={cx}
                  y={47}
                  textAnchor="middle"
                  fontSize="9"
                  fill="var(--color-text-secondary)"
                  fontFamily="inherit"
                >
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Helper copy. Two states with friendly copy that explains
          what just happened. */}
      <p className="mt-2 text-[11.5px] leading-relaxed text-[var(--color-text-secondary)]">
        {drifted ? (
          <>
            An extraction, document, or borrower-supplied field changed since the
            decision was drafted. Forward stage transitions are blocked until
            the decision agent re-runs against the current materials.
          </>
        ) : (
          <>
            Every input the decision used — documents, accepted extractions,
            borrower meta, guarantor list — is unchanged. The cryptographic
            hash above is what we'll compare against on the next mutation.
          </>
        )}
      </p>
    </motion.div>
  );
}

/** Icon set re-exported for adjacent components that want to mirror
 *  the node palette. Kept inline to avoid a tiny shared module. */
export const MATERIALS_NODE_ICONS = {
  documents: IconFileText,
  extractions: IconScale,
  rules: IconShieldCheck,
  decision: IconGavel,
} as const;
